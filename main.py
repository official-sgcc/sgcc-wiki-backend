import logging
import os
import smtplib
import sqlite3
from email.message import EmailMessage
from logging.handlers import RotatingFileHandler
from fastapi import FastAPI, HTTPException, Depends, status, Header, Request
from sqlmodel import create_engine, Session, select, SQLModel
from sqlalchemy.exc import IntegrityError
from login_utils import (
    hash_password, verify_password, create_jwt_token, verify_jwt_token,
    validate_username, validate_password, validate_email,
    create_mfa_token, verify_mfa_token,
    create_password_reset_token, read_reset_token_subject, verify_password_reset_token,
    create_email_verification_token, verify_email_verification_token,
    generate_totp_secret, totp_provisioning_uri, matched_totp_step,
)
from schemas.wiki_doc import WikiDoc, WikiDocCreate, WikiDocUpdate, WikiDocVersion
from schemas.wiki_user import (
    WikiUser, UserIdAndPassword,
    PasswordResetRequest, PasswordResetConfirm, TotpCode, TotpLogin,
    EmailUpdate, EmailVerify,
)
from schemas.permissions import Permissions
from schemas.tags import WikiTag, WikiTagCreate
from schemas.categories import WikiCategory, WikiCategoryCreate, WikiCategoryNode
from datetime import datetime, timezone
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
from diff_match_patch import diff_match_patch
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

load_dotenv()

LOG_DIR = './logs'
os.makedirs(LOG_DIR, exist_ok=True)

log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
file_handler = RotatingFileHandler(f'{LOG_DIR}/app.log', maxBytes=5_000_000, backupCount=5)
file_handler.setFormatter(log_formatter)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[stream_handler, file_handler])
logger = logging.getLogger('sgcc-wiki')

BACKUP_DIR = './db_backups'
DB_PATH = os.getenv('DB_PATH', 'wiki.db')
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:5173')
RESERVED_USERNAMES = {'guest', 'admin', 'system', 'bot', 'anonymous'}

SMTP_HOST = os.getenv('SMTP_HOST')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
SMTP_USER = os.getenv('SMTP_USER')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')
SMTP_FROM = os.getenv('SMTP_FROM', 'no-reply@sgcc-wiki.local')

os.makedirs(BACKUP_DIR, exist_ok=True)

def send_email(to: str, subject: str, body: str):
    """이메일을 발송한다. SMTP가 설정돼 있으면 실제 전송, 아니면 로그로 대체한다.

    SMTP_HOST 환경변수가 없으면(개발/미설정) 실제 발송 대신 내용을 로그에 남긴다.
    비밀번호 재설정 링크가 여기로 흐르므로, 로그를 보면 흐름을 확인할 수 있다.
    나중에 SMTP_* 환경변수만 채우면 코드 변경 없이 실제 발송으로 전환된다.
    """
    if not SMTP_HOST:
        logger.info('email not configured; would send to %s [%s]:\n%s', to, subject, body)
        return

    msg = EmailMessage()
    msg['From'] = SMTP_FROM
    msg['To'] = to
    msg['Subject'] = subject
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
    logger.info('email sent to %s [%s]', to, subject)

def send_email_verification(username: str, email: str):
    """해당 이메일로 인증 링크를 발송한다."""
    token = create_email_verification_token(username, email)
    verify_link = f'{FRONTEND_URL}/verify-email?token={token}'
    send_email(
        email,
        'SGCC Wiki 이메일 인증',
        f'아래 링크로 이메일을 인증하세요 (24시간 내 유효):\n\n{verify_link}',
    )

def backup_database():
    """현재 SQLite DB를 db_backups/에 타임스탬프 파일로 스냅샷한다.

    단순 파일 복사(shutil.copy2)가 아니라 sqlite3.Connection.backup(SQLite Backup API)을
    쓴다. 쓰기 트랜잭션이 진행 중이어도 일관된 스냅샷을 뜨기 위함이며,
    단순 파일 복사로 회귀시키면 백업이 깨질 수 있다.

    lifespan에 등록된 자정(00:00) cron 스케줄러가 호출한다. 백업 파일명은
    `db_backup_YYYYMMDD_HHhMMmSSs.db` 형식.

    Raises:
        Exception: 백업 실패 시 로그를 남기고 예외를 그대로 재전파한다.
                   (원본/대상 커넥션은 finally에서 항상 닫힌다.)
    """
    today_str = datetime.now().strftime('%Y%m%d_%Hh%Mm%Ss')
    backup_path = f'{BACKUP_DIR}/db_backup_{today_str}.db'

    source = sqlite3.connect(DB_PATH)
    dest = sqlite3.connect(backup_path)
    try:
        with dest:
            source.backup(dest)
        logger.info('database backup created: %s', backup_path)
    except Exception:
        logger.exception('database backup failed: %s', backup_path)
        raise
    finally:
        source.close()
        dest.close()

def bootstrap_admin():
    """환경변수로 지정된 관리자 계정을 앱 시작 시 보장한다.

    ADMIN_USERNAME / ADMIN_PASSWORD 환경변수를 읽어:
      - 둘 중 하나라도 비어 있으면 아무 동작도 하지 않고 반환한다.
      - 해당 사용자가 없으면 permission='admin'으로 새로 생성한다.
      - 이미 있으면 permission을 'admin'으로 승격한다(이미 admin이면 그대로 둠).

    register API는 RESERVED_USERNAMES 때문에 'admin' 가입을 막으므로, 관리자 계정은
    이 부트스트랩 경로로만 만들어진다.
    """
    admin_username = os.getenv('ADMIN_USERNAME')
    admin_password = os.getenv('ADMIN_PASSWORD')
    if not admin_username or not admin_password:
        return

    with Session(engine) as session:
        user = session.get(WikiUser, admin_username)
        if user:
            if user.permission != 'admin':
                user.permission = 'admin'
                session.add(user)
                session.commit()
                logger.info('admin bootstrap: promoted existing user to admin: %s', admin_username)
        else:
            user = WikiUser(
                username=admin_username,
                password=hash_password(admin_password),
                permission='admin',
                bio='',
                email=None,
            )
            session.add(user)
            session.commit()
            logger.info('admin bootstrap: created admin user: %s', admin_username)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 수명주기 훅.

    시작 시: 관리자 계정 부트스트랩 후 백그라운드 스케줄러를 띄워 매일 자정
    backup_database를 예약한다. 종료 시: 스케줄러를 정리한다.

    Args:
        app: FastAPI 인스턴스(프레임워크가 주입, 여기서는 사용하지 않음).
    """
    bootstrap_admin()
    scheduler = BackgroundScheduler()
    scheduler.add_job(backup_database, 'cron', hour=0, minute=0)
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(
    title='SGCC Wiki API',
    description='소규모 위키 백엔드 — 문서 CRUD/버전·diff, JWT 인증, 태그·카테고리, 문서별 권한, 자동 백업',
    version='1.0.0',
    lifespan=lifespan,
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, 'http://localhost:5173', 'http://127.0.0.1:5173'],
    allow_credentials=True,
    allow_methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
    allow_headers=['Content-Type', 'auth', 'Authorization'],
)

engine = create_engine(f'sqlite:///{DB_PATH}')
SQLModel.metadata.create_all(engine)

async def get_current_user(
    auth: str | None = Header(None),
    authorization: str | None = Header(None),
):
    """요청 헤더의 JWT를 해석해 현재 로그인 사용자를 반환하는 인증 의존성.

    토큰을 두 헤더에서 찾는다(우선순위대로):
      1. `Authorization: Bearer <token>` — 표준 방식
      2. `auth: <token>` — 구버전 프론트엔드 호환용. 프론트를 깨지 않기 위해 유지.

    토큰이 아예 없으면 예외 대신 None을 반환한다. 따라서 이 의존성을 쓰는 핸들러는
    "비로그인 허용"이 기본이며, 로그인 필수 여부는 각 핸들러에서 `current_user is None`을
    직접 검사해 결정한다.

    Args:
        auth: `auth` 헤더 값(raw 토큰). 없으면 None.
        authorization: `Authorization` 헤더 값(`Bearer ` 접두사 포함). 없으면 None.

    Returns:
        WikiUser | None: 유효한 토큰이면 해당 사용자, 토큰이 없으면 None.

    Raises:
        HTTPException 401: 토큰은 있으나 가리키는 사용자가 DB에 없을 때.
                           (토큰 자체가 잘못된 경우는 verify_jwt_token에서 처리)
    """
    token = None
    if authorization and authorization.lower().startswith('bearer '):
        token = authorization[7:].strip()
    elif auth:
        token = auth

    if not token:
        return None

    username = verify_jwt_token(token)

    with Session(engine) as session:
        user = session.get(WikiUser, username)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='User not found')
        return user

def check_document_permission(session: Session, current_user: WikiUser, title: str, action: str):
    """문서별 권한(Permissions 테이블)으로 특정 동작 수행 가능 여부를 검사한다.

    Permissions는 문서마다 action별 허용 권한 등급 리스트를 JSON으로 갖는다
    (예: update=['admin', 'club_member', 'login_user']). current_user의 권한 등급이
    해당 action의 허용 목록에 들어 있어야 통과한다.

    Args:
        session: 활성 DB 세션.
        current_user: 현재 사용자. None(비로그인)이면 권한 등급을 None으로 취급해 거부된다.
        title: 대상 문서 제목(Permissions PK).
        action: 검사할 동작. 'update' / 'move' / 'delete' / 'comment' 중 하나.

    Raises:
        HTTPException 403: 문서 권한 설정이 없거나, 허용 목록이 비었거나,
                           current_user의 권한 등급이 목록에 없을 때.
    """
    permission = session.get(Permissions, title)
    if not permission:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Document permissions not configured')
    allowed = getattr(permission, action, None)
    current_user_permission = current_user.permission if current_user else None
    if not allowed or current_user_permission not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f'Requires document-specific \'{action}\' permission')

def validate_tags_and_category(session: Session, tags, category):
    """문서에 지정된 태그·카테고리가 DB에 실제로 존재하는지 검증한다.

    문서 생성/수정 시 존재하지 않는 태그·카테고리 참조를 막기 위한 가드.
    입력은 pydantic 모델(.name 속성) 또는 dict({'name': ...}) 둘 다 허용한다.

    Args:
        session: 활성 DB 세션.
        tags: WikiTag 유사 객체들의 리스트(또는 None).
        category: WikiCategory 유사 객체(또는 None). None이면 카테고리 검사를 건너뛴다.

    Raises:
        HTTPException 400: 참조한 카테고리 또는 태그가 DB에 없을 때.
    """
    if category is not None:
        cat_name = category.name if hasattr(category, 'name') else category.get('name')
        if not session.get(WikiCategory, cat_name):
            raise HTTPException(status_code=400, detail=f"Category '{cat_name}' does not exist.")

    for tag in tags or []:
        tag_name = tag.name if hasattr(tag, 'name') else tag.get('name')
        if session.get(WikiTag, tag_name):
            continue
        if not create_missing_tags:
            raise HTTPException(status_code=400, detail=f"Tag '{tag_name}' does not exist.")

@app.get('/documents')
async def get_documents(keyword: str | None = None, limit: int | None = None, offset: int = 0):
    """문서 목록을 조회한다. (인증 불필요)

    Args:
        keyword: 있으면 제목 또는 본문에 이 문자열을 포함하는 문서만 반환한다.
                 앞뒤 공백은 제거되며, 비면 전체 조회로 처리된다.
        limit: 반환 최대 개수. None이면 제한 없이 전부 반환한다.
        offset: 건너뛸 개수(페이지네이션). limit이 있을 때만 적용된다.

    Returns:
        list[WikiDoc]: 조건에 맞는 문서 목록.
    """
    keyword = keyword.strip() if keyword else None
    with Session(engine) as session:
        if keyword:
            statement = select(WikiDoc).where(
                WikiDoc.title.contains(keyword) | WikiDoc.content.contains(keyword)
            )
        else:
            statement = select(WikiDoc)
        if limit is not None:
            statement = statement.offset(offset).limit(limit)
        return session.exec(statement).all()

@app.post('/documents')
async def create_document(doc_in: WikiDocCreate, current_user: WikiUser = Depends(get_current_user)):
    """새 위키 문서를 생성한다. (로그인 필요)

    문서 본체와 함께 버전 1(WikiDocVersion)과 기본 문서 권한(Permissions)을 한 트랜잭션에
    생성한다. created_by에는 생성자 username이 기록되며, 이후 작성자 삭제 권한의 근거가 된다.
    기본 권한은 update/comment=admin·club_member·login_user, move/delete=admin.

    Args:
        doc_in: 생성할 문서(title, content, category, tags).
        current_user: 인증 사용자. None이면 401.

    Returns:
        dict: `{'message': '...has been created.'}`

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: 같은 제목의 문서가 이미 있거나, 참조 태그·카테고리가 없을 때.
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required to create a document.')
    with Session(engine) as session:
        if session.get(WikiDoc, doc_in.title):
            raise HTTPException(status_code=400, detail='There is already a document with the same name.')

        validate_tags_and_category(session, doc_in.tags, doc_in.category, current_user=current_user, create_missing_tags=True)

        doc = WikiDoc(**doc_in.model_dump())
        doc.created_by = current_user.username
        doc.updated_at = datetime.now(timezone.utc)

        version = WikiDocVersion(
            wiki_doc=doc,
            wiki_doc_title=doc.title,
            version_number=1,
            content=doc.content,
            category=doc.category,
            tags=[{'name': tag.name} if hasattr(tag, 'name') else tag for tag in doc.tags],
            updated_at=doc.updated_at,
            updated_by=current_user.username
        )
        doc.versions.append(version)

        session.add(doc)
        default_permissions = Permissions(
            wiki_doc_title=doc.title,
            update=['admin', 'club_member', 'login_user'],
            move=['admin'],
            delete=['admin'],
            comment=['admin', 'club_member', 'login_user']
        )
        session.add(default_permissions)
        session.commit()
        session.refresh(doc)
        logger.info('document created: %s by %s', doc.title, current_user.username)
        return {'message': f'The document named {doc.title} has been created.'}

@app.get('/documents/{title}')
async def get_document(title: str):
    """제목으로 문서 하나를 조회한다. (인증 불필요)

    Args:
        title: 조회할 문서 제목(PK).

    Returns:
        WikiDoc: 본문·카테고리·태그·버전 관계를 포함한 문서.

    Raises:
        HTTPException 404: 해당 제목의 문서가 없을 때.
    """
    with Session(engine) as session:
        doc = session.get(WikiDoc, title)
        if not doc:
            raise HTTPException(status_code=404, detail='Cannot find a document with the corresponding name.')
        return doc

@app.put('/documents/{title}')
async def update_document(title: str, update_data: WikiDocUpdate, current_user: WikiUser = Depends(get_current_user)):
    """문서를 수정하고 새 버전을 추가한다. (문서별 `update` 권한 필요)

    content/tags/category 중 None이 아닌 필드만 갱신한다. 매 수정마다 새
    WikiDocVersion(version_number = 기존 버전 수 + 1)을 남긴다.

    동시성: 두 요청이 같은 다음 version_number를 만들면 PK 충돌(IntegrityError)이
    난다. 이 경우 롤백 후 문서를 다시 읽어 최대 3회 재시도하고, 그래도 실패하면 409.

    Args:
        title: 수정할 문서 제목.
        update_data: 부분 수정 페이로드(content/category/tags, 모두 선택).
        current_user: 인증 사용자(권한 검사 및 버전의 updated_by 기록에 사용).

    Returns:
        WikiDoc: 갱신된 문서.

    Raises:
        HTTPException 404: 대상 문서가 없을 때.
        HTTPException 403: 문서별 `update` 권한이 없을 때.
        HTTPException 400: 참조 태그·카테고리가 없을 때.
        HTTPException 409: 동시 수정 충돌로 3회 재시도 후에도 저장 실패.
    """
    with Session(engine) as session:
        if not (doc := session.get(WikiDoc, title)):
            raise HTTPException(status_code=404, detail='Cannot find document to update')

        check_document_permission(session, current_user, title, 'update')

        validate_tags_and_category(session, update_data.tags, update_data.category, current_user=current_user, create_missing_tags=True)

        for _ in range(3):
            if update_data.content is not None:
                doc.content = update_data.content

            if update_data.tags is not None:
                doc.tags = [tag.model_dump() if hasattr(tag, 'model_dump') else tag for tag in update_data.tags]

            if update_data.category is not None:
                doc.category = (update_data.category.model_dump() if hasattr(update_data.category, 'model_dump') else update_data.category)

            doc.updated_at = datetime.now(timezone.utc)

            version = WikiDocVersion(
                wiki_doc=doc,
                wiki_doc_title=doc.title,
                version_number=len(doc.versions) + 1,
                content=doc.content,
                category=doc.category,
                tags=doc.tags,
                updated_at=doc.updated_at,
                updated_by=current_user.username
            )
            doc.versions.append(version)

            try:
                session.add(doc)
                session.commit()
                break
            except IntegrityError:
                session.rollback()
                doc = session.get(WikiDoc, title)
        else:
            logger.warning('document update gave up after retries: %s', title)
            raise HTTPException(status_code=409, detail='Could not save document version due to concurrent updates. Try again.')

        session.refresh(doc)
        logger.info('document updated: %s by %s (version %d)', title, current_user.username, len(doc.versions))
        return doc

@app.delete('/documents/{title}')
async def delete_document(title: str, current_user: WikiUser = Depends(get_current_user)):
    """문서를 삭제한다. (작성자 본인 또는 문서별 `delete` 권한)

    작성자 본인(current_user.username == doc.created_by)이면 권한 검사 없이 삭제할 수
    있다. 이 예외는 삭제에만 적용되며 update/move 등 다른 동작에는 적용되지 않는다.
    작성자가 아니면 문서별 `delete` 권한을 검사한다. 문서 삭제 시 연결된
    버전·권한 레코드도 cascade로 함께 제거된다.

    Args:
        title: 삭제할 문서 제목.
        current_user: 인증 사용자(작성자 판별 및 권한 검사에 사용).

    Returns:
        dict: `{'message': '...has been deleted.'}`

    Raises:
        HTTPException 404: 대상 문서가 없을 때.
        HTTPException 403: 작성자가 아니고 문서별 `delete` 권한도 없을 때.
    """
    with Session(engine) as session:
        if not (doc := session.get(WikiDoc, title)):
            raise HTTPException(status_code=404, detail='Cannot find document to delete')

        is_creator = (
            current_user is not None
            and doc.created_by is not None
            and doc.created_by == current_user.username
        )
        if not is_creator:
            check_document_permission(session, current_user, title, 'delete')

        session.delete(doc)
        session.commit()
        logger.info('document deleted: %s by %s', title, current_user.username if current_user else 'unknown')
        return {'message': f'The document named {title} has been deleted.'}

@app.get('/search')
async def search_documents(keyword: str, search_type: str = 'title', limit: int | None = None, offset: int = 0):
    """문서를 검색한다. (인증 불필요)

    Args:
        keyword: 검색어(필수). strip 후 빈 문자열이면 400.
        search_type: 검색 방식.
            - 'title'(기본): 제목 부분 일치.
            - 'title_content': 제목 또는 본문 부분 일치.
            - 'tag': 태그명 정확 일치(JSON 부분검색으로 후보를 좁힌 뒤 파이썬에서 정확 매칭).
        limit: 반환 최대 개수. None이면 제한 없음.
        offset: 건너뛸 개수(limit이 있을 때만 적용).

    Returns:
        list[WikiDoc]: 검색 결과 문서 목록.

    Raises:
        HTTPException 400: keyword가 비었거나, search_type이 위 세 값이 아닐 때.
    """
    keyword = keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail='Search keyword cannot be empty.')
    with Session(engine) as session:
        if search_type == 'title':
            statement = select(WikiDoc).where(WikiDoc.title.contains(keyword))
        elif search_type == 'title_content':
            statement = select(WikiDoc).where(
                WikiDoc.title.contains(keyword) | WikiDoc.content.contains(keyword)
            )
        elif search_type == 'tag':
            statement = select(WikiDoc).where(WikiDoc.tags.contains(f'"{keyword}"'))
            docs = session.exec(statement).all()
            docs = [
                d for d in docs
                if any((t.get('name') if isinstance(t, dict) else getattr(t, 'name', None)) == keyword for t in (d.tags or []))
            ]
            if limit is not None:
                docs = docs[offset:offset + limit]
            return docs
        else:
            raise HTTPException(status_code=400, detail='Invalid search type.')
        if limit is not None:
            statement = statement.offset(offset).limit(limit)
        return session.exec(statement).all()

@app.post('/register')
@limiter.limit('3/minute')
async def register_user(request: Request, user_info: UserIdAndPassword):
    """새 사용자를 등록한다. (rate limit: IP당 분당 3회)

    username(3~32자, [a-zA-Z0-9_-])과 password(8자 이상, 영문+숫자 포함) 정책을
    검증한 뒤, 중복 아이디와 예약어(RESERVED_USERNAMES)를 거부한다. 신규 계정 권한은
    'login_user'이며 비밀번호는 bcrypt로 해시해 저장한다.

    Args:
        request: slowapi rate limiter가 요구하는 요청 객체(직접 사용하지 않음).
        user_info: 가입 정보(username, password).

    Returns:
        dict: `{'message': 'User ... has been registered successfully.'}`

    Raises:
        HTTPException 400: username/password 정책 위반, 중복 아이디, 또는 예약어일 때.
        HTTPException 429: 분당 요청 한도 초과(rate limit).
    """
    validate_username(user_info.username)
    validate_password(user_info.password)

    with Session(engine) as session:
        if session.get(WikiUser, user_info.username):
            raise HTTPException(status_code=400, detail='Username already exists.')

        if user_info.username.lower() in RESERVED_USERNAMES:
            raise HTTPException(
                status_code=400,
                detail='This username is reserved and cannot be used.',
            )

        user = WikiUser(username=user_info.username, password=hash_password(user_info.password), permission='login_user', bio='', email=None)

        session.add(user)
        session.commit()
        session.refresh(user)
        logger.info('user registered: %s', user_info.username)
        return {'message': f'User {user_info.username} has been registered successfully.'}

@app.get('/users/{username}')
async def get_user_info(username: str, current_user: WikiUser = Depends(get_current_user)):
    """사용자 프로필과 편집 이력을 조회한다. (인증 선택)

    사용자가 작성한 모든 문서 버전을 최신순으로 함께 반환한다. password와 totp_secret은
    항상 제외하며, 본인 조회(토큰의 사용자 == 조회 대상)가 아니면 email도 숨긴다.

    Args:
        username: 조회 대상 사용자명.
        current_user: 인증 사용자(본인 여부 판별용). 없으면 비본인으로 취급.

    Returns:
        dict: WikiUser 필드(password 제외, 비본인은 email도 제외)에
              `edit_versions`(list[WikiDocVersion], 최신순)를 더한 객체.

    Raises:
        HTTPException 404: 해당 사용자가 없을 때.
    """
    with Session(engine) as session:
        user = session.get(WikiUser, username)
        if not user:
            raise HTTPException(status_code=404, detail='Cannot find user with the corresponding username.')

        edit_versions = session.exec(
            select(WikiDocVersion)
            .where(WikiDocVersion.updated_by == username)
            .order_by(WikiDocVersion.updated_at.desc())
        ).all()
        if current_user is None or current_user.username != username:
            user_data = user.model_dump(exclude={'password', 'email', 'totp_secret'})
        else:
            user_data = user.model_dump(exclude={'password', 'totp_secret'})
        user_data['edit_versions'] = edit_versions
        return user_data

@app.post('/login')
@limiter.limit('5/minute')
async def login_user(request: Request, user_info: UserIdAndPassword):
    """자격 증명을 검증하고 JWT를 발급한다. (rate limit: IP당 분당 5회)

    보안상 "아이디 없음"과 "비밀번호 불일치"를 구분하지 않고 동일한 401 메시지를
    반환한다(username enumeration 방지). 이 메시지를 분리하지 말 것.

    2단계 인증: 대상 사용자가 2FA를 켜 두었다면(totp_enabled) 이 단계에서는 진짜
    토큰을 주지 않고 `{'mfa_required': True, 'mfa_token': ...}`를 반환한다. 프론트는
    받은 mfa_token과 인증앱 6자리 코드를 `/login/2fa`로 보내 최종 토큰을 받는다.
    2FA를 쓰지 않는 사용자는 기존과 동일하게 곧바로 `{'token': ...}`를 받는다.

    Args:
        request: slowapi rate limiter가 요구하는 요청 객체(직접 사용하지 않음).
        user_info: 로그인 정보(username, password).

    Returns:
        dict: 2FA 미사용 시 `{'token': '<JWT>'}`.
              2FA 사용 시 `{'mfa_required': True, 'mfa_token': '<임시 JWT>'}`.

    Raises:
        HTTPException 401: 아이디가 없거나 비밀번호가 틀렸을 때(동일 메시지).
        HTTPException 429: 분당 요청 한도 초과(rate limit).
    """
    with Session(engine) as session:
        user = session.get(WikiUser, user_info.username)
        if not user or not verify_password(user_info.password, user.password):
            logger.warning('login failed for username: %s', user_info.username)
            raise HTTPException(status_code=401, detail='Invalid username or password.')

        if user.totp_enabled:
            logger.info('login step 1 ok, awaiting 2fa: %s', user_info.username)
            return {'mfa_required': True, 'mfa_token': create_mfa_token(user_info.username)}

        token = create_jwt_token(user_info.username)
        logger.info('login success: %s', user_info.username)
        return {'token': token}

@app.post('/login/2fa')
@limiter.limit('5/minute')
async def login_verify_2fa(request: Request, mfa_in: TotpLogin):
    """2단계 인증 로그인의 두 번째 단계. (rate limit: IP당 분당 5회)

    `/login`이 발급한 임시 mfa_token과 인증앱 6자리 코드를 검증해 최종 JWT를 발급한다.

    Args:
        request: slowapi rate limiter가 요구하는 요청 객체(직접 사용하지 않음).
        mfa_in: `{mfa_token, code}`.

    Returns:
        dict: `{'token': '<JWT>'}`.

    Raises:
        HTTPException 401: mfa_token이 없거나 만료됐을 때, 또는 사용자가 없을 때.
        HTTPException 400: 2FA가 켜져 있지 않거나 코드가 틀렸을 때.
        HTTPException 429: 분당 요청 한도 초과(rate limit).
    """
    username = verify_mfa_token(mfa_in.mfa_token)
    with Session(engine) as session:
        user = session.get(WikiUser, username)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='User not found')
        if not user.totp_enabled or not user.totp_secret:
            raise HTTPException(status_code=400, detail='Two-factor authentication is not enabled for this account.')
        step = matched_totp_step(user.totp_secret, mfa_in.code)
        if step is None or (user.totp_last_step is not None and step <= user.totp_last_step):
            logger.warning('2fa verification failed: %s', username)
            raise HTTPException(status_code=400, detail='Invalid authentication code.')

        user.totp_last_step = step
        session.add(user)
        session.commit()
        token = create_jwt_token(username)
        logger.info('2fa login success: %s', username)
        return {'token': token}

@app.post('/password-reset/request')
@limiter.limit('3/minute')
async def request_password_reset(request: Request, reset_in: PasswordResetRequest):
    """비밀번호 재설정 링크를 이메일로 발송한다. (rate limit: IP당 분당 3회)

    사용자 존재 여부·이메일 등록 여부와 무관하게 항상 동일한 200 응답을 반환한다
    (username/email enumeration 방지). 대상 사용자가 있고 **인증된 이메일**이 등록돼
    있을 때만 실제로 재설정 링크를 발송한다. 링크는 FRONTEND_URL 기준으로 만들어지며
    토큰은 30분(PASSWORD_RESET_EXPIRE_MINUTES) 후 만료된다.

    Args:
        request: slowapi rate limiter가 요구하는 요청 객체(직접 사용하지 않음).
        reset_in: `{username}`.

    Returns:
        dict: 항상 동일한 안내 메시지.

    Raises:
        HTTPException 429: 분당 요청 한도 초과(rate limit).
    """
    with Session(engine) as session:
        user = session.get(WikiUser, reset_in.username)
        if user and user.email and user.email_verified:
            token = create_password_reset_token(user.username, user.password)
            reset_link = f'{FRONTEND_URL}/reset-password?token={token}'
            send_email(
                user.email,
                'SGCC Wiki 비밀번호 재설정',
                f'아래 링크에서 비밀번호를 재설정하세요 (30분 내 유효):\n\n{reset_link}',
            )
            logger.info('password reset requested: %s', user.username)
    return {'message': 'If the account exists, a password reset link has been sent.'}

@app.post('/password-reset/confirm')
@limiter.limit('5/minute')
async def confirm_password_reset(request: Request, confirm_in: PasswordResetConfirm):
    """재설정 토큰과 새 비밀번호로 비밀번호를 교체한다. (rate limit: IP당 분당 5회)

    토큰은 발급 시점의 비밀번호 해시로 서명되므로, 한 번 재설정에 성공하면(해시 변경)
    같은 토큰을 재사용할 수 없다(단일 사용). 새 비밀번호는 기존 정책 검증을 거친다.

    Args:
        request: slowapi rate limiter가 요구하는 요청 객체(직접 사용하지 않음).
        confirm_in: `{token, new_password}`.

    Returns:
        dict: 성공 메시지.

    Raises:
        HTTPException 400: 토큰이 유효하지 않거나 만료됐을 때, 또는 새 비밀번호가
                           정책을 위반했을 때.
    """
    validate_password(confirm_in.new_password)
    username = read_reset_token_subject(confirm_in.token)
    with Session(engine) as session:
        user = session.get(WikiUser, username) if username else None
        if not user:
            raise HTTPException(status_code=400, detail='Invalid or expired reset token.')

        verify_password_reset_token(confirm_in.token, user.password)

        user.password = hash_password(confirm_in.new_password)
        session.add(user)
        session.commit()
        logger.info('password reset completed: %s', user.username)
        return {'message': 'Password has been reset successfully.'}

@app.post('/2fa/setup')
async def setup_2fa(current_user: WikiUser = Depends(get_current_user)):
    """2FA용 TOTP 시크릿을 발급한다. (로그인 필요, 아직 활성화 아님)

    새 시크릿을 생성해 저장하고, 인증앱(Google Authenticator 등)에 등록할 수 있는
    provisioning URI(otpauth://...)를 반환한다. 실제 활성화는 `/2fa/enable`에서 코드를
    확인한 뒤에 이뤄진다. 이미 활성화된 계정에서 재호출하면 400.

    Returns:
        dict: `{'secret': '<base32>', 'otpauth_uri': 'otpauth://...'}`.
              프론트는 otpauth_uri로 QR을 그리거나 secret을 수동 입력하게 한다.

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: 이미 2FA가 활성화돼 있을 때.
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required.')
    with Session(engine) as session:
        user = session.get(WikiUser, current_user.username)
        if user.totp_enabled:
            raise HTTPException(status_code=400, detail='Two-factor authentication is already enabled.')

        secret = generate_totp_secret()
        user.totp_secret = secret
        session.add(user)
        session.commit()
        logger.info('2fa setup initiated: %s', user.username)
        return {'secret': secret, 'otpauth_uri': totp_provisioning_uri(secret, user.username)}

@app.post('/2fa/enable')
async def enable_2fa(body: TotpCode, current_user: WikiUser = Depends(get_current_user)):
    """`/2fa/setup`으로 받은 시크릿을 코드로 확인하고 2FA를 활성화한다. (로그인 필요)

    Args:
        body: `{code}` — 인증앱이 표시하는 현재 6자리 코드.
        current_user: 인증 사용자. None이면 401.

    Returns:
        dict: 성공 메시지.

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: setup을 먼저 하지 않았거나, 이미 활성화됐거나, 코드가 틀렸을 때.
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required.')
    with Session(engine) as session:
        user = session.get(WikiUser, current_user.username)
        if user.totp_enabled:
            raise HTTPException(status_code=400, detail='Two-factor authentication is already enabled.')
        if not user.totp_secret:
            raise HTTPException(status_code=400, detail='Call /2fa/setup before enabling.')
        # enable은 소유 증명일 뿐이라 스텝을 소비하지 않는다. single-use는 실제 인증
        # 이벤트(login/2fa, disable)에서만 강제해, 같은 창에서 enable 직후 로그인이
        # 정상 동작하도록 한다.
        if matched_totp_step(user.totp_secret, body.code) is None:
            raise HTTPException(status_code=400, detail='Invalid authentication code.')

        user.totp_enabled = True
        session.add(user)
        session.commit()
        logger.info('2fa enabled: %s', user.username)
        return {'message': 'Two-factor authentication has been enabled.'}

@app.post('/2fa/disable')
async def disable_2fa(body: TotpCode, current_user: WikiUser = Depends(get_current_user)):
    """현재 코드를 확인하고 2FA를 비활성화한다. (로그인 필요)

    소유 증명을 위해 유효한 인증 코드를 요구한다. 성공 시 시크릿도 함께 제거한다.

    Args:
        body: `{code}` — 인증앱이 표시하는 현재 6자리 코드.
        current_user: 인증 사용자. None이면 401.

    Returns:
        dict: 성공 메시지.

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: 2FA가 켜져 있지 않거나 코드가 틀렸을 때.
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required.')
    with Session(engine) as session:
        user = session.get(WikiUser, current_user.username)
        if not user.totp_enabled or not user.totp_secret:
            raise HTTPException(status_code=400, detail='Two-factor authentication is not enabled.')
        step = matched_totp_step(user.totp_secret, body.code)
        if step is None or (user.totp_last_step is not None and step <= user.totp_last_step):
            raise HTTPException(status_code=400, detail='Invalid authentication code.')

        user.totp_enabled = False
        user.totp_secret = None
        user.totp_last_step = None
        session.add(user)
        session.commit()
        logger.info('2fa disabled: %s', user.username)
        return {'message': 'Two-factor authentication has been disabled.'}

@app.put('/email')
async def set_email(body: EmailUpdate, current_user: WikiUser = Depends(get_current_user)):
    """본인 계정에 이메일을 등록하거나 변경한다. (로그인 필요)

    새 이메일은 항상 미인증 상태(email_verified=False)로 저장되고, 곧바로 인증 링크를
    발송한다. 이메일은 계정 간 유일해야 한다.

    Args:
        body: `{email}`.
        current_user: 인증 사용자. None이면 401.

    Returns:
        dict: 안내 메시지.

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: 이메일 형식이 올바르지 않을 때.
        HTTPException 409: 다른 사용자가 이미 사용 중인 이메일일 때.
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required.')
    validate_email(body.email)
    with Session(engine) as session:
        owner = session.exec(select(WikiUser).where(WikiUser.email == body.email)).first()
        if owner and owner.username != current_user.username:
            raise HTTPException(status_code=409, detail='Email already in use.')

        user = session.get(WikiUser, current_user.username)
        user.email = body.email
        user.email_verified = False
        try:
            session.add(user)
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(status_code=409, detail='Email already in use.')

        send_email_verification(user.username, body.email)
        logger.info('email set (unverified): %s', user.username)
        return {'message': 'Email updated. A verification link has been sent.'}

@app.post('/email/verify-request')
@limiter.limit('3/minute')
async def request_email_verification(request: Request, current_user: WikiUser = Depends(get_current_user)):
    """현재 등록된 이메일로 인증 링크를 재발송한다. (로그인 필요, 분당 3회)

    Args:
        request: slowapi rate limiter가 요구하는 요청 객체(직접 사용하지 않음).
        current_user: 인증 사용자. None이면 401.

    Returns:
        dict: 안내 메시지.

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: 등록된 이메일이 없거나 이미 인증됐을 때.
        HTTPException 429: 분당 요청 한도 초과(rate limit).
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required.')
    with Session(engine) as session:
        user = session.get(WikiUser, current_user.username)
        if not user.email:
            raise HTTPException(status_code=400, detail='No email registered.')
        if user.email_verified:
            raise HTTPException(status_code=400, detail='Email is already verified.')

        send_email_verification(user.username, user.email)
        logger.info('email verification resent: %s', user.username)
        return {'message': 'A verification link has been sent.'}

@app.post('/email/verify')
@limiter.limit('5/minute')
async def verify_email(request: Request, body: EmailVerify):
    """인증 토큰으로 이메일 인증을 완료한다. (비로그인 — 메일 링크에서 호출, 분당 5회)

    토큰에 담긴 이메일이 현재 계정 이메일과 일치할 때만 인증 처리한다(그 사이 이메일을
    바꿨다면 토큰은 무효).

    Args:
        body: `{token}` — 인증 메일 링크에 담긴 토큰.

    Returns:
        dict: 성공 메시지.

    Raises:
        HTTPException 400: 토큰이 유효하지 않거나 만료됐을 때, 또는 그 사이 이메일이
                           변경돼 토큰의 이메일과 현재 이메일이 다를 때.
        HTTPException 429: 분당 요청 한도 초과(rate limit).
    """
    username, email = verify_email_verification_token(body.token)
    with Session(engine) as session:
        user = session.get(WikiUser, username)
        if not user or user.email != email:
            raise HTTPException(status_code=400, detail='Invalid or expired verification token.')
        if user.email_verified:
            return {'message': 'Email is already verified.'}

        user.email_verified = True
        session.add(user)
        session.commit()
        logger.info('email verified: %s', user.username)
        return {'message': 'Email has been verified.'}

@app.get('/tags')
async def get_tags():
    """전체 태그 목록을 조회한다. (인증 불필요)

    Returns:
        list[WikiTag]: 등록된 모든 태그.
    """
    with Session(engine) as session:
        return session.exec(select(WikiTag)).all()

@app.post('/tags')
async def create_tag(tag_in: WikiTagCreate, current_user: WikiUser = Depends(get_current_user)):
    """새 태그를 생성한다. (로그인 필요)

    Args:
        tag_in: 생성할 태그(name).
        current_user: 인증 사용자. None이면 401.

    Returns:
        dict: `{'message': '...has been created.'}`

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: 같은 이름의 태그가 이미 있을 때.
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required to create a tag.')
    with Session(engine) as session:
        if session.get(WikiTag, tag_in.name):
            raise HTTPException(status_code=400, detail='Tag name already exists.')

        tag = WikiTag(**tag_in.model_dump())
        session.add(tag)
        session.commit()
        session.refresh(tag)
        logger.info('tag created: %s by %s', tag_in.name, current_user.username)
        return {'message': f'The tag named {tag_in.name} has been created.'}

@app.get('/tags/{name}/documents')
async def get_documents_by_tag(name: str, limit: int | None = None, offset: int = 0):
    """해당 태그가 달린 모든 문서를 조회한다. (인증 불필요)

    JSON 부분검색으로 후보를 좁힌 뒤, 태그명이 정확히 일치하는 문서만 남긴다
    (검색의 search_type='tag'와 동일한 매칭 규칙).

    Args:
        name: 대상 태그 이름. DB에 존재하지 않으면 404.
        limit: 반환 최대 개수. None이면 제한 없음.
        offset: 건너뛸 개수(limit이 있을 때만 적용).

    Returns:
        list[WikiDoc]: 태그가 달린 문서 목록(없으면 빈 리스트).

    Raises:
        HTTPException 404: 해당 태그가 없을 때.
    """
    with Session(engine) as session:
        if not session.get(WikiTag, name):
            raise HTTPException(status_code=404, detail='Cannot find the corresponding tag.')

        docs = session.exec(
            select(WikiDoc).where(WikiDoc.tags.contains(f'"{name}"'))
        ).all()
        docs = [
            d for d in docs
            if any((t.get('name') if isinstance(t, dict) else getattr(t, 'name', None)) == name for t in (d.tags or []))
        ]
        if limit is not None:
            docs = docs[offset:offset + limit]
        return docs

@app.delete('/tags/{name}')
async def delete_tag(name: str, current_user: WikiUser = Depends(get_current_user)):
    """태그를 삭제한다. (admin 전용)

    삭제와 동시에 이 태그를 참조하는 모든 문서의 tags 목록에서도 해당 태그를 제거해
    끊어진 참조가 남지 않게 한다.

    Args:
        name: 삭제할 태그 이름.
        current_user: 인증 사용자. permission이 'admin'이 아니면 403.

    Returns:
        dict: `{'message': '...has been deleted.'}`

    Raises:
        HTTPException 403: 비로그인이거나 admin이 아닐 때.
        HTTPException 404: 삭제할 태그가 없을 때.
    """
    if current_user is None or current_user.permission != 'admin':
        raise HTTPException(status_code=403, detail='Admin permission required to delete tags.')
    with Session(engine) as session:
        if not (tag := session.get(WikiTag, name)):
            raise HTTPException(status_code=404, detail='Cannot find tag to delete.')

        # Remove this tag from every document that references it
        for doc in session.exec(select(WikiDoc)).all():
            new_tags = [t for t in (doc.tags or []) if (t.get('name') if isinstance(t, dict) else getattr(t, 'name', None)) != name]
            if len(new_tags) != len(doc.tags or []):
                doc.tags = new_tags
                session.add(doc)

        session.delete(tag)
        session.commit()
        logger.info('tag deleted: %s by %s', name, current_user.username)
        return {'message': f'The tag named {name} has been deleted.'}

@app.get('/documents/{title}/versions')
async def get_document_versions(title: str):
    """문서의 전체 버전 이력을 조회한다. (인증 불필요)

    Args:
        title: 대상 문서 제목.

    Returns:
        list[WikiDocVersion]: 해당 문서의 모든 버전.

    Raises:
        HTTPException 404: 해당 문서가 없을 때.
    """
    with Session(engine) as session:
        doc = session.get(WikiDoc, title)
        if not doc:
            raise HTTPException(status_code=404, detail='Cannot find document with the corresponding name.')
        return doc.versions

@app.get('/documents/{title}/versions/{version_number}')
async def get_document_version(title: str, version_number: int):
    """문서의 특정 버전을 조회한다. (인증 불필요)

    Args:
        title: 대상 문서 제목.
        version_number: 조회할 버전 번호(1부터 시작).

    Returns:
        WikiDocVersion: 해당 (문서, 버전) 스냅샷.

    Raises:
        HTTPException 404: 해당 (문서, 버전) 조합이 없을 때.
    """
    with Session(engine) as session:
        version = session.get(WikiDocVersion, (title, version_number))
        if not version:
            raise HTTPException(status_code=404, detail='Cannot find the corresponding document version.')
        return version

@app.get('/documents/{title}/diff/{version_number}')
async def get_document_update_diff(title: str, version_number: int):
    """지정 버전과 직전 버전(version_number - 1)의 본문 diff를 반환한다. (인증 불필요)

    diff-match-patch로 두 버전의 content를 비교한 뒤 diff_cleanupSemantic으로 사람이
    읽기 좋게 정리한다.

    Args:
        title: 대상 문서 제목.
        version_number: 비교 기준이 되는 최신 쪽 버전(2 이상이어야 함).

    Returns:
        list[tuple[int, str]]: (op, text) 형태의 diff 목록.
            op는 -1(삭제) / 0(유지) / 1(추가).

    Raises:
        HTTPException 400: version_number가 1 이하라 비교할 이전 버전이 없을 때.
        HTTPException 404: 두 버전 중 하나라도 없을 때.
    """
    if version_number <= 1:
        raise HTTPException(status_code=400, detail='No previous version to compare with.')
    with Session(engine) as session:
        original = session.get(WikiDocVersion, (title, version_number - 1))
        updated = session.get(WikiDocVersion, (title, version_number))
        if not original or not updated:
            raise HTTPException(status_code=404, detail='Cannot find the corresponding document versions.')
        dmp = diff_match_patch()
        diffs = dmp.diff_main(original.content, updated.content)
        dmp.diff_cleanupSemantic(diffs)
        return diffs

@app.get('/categories')
async def get_categories():
    """전체 카테고리 목록을 조회한다. (인증 불필요)

    Returns:
        list[WikiCategory]: 등록된 모든 카테고리.
    """
    with Session(engine) as session:
        all_cats = session.exec(select(WikiCategory)).all()
        cat_map = {cat.name: cat for cat in all_cats}
        
        def build_node(cat_name: str) -> WikiCategoryNode:
            cat = cat_map[cat_name]
            children = [build_node(c.name) for c in all_cats if c.parent == cat_name]
            return WikiCategoryNode(name=cat.name, parent=cat.parent, children=children)
        
        root_cats = [cat for cat in all_cats if cat.parent is None]
        return [build_node(cat.name) for cat in root_cats]

@app.post('/categories')
async def create_category(category_in: WikiCategoryCreate, current_user: WikiUser = Depends(get_current_user)):
    """새 카테고리를 생성한다. (로그인 필요)

    Args:
        category_in: 생성할 카테고리(name).
        current_user: 인증 사용자. None이면 401.

    Returns:
        dict: `{'message': '...has been created.'}`

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: 같은 이름의 카테고리가 이미 있을 때.
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required to create a category.')
    with Session(engine) as session:
        if session.get(WikiCategory, category_in.name):
            raise HTTPException(status_code=400, detail='Category name already exists.')
        
        category = WikiCategory(**category_in.model_dump())
        session.add(category)
        session.commit()
        session.refresh(category)
        logger.info('category created: %s by %s', category_in.name, current_user.username)
        return {'message': f'The category named {category_in.name} has been created.'}

@app.get('/categories/{name}')
async def get_category(name: str):
    """이름으로 카테고리 하나를 조회한다. (인증 불필요)

    Args:
        name: 조회할 카테고리 이름(PK).

    Returns:
        WikiCategory: 해당 카테고리.

    Raises:
        HTTPException 404: 해당 카테고리가 없을 때.
    """
    with Session(engine) as session:
        category = session.get(WikiCategory, name)
        if not category:
            raise HTTPException(status_code=404, detail='Cannot find the corresponding category.')
        return category

# Delete category
@app.delete('/categories/{name}')
async def delete_category(name: str, current_user: WikiUser = Depends(get_current_user)):
    """카테고리를 삭제한다. (admin 전용)

    사용 중인 카테고리는 삭제를 거부한다. 이 카테고리를 참조하는 문서가 하나라도
    있으면 409를 반환하며, 먼저 문서들을 다른 카테고리로 옮겨야 한다.

    Args:
        name: 삭제할 카테고리 이름.
        current_user: 인증 사용자. permission이 'admin'이 아니면 403.

    Returns:
        dict: `{'message': '...has been deleted.'}`

    Raises:
        HTTPException 403: 비로그인이거나 admin이 아닐 때.
        HTTPException 404: 삭제할 카테고리가 없을 때.
        HTTPException 409: 이 카테고리를 사용하는 문서가 남아 있을 때.
    """
    if current_user is None or current_user.permission != 'admin':
        raise HTTPException(status_code=403, detail='Admin permission required to delete categories.')
    with Session(engine) as session:
        if not (category := session.get(WikiCategory, name)):
            raise HTTPException(status_code=404, detail='Cannot find category to delete.')

        # Reject if any document still uses this category or its subcategories
        def get_all_descendant_names(cat_name: str) -> set:
            descendants = {cat_name}
            for cat in session.exec(select(WikiCategory)).all():
                if cat.parent == cat_name:
                    descendants.update(get_all_descendant_names(cat.name))
            return descendants
        
        all_descendants = get_all_descendant_names(name)
        in_use = sum(
            1 for doc in session.exec(select(WikiDoc)).all()
            if (doc.category.get('name') if isinstance(doc.category, dict) else getattr(doc.category, 'name', None)) in all_descendants
        )
        if in_use:
            raise HTTPException(status_code=409, detail=f"Category '{name}' or its subcategories are in use by {in_use} document(s). Move them to another category first.")

        # Recursively delete all subcategories
        def delete_recursive(cat_name: str):
            for child_cat in session.exec(select(WikiCategory).where(WikiCategory.parent == cat_name)).all():
                delete_recursive(child_cat.name)
            session.delete(session.get(WikiCategory, cat_name))
        
        delete_recursive(name)
        session.commit()
        logger.info('category deleted (with subcategories): %s by %s', name, current_user.username)
        return {'message': f'The category named {name} and its subcategories have been deleted.'}
