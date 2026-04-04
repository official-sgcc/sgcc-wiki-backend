from sqlmodel import SQLModel, Field, Relationship
from datetime import datetime
from sqlalchemy.types import JSON
from .permissions import Permissions

class WikiDoc(SQLModel, table=True):
    title: str = Field(primary_key=True)
    content: str
    tags: list[str] = Field(sa_type=JSON)
    updated_at: datetime
    permissions: Permissions | None = Relationship()