from fastapi import FastAPI, HTTPException, Depends, status, Header
from sqlmodel import create_engine, Session, select, SQLModel
from login_utils import hash_password, verify_password, create_jwt_token, verify_jwt_token
from schemas.wiki_doc import WikiDoc, WikiDocCreate, WikiDocUpdate
from schemas.wiki_user import WikiUser
from schemas.permissions import Permissions
from datetime import datetime, timezone

app = FastAPI()

engine = create_engine("sqlite:///wiki.db")
SQLModel.metadata.create_all(engine)

async def get_current_user(auth: str = Header(...)):
    username = verify_jwt_token(auth)

    with Session(engine) as session:
        user = session.get(WikiUser, username)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        return user

# Check document specific permission
def check_document_permission(session: Session, current_user: WikiUser, title: str, action: str):
    permission = session.get(Permissions, title)
    if not permission:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Document permissions not configured')
    allowed = getattr(permission, action, None)
    if not allowed or current_user.permission not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Requires document-specific '{action}' permission")

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
async def create_document(doc_in: WikiDocCreate):
    with Session(engine) as session:
        if session.get(WikiDoc, doc_in.title):
            raise HTTPException(status_code=400, detail='There is already a document with the same name.')
        
        doc = WikiDoc(**doc_in.model_dump())
        doc.updated_at = datetime.now(timezone.utc)
        session.add(doc)
        
        default_permissions = Permissions(
            wiki_doc_title=doc.title,
            update=['admin', 'club_member'],
            move=['admin'],
            delete=['admin'],
            comment=['admin', 'club_member', 'login_user']
        )
        session.add(default_permissions)
        session.commit()
        session.refresh(doc)
        return doc

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
            doc.tags = update_data.tags
        
        doc.updated_at = datetime.now(timezone.utc)
        
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
    
# Register user
@app.post('/register')
async def register_user(username: str, password: str):
    with Session(engine) as session:
        if session.get(WikiUser, username):
            raise HTTPException(status_code=400, detail='Username already exists.')
        
        user = WikiUser(username=username, password=hash_password(password), permission='login_user')
        session.add(user)
        session.commit()
        session.refresh(user)
        return {'message': f'User {username} has been registered successfully.'}
    
# Get user info
@app.get('/users/{username}')
async def get_user_info(username: str):
    with Session(engine) as session:
        user = session.get(WikiUser, username)
        if not user:
            raise HTTPException(status_code=404, detail='Cannot find user with the corresponding username.')
        return user
    
# Login user
@app.post('/login')
async def login_user(username: str, password: str):
    with Session(engine) as session:
        user = session.get(WikiUser, username)
        if not user:
            raise HTTPException(status_code=404, detail='Cannot find user with the corresponding username.')
        
        if not verify_password(password, user.password):
            raise HTTPException(status_code=401, detail='Incorrect password.')
        
        token = create_jwt_token(username)
        
        return {'token': token}