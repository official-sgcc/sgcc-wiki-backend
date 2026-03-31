from fastapi import FastAPI

app = FastAPI()

@app.get('/')
async def root():
    return {'message': 'Hello World'}

@app.get('/document/{title}')
async def get_document(title: str):
    pass

