from fastapi import FastAPI, HTTPException
from sqlmodel import create_engine, Session, select, SQLModel
from schemas.wiki_doc import WikiDoc, WikiDocCreate, WikiDocUpdate
from schemas.wiki_user import WikiUser
from schemas.permissions import Permissions
from datetime import datetime

app = FastAPI()

engine = create_engine("sqlite:///wiki.db")
SQLModel.metadata.create_all(engine)

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
        doc.updated_at = datetime.now()
        
        session.add(doc)
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
async def update_document(title: str, update_data: WikiDocUpdate):
    with Session(engine) as session:
        if not (doc := session.get(WikiDoc, title)):
            raise HTTPException(status_code=404, detail='Cannot find document to update')

        if update_data.content is not None:
            doc.content = update_data.content
            
        if update_data.tags is not None:
            doc.tags = update_data.tags
        
        doc.updated_at = datetime.now()
        
        session.add(doc)
        session.commit()
        session.refresh(doc)
        return doc

# Delete document
@app.delete('/documents/{title}')
async def delete_document(title: str):
    with Session(engine) as session:
        if not (doc := session.get(WikiDoc, title)):
            raise HTTPException(status_code=404, detail='Cannot find document to delete')

        session.delete(doc)
        session.commit()
        return {'message': f'The document named {title} has been deleted.'}
    
# Register user
@app.post('/register')
async def register_user(username: str, password: str):
    with Session(engine) as session:
        if session.get(WikiUser, username):
            raise HTTPException(status_code=400, detail='Username already exists.')
        
        user = WikiUser(username=username, password=password, permission='login_user')
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