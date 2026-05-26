import shutil
import os
from fastapi import FastAPI, HTTPException, Depends, status, Header
from sqlmodel import create_engine, Session, select, SQLModel
from login_utils import hash_password, verify_password, create_jwt_token, verify_jwt_token
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

BACKUP_DIR = './db_backups'
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:5173')
RESERVED_USERNAMES = {'guest', 'admin', 'system', 'bot', 'anonymous'}

os.makedirs(BACKUP_DIR, exist_ok=True)

def backup_database():
    today_str = datetime.now().strftime('%Y%m%d_%Hh%Mm%Ss')
    backup_path = f'{BACKUP_DIR}/db_backup_{today_str}.db'

    shutil.copy2('wiki.db', backup_path)

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler()
    scheduler.add_job(backup_database, 'cron', hour=0, minute=0)
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

load_dotenv()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, 'http://localhost:5173', 'http://127.0.0.1:5173'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*']
)

engine = create_engine('sqlite:///wiki.db')
SQLModel.metadata.create_all(engine)

async def get_current_user(auth: str = Header(None)):
    if auth is None:
        return None
    username = verify_jwt_token(auth)

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

# Get documents
@app.get('/documents')
async def get_documents(keyword: str | None = None):
    with Session(engine) as session:
        if keyword:
            statement = select(WikiDoc).where(
                WikiDoc.title.contains(keyword) | WikiDoc.content.contains(keyword)
            )
            return session.exec(statement).all()
        else:
            return session.exec(select(WikiDoc)).all()

# Create document
@app.post('/documents')
async def create_document(doc_in: WikiDocCreate, current_user: WikiUser | None = Depends(get_current_user)):
    with Session(engine) as session:
        if session.get(WikiDoc, doc_in.title):
            raise HTTPException(status_code=400, detail='There is already a document with the same name.')
        
        doc = WikiDoc(**doc_in.model_dump())
        doc.updated_at = datetime.now(timezone.utc)

        version = WikiDocVersion(
            wiki_doc=doc,
            wiki_doc_title=doc.title,
            version_number=1,
            content=doc.content,
            category=doc.category,
            tags=[{'name': tag.name} if hasattr(tag, 'name') else tag for tag in doc.tags],
            updated_at=doc.updated_at,
            updated_by=current_user.username if current_user else 'guest'
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

        session.add(doc)
        session.commit()
        session.refresh(doc)
        return doc

@app.delete('/documents/{title}')
async def delete_document(title: str, current_user: WikiUser = Depends(get_current_user)):
    with Session(engine) as session:
        if not (doc := session.get(WikiDoc, title)):
            raise HTTPException(status_code=404, detail='Cannot find document to delete')

        check_document_permission(session, current_user, title, 'delete')

        session.delete(doc)
        session.commit()
        return {'message': f'The document named {title} has been deleted.'}

# Search documents
@app.get('/search')
async def search_documents(keyword: str, search_type: str = 'title'):
    with Session(engine) as session:
        if search_type == 'title':
            statement = select(WikiDoc).where(WikiDoc.title.contains(keyword))
        elif search_type == 'title_content':
            statement = select(WikiDoc).where(
                WikiDoc.title.contains(keyword) | WikiDoc.content.contains(keyword)
            )
        elif search_type == 'tag':
            statement = select(WikiDoc).where(WikiDoc.tags.contains(keyword))
        else:
            raise HTTPException(status_code=400, detail='Invalid search type.')
        return session.exec(statement).all()

# Register user
@app.post('/register')
async def register_user(user_info: UserIdAndPassword):
    with Session(engine) as session:
        if session.get(WikiUser, user_info.username):
            raise HTTPException(status_code=400, detail='Username already exists.')
        
        if user_info.username.lower() in RESERVED_USERNAMES:
            raise HTTPException(
                status_code=400,
                detail='This username is reserved and cannot be used.',
            )
    
        user = WikiUser(username=user_info.username, password=hash_password(user_info.password), permission='login_user', bio='', email='')

        session.add(user)
        session.commit()
        session.refresh(user)
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
async def login_user(user_info: UserIdAndPassword):
    with Session(engine) as session:
        user = session.get(WikiUser, user_info.username)
        if not user:
            raise HTTPException(status_code=404, detail='Cannot find user with the corresponding username.')
        
        if not verify_password(user_info.password, user.password):
            raise HTTPException(status_code=401, detail='Incorrect password.')
        
        token = create_jwt_token(user_info.username)
        
        return {'token': token}

# Get all tags
@app.get('/tags')
async def get_tags():
    with Session(engine) as session:
        return session.exec(select(WikiTag)).all()

# Create tag
@app.post('/tags')
async def create_tag(tag_in: WikiTagCreate, current_user: WikiUser = Depends(get_current_user)):
    with Session(engine) as session:
        if session.get(WikiTag, tag_in.name):
            raise HTTPException(status_code=400, detail='Tag name already exists.')
        
        tag = WikiTag(**tag_in.model_dump())
        session.add(tag)
        session.commit()
        session.refresh(tag)
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
    with Session(engine) as session:
        if current_user.permission != 'admin':
            raise HTTPException(status_code=403, detail='Admin permission required to delete tags.')
        if not (tag := session.get(WikiTag, name)):
            raise HTTPException(status_code=404, detail='Cannot find tag to delete.')

        session.delete(tag)
        session.commit()
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
    with Session(engine) as session:
        if session.get(WikiCategory, category_in.name):
            raise HTTPException(status_code=400, detail='Category name already exists.')
        
        category = WikiCategory(**category_in.model_dump())
        session.add(category)
        session.commit()
        session.refresh(category)
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
    with Session(engine) as session:
        if not (category := session.get(WikiCategory, name)):
            raise HTTPException(status_code=404, detail='Cannot find category to delete.')

        session.delete(category)
        session.commit()
        return {'message': f'The category named {name} has been deleted.'}
