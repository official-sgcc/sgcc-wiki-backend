# CLAUDE.md

## 프로젝트 컨텍스트

이 프로젝트는 FastAPI + SQLModel + SQLite로 만든 소규모 위키 백엔드(`sgcc-wiki-backend`)입니다. 위키 문서 CRUD와 버전/diff 관리, JWT 인증, 태그·카테고리 분류, 문서별 권한, 자동 백업을 제공합니다. 프론트엔드는 별도 리포지토리에서 `http://localhost:5173`(개발) 또는 `FRONTEND_URL`(운영)로 동작합니다.

## 코드 스타일

- 들여쓰기는 스페이스 4칸, 따옴표는 **싱글 쿼트**(`'foo'`)를 우선
- 라우터를 별도 모듈로 분리하지 않고 **`main.py` 한 파일**에 둡니다. 새 엔드포인트도 같은 파일에 추가
- 모델/스키마는 `schemas/` 하위에 한 도메인 = 한 파일
- 인증·검증 유틸리티는 `login_utils.py`로 분리 (해시, JWT, `validate_username`, `validate_password`)
- 모든 핸들러는 `async def`로 작성
- DB 세션은 **`with Session(engine) as session:`** 블록 패턴. 핸들러 안에서 직접 세션을 열고 닫음(의존성 주입 패턴 사용하지 않음)
- 한 줄 walrus 사용 OK: `if not (doc := session.get(WikiDoc, title)):`
- 에러는 `HTTPException(status_code=..., detail='...')` 패턴, detail은 영어 한 문장
- import 순서: 표준 라이브러리 → 서드파티 → 로컬, 한 줄에 하나씩
- 새 파이썬 파일을 함부로 만들지 말고, 기존 파일에 추가하는 쪽을 우선
- 주석은 **WHY가 비자명할 때만** 한 줄. "WHAT은 이름이 설명한다"가 원칙
- README/CLAUDE 외에 새 마크다운 문서를 만들지 말 것

## 명령어

```bash
# 의존성 설치 (런타임)
pip install fastapi sqlmodel bcrypt pyjwt python-dotenv uvicorn apscheduler diff_match_patch slowapi pyotp

# 의존성 설치 (테스트)
pip install pytest httpx

# 개발 서버 실행 (자동 리로드)
uvicorn main:app --reload

# 테스트 전체 실행
pytest

# 테스트 한 파일만
pytest tests/test_documents.py -v

# 문법 검증 (린트 도구 미설정, py_compile로 빠른 체크)
python3 -m py_compile main.py login_utils.py schemas/*.py
```

빌드/배포 스크립트는 없습니다. 운영은 `uvicorn` 직접 실행 또는 systemd/Docker 같은 외부 도구에 위임.

## 주의 사항

### 모듈 로드 순서가 중요한 부분

- `load_dotenv()`는 **`os.getenv` 호출보다 반드시 먼저**. `main.py` 상단에서 import 직후 호출하는 패턴을 깨지 말 것 (과거에 이 순서가 뒤바뀌어 `FRONTEND_URL`이 적용 안 됐던 적 있음).
- `engine = create_engine(f'sqlite:///{DB_PATH}')`와 `SQLModel.metadata.create_all(engine)`은 모듈 임포트 시점에 실행됨. 테스트는 `DB_PATH` 환경변수 + `importlib.reload(main)`로 격리 (`tests/conftest.py` 참조).

### 인증 헤더 (이중 지원)

`get_current_user`는 두 헤더를 모두 받습니다:
- 표준: `Authorization: Bearer <token>`
- 호환: `auth: <token>` (구버전 프론트 호환용)

**둘 다 살려둘 것.** 프론트엔드를 깨지 않기 위함. CORS `allow_headers`에도 둘 다 등록되어야 함.

### JWT 토큰 종류 구분 (`purpose` 클레임)

같은 `JWT_SECRET_KEY`로 서명되는 토큰이 여러 종류(access / mfa / email_verify 등)라, 각 토큰은 `purpose` 클레임으로 용도를 구분한다. **정식 세션 토큰(`create_jwt_token`)은 `purpose='access'`를 담고, `verify_jwt_token`은 `purpose == 'access'`를 반드시 확인한다.** 이 검사를 빼면 2FA 1단계의 `mfa_token`이나 이메일 인증 토큰이 세션 토큰으로 통과해 **2FA가 우회**된다(실제로 있었던 취약점). 새 토큰 종류를 추가할 때도 고유한 `purpose`를 부여하고 해당 검증 함수에서 확인할 것.

> 이 규칙 도입 시점 이전에 발급된 access 토큰에는 `purpose`가 없어 **전부 무효화**된다(사용자 재로그인 필요). 서명 키를 바꾸는 것과 동일한 영향이므로 배포 노트에 남길 것.

### 2FA(TOTP) 재사용 방지

TOTP 코드는 `matched_totp_step`이 매칭된 타임스텝을 반환하고, `WikiUser.totp_last_step`에 마지막 성공 스텝을 저장해 **같은 코드 재사용(replay)을 막는다**(login/2fa·disable에서 `step > totp_last_step` 강제). `/2fa/enable`은 소유 증명일 뿐이라 스텝을 소비하지 않는다(설정 직후 같은 창에서의 정상 로그인 유지). bool만 반환하는 단순 `verify`로 회귀시키지 말 것.

### 스키마 변경

`SQLModel.metadata.create_all(engine)`은 **없는 테이블만 만들고 기존 테이블은 절대 변경하지 않습니다.** 모델에 컬럼을 추가/변경할 때:

1. 새 컬럼은 `nullable=True`(또는 `| None`) 형태로 추가해서 기존 row를 깨지 않게 함
2. 기존 데이터 백필이 필요하면 README의 "스키마 변경 시 주의" 섹션에 SQL 스니펫을 추가

`created_by` 컬럼이 대표적 예시. Alembic은 아직 도입 안 함.

### 권한 모델

- 사용자 권한: `admin` / `club_member` / `login_user` / (비로그인은 `current_user=None`)
- 문서별 권한은 `Permissions` 테이블에 JSON 리스트로 저장 (`update`/`move`/`delete`/`comment`)
- `check_document_permission`은 `None`을 안전하게 처리하지만, 새 엔드포인트를 추가할 때 **`current_user is None` 체크를 직접 넣는 패턴을 따를 것** (`/tags`, `/categories` 생성 핸들러 참고)
- **문서 작성자**(`WikiDoc.created_by`)는 자기 문서를 권한 체크 없이 삭제 가능. 다른 동작(update 등)에는 적용 안 됨

### Rate limiting

- `/login` 분당 5회, `/register` 분당 3회. 새 인증/민감 엔드포인트를 추가하면 `@limiter.limit(...)` 데코레이터와 `request: Request` 인자를 같이 붙일 것

### 동시성

- 문서 업데이트 시 `version_number` PK 충돌 가능 → `IntegrityError` 재시도(최대 3회) 패턴이 `update_document`에 들어 있음. 비슷한 컬럼/PK를 다룰 때 동일 패턴 적용

### 백업과 로그

- 백업은 `shutil.copy2`가 아니라 **`sqlite3` Backup API** 사용 (트랜잭션 안전). `backup_database`를 수정할 때 다시 단순 파일 복사로 회귀시키지 말 것
- 로그는 stdout + `logs/app.log` 둘 다로 흐름. `RotatingFileHandler`로 5MB × 5개 회전

### 직접 수정하지 말 것

- `.env` — 시크릿. **절대 git에 커밋되지 않도록** 유지 (`.gitignore`에 포함됨)
- `wiki.db` — SQLite 데이터. API/마이그레이션을 통해서만 변경. 임시로 수정해야 하면 `db_backups/`에 먼저 사본
- `db_backups/`, `logs/`, `__pycache__/` — 자동 생성물. git에 포함하지 말 것
- 기존 마이그레이션 안내(README의 SQL 스니펫)는 **누적**해서 적어둘 것. 지우지 말 것

### 입력 검증 정책 (변경 시 README와 함께 갱신)

- username: 3~32자, `[a-zA-Z0-9_-]`만 허용
- password: 8자 이상, 영문+숫자 모두 포함
- `RESERVED_USERNAMES = {'guest', 'admin', 'system', 'bot', 'anonymous'}` — 가입 거부
- 태그/카테고리는 문서 생성·수정 시 존재 여부 검증 (`validate_tags_and_category`)
- 검색 키워드는 `strip()` 후 빈 문자열이면 400

### 로그인 응답 통일

`POST /login`은 "아이디가 없음"과 "비밀번호 틀림"을 **같은 메시지(`401 Invalid username or password.`)** 로 반환. **username enumeration 방지**가 목적이라 메시지를 분리하지 말 것.

### 테스트

- 각 테스트는 임시 SQLite 파일로 격리됨 (`tests/conftest.py`의 `client` fixture)
- `admin_headers` fixture는 DB에 직접 admin 사용자를 만들어 토큰을 발급함 (register API는 `RESERVED_USERNAMES` 때문에 admin 가입 불가)
- 새 기능을 추가하면 **smoke test 한 개라도 같이** 추가하는 게 표준
