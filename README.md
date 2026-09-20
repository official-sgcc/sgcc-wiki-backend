# sgcc-wiki-backend

소규모 위키 서비스를 위한 FastAPI 백엔드. 문서/태그/카테고리 관리, 버전 기록과 diff, JWT 인증(2FA·이메일 인증 포함), 자동 백업을 제공합니다.

프론트엔드는 별도 리포지토리이며 개발 환경에서는 `http://localhost:5173`, 운영에서는 `FRONTEND_URL`로 동작합니다.

## 주요 기능

- 위키 문서 CRUD + 버전 관리 + 버전 간 diff
- JWT 인증, bcrypt 비밀번호 해시
- 2단계 인증(TOTP), 이메일 등록·인증, 이메일 기반 비밀번호 재설정
- 태그·카테고리 분류 (CRUD + 존재 검증, 카테고리 트리)
- 문서별 권한 (`update` / `move` / `delete`)
- 비공개 문서 (관리자만 생성·수정·조회)
- 매일 자정 자동 DB 백업 (SQLite Backup API)
- 인증 엔드포인트 rate limiting, 관리자 계정 자동 부트스트랩
- DB 커넥션까지 확인하는 헬스체크 (`GET /healthz`)

## 기술 스택

**FastAPI** (웹) · **SQLModel + SQLAlchemy + SQLite** (ORM/DB) · **bcrypt + PyJWT + pyotp** (인증) · **slowapi** (rate limit) · **APScheduler** (백업 스케줄) · **diff-match-patch** (버전 diff) · **pytest + httpx** (테스트)

## 디렉토리 구조

```
sgcc-wiki-backend/
├── main.py                 # 기존 EC2 실행 명령을 위한 호환 진입점
├── src/sgcc_wiki_backend/
│   ├── main.py             # 앱 조립 (미들웨어, lifespan, 라우터 등록)
│   ├── core/
│   ├── config.py           # 환경변수, 로깅, rate limiter
│   ├── database.py         # SQLite 엔진과 테이블 생성
│   ├── deps.py             # 인증 의존성, 권한·입력 검증 헬퍼
│   ├── login_utils.py      # 비밀번호 해시, JWT/TOTP 토큰, 입력 검증
│   └── maintenance.py      # 메일 발송(provider·재시도·한도), DB 백업, 관리자 부트스트랩
│   ├── routers/
│   ├── documents.py        # 문서 CRUD, 버전·diff, 검색
│   ├── users.py            # 가입·로그인, 2FA, 이메일, 비밀번호 재설정
│   ├── tags.py             # 태그
│   ├── categories.py       # 카테고리
│   └── health.py           # 헬스체크
│   └── schemas/
│   ├── wiki_doc.py         # WikiDoc, WikiDocVersion
│   ├── wiki_user.py        # WikiUser + 요청 바디 모델
│   ├── permissions.py      # 문서별 권한
│   ├── tags.py             # 태그
│   └── categories.py       # 카테고리 (+ 트리 응답 모델)
├── tests/                  # pytest (conftest.py가 임시 DB로 격리)
├── pyproject.toml          # 프로젝트 의존성
├── uv.lock                 # 고정된 의존성 버전
├── db_backups/             # 자동 백업 (gitignore)
├── logs/                   # app.log + 회전 백업 (gitignore)
├── wiki.db                 # SQLite 데이터베이스 (gitignore, 없으면 서버 시작 시 자동 생성)
└── .env                    # 환경변수 (gitignore)
```

## 빠른 시작

```bash
uv sync --locked
```

프로젝트 루트에 `.env`를 만들고 최소한 `JWT_SECRET_KEY`를 설정한 뒤 실행합니다(미설정이면 서버가 시작을 거부합니다).

```bash
uv run uvicorn sgcc_wiki_backend:app --reload
```

- 서버: http://127.0.0.1:8000
- Swagger UI: http://127.0.0.1:8000/docs

테스트:

```bash
uv run pytest
```

## Docker

이 프로젝트는 루트의 `Dockerfile`로 컨테이너 이미지를 빌드할 수 있습니다. Python 3.12 기반 이미지에서 잠금 파일로 의존성을 설치하고 `sgcc_wiki_backend:app`을 실행합니다.

### 이미지 빌드

```bash
docker build -t sgcc-wiki-backend .
```

### 컨테이너 실행

프로젝트 루트에 `.env`를 두고 필수 환경변수인 `JWT_SECRET_KEY`를 포함한 뒤 실행합니다.

```bash
docker run --rm -it \
  --name sgcc-wiki-backend \
  --env-file .env \
  -p 8000:8000 \
  sgcc-wiki-backend
```

- 서버 주소: http://localhost:8000
- Swagger UI: http://localhost:8000/docs

### Dockerfile 동작 요약

- Python 3.12 slim 이미지 사용
- `uv.lock`으로 런타임 의존성 구성
- 소스코드를 `/app`에 복사
- 컨테이너 내부에서 `uvicorn`으로 FastAPI 앱 실행
- 기본 포트는 `8000`이며, 현재 `CMD`는 포트를 하드코딩하고 있어 컨테이너 실행 시 `-p 8000:8000` 조합이 가장 안전합니다

> 현재 Dockerfile은 `ARG PORT`를 선언하지만 실제 실행 명령은 `--port 8000`으로 고정되어 있으므로, 포트 변경이 필요하면 Dockerfile의 `CMD`도 함께 맞춰야 합니다.

### 운영 시 주의사항

- `.env`는 호스트에 두고 `--env-file .env`로 주입해야 합니다
- `FRONTEND_URL`, `JWT_SECRET_KEY`가 없으면 API 동작이 제한되고, 메일 관련 값(`RESEND_API_KEY` 또는 `SMTP_*`)이 없으면 인증 메일이 로그로만 출력됩니다
- 메일 발송 한도는 프로세스 메모리에서 세므로 `uvicorn --workers`로 여러 프로세스를 띄우지 마세요(단일 프로세스 전제)
- `wiki.db`와 `logs/`, `db_backups/`는 컨테이너 내부 경로와 호스트 경로를 연결해 관리하는 것이 안전합니다

```bash
docker run --rm -it \
  --env-file .env \
  -p 8000:8000 \
  -v "${PWD}/wiki.db:/app/wiki.db" \
  -v "${PWD}/logs:/app/logs" \
  -v "${PWD}/db_backups:/app/db_backups" \
  sgcc-wiki-backend
```

## 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `JWT_SECRET_KEY` | **(필수)** | JWT 서명 키. 미설정이면 기동 거부(fail-fast) |
| `JWT_ALGORITHM` | `HS256` | JWT 알고리즘 |
| `JWT_TOKEN_EXPIRE_MINUTES` | `180` | 세션 토큰 만료(분) |
| `MFA_TOKEN_EXPIRE_MINUTES` | `5` | 2FA 임시 토큰 만료(분) |
| `PASSWORD_RESET_EXPIRE_MINUTES` | `30` | 비밀번호 재설정 토큰 만료(분) |
| `EMAIL_VERIFY_EXPIRE_MINUTES` | `1440` | 이메일 인증 토큰 만료(분, 기본 24시간) |
| `TOTP_ISSUER` | `SGCC Wiki` | 인증앱에 표시되는 발급자 이름 |
| `FRONTEND_URL` | `http://localhost:5173` | CORS 허용 origin, 메일 링크 base URL |
| `DB_PATH` | `wiki.db` | SQLite 파일 경로 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | — | 관리자 부트스트랩. 둘 중 하나라도 비면 skip |
| `EMAIL_PROVIDER` | 자동 추론 | `resend` / `smtp` / `log`. 미설정이면 `RESEND_API_KEY`가 있을 때 resend, `SMTP_HOST`가 있을 때 smtp, 둘 다 없으면 log(발송 대신 로그 출력). 잘못된 값이거나 필요한 키가 없으면 기동 거부 |
| `RESEND_API_KEY` | — | Resend API 키(`re_...`). provider가 resend일 때 필수 |
| `EMAIL_FROM` | `SMTP_FROM` 값 | 보내는 사람 주소. Resend는 인증한 도메인의 주소여야 함 |
| `EMAIL_DAILY_LIMIT` | `90` | 24시간 발송 상한(모든 메일 합산). 이 중 10%는 비밀번호 재설정 몫으로 예약. Resend 무료 한도(하루 100통) 아래로 유지 |
| `EMAIL_COOLDOWN_SECONDS` | `60` | 같은 주소로 다시 보내기까지 최소 간격 |
| `SMTP_HOST` | — | SMTP 서버. provider가 smtp일 때 필수 |
| `SMTP_PORT` | `587` | SMTP 포트. STARTTLS만 지원(465 SSL 직결 불가) |
| `SMTP_USER` / `SMTP_PASSWORD` | — | SMTP 로그인 정보. 비면 로그인 생략 |
| `SMTP_FROM` | `no-reply@sgcc-wiki.local` | `EMAIL_FROM` 미설정 시의 보내는 사람 주소(구버전 호환) |

`ADMIN_USERNAME`/`ADMIN_PASSWORD`가 설정되면 서버 시작 시 해당 계정을 `admin`으로 승격하거나 새로 생성합니다.

지정된 초기 관리자의 비밀번호는 매 기동 시 `ADMIN_PASSWORD`와 동기화합니다. 현재 해시와 비교해 비밀번호가 달라졌을 때만 새 해시를 저장하고 `session_version`을 증가시켜 기존 로그인·MFA 토큰을 무효화합니다. 같은 비밀번호이면 해시와 세션 버전을 유지합니다. 다른 사용자에게는 적용하지 않으며, 설정 중 하나라도 비어 있으면 동기화하지 않습니다. 비밀번호 재설정 기능으로 이 계정의 비밀번호를 바꿨다면 설정도 함께 갱신해야 다음 재시작에서 이전 설정으로 돌아가지 않습니다. `.env`보다 서비스 프로세스에 이미 설정된 환경변수가 우선하므로 서비스 환경도 확인하세요.

## 인증

### 토큰

`POST /login`(2FA 사용 시 `POST /login/2fa`)이 JWT를 발급합니다. 요청에는 다음 두 헤더 중 하나로 실어 보냅니다.

- 표준: `Authorization: Bearer <token>`
- 호환: `auth: <token>` (기존 프론트 형식, 계속 지원)

만료·위조 토큰은 401입니다. 세션 토큰에는 `purpose='access'` 클레임이 들어 있고 검증 시 이를 강제합니다(2FA 우회 방지).

> **배포 주의**: `purpose` 검증 도입 이전에 발급된 토큰에는 이 클레임이 없어 **모두 무효화**됩니다. 배포 후 전 사용자가 재로그인해야 합니다.

### 2단계 인증 (TOTP)

1. `POST /2fa/setup` — 시크릿과 `otpauth://` URI 발급 (아직 비활성). 프론트는 이 URI로 QR을 그리거나 시크릿을 수동 입력하게 함
2. `POST /2fa/enable` — 인증앱 6자리 코드 확인 후 활성화
3. 이후 로그인은 2단계로 동작: `POST /login` → `{mfa_required: true, mfa_token}` → `POST /login/2fa` → `{token}`
4. `POST /2fa/disable` — 코드 확인 후 비활성화

한 번 인증에 성공한 코드는 유효 창 안에서도 재사용할 수 없습니다(replay 방지). 2FA를 켜지 않은 사용자는 `POST /login`에서 곧바로 `{token}`을 받습니다.

### 이메일 등록·인증

회원가입 시 이메일을 인증해야 합니다.

- `POST /register/verify-email` — 회원가입 이메일 인증 발송.
- `POST /register/verify-status` — 이메일 인증 여부 확인. EmailVerification으로 저장하여 다른 기기에서 인증해도 확인할 수 있음.

1. `PUT /email` — 이메일 등록/변경. 저장 시 항상 **미인증** 상태가 되고 인증 링크가 발송됨. 계정 간 유일(중복 409)
2. 메일 링크 → `POST /email/verify`
3. `POST /email/verify-request` — 인증 메일 재발송

인증 토큰에 대상 이메일이 담겨 있어, 인증 전에 이메일을 바꾸면 이전 링크는 무효가 됩니다.

같은 주소로 `EMAIL_COOLDOWN_SECONDS`(기본 60초) 안에 다시 요청하거나 24시간 발송 상한(`EMAIL_DAILY_LIMIT`)에 도달하면 `POST /register/verify-email`·`POST /email/verify-request`는 429를 반환합니다. 발송 자체는 백그라운드에서 이뤄지므로 응답이 200이어도 실제 전송 결과는 `logs/app.log`의 `email sent` / `email failed` 로그로 확인합니다(운영 노트 "메일 발송" 참고).

### 비밀번호 재설정

**인증된(verified) 이메일이 등록된 계정에만** 실제 링크가 발송됩니다.

1. `POST /password-reset/request` — 계정·이메일 존재 여부와 무관하게 항상 같은 200을 반환(enumeration 방지). 발송 쿨다운·상한에 걸려도 응답은 같고 발송만 건너뜁니다
2. 메일 링크 → `POST /password-reset/confirm`

재설정 토큰은 발급 시점의 비밀번호 해시로 서명되어 **한 번 쓰면 무효**이며 30분 후 만료됩니다.

### 권한

- `admin` — 등급 100, 관리자 전용 행동 가능
- `club_member` — 등급 50, 동아리 회원
- `login_user` — 등급 10, 일반 가입자 기본값
- 비로그인 — 조회만

문서 생성·수정은 카테고리별 `write_permission` 최소 등급을 검사합니다. 기본값은 `club_member`이며 `admin`은 항상 등급 조건을 충족합니다. 관리자는 각 카테고리에서 `admin` / `club_member` / `login_user` 중 최소 등급을 설정할 수 있습니다. 모든 조상의 최소 권한을 하한으로 상속합니다. 실제 제한은 자신과 조상 중 가장 높은 제한이며, 관리자는 이 제한을 우회합니다. 부모 변경·권한 상향은 기존 하위 문서에도 즉시 적용되고, 부모보다 낮은 권한 저장 요청은 거부됩니다. 기존 카테고리는 서버 시작 시 신규 컬럼과 기본값을 자동으로 보완하며, 기존 문서에도 즉시 적용합니다. 카테고리 이동 시 원래 카테고리와 대상 카테고리 모두 작성 권한을 확인합니다.

공통 정책은 `core/permissions.py`의 `Role`, `Action`, `MINIMUM_ROLES`에 있습니다. 일반 행동은 최소 등급 이상이면 통과하며, 관리자 전용 행동과 관리자 전용 카테고리는 등급과 별개로 `is_admin`을 통해 실제 `admin` 역할을 확인합니다. 새 역할은 `Role` Enum에 `(저장할 이름, 등급, 표시 이름)`을 추가하면 역할 목록·최소 등급 검사·관리자 선택 목록에 반영됩니다. 알 수 없는 역할이나 정책은 거부합니다.

문서별 `Permissions` 목록을 공통 등급 검사와 함께 적용합니다. 수정은 전역 최소 등급 `club_member`, 문서별 목록의 최소 등급, 카테고리 상속 제한을 모두 통과해야 합니다. 제목 변경은 `rename`과 `update`를 검사합니다. 카테고리 이동은 실제 관리자만 가능합니다. 문서 삭제는 관리자 또는 작성자가 자기 문서를 삭제하는 경우에 허용합니다. 관리자는 권한 검사에서 우선 통과합니다. 신규 문서는 update/rename = ['club_member'], move/delete = ['admin']으로 저장합니다. 기존 DB의 목록은 덮어쓰지 않습니다. 기존 목록에 login_user가 있어도 전역 하한이 적용됩니다. 카테고리 생성·수정·삭제와 태그 삭제는 관리자 전용이며 태그 생성은 문서 저장 트랜잭션에서만 가능합니다.


`GET /permissions`는 역할 정의·현재 사용자의 전역 행동·카테고리 작성 권한을 반환합니다. `GET /documents/by-title/permissions?title=...`는 해당 문서의 `document_update` / `document_rename` / `document_move` / `document_delete` 가능 여부를 반환하며 조회수를 올리지 않습니다. 프론트는 서버 결과로 UI를 제어하고, 실제 API도 같은 정책을 다시 검사합니다.

문서 이력·diff는 문서와 동급의 열람 정책을 적용하며 현재 모두 공개입니다. 프론트의 카테고리 선택은 `document_move`가 허용될 때만 활성화됩니다.

`POST /logout`은 현재 액세스 토큰의 SHA-256 해시와 만료 시각을 `revokedtoken` 테이블에 저장합니다. 이후 모든 인증 요청에서 폐기 여부를 확인하므로 서버 재시작 후에도 차단됩니다. 다른 로그인 토큰은 유지되며, 신규 액세스 토큰은 고유 `jti`를 포함합니다. 만료된 폐기 기록은 로그아웃 요청 시 정리합니다. 프론트는 서버 처리 성공(또는 이미 무효한 토큰의 401) 후 로컬 토큰을 삭제하고, 통신 실패는 사용자에게 알립니다. 신규 테이블은 서버 시작 시 자동 생성됩니다.

### 입력 정책

- **아이디**: 3~32자, 영문/숫자/`_`/`-`
- **비밀번호**: 최소 8자, 영문·숫자 모두 포함
- `guest`, `admin`, `system`, `bot`, `anonymous`는 가입 불가(예약어)

로그인 실패 시 아이디/비밀번호 중 어느 쪽이 틀렸는지 구분되지 않습니다(enumeration 방지).

### Rate limiting

IP 기준이며 초과 시 `429`입니다.

| 분당 3회 | 분당 5회 |
|---|---|
| `POST /register`<br>`POST /password-reset/request`<br>`POST /email/verify-request`<br>`POST /email/test`<br>`PUT /email` | `POST /login`<br>`POST /login/2fa`<br>`POST /password-reset/confirm`<br>`POST /email/verify` |

## API

인증 열: `-` 불필요 / `필요` 로그인 / `카테고리 작성권한` 자신과 조상의 최소 권한 / `admin` 관리자.
목록 조회는 공통으로 `limit`(미지정 시 전체)·`offset`(기본 0)을 받으며, **`offset`은 `limit`이 있을 때만 적용**됩니다. 두 값은 0 이상의 정수여야 하며 음수는 422로 거부합니다. `limit=0`은 빈 목록을 반환합니다.

### 문서

| 엔드포인트 | 인증 | 설명 |
|---|---|---|
| `GET /documents` | - | 문서 목록. `keyword`가 있으면 제목·본문 부분 일치로 필터 |
| `POST /documents` | 필요 | 문서 생성. 바디: `title`, `content`, `category`, `tags` |
| `GET /documents/count` | - | 총 문서 수 |
| `GET /documents/{title}` | - | 문서 단건 조회 (호출 시 `view_count` 증가) |
| `PUT /documents/{title}` | 카테고리 작성권한 | `content`/`category`/`tags` 중 보낸 필드만 수정, 새 버전 생성 |
| `PUT /documents/{title}/move` | 동아리 회원 이상 + 카테고리 작성권한 | 바디: `new_title`로 제목 변경 |
| `DELETE /documents/{title}` | admin 또는 작성자 | 문서 + 버전 + 권한 레코드 삭제 |
| `GET /documents/{title}/versions` | - | 버전 목록 |
| `GET /documents/{title}/versions/{n}` | - | 특정 버전 |
| `GET /documents/{title}/diff/{n}` | - | `n`번 버전과 직전 버전의 본문 diff. `(op, text)` 목록(op: -1 삭제 / 0 유지 / 1 추가). `n <= 1`이면 400 |
| `GET /search` | - | `keyword`(필수) + `search_type` = `title`(기본) / `title_content` / `tag` |

- 제목에 `/` 등 경로 구분자가 포함될 수 있으므로 신규 클라이언트는 쿼리 방식 API를 사용합니다.
  - 상세·수정·삭제: `GET|PUT|DELETE /documents/by-title?title=...`
  - 제목 이동: `PUT /documents/by-title/move?title=...`
  - 버전 목록: `GET /documents/by-title/versions?title=...`
  - 특정 버전: `GET /documents/by-title/version?title=...&version_number=...`
  - 버전 차이: `GET /documents/by-title/diff?title=...&version_number=...`
- 기존 `{title}` 경로 API는 이전 클라이언트 호환을 위해 유지됩니다.
- 문서 생성·수정 시 카테고리는 미리 존재해야 하고(없으면 400), **없는 태그는 자동 생성**됩니다.
- 동시 수정으로 버전 번호가 충돌하면 최대 3회 재시도하고, 그래도 실패하면 409입니다.
- 태그 검색(`search_type=tag`)은 **정확 일치**입니다 — `Python`으로 검색해도 `PythonDev` 문서는 나오지 않습니다.

### 사용자

| 엔드포인트 | 인증 | 설명 |
|---|---|---|
| `POST /register` | - | 바디: `username`, `password` |
| `POST /login` | - | `{token}` 또는 `{mfa_required, mfa_token}` |
| `POST /login/2fa` | - | 바디: `mfa_token`, `code` → `{token}` |
| `GET /users/{username}` | 선택 | 프로필 + `edit_versions`(작성한 문서 버전, 최신순). `password`/`totp_secret`은 항상 제외, `email`은 본인 조회에만 포함 |
| `GET /admin/users` | admin | 사용자 목록 |
| `GET /admin/permissions` | admin | 사용자 권한 종류 목록. 응답: `{'permissions': ['admin', 'club_member', 'login_user']}` |
| `PUT /admin/users/{username}/permission` | admin | 권한 변경. 바디: `permission`. 허용 값: `admin` / `club_member` / `login_user` |
| `PUT /users/{username}/bio` | 필요 | 소개문 수정. 바디: `bio` |
| `POST /2fa/setup` | 필요 | `{secret, otpauth_uri}` |
| `POST /2fa/enable` | 필요 | 바디: `code` |
| `POST /2fa/disable` | 필요 | 바디: `code` |
| `PUT /email` | 필요 | 바디: `email`. 형식 오류 400, 중복 409, 분당 3회 초과 429 |
| `POST /email/verify-request` | 필요 | 인증 메일 재발송. 같은 주소 쿨다운·일일 상한이면 429 |
| `POST /email/test` | admin | 메일 설정 점검용 테스트 발송. 바디: `email`. 동기 발송 후 `{message, provider, message_id}`, 실패 시 502 + provider 오류 메시지 |
| `POST /email/verify` | - | 바디: `token` |
| `POST /password-reset/request` | - | 바디: `username`. 항상 동일 응답 |
| `POST /password-reset/confirm` | - | 바디: `token`, `new_password` |

### 태그

| 엔드포인트 | 인증 | 설명 |
|---|---|---|
| `GET /tags` | - | 태그 전체 목록 |
| `GET /tags/{name}/documents` | - | 해당 태그가 달린 문서 목록(태그명 정확 일치). 태그가 없으면 404 |
| `DELETE /tags/{name}` | admin | 삭제 시 이 태그를 쓰던 모든 문서에서도 함께 제거 |

### 카테고리

| 엔드포인트 | 인증 | 설명 |
|---|---|---|
| `GET /categories` | - | 카테고리 트리(루트부터 `children` 중첩) |
| `POST /categories` | admin | 바디: `name`, `parent`(선택) |
| `GET /categories/{name}` | - | 해당 카테고리 노드. `children`은 **하위 카테고리**이며 문서가 아님 |
| `GET /categories/{name}/documents` | - | 카테고리에 속한 **문서** 목록. `recursive=true`면 하위 카테고리 문서까지 포함 |
| `PUT /categories/{name}` | admin | 바디: `parent`. 자기 하위 노드를 부모로 지정하는 순환 참조는 거부 |
| `DELETE /categories/{name}` | admin | 하위 카테고리까지 삭제. 문서가 사용 중이면 409 |

### 헬스체크

| 엔드포인트 | 인증 | 설명 |
|---|---|---|
| `GET /healthz` | - | 서비스 상태 확인. DB 커넥션까지 검사해 정상이면 `{"status": "ok"}`, DB 접근 실패 시 503 |

기능(로그인 등)이 정상인지는 검사하지 않습니다. `/login`은 분당 5회 제한이고 실패해도 bcrypt를 돌리므로, 프로브가 rate limit을 소진해 멀쩡한 노드를 장애로 오판하게 만듭니다. 기능 검증은 배포 전 `pytest`, 운영 중에는 실제 요청의 5xx 에러율 알람으로 합니다.

## 운영 노트

### 본문(content) 렌더링과 XSS

백엔드는 문서 본문을 가공 없이 그대로 저장합니다(마크다운·위키 문법 보존).

**프론트엔드는 렌더링 시 반드시 sanitization을 적용해야 합니다.** 마크다운 렌더러의 HTML 인라인 옵션을 끄거나 DOMPurify 등으로 정제하세요. `<script>`, `<iframe>`, `on*` 핸들러가 그대로 렌더링되면 XSS 위험이 있습니다.

### 자동 백업

- 매일 자정 `db_backups/db_backup_YYYYMMDD_HHhMMmSSs.db`로 백업
- `shutil` 파일 복사가 아닌 SQLite Backup API를 사용해 트랜잭션 안전하게 복사

### 메일 발송

가입 인증·비밀번호 재설정 메일은 핸들러가 직접 보내지 않고 `send_email`이 백그라운드 스레드에 넘깁니다. 요청 응답은 즉시 돌아가고, 전송은 `EMAIL_PROVIDER`에 따라 다음 중 하나로 이뤄집니다.

| provider | 전송 방식 | 필요한 설정 |
|---|---|---|
| `resend` | Resend HTTP API (`https://api.resend.com/emails`, 443 포트) | `RESEND_API_KEY`, `EMAIL_FROM`(인증한 도메인 주소) |
| `smtp` | `smtplib` + STARTTLS, 10초 타임아웃 | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM` |
| `log` | 발송하지 않고 본문(링크 포함)을 로그에 출력 | 없음(개발 기본값) |

- 메일은 텍스트와 HTML 두 벌로 나갑니다. HTML은 버튼 하나짜리 공용 템플릿(`render_email_html`)이며, HTML을 표시하지 않는 클라이언트에는 텍스트 본문이 보입니다.
- 네트워크 오류·5xx·429는 5초, 30초 후 두 번 재시도합니다. 잘못된 API 키·미인증 도메인·수신자 거부 같은 4xx는 재시도하지 않고 바로 실패 로그를 남깁니다.
- Resend 요청에는 발송마다 고유한 `Idempotency-Key`를 실어, 타임아웃 뒤 재시도해도 같은 메일이 두 번 나가지 않습니다.
- 발송 한도(`EMAIL_DAILY_LIMIT`, `EMAIL_COOLDOWN_SECONDS`)는 프로세스 메모리에서 세므로 재시작하면 초기화되고, 여러 프로세스를 띄우면 프로세스마다 따로 셉니다. 상한의 10%는 비밀번호 재설정 몫으로 남겨 두어, 가입 인증 메일이 폭주해도 계정 복구는 계속 됩니다.
- 로그 키워드: `email queued` → `email sent` 또는 `email send failed (attempt n)` / `email failed permanently` / `email failed after n attempts`, 한도 차단은 `email suppressed`.

**Resend 설정 순서**

1. https://resend.com 가입(동아리 공용 계정 권장) → **Domains**에서 동아리 도메인 추가 → 안내하는 DNS 레코드(DKIM TXT, SPF, 필요 시 MX)를 도메인 DNS에 등록하고 Verified가 될 때까지 대기
2. **API Keys**에서 키 발급(권한은 Sending access면 충분). 키는 한 번만 표시되므로 바로 `.env`에 기록
3. `.env`에 아래를 추가하고 서버 재시작

```
RESEND_API_KEY=re_xxxxxxxxxxxxxxxx
EMAIL_FROM=no-reply@동아리도메인
```

4. admin 계정으로 로그인해 테스트 메일을 보내 확인

```bash
curl -X POST http://localhost:8000/email/test   -H 'Authorization: Bearer <admin token>'   -H 'Content-Type: application/json'   -d '{"email": "본인주소@example.com"}'
```

정상이면 `{"message": "Test email sent.", "provider": "resend", "message_id": "..."}`, 설정 오류면 502와 함께 Resend가 준 오류 메시지가 돌아옵니다. 도메인 없이 Gmail을 쓸 때는 `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER`/`EMAIL_FROM`에 Gmail 주소, `SMTP_PASSWORD`에 앱 비밀번호를 넣습니다.

### 로깅

stdout과 `logs/app.log`에 동시 출력하며, 5MB마다 회전해 최대 5개(`app.log.1` ~ `app.log.5`)를 보관합니다. 기록 대상: 회원가입, 로그인 성공/실패, 문서·태그·카테고리 변경, 관리자 부트스트랩, 백업 결과.

### CORS

허용 origin은 `FRONTEND_URL`과 로컬 개발용(`localhost:5173`, `127.0.0.1:5173`)입니다. 메서드와 헤더는 명시적 화이트리스트이며, 헤더에는 `Authorization`과 구버전 호환용 `auth`가 모두 포함됩니다.

### 스키마 마이그레이션

#### 비밀번호 재설정 시 세션 무효화

서버 시작 시 `wikiuser.session_version`이 없으면 자동으로 추가하며 기존 사용자에는 0을 적용합니다. 비밀번호 재설정이 성공하면 값을 증가시켜 기존 액세스 토큰과 로그인 대기 중인 MFA 토큰을 거부합니다. 기존 버전 정보가 없는 액세스 토큰은 버전 0으로 취급하므로 배포만으로 로그아웃되지는 않습니다. 재시작은 저장된 버전을 덮어쓰지 않습니다. 수동 적용 시 컬럼이 없는 경우에만 실행하세요.

```sql
ALTER TABLE wikiuser ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0;
```

가입 화면은 이메일 발송 요청 시 생성한 임의의 `registration_secret`을 인증 상태 조회와 최종 가입에 동일하게 전달해야 합니다. 이메일 인증 완료 여부만으로는 가입할 수 없습니다. 이메일 링크의 `verification_token`을 직접 제출하는 방식은 유지합니다. 프론트와 백엔드를 함께 배포하고, 배포 전에 열어 둔 가입 화면은 새로고침 후 인증을 다시 요청해야 합니다.

권한 점검 결과와 유지한 정책은 [AUTHORIZATION_AUDIT.md](AUTHORIZATION_AUDIT.md)를 참고하세요.

#### 제목 변경 권한 분리

서버 시작 시 `permissions.rename`이 없으면 자동으로 추가합니다. 호환용 컬럼으로, 실제 제목 변경 권한은 공통 정책과 카테고리에서 판정합니다. 기존 `update` / `move` / `delete`와 문서·버전 데이터는 유지합니다. 이후 재시작에서는 저장된 `rename` 설정을 덮어쓰지 않습니다. 수동 적용 시 컬럼이 없는 경우에만 실행하세요.

```sql
ALTER TABLE permissions ADD COLUMN rename JSON NOT NULL DEFAULT '["club_member"]';
```

#### 카테고리 작성 권한

서버 시작 시 `core/database.py`가 기존 카테고리 테이블에 다음 컬럼을 자동 추가합니다. 이미 컬럼이 있으면 저장된 관리자 설정을 유지합니다. 기존 행에도 `club_member` 기본값이 적용됩니다. 수동 적용 시 컬럼이 없는 경우에만 실행하세요.

```sql
ALTER TABLE wikicategory ADD COLUMN write_permission VARCHAR NOT NULL DEFAULT 'club_member';
```

마이그레이션 도구는 사용하지 않습니다. 서버 시작 시 `SQLModel.metadata.create_all(engine)`이 **없는 테이블만 만들고 기존 테이블은 변경하지 않으므로**, 컬럼 변경은 아래 스니펫처럼 직접 적용해야 합니다. 실행 전 반드시 `db_backups/`에 백업본을 확보하세요. 새 마이그레이션은 이 섹션에 **누적**해서 추가합니다.

#### `created_by` 컬럼 백필 (작성자 삭제 권한 도입 시)

```sql
ALTER TABLE wikidoc ADD COLUMN created_by VARCHAR;
UPDATE wikidoc
SET created_by = (
    SELECT updated_by FROM wikidocversion
    WHERE wiki_doc_title = wikidoc.title AND version_number = 1
);
```

#### 2FA 컬럼 추가 (2단계 인증 도입 시)

미실행 시 사용자 조회가 깨집니다.

```sql
ALTER TABLE wikiuser ADD COLUMN totp_secret VARCHAR;
ALTER TABLE wikiuser ADD COLUMN totp_enabled BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE wikiuser ADD COLUMN totp_last_step INTEGER;
```

`totp_last_step`은 마지막으로 성공한 TOTP 타임스텝을 저장해 같은 코드의 재사용(replay)을 막습니다.

#### 이메일 인증 컬럼 추가

```sql
ALTER TABLE wikiuser ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT 0;
```

#### 코멘트 권한

코멘트 기능과 권한 필드는 현재 구현하지 않습니다. 운영 DB에도 comment 컬럼이 없음을 확인했으며 사용자 요청에 따라 추가하지 않습니다.

#### 아주 오래된 DB의 `email` NOT NULL 제거 (이메일 선택 등록 도입 시)

과거 `email`이 필수였던 DB라면 이메일 없이 가입할 수 없습니다. SQLite는 `NOT NULL` 제약을 `ALTER`로 뗄 수 없어 테이블을 재빌드해야 합니다(다른 연결이 없을 때, **위 2FA·이메일 인증 컬럼 추가를 끝낸 상태**에서 실행).

```sql
PRAGMA foreign_keys=off;
BEGIN;
ALTER TABLE wikiuser RENAME TO wikiuser_legacy;
CREATE TABLE wikiuser (
    username VARCHAR NOT NULL PRIMARY KEY,
    password VARCHAR NOT NULL,
    permission VARCHAR NOT NULL,
    bio VARCHAR NOT NULL,
    email VARCHAR,
    email_verified BOOLEAN NOT NULL DEFAULT 0,
    totp_secret VARCHAR,
    totp_enabled BOOLEAN NOT NULL DEFAULT 0,
    totp_last_step INTEGER
);
INSERT INTO wikiuser (username, password, permission, bio, email, email_verified, totp_secret, totp_enabled, totp_last_step)
    SELECT username, password, permission, bio, email, email_verified, totp_secret, totp_enabled, totp_last_step FROM wikiuser_legacy;
DROP TABLE wikiuser_legacy;
CREATE UNIQUE INDEX ix_wikiuser_email ON wikiuser (email);

COMMIT;
PRAGMA foreign_keys=on;
```

데이터가 의미 있게 쌓이면 Alembic 도입을 검토하세요.

cicd 확인용 문구#1
