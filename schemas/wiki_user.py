from sqlmodel import Relationship, SQLModel, Field
from .permissions import Permissions

class WikiUser(SQLModel, table=True):
    username: str = Field(primary_key=True)
    password: str
    permission: str