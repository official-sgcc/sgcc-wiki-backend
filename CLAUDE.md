# CLAUDE.md

## 프로젝트 컨텍스트

FastAPI + SQLModel + SQLite로 만든 소규모 위키 백엔드(`sgcc-wiki-backend`). 문서 CRUD와 버전/diff, JWT 인증(+2FA·이메일 인증), 태그·카테고리 분류, 문서별 권한, 자동 백업을 제공한다. 프론트엔드는 별도 리포지토리이며 `http://localhost:5173`(개발) 또는 `FRONTEND_URL`(운영)에서 이 API를 호출한다.

**엔드포인트 경로·응답 형태·인증 헤더는 프론트와의 계약이다.** 리팩터링하더라도 계약을 바꾸지 말 것.

## 모듈 구성

**루트에 두는 파이썬 파일은 `main.py` 하나뿐이다.** 나머지는 전부 패키지 안에 넣는다.

```
main.py              앱 조립 — 미들웨어, lifespan, include_router만. 여기에 엔드포인트를 두지 말 것
core/
  config.py          load_dotenv, 로깅, 환경변수 상수, limiter
  database.py        engine + create_all (모델 임포트로 metadata 등록)
  deps.py            get_current_user, check_document_permission, validate_tags_and_category
  login_utils.py     비밀번호 해시, JWT/TOTP 토큰, 입력 검증
  maintenance.py     send_email, backup_database, bootstrap_admin
routers/             도메인별 엔드포인트: documents(+검색·버전·diff) / users(+2FA·이메일·재설정) / tags / categories
schemas/             SQLModel 테이블 + 요청 바디 모델
```

의존 방향은 `main → routers → core.deps/core.maintenance → core.database → core.config` 한 방향이다. 역방향 임포트(순환)를 만들지 말 것. 임포트는 상대(`from .config import`)가 아니라 **절대 경로**(`from core.config import`)로 쓴다. 새 엔드포인트는 해당 도메인 라우터에 추가하고, 도메인이 늘면 `routers/`에 파일을 하나 더 만들어 `main.py`에서 `include_router`한다.

## 코드 스타일

- 들여쓰기 스페이스 4칸, 따옴표는 **싱글 쿼트**(`'foo'`) 우선
- 모델/스키마는 `schemas/` 하위에 한 도메인 = 한 파일
- 인증·검증 유틸은 `core/login_utils.py`에 모은다 (해시, JWT/TOTP 토큰, `validate_username` / `validate_password` / `validate_email`)
- 모든 핸들러는 `async def`
- DB 세션은 **`with Session(engine) as session:`** 블록. 핸들러 안에서 직접 열고 닫는다(의존성 주입 패턴 미사용)
- 한 줄 walrus OK: `if not (doc := session.get(WikiDoc, title)):`
- 에러는 `HTTPException(status_code=..., detail='...')`, detail은 영어 한 문장
- import 순서: 표준 라이브러리 → 서드파티 → 로컬, 한 줄에 하나씩
- 새 파이썬 파일을 함부로 만들지 말고 기존 파일에 추가하는 쪽을 우선
- 핸들러·헬퍼에는 한국어 docstring(요약 / Args / Returns / Raises). 본문 주석은 **WHY가 비자명할 때만** 한 줄
- README/CLAUDE 외에 새 마크다운 문서를 만들지 말 것

## 명령어

```bash
# 의존성 설치 (런타임)
pip install fastapi sqlmodel bcrypt pyjwt python-dotenv uvicorn apscheduler diff_match_patch slowapi pyotp

# 의존성 설치 (테스트)
pip install pytest httpx

# 개발 서버 실행 (자동 리로드)
uvicorn main:app --reload

# 테스트 전체 / 한 파일
pytest
pytest tests/test_documents.py -v

# 문법 검증 (린트 도구 미설정)
python3 -m py_compile main.py core/*.py routers/*.py schemas/*.py
```

빌드/배포 스크립트는 없다. 운영은 `uvicorn` 직접 실행 또는 systemd/Docker 같은 외부 도구에 위임.

## 깨뜨리면 안 되는 것들

### 모듈 로드 순서

- `load_dotenv()`는 **`os.getenv` 호출보다 반드시 먼저**. `core/config.py`/`core/login_utils.py` 상단의 호출 패턴을 깨지 말 것 (과거에 순서가 뒤바뀌어 `FRONTEND_URL`이 적용 안 됐던 적 있음). 환경변수는 각 모듈에서 `os.getenv`로 다시 읽지 말고 `core/config.py`에서 가져다 쓴다
- `engine = create_engine(...)`과 `SQLModel.metadata.create_all(engine)`은 `core/database.py` 임포트 시점에 실행된다. 그래서 테스트는 `DB_PATH`를 바꾼 뒤 `sys.modules`에서 앱 모듈(`main`/`core.*`/`routers.*`)을 지우고 다시 임포트한다(`tests/conftest.py`의 `reload_app`). **`schemas`는 지우면 안 된다** — 테이블 클래스가 같은 metadata에 재등록되며 에러가 난다
- 라우터는 `from core.maintenance import send_email`처럼 이름을 자기 네임스페이스로 가져온다. 테스트에서 monkeypatch할 때는 원본 모듈이 아니라 **사용하는 쪽**(`routers.users.send_email`)을 패치할 것
- `JWT_SECRET_KEY`가 없으면 `login_utils` 임포트 단계에서 `RuntimeError`로 기동을 거부한다(fail-fast). 기본 서명 키로 돌아가는 fallback을 넣지 말 것

### 인증 헤더 (이중 지원)

`get_current_user`는 두 헤더를 모두 받는다.
- 표준: `Authorization: Bearer <token>`
- 호환: `auth: <token>` (구버전 프론트용)

**둘 다 살려둘 것.** CORS `allow_headers`에도 둘 다 등록되어야 한다. 토큰이 없으면 예외 대신 `None`을 반환하므로, 로그인 필수 여부는 각 핸들러가 `current_user is None`을 직접 검사해 정한다.

### JWT 토큰 종류 구분 (`purpose` 클레임)

같은 `JWT_SECRET_KEY`로 서명되는 토큰이 여러 종류(access / mfa / password_reset / email_verify)라, 각 토큰은 `purpose` 클레임으로 용도를 구분한다. **`create_jwt_token`은 `purpose='access'`를 담고 `verify_jwt_token`은 `purpose == 'access'`를 반드시 확인한다.** 이 검사를 빼면 2FA 1단계의 `mfa_token`이나 이메일 인증 토큰이 세션 토큰으로 통과해 **2FA가 우회**된다(실제로 있었던 취약점, 회귀 테스트: `test_mfa_token_cannot_be_used_as_access_token`). 새 토큰 종류를 추가할 때도 고유 `purpose`를 부여하고 해당 검증 함수에서 확인할 것.

비밀번호 재설정 토큰은 `JWT_SECRET_KEY + 현재 비밀번호 해시`로 서명한다 → 재설정에 성공하면 해시가 바뀌어 토큰이 자동 무효(단일 사용). 이 서명 방식을 단순 비밀키 서명으로 되돌리지 말 것.

### 2FA(TOTP) 재사용 방지

`matched_totp_step`은 bool이 아니라 **매칭된 타임스텝**을 반환하고, `WikiUser.totp_last_step`에 마지막 성공 스텝을 저장해 같은 코드의 재사용(replay)을 막는다(`/login/2fa`·`/2fa/disable`에서 `step > totp_last_step` 강제). `/2fa/enable`은 소유 증명일 뿐이라 스텝을 소비하지 않는다(설정 직후 같은 창에서의 정상 로그인 유지). bool만 반환하는 `pyotp.verify`로 회귀시키지 말 것.

### 로그인 응답 통일

`POST /login`은 "아이디 없음"과 "비밀번호 틀림"을 **같은 401 `Invalid username or password.`** 로 반환한다(username enumeration 방지). 아이디가 없을 때도 `DUMMY_PASSWORD_HASH`로 bcrypt 검증을 수행해 응답 시간을 맞춘다(timing attack 방지). 메시지를 분리하거나 dummy 해시 검증을 걷어내지 말 것.

같은 이유로 `POST /password-reset/request`는 계정·이메일 존재 여부와 무관하게 항상 동일한 200을 반환한다.

### JSON 컬럼 검색

`WikiDoc.tags` / `WikiDoc.category`는 SQLAlchemy `JSON` 컬럼이다. **`.contains(...)`는 JSON 구조 검색이 아니라 직렬화된 문자열 LIKE**라서 `category.contains({'name': 'X'})`처럼 쓰면 저장된 `{"name": "X", "parent": null}`과 매칭되지 않는다(실제로 카테고리 문서 조회가 통째로 빈 배열을 반환한 적 있음). SQLite의 JSON 함수를 쓸 것:

```python
func.json_extract(WikiDoc.category, '$.name').in_(target_names)                      # 카테고리 정확 일치
tag_entries = func.json_each(WikiDoc.tags).table_valued('value', joins_implicitly=True)
select(1).select_from(tag_entries).where(func.json_extract(tag_entries.c.value, '$.name') == name).exists()
```

`GET /search?search_type=tag`는 LIKE로 후보를 좁힌 뒤 파이썬에서 정확 매칭하는 방식을 쓴다. 어느 쪽이든 **부분 문자열이 매칭되면 안 된다**(`Python` 검색에 `PythonDev`가 걸리면 회귀).

### 페이지네이션

목록 계열(`/documents`, `/search`, `/tags/{name}/documents`, `/categories/{name}/documents`)은 `limit`(기본 None = 전체) + `offset`(기본 0)을 받고, **`limit`이 있을 때만 offset이 적용된다.** 가능하면 SQL(`.offset().limit()`)에서 자르고, 파이썬 슬라이싱으로 되돌리지 말 것.

### 동시성

문서 수정 시 `WikiDocVersion`의 `version_number` PK가 충돌할 수 있어 `update_document`에 `IntegrityError` 재시도(최대 3회, 실패 시 409) 패턴이 들어 있다. 비슷한 "카운트 + 1" PK를 다룰 때 같은 패턴을 적용할 것.

### 권한 모델

- 사용자 권한: `admin` / `club_member` / `login_user` (비로그인은 `current_user=None`)
- 문서별 권한은 `Permissions` 테이블에 action별 JSON 리스트(`update`/`move`/`delete`/`comment`). 문서 생성 시 기본값은 update·comment = 전체 로그인 등급, move·delete = admin
- `check_document_permission`은 `None`을 안전하게 거부하지만, 로그인 자체가 필수인 엔드포인트는 **`current_user is None` 체크를 직접 넣는 패턴**을 따를 것 (`POST /documents`, `POST /tags`, `POST /categories` 참고)
- **문서 작성자**(`WikiDoc.created_by`)는 자기 문서를 권한 체크 없이 삭제 가능. 삭제에만 적용되며 update/move에는 적용하지 않는다
- `DELETE /tags`, `PUT|DELETE /categories`는 admin 전용

### Rate limiting

`@limiter.limit(...)` + `request: Request` 인자를 **같이** 붙여야 동작한다(인자를 빠뜨리면 slowapi가 못 잡는다).

| 분당 3회 | 분당 5회 |
|---|---|
| `/register`, `/password-reset/request`, `/email/verify-request` | `/login`, `/login/2fa`, `/password-reset/confirm`, `/email/verify` |

새 인증·메일 발송·민감 엔드포인트를 추가하면 같은 데코레이터를 붙일 것.

### 스키마 변경

`SQLModel.metadata.create_all(engine)`은 **없는 테이블만 만들고 기존 테이블은 절대 변경하지 않는다.** 컬럼을 추가/변경할 때:

1. 새 컬럼은 `| None`(nullable) 또는 기본값이 있는 형태로 추가해 기존 row를 깨지 않게 한다
2. 기존 데이터 백필·제약 변경이 필요하면 README "스키마 마이그레이션" 섹션에 SQL 스니펫을 **누적**해서 추가한다(기존 스니펫을 지우지 말 것)

Alembic은 아직 도입하지 않았다.

### 백업과 로그

- 백업은 `shutil.copy2`가 아니라 **`sqlite3` Backup API**(트랜잭션 안전). `backup_database`를 단순 파일 복사로 회귀시키지 말 것
- 로그는 stdout + `logs/app.log` 둘 다. `RotatingFileHandler` 5MB × 5개

### 입력 검증 정책 (변경 시 README도 같이 갱신)

- username: 3~32자, `[a-zA-Z0-9_-]`만
- password: 8자 이상, 영문 + 숫자 모두 포함
- email: `validate_email` 정규식 + DB unique(중복 시 409). 저장하면 항상 미인증 상태로 리셋
- `RESERVED_USERNAMES = {'guest', 'admin', 'system', 'bot', 'anonymous'}` — 가입 거부
- 태그/카테고리는 문서 생성·수정 시 존재 검증(`validate_tags_and_category`). 로그인 사용자의 문서 생성·수정에 한해 **없는 태그는 자동 생성**, 카테고리는 자동 생성하지 않고 400
- 검색 키워드는 `strip()` 후 빈 문자열이면 400

### 테스트

- 각 테스트는 임시 SQLite 파일로 격리 (`tests/conftest.py`의 `client` fixture)
- `auth_headers`는 register→login으로 일반 사용자를, `admin_headers`는 DB에 직접 admin을 만들어 토큰을 발급한다(register API는 `RESERVED_USERNAMES` 때문에 admin 가입 불가)
- 두 fixture 모두 `({'auth': token}, username)` 튜플을 반환한다
- 새 기능을 추가하면 **smoke test 한 개라도 같이** 추가하는 게 표준. 보안 관련 수정은 회귀 테스트를 남길 것

### 직접 수정하지 말 것

- `.env` — 시크릿. 절대 커밋하지 말 것 (`.gitignore` 포함)
- `wiki.db` — 실제 데이터. API/마이그레이션을 통해서만 변경하고, 손대야 하면 `db_backups/`에 사본부터
- `db_backups/`, `logs/`, `__pycache__/` — 자동 생성물, git 포함 금지
