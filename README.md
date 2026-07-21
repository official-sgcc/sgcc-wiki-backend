# sgcc-wiki-backend

소규모 위키 서비스를 위한 FastAPI 백엔드. 문서/태그/카테고리 관리, 버전 기록과 diff, JWT 인증(2FA·이메일 인증 포함), 자동 백업을 제공합니다.

프론트엔드는 별도 리포지토리이며 개발 환경에서는 `http://localhost:5173`, 운영에서는 `FRONTEND_URL`로 동작합니다.

## 주요 기능

- 위키 문서 CRUD + 버전 관리 + 버전 간 diff
- JWT 인증, bcrypt 비밀번호 해시
- 2단계 인증(TOTP), 이메일 등록·인증, 이메일 기반 비밀번호 재설정
- 태그·카테고리 분류 (CRUD + 존재 검증, 카테고리 트리)
- 문서별 권한 (`update` / `move` / `delete` / `comment`)
- 매일 자정 자동 DB 백업 (SQLite Backup API)
- 인증 엔드포인트 rate limiting, 관리자 계정 자동 부트스트랩

## 기술 스택

**FastAPI** (웹) · **SQLModel + SQLAlchemy + SQLite** (ORM/DB) · **bcrypt + PyJWT + pyotp** (인증) · **slowapi** (rate limit) · **APScheduler** (백업 스케줄) · **diff-match-patch** (버전 diff) · **pytest + httpx** (테스트)

## 디렉토리 구조

```
sgcc-wiki-backend/
├── main.py                 # 앱 조립 (미들웨어, lifespan, 라우터 등록) — 루트의 유일한 파이썬 파일
├── core/
│   ├── config.py           # 환경변수, 로깅, rate limiter
│   ├── database.py         # SQLite 엔진과 테이블 생성
│   ├── deps.py             # 인증 의존성, 권한·입력 검증 헬퍼
│   ├── login_utils.py      # 비밀번호 해시, JWT/TOTP 토큰, 입력 검증
│   └── maintenance.py      # 메일 발송, DB 백업, 관리자 부트스트랩
├── routers/
│   ├── documents.py        # 문서 CRUD, 버전·diff, 검색
│   ├── users.py            # 가입·로그인, 2FA, 이메일, 비밀번호 재설정
│   ├── tags.py             # 태그
│   └── categories.py       # 카테고리
├── schemas/
│   ├── wiki_doc.py         # WikiDoc, WikiDocVersion
│   ├── wiki_user.py        # WikiUser + 요청 바디 모델
│   ├── permissions.py      # 문서별 권한
│   ├── tags.py             # 태그
│   └── categories.py       # 카테고리 (+ 트리 응답 모델)
├── tests/                  # pytest (conftest.py가 임시 DB로 격리)
├── db_backups/             # 자동 백업 (gitignore)
├── logs/                   # app.log + 회전 백업 (gitignore)
├── wiki.db                 # SQLite 데이터베이스
└── .env                    # 환경변수 (gitignore)
```

## 빠른 시작

```bash
pip install fastapi sqlmodel bcrypt pyjwt python-dotenv uvicorn apscheduler diff_match_patch slowapi pyotp
```

프로젝트 루트에 `.env`를 만들고 최소한 `JWT_SECRET_KEY`를 설정한 뒤 실행합니다(미설정이면 서버가 시작을 거부합니다).

```bash
uvicorn main:app --reload
```

- 서버: http://127.0.0.1:8000
- Swagger UI: http://127.0.0.1:8000/docs

테스트:

```bash
pip install pytest httpx
pytest
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
| `SMTP_HOST` | — | 메일 서버. 미설정이면 링크를 로그로만 출력 |
| `SMTP_PORT` | `587` | 메일 서버 포트(STARTTLS) |
| `SMTP_USER` / `SMTP_PASSWORD` | — | SMTP 로그인 정보. 비면 로그인 생략 |
| `SMTP_FROM` | `no-reply@sgcc-wiki.local` | 보내는 사람 주소 |

`ADMIN_USERNAME`/`ADMIN_PASSWORD`가 설정되면 서버 시작 시 해당 계정을 `admin`으로 승격하거나 새로 생성합니다.

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

회원가입은 아이디/비밀번호만 받고, 이메일은 로그인 후 등록합니다.

1. `PUT /email` — 이메일 등록/변경. 저장 시 항상 **미인증** 상태가 되고 인증 링크가 발송됨. 계정 간 유일(중복 409)
2. 메일 링크 → `POST /email/verify`
3. `POST /email/verify-request` — 인증 메일 재발송

인증 토큰에 대상 이메일이 담겨 있어, 인증 전에 이메일을 바꾸면 이전 링크는 무효가 됩니다.

### 비밀번호 재설정

**인증된(verified) 이메일이 등록된 계정에만** 실제 링크가 발송됩니다.

1. `POST /password-reset/request` — 계정·이메일 존재 여부와 무관하게 항상 같은 200을 반환(enumeration 방지)
2. 메일 링크 → `POST /password-reset/confirm`

재설정 토큰은 발급 시점의 비밀번호 해시로 서명되어 **한 번 쓰면 무효**이며 30분 후 만료됩니다.

### 권한

- `admin` — 모든 작업
- `club_member` — 문서별 권한 설정에 따름
- `login_user` — 일반 가입자 기본값
- 비로그인 — 조회만

문서 수정·이동·삭제·댓글은 **문서별 권한**(`Permissions` 테이블)으로 결정됩니다. 문서 생성 시 기본값은 `update`/`comment` = 로그인 사용자 전체, `move`/`delete` = admin입니다. 단, **문서 작성자**는 자기 문서를 권한 설정과 무관하게 삭제할 수 있습니다. 태그 삭제와 카테고리 수정·삭제는 admin 전용입니다.

### 입력 정책

- **아이디**: 3~32자, 영문/숫자/`_`/`-`
- **비밀번호**: 최소 8자, 영문·숫자 모두 포함
- `guest`, `admin`, `system`, `bot`, `anonymous`는 가입 불가(예약어)

로그인 실패 시 아이디/비밀번호 중 어느 쪽이 틀렸는지 구분되지 않습니다(enumeration 방지).

### Rate limiting

IP 기준이며 초과 시 `429`입니다.

| 분당 3회 | 분당 5회 |
|---|---|
| `POST /register`<br>`POST /password-reset/request`<br>`POST /email/verify-request` | `POST /login`<br>`POST /login/2fa`<br>`POST /password-reset/confirm`<br>`POST /email/verify` |

## API

인증 열: `-` 불필요 / `필요` 로그인 / `문서권한` 문서별 권한 / `admin` 관리자.
목록 조회는 공통으로 `limit`(미지정 시 전체)·`offset`(기본 0)을 받으며, **`offset`은 `limit`이 있을 때만 적용**됩니다.

### 문서

| 엔드포인트 | 인증 | 설명 |
|---|---|---|
| `GET /documents` | - | 문서 목록. `keyword`가 있으면 제목·본문 부분 일치로 필터 |
| `POST /documents` | 필요 | 문서 생성. 바디: `title`, `content`, `category`, `tags` |
| `GET /documents/{title}` | - | 문서 단건 조회 |
| `PUT /documents/{title}` | 문서권한 `update` | `content`/`category`/`tags` 중 보낸 필드만 수정, 새 버전 생성 |
| `DELETE /documents/{title}` | 작성자 또는 문서권한 `delete` | 문서 + 버전 + 권한 레코드 삭제 |
| `GET /documents/{title}/versions` | - | 버전 목록 |
| `GET /documents/{title}/versions/{n}` | - | 특정 버전 |
| `GET /documents/{title}/diff/{n}` | - | `n`번 버전과 직전 버전의 본문 diff. `(op, text)` 목록(op: -1 삭제 / 0 유지 / 1 추가). `n <= 1`이면 400 |
| `GET /search` | - | `keyword`(필수) + `search_type` = `title`(기본) / `title_content` / `tag` |

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
| `POST /2fa/setup` | 필요 | `{secret, otpauth_uri}` |
| `POST /2fa/enable` | 필요 | 바디: `code` |
| `POST /2fa/disable` | 필요 | 바디: `code` |
| `PUT /email` | 필요 | 바디: `email`. 형식 오류 400, 중복 409 |
| `POST /email/verify-request` | 필요 | 인증 메일 재발송 |
| `POST /email/verify` | - | 바디: `token` |
| `POST /password-reset/request` | - | 바디: `username`. 항상 동일 응답 |
| `POST /password-reset/confirm` | - | 바디: `token`, `new_password` |

### 태그

| 엔드포인트 | 인증 | 설명 |
|---|---|---|
| `GET /tags` | - | 태그 전체 목록 |
| `POST /tags` | 필요 | 바디: `name` |
| `GET /tags/{name}/documents` | - | 해당 태그가 달린 문서 목록(태그명 정확 일치). 태그가 없으면 404 |
| `DELETE /tags/{name}` | admin | 삭제 시 이 태그를 쓰던 모든 문서에서도 함께 제거 |

### 카테고리

| 엔드포인트 | 인증 | 설명 |
|---|---|---|
| `GET /categories` | - | 카테고리 트리(루트부터 `children` 중첩) |
| `POST /categories` | 필요 | 바디: `name`, `parent`(선택) |
| `GET /categories/{name}` | - | 해당 카테고리 노드. `children`은 **하위 카테고리**이며 문서가 아님 |
| `GET /categories/{name}/documents` | - | 카테고리에 속한 **문서** 목록. `recursive=true`면 하위 카테고리 문서까지 포함 |
| `PUT /categories/{name}` | admin | 바디: `parent`. 자기 하위 노드를 부모로 지정하는 순환 참조는 거부 |
| `DELETE /categories/{name}` | admin | 하위 카테고리까지 삭제. 문서가 사용 중이면 409 |

## 운영 노트

### 본문(content) 렌더링과 XSS

백엔드는 문서 본문을 가공 없이 그대로 저장합니다(마크다운·위키 문법 보존).

**프론트엔드는 렌더링 시 반드시 sanitization을 적용해야 합니다.** 마크다운 렌더러의 HTML 인라인 옵션을 끄거나 DOMPurify 등으로 정제하세요. `<script>`, `<iframe>`, `on*` 핸들러가 그대로 렌더링되면 XSS 위험이 있습니다.

### 자동 백업

- 매일 자정 `db_backups/db_backup_YYYYMMDD_HHhMMmSSs.db`로 백업
- `shutil` 파일 복사가 아닌 SQLite Backup API를 사용해 트랜잭션 안전하게 복사

### 로깅

stdout과 `logs/app.log`에 동시 출력하며, 5MB마다 회전해 최대 5개(`app.log.1` ~ `app.log.5`)를 보관합니다. 기록 대상: 회원가입, 로그인 성공/실패, 문서·태그·카테고리 변경, 관리자 부트스트랩, 백업 결과.

### CORS

허용 origin은 `FRONTEND_URL`과 로컬 개발용(`localhost:5173`, `127.0.0.1:5173`)입니다. 메서드와 헤더는 명시적 화이트리스트이며, 헤더에는 `Authorization`과 구버전 호환용 `auth`가 모두 포함됩니다.

### 스키마 마이그레이션

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
