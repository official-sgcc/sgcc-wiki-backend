from pydantic import BaseModel

class Permissions(BaseModel):
    update: list[str]
    move: list[str]
    delete: list[str]
    comment: list[str]