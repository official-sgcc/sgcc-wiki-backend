from sqlmodel import Relationship, SQLModel, Field
from pydantic import BaseModel
from .permissions import Permissions

class UserRegister(BaseModel):
    username: str
    password: str

class WikiUser(SQLModel, table=True):
    username: str = Field(primary_key=True)
    password: str
    permission: str