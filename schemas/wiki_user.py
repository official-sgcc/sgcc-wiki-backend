from sqlmodel import Relationship, SQLModel, Field
from pydantic import BaseModel
from .permissions import Permissions

class UserIdAndPassword(BaseModel):
    username: str
    password: str

class WikiUser(SQLModel, table=True):
    username: str = Field(primary_key=True)
    password: str
    permission: str
    bio: str
    email: str