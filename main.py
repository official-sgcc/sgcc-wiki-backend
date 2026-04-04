from fastapi import FastAPI
from sqlmodel import create_engine, Session, select, SQLModel
from schemas.wiki_doc import WikiDoc
from schemas.permissions import Permissions

app = FastAPI()

engine = create_engine("sqlite:///wiki.db")
SQLModel.metadata.create_all(engine)

# Hello World GET
@app.get("/")
async def root():
    return {'message': 'Hello World'}

@app.get('/document/{title}')
async def get_document(title: str):
    pass

@app.get("/documents")
async def get_all_documents():
    with Session(engine) as session:
        documents = session.exec(select(WikiDoc)).all()
        return documents

