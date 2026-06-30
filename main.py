import logging
import os
import sqlite3
from logging.handlers import RotatingFileHandler
from fastapi import FastAPI, HTTPException, Depends, status, Header, Request
from sqlmodel import create_engine, Session, select, SQLModel
from sqlalchemy.exc import IntegrityError
from login_utils import hash_password, verify_password, create_jwt_token, verify_jwt_token, validate_username, validate_password
from schemas.wiki_doc import WikiDoc, WikiDocCreate, WikiDocUpdate, WikiDocVersion
from schemas.wiki_user import WikiUser, UserIdAndPassword
from schemas.permissions import Permissions
from schemas.tags import WikiTag, WikiTagCreate
from schemas.categories import WikiCategory, WikiCategoryCreate
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

os.makedirs(BACKUP_DIR, exist_ok=True)

def backup_database():
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
    bootstrap_admin()
    scheduler = BackgroundScheduler()
    scheduler.add_job(backup_database, 'cron', hour=0, minute=0)
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

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

# Check document specific permission
def check_document_permission(session: Session, current_user: WikiUser, title: str, action: str):
    permission = session.get(Permissions, title)
    if not permission:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Document permissions not configured')
    allowed = getattr(permission, action, None)
    current_user_permission = current_user.permission if current_user else None
    if not allowed or current_user_permission not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f'Requires document-specific \'{action}\' permission')

# Validate that referenced tags/category exist in DB
def validate_tags_and_category(session: Session, tags, category, current_user=None, create_missing_tags=False):
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
        if current_user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required to create a tag.')

        new_tag = WikiTag(name=tag_name)
        session.add(new_tag)
        session.commit()
        session.refresh(new_tag)

# Get documents
@app.get('/documents')
async def get_documents(keyword: str | None = None, limit: int | None = None, offset: int = 0):
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

# Create document
@app.post('/documents')
async def create_document(doc_in: WikiDocCreate, current_user: WikiUser = Depends(get_current_user)):
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

# Read document
@app.get('/documents/{title}')
async def get_document(title: str):
    with Session(engine) as session:
        doc = session.get(WikiDoc, title)
        if not doc:
            raise HTTPException(status_code=404, detail='Cannot find a document with the corresponding name.')
        return doc

# Update document
@app.put('/documents/{title}')
async def update_document(title: str, update_data: WikiDocUpdate, current_user: WikiUser = Depends(get_current_user)):
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

# Search documents
@app.get('/search')
async def search_documents(keyword: str, search_type: str = 'title', limit: int | None = None, offset: int = 0):
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

# Register user
@app.post('/register')
@limiter.limit('3/minute')
async def register_user(request: Request, user_info: UserIdAndPassword):
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
    
# Get user info
@app.get('/users/{username}')
async def get_user_info(username: str, current_user: WikiUser = Depends(get_current_user)):
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
            user_data = user.model_dump(exclude={'password', 'email'})
        else:
            user_data = user.model_dump(exclude={'password'})
        user_data['edit_versions'] = edit_versions
        return user_data
    
# Login user
@app.post('/login')
@limiter.limit('5/minute')
async def login_user(request: Request, user_info: UserIdAndPassword):
    with Session(engine) as session:
        user = session.get(WikiUser, user_info.username)
        if not user or not verify_password(user_info.password, user.password):
            logger.warning('login failed for username: %s', user_info.username)
            raise HTTPException(status_code=401, detail='Invalid username or password.')

        token = create_jwt_token(user_info.username)
        logger.info('login success: %s', user_info.username)
        return {'token': token}

# Get all tags
@app.get('/tags')
async def get_tags():
    with Session(engine) as session:
        return session.exec(select(WikiTag)).all()

# Create tag
@app.post('/tags')
async def create_tag(tag_in: WikiTagCreate, current_user: WikiUser = Depends(get_current_user)):
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

# Get tag
@app.get('/tags/{name}')
async def get_tag(name: str):
    with Session(engine) as session:
        tag = session.get(WikiTag, name)
        if not tag:
            raise HTTPException(status_code=404, detail='Cannot find the corresponding tag.')
        return tag

# Delete tag
@app.delete('/tags/{name}')
async def delete_tag(name: str, current_user: WikiUser = Depends(get_current_user)):
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
    
# Get document versions
@app.get('/documents/{title}/versions')
async def get_document_versions(title: str):
    with Session(engine) as session:
        doc = session.get(WikiDoc, title)
        if not doc:
            raise HTTPException(status_code=404, detail='Cannot find document with the corresponding name.')
        return doc.versions
    
# Get specific document version
@app.get('/documents/{title}/versions/{version_number}')
async def get_document_version(title: str, version_number: int):
    with Session(engine) as session:
        version = session.get(WikiDocVersion, (title, version_number))
        if not version:
            raise HTTPException(status_code=404, detail='Cannot find the corresponding document version.')
        return version
    
# Get difference of document update
@app.get('/documents/{title}/diff/{version_number}')
async def get_document_update_diff(title: str, version_number: int):
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
    
# Get all categories
@app.get('/categories')
async def get_categories():
    with Session(engine) as session:
        return session.exec(select(WikiCategory)).all()

# Create category
@app.post('/categories')
async def create_category(category_in: WikiCategoryCreate, current_user: WikiUser = Depends(get_current_user)):
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

# Get category
@app.get('/categories/{name}')
async def get_category(name: str):
    with Session(engine) as session:
        category = session.get(WikiCategory, name)
        if not category:
            raise HTTPException(status_code=404, detail='Cannot find the corresponding category.')
        return category

# Delete category
@app.delete('/categories/{name}')
async def delete_category(name: str, current_user: WikiUser = Depends(get_current_user)):
    if current_user is None or current_user.permission != 'admin':
        raise HTTPException(status_code=403, detail='Admin permission required to delete categories.')
    with Session(engine) as session:
        if not (category := session.get(WikiCategory, name)):
            raise HTTPException(status_code=404, detail='Cannot find category to delete.')

        # Reject if any document still uses this category
        in_use = sum(
            1 for doc in session.exec(select(WikiDoc)).all()
            if (doc.category.get('name') if isinstance(doc.category, dict) else getattr(doc.category, 'name', None)) == name
        )
        if in_use:
            raise HTTPException(status_code=409, detail=f"Category '{name}' is in use by {in_use} document(s). Move them to another category first.")

        session.delete(category)
        session.commit()
        logger.info('category deleted: %s by %s', name, current_user.username)
        return {'message': f'The category named {name} has been deleted.'}
