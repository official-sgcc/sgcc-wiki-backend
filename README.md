# sgcc-wiki-backend

소규모 위키 서비스를 위한 FastAPI 백엔드. 문서/태그/카테고리 관리, 버전 기록, JWT 인증, 자동 백업을 제공합니다.

## 주요 기능

- 위키 문서 CRUD + 버전 관리 + 버전 간 diff
- JWT 기반 인증, bcrypt 비밀번호 해시
- 2단계 인증(2FA, TOTP) + 이메일 등록·인증 + 이메일 기반 비밀번호 재설정
- 아이디·비밀번호 정책 검증
- 태그·카테고리 분류 (CRUD + 존재 검증)
- 문서별 권한 시스템 (`update`/`move`/`delete`/`comment`)
- 자정 자동 DB 백업 (SQLite Backup API 사용)
- 로그인·회원가입 rate limiting
- 관리자 계정 자동 부트스트랩

## 기술 스택

- **FastAPI** — 웹 프레임워크
- **SQLModel + SQLAlchemy + SQLite** — ORM/DB
- **bcrypt + PyJWT** — 인증
- **slowapi** — rate limiting
- **APScheduler** — 백업 스케줄러
- **diff-match-patch** — 버전 diff
- **pytest + httpx** — 테스트

## 디렉토리 구조

```
sgcc-wiki-backend/
├── main.py                 # FastAPI 앱, 모든 엔드포인트
├── login_utils.py          # 비밀번호 해시, JWT, 입력 검증
├── schemas/
│   ├── wiki_doc.py         # WikiDoc, WikiDocVersion 모델
│   ├── wiki_user.py        # WikiUser 모델
│   ├── permissions.py      # 문서별 권한 모델
│   ├── tags.py             # 태그 모델
│   └── categories.py       # 카테고리 모델
├── tests/                  # pytest 테스트
├── db_backups/             # 자동 백업 파일 저장 위치
├── logs/                   # 애플리케이션 로그 (app.log, 회전 백업)
├── wiki.db                 # SQLite 데이터베이스 (gitignore 권장)
└── .env                    # 환경변수
```

## 빠른 시작

```bash
pip install fastapi sqlmodel bcrypt pyjwt python-dotenv uvicorn apscheduler diff_match_patch slowapi pyotp
```

`.env` 파일을 프로젝트 루트에 작성한 뒤 서버 실행:

```bash
uvicorn main:app --reload
```

- 서버: http://127.0.0.1:8000
- Swagger UI: http://127.0.0.1:8000/docs

테스트 실행:

```bash
pip install pytest httpx
pytest
```

## 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `JWT_SECRET_KEY` | **(필수, 기본값 없음)** | JWT 서명 키. 미설정이면 서버가 시작을 거부한다(fail-fast). 강력한 랜덤 값으로 설정 |
| `JWT_ALGORITHM` | `HS256` | JWT 알고리즘 |
| `JWT_TOKEN_EXPIRE_MINUTES` | `180` | 토큰 만료(분) |
| `PASSWORD_RESET_EXPIRE_MINUTES` | `30` | 비밀번호 재설정 토큰 만료(분) |
| `MFA_TOKEN_EXPIRE_MINUTES` | `5` | 2FA 임시 토큰 만료(분) |
| `EMAIL_VERIFY_EXPIRE_MINUTES` | `1440` | 이메일 인증 토큰 만료(분, 기본 24시간) |
| `TOTP_ISSUER` | `SGCC Wiki` | 인증앱에 표시되는 발급자 이름 |
| `FRONTEND_URL` | `http://localhost:5173` | CORS 허용 origin, 재설정 링크 base URL |
| `DB_PATH` | `wiki.db` | SQLite 파일 경로 |
| `ADMIN_USERNAME` | — | 관리자 부트스트랩용 아이디 (비어있으면 skip) |
| `ADMIN_PASSWORD` | — | 관리자 부트스트랩용 비밀번호 |
| `SMTP_HOST` | — | 메일 서버 호스트. 미설정 시 재설정 링크를 로그로만 출력 |
| `SMTP_PORT` | `587` | 메일 서버 포트(STARTTLS) |
| `SMTP_USER` | — | SMTP 로그인 사용자 (비어있으면 로그인 생략) |
| `SMTP_PASSWORD` | — | SMTP 로그인 비밀번호 |
| `SMTP_FROM` | `no-reply@sgcc-wiki.local` | 보내는 사람 주소 |

## 관리자 계정 부트스트랩

`.env`에 `ADMIN_USERNAME`/`ADMIN_PASSWORD`를 설정하면 서버 시작 시 자동으로 처리됩니다.

- 해당 아이디의 사용자가 존재하면 → `admin` 권한으로 승격
- 존재하지 않으면 → 새 admin 계정 생성
- 두 값 중 하나라도 비어있으면 skip

## 인증

### 토큰 발급

`POST /login`이 성공하면 JWT 토큰을 반환합니다.

### 토큰 전송

두 가지 헤더 형식을 모두 지원합니다.

- **표준**: `Authorization: Bearer <token>`
- **호환**: `auth: <token>` (기존 형식)

토큰 만료 시 401을 반환합니다. 클라이언트는 다시 로그인해서 새 토큰을 받아야 합니다.

> **주의**: `JWT_SECRET_KEY`가 설정되지 않으면 서버는 시작을 거부합니다(fail-fast). 기본 서명 키로 실행되지 않도록 하기 위함입니다.

> **배포 주의**: 세션 토큰은 `purpose='access'` 클레임을 담고 검증하도록 강화되었습니다(2FA 우회 방지). 이 변경 이전에 발급된 토큰에는 해당 클레임이 없어 **모두 무효화**되므로, 배포 후 모든 사용자가 다시 로그인해야 합니다.

### 2단계 인증 (2FA, TOTP)

인증앱(Google Authenticator 등)을 이용한 선택적 2단계 인증을 지원합니다.

1. `POST /2fa/setup` — 시크릿과 `otpauth://` URI 발급 (아직 활성화 아님). 프론트는 이 URI로 QR을 그리거나 secret을 수동 입력하게 함
2. `POST /2fa/enable` — 인증앱의 6자리 코드로 확인 후 활성화
3. 이후 로그인은 **2단계**로 동작:
   - `POST /login` → 진짜 토큰 대신 `{mfa_required: true, mfa_token}` 반환
   - `POST /login/2fa` (`{mfa_token, code}`) → 최종 `{token}` 발급
4. `POST /2fa/disable` — 현재 코드를 확인하고 비활성화

2FA를 켜지 않은 사용자는 기존과 동일하게 `POST /login`에서 곧바로 `{token}`을 받습니다.

### 이메일 등록·인증

회원가입은 아이디/비밀번호만 받으며, 이메일은 로그인 후 별도로 등록합니다.

1. `PUT /email` (`{email}`) — 본인 이메일 등록/변경(로그인 필요). 저장 시 항상 **미인증** 상태가 되고 인증 링크가 발송됨. 이메일은 계정 간 유일(중복 시 409)
2. 메일의 링크로 접속 → `POST /email/verify` (`{token}`)로 인증 완료
3. `POST /email/verify-request` — 인증 메일 재발송(로그인 필요)

인증 토큰에는 대상 이메일이 담겨 있어, 인증 전에 이메일을 다시 바꾸면 이전 링크는 무효가 됩니다. 토큰은 24시간(`EMAIL_VERIFY_EXPIRE_MINUTES`) 후 만료됩니다.

### 비밀번호 재설정

이메일 기반 재설정 플로우입니다. **계정에 인증된(verified) 이메일이 등록돼 있어야** 실제로 링크가 발송됩니다.

1. `POST /password-reset/request` (`{username}`) — 재설정 링크를 이메일로 발송. 계정/이메일 존재·인증 여부와 무관하게 항상 동일한 200을 반환(enumeration 방지)
2. 메일의 링크로 접속 → `POST /password-reset/confirm` (`{token, new_password}`)

재설정 토큰은 발급 시점의 비밀번호 해시로 서명되어 **한 번 쓰면 무효**(단일 사용)이며 30분 후 만료됩니다. 메일 전송은 `SMTP_*` 환경변수가 설정되면 실제 발송, 미설정이면 링크를 로그에 남깁니다.

### 사용자 권한

- `admin` — 모든 작업 가능
- `club_member` — 일부 권한
- `login_user` — 일반 가입자 기본값
- (`guest` — 비로그인)

조회/생성 외 일부 엔드포인트는 admin 권한이 필요합니다 (`DELETE /tags`, `DELETE /categories`).

문서 수정·삭제·이동·댓글은 **문서별로 따로 설정된 권한**(`Permissions` 테이블)에 따라 허용 여부가 결정됩니다. 단, 문서 작성자(`WikiDoc.created_by`)는 자신이 만든 문서를 별도 권한 없이 삭제할 수 있습니다.

### 입력 정책

- **아이디**: 3~32자, 영문/숫자/`_`/`-`만 허용
- **비밀번호**: 최소 8자, 영문·숫자 모두 포함
- `guest`, `admin`, `system`, `bot`, `anonymous`는 가입 불가 (예약어)

### Rate limiting

| 엔드포인트 | 제한 |
|---|---|
| `POST /login` | 분당 5회 / IP |
| `POST /login/2fa` | 분당 5회 / IP |
| `POST /register` | 분당 3회 / IP |
| `POST /password-reset/request` | 분당 3회 / IP |
| `POST /password-reset/confirm` | 분당 5회 / IP |
| `POST /email/verify-request` | 분당 3회 / IP |
| `POST /email/verify` | 분당 5회 / IP |

초과 시 `429 Too Many Requests`.

## API

### 문서

- `GET /documents` - 문서 목록 조회
  - parameter:
    - keyword - 제목 및 본문 검색어 (선택)
    - limit - 한 번에 가져올 개수 (선택, 미지정 시 전체)
    - offset - 건너뛸 개수 (선택, 기본 0)
  - response:
    - 문서 목록 (제목, 본문, 태그 등)

- `POST /documents` - 문서 생성
  - headers:
    - jwt 토큰
  - request body:
    - title - 문서 제목
    - content - 본문
    - category - 카테고리 (이미 존재해야 함)
    - tags - 태그 리스트 (로그인 상태일 시 존재하지 않는 태그는 생성)
  - response:
    - 문서 생성 메시지

- `GET /documents/{title}` - 문서 조회
  - parameter:
    - title - 문서 제목
  - response:
    - 해당 문서

- `PUT /documents/{title}` - 문서 수정
  - parameter:
    - title - 문서 제목
  - headers:
    - jwt 토큰
  - request body:
    - 수정할 문서 내용 (content, tags 등)
  - response:
    - 수정된 문서

- `DELETE /documents/{title}` - 문서 삭제
  - parameter:
    - title - 문서 제목
  - headers:
    - jwt 토큰 (해당 문서의 작성자이거나, 해당 문서의 delete 권한 보유자)
  - response:
    - 삭제 완료 메시지

- `GET /documents/{title}/versions` - 문서 버전 목록 조회
  - parameter:
    - title - 문서 제목
  - response:
    - 문서 버전 리스트

- `GET /documents/{title}/versions/{version_number}` - 문서 특정 버전 조회
  - parameter:
    - title - 문서 제목
    - version_number - 문서 버전
  - response:
    - 특정 버전 문서

- `GET /documents/{title}/diff/{version_number}` - 특정 버전 수정 사항(diff)
  - parameter:
    - title - 문서 제목
    - version_number - 문서 버전
  - response:
    - 수정 사항(diff 객체)

- `GET /search` - 문서 검색
  - parameter:
    - keyword - 검색어
    - search_type - 검색 규칙(title, title_content, tag)
    - limit - 한 번에 가져올 개수 (선택, 미지정 시 전체)
    - offset - 건너뛸 개수 (선택, 기본 0)
  - response:
    - 검색 결과 문서 리스트
  - 태그 검색은 정확 일치 매칭입니다 (`Python` 검색 시 `PythonDev`는 매칭되지 않음).

### 사용자

- `POST /register` - 회원가입 (분당 3회 제한)
  - request body:
    - username - 아이디 (3~32자, 영문/숫자/`_`/`-`)
    - password - 비밀번호 (최소 8자, 영문/숫자 모두 포함)
  - response:
    - 가입 완료

- `POST /login` - 로그인 (분당 5회 제한)
  - request body:
    - username - 아이디
    - password - 비밀번호
  - response:
    - 2FA 미사용: jwt 토큰 (`{token}`)
    - 2FA 사용: `{mfa_required: true, mfa_token}` — `POST /login/2fa`로 이어짐
  - 실패 시 아이디/비밀번호 어느 쪽이 틀렸는지 구분되지 않습니다.

- `POST /login/2fa` - 2단계 인증 로그인 마무리 (분당 5회 제한)
  - request body:
    - mfa_token - `/login`이 준 임시 토큰
    - code - 인증앱 6자리 코드
  - response:
    - jwt 토큰

- `GET /users/{username}` - 사용자 정보 조회
  - parameter:
    - username - 아이디
  - response:
    - 사용자 정보 (본인 조회 시에만 email 포함, `password`/`totp_secret`은 항상 제외)

- `POST /password-reset/request` - 재설정 링크 발송 (분당 3회 제한)
  - request body: username
  - response: 항상 동일한 안내 메시지 (enumeration 방지)

- `POST /password-reset/confirm` - 재설정 실행 (분당 5회 제한)
  - request body: token, new_password
  - response: 재설정 완료 / 토큰 무효·만료 시 400

- `POST /2fa/setup` - 2FA 시크릿 발급 (로그인 필요)
  - response: `{secret, otpauth_uri}`

- `POST /2fa/enable` - 코드 확인 후 2FA 활성화 (로그인 필요)
  - request body: code

- `POST /2fa/disable` - 코드 확인 후 2FA 비활성화 (로그인 필요)
  - request body: code

- `PUT /email` - 본인 이메일 등록/변경 (로그인 필요)
  - request body: email
  - response: 저장 후 인증 링크 발송 / 형식 오류 400 / 중복 409

- `POST /email/verify-request` - 인증 메일 재발송 (로그인 필요, 분당 3회)

- `POST /email/verify` - 이메일 인증 완료
  - request body: token

### 태그

- `GET /tags` - 태그 전체 목록 조회
  - response:
    - 태그 전체 목록

- `POST /tags` - 태그 생성
  - headers:
    - jwt 토큰
  - request body:
    - name - 태그 이름
  - response:
    - 태그 생성 완료 메시지

- `GET /tags/{name}/documents` - 해당 태그가 달린 문서 목록 조회
  - parameter:
    - name - 태그 이름 (존재하지 않으면 404)
    - limit - 한 번에 가져올 개수 (선택, 미지정 시 전체)
    - offset - 건너뛸 개수 (선택, 기본 0)
  - response:
    - 태그가 달린 문서 목록 (없으면 빈 리스트)
  - 태그명 정확 일치 매칭입니다 (`Python` 조회 시 `PythonDev` 문서는 제외).
  - **변경**: 기존 `GET /tags/{name}`(태그 단건 조회)를 대체합니다.

- `DELETE /tags/{name}` - 태그 삭제
  - parameter:
    - name - 삭제할 태그 이름
  - headers:
    - jwt 토큰 (admin 권한)
  - response:
    - 삭제 완료 메시지
  - 해당 태그를 사용 중인 모든 문서에서 자동으로 제거됩니다.

### 카테고리

- `GET /categories` - 카테고리 전체 목록 조회
  - response:
    - 카테고리 전체 목록(parent: 상위 카테고리, children: 하위 카테고리)

- `POST /categories` - 카테고리 생성
  - headers:
    - jwt 토큰
  - request body:
    - name - 카테고리 이름
    - parent - 상위 카테고리
  - response:
    - 카테고리 생성 완료 메시지

- `GET /categories/{name}` - 특정 카테고리 정보 조회
  - parameter:
    - name - 카테고리 이름
  - response:
    - 특정 카테고리 정보

- `PUT /categories/{name}` - 카테고리 수정
  - parameter:
    - name - 카테고리 이름
  - headers:
    - jwt 토큰 (admin 권한)
  - request body:
    - parent - 상위 카테고리
  - response:
    - 수정 완료 메시지
  - 카테고리의 하위 노드를 상위 노드로 순환 참조할 수 없습니다.

- `DELETE /categories/{name}` - 카테고리 삭제(하위 카테고리 포함)
  - parameter:
    - name - 삭제할 카테고리 이름
  - headers:
    - jwt 토큰 (admin 권한)
  - response:
    - 삭제 완료 메시지
  - 사용 중인 카테고리는 삭제할 수 없습니다(409). 해당 문서들의 카테고리를 먼저 다른 값으로 옮겨야 합니다.

## 운영 노트

### 본문(content) 렌더링과 XSS

백엔드는 문서 본문(`content`)을 가공 없이 그대로 저장합니다. 마크다운/위키 문법을 자유롭게 보존하기 위함입니다.

**프론트엔드는 본문을 렌더링할 때 반드시 안전한 sanitization을 적용해야 합니다.**

- 마크다운 렌더러 사용 시 HTML 인라인 옵션을 끄거나, DOMPurify 등으로 sanitize
- `<script>`, `<iframe>`, `on*` 이벤트 핸들러 등이 그대로 렌더링되면 XSS 위험

### 자동 백업

- `db_backups/db_backup_YYYYMMDD_HHhMMmSSs.db` 형식으로 매일 자정 백업
- SQLite Backup API를 사용해 트랜잭션 안전하게 복사
- `db_backups/`는 `.gitignore`에 포함되어 있습니다

### 로깅

로그는 stdout과 `logs/app.log`에 동시에 출력됩니다. `logs/app.log`는 5MB마다 자동 회전하며 최대 5개(`app.log.1` ~ `app.log.5`)까지 보관됩니다. `logs/` 디렉토리는 `.gitignore`에 포함되어 있습니다.

다음 이벤트가 기록됩니다.

- 회원가입 성공
- 로그인 성공/실패
- 문서/태그/카테고리 생성·삭제·수정
- 관리자 부트스트랩 결과
- 백업 성공/실패

### 스키마 변경 시 주의

현재 별도의 마이그레이션 도구는 사용하지 않습니다. 서버 시작 시 `SQLModel.metadata.create_all(engine)`이 호출되어 **없는 테이블만 새로 만들고, 이미 존재하는 테이블은 변경하지 않습니다.**

모델(`schemas/*.py`) 컬럼/타입을 바꿔야 할 때는:

1. `db_backups/`로 현재 DB가 백업되어 있는지 확인
2. `wiki.db`를 임시로 옮기거나 백업 후, 서버를 다시 띄워 새 스키마로 재생성
3. 필요 시 이전 데이터를 마이그레이션해서 다시 채워 넣기

#### 기존 wiki.db에 `created_by` 컬럼 백필 (작성자 삭제 권한 도입 시)

`WikiDoc.created_by` 컬럼은 새 문서에는 자동으로 채워지지만, 기존 문서는 `NULL`이라 작성자 삭제 권한을 못 받습니다. 백필이 필요하면 SQLite에서 직접 실행하세요:

```sql
ALTER TABLE wikidoc ADD COLUMN created_by VARCHAR;
UPDATE wikidoc
SET created_by = (
    SELECT updated_by FROM wikidocversion
    WHERE wiki_doc_title = wikidoc.title AND version_number = 1
);
```

#### 기존 wiki.db에 2FA 컬럼 추가 (2단계 인증 도입 시)

`WikiUser`에 `totp_secret`/`totp_enabled` 컬럼이 추가되었습니다. 기존 `wiki.db`에는 없으므로, 백업 후 아래를 실행하세요(미실행 시 사용자 조회가 깨집니다):

```sql
ALTER TABLE wikiuser ADD COLUMN totp_secret VARCHAR;
ALTER TABLE wikiuser ADD COLUMN totp_enabled BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE wikiuser ADD COLUMN totp_last_step INTEGER;
```

`totp_last_step`은 마지막으로 성공한 TOTP 타임스텝을 저장해 같은 코드의 재사용(replay)을 막습니다.

#### 기존 wiki.db에 이메일 인증 컬럼 추가 (이메일 인증 도입 시)

`WikiUser.email_verified` 컬럼이 추가되었습니다. 기존 `wiki.db`에는 없으므로 백업 후 아래를 실행하세요:

```sql
ALTER TABLE wikiuser ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT 0;
```

추후 데이터가 의미 있게 누적되면 Alembic 같은 마이그레이션 도구 도입을 검토하세요.

### CORS

허용 origin은 `FRONTEND_URL`과 로컬 개발용(`localhost:5173`, `127.0.0.1:5173`)입니다. 허용 메서드/헤더는 명시적 화이트리스트입니다.
