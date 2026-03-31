from fastapi import FastAPI

app = FastAPI()

# Hello World GET
@app.get("/")
async def root():
    return {'message': 'Hello World'}