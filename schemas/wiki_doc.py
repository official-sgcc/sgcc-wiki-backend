from sqlmodel import SQLModel, Field, Relationship
from pydantic import BaseModel
from datetime import datetime
from sqlalchemy.types import JSON
from .permissions import Permissions

class WikiDoc(SQLModel, table=True):
    title: str = Field(primary_key=True)
    content: str
    tags: list[str] = Field(default_factory=list, sa_type=JSON)
    updated_at: datetime
    permissions: Permissions | None = Relationship()

class WikiDocCreate(BaseModel):
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)

class WikiDocUpdate(BaseModel):
    content: str | None = None
    tags: list[str] | None = None