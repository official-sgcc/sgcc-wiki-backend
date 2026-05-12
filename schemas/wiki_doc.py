from sqlmodel import SQLModel, Field, Relationship
from pydantic import BaseModel
from datetime import datetime
from sqlalchemy.types import JSON
from .permissions import Permissions
from .tags import WikiTag

class WikiDoc(SQLModel, table=True):
    title: str = Field(primary_key=True)
    content: str
    tags: list[WikiTag] = Field(default_factory=list, sa_type=JSON)
    updated_at: datetime
    permissions: Permissions | None = Relationship()
    versions: list['WikiDocVersion'] = Relationship(back_populates='wiki_doc')

class WikiDocCreate(BaseModel):
    title: str
    content: str
    tags: list[WikiTag] = Field(default_factory=list)

class WikiDocUpdate(BaseModel):
    content: str | None = None
    tags: list[WikiTag] | None = None

class WikiDocVersion(SQLModel, table=True):
    wiki_doc_title: str = Field(foreign_key='wikidoc.title', primary_key=True)
    version_number: int = Field(primary_key=True)
    wiki_doc: WikiDoc = Relationship(back_populates='versions')
    content: str
    tags: list = Field(default_factory=list, sa_type=JSON)
    updated_at: datetime
    updated_by: str