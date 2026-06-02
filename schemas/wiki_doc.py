from sqlmodel import SQLModel, Field, Relationship
from pydantic import BaseModel
from datetime import datetime
from sqlalchemy.types import JSON
from .permissions import Permissions
from .tags import WikiTag
from .categories import WikiCategory

class WikiDoc(SQLModel, table=True):
    title: str = Field(primary_key=True)
    content: str
    category: WikiCategory = Field(sa_type=JSON)
    tags: list[WikiTag] = Field(default_factory=list, sa_type=JSON)
    updated_at: datetime
    permissions: Permissions | None = Relationship(
        sa_relationship_kwargs={'cascade': 'all, delete-orphan', 'single_parent': True}
    )
    versions: list['WikiDocVersion'] = Relationship(
        back_populates='wiki_doc',
        sa_relationship_kwargs={'cascade': 'all, delete-orphan'}
    )

class WikiDocCreate(BaseModel):
    title: str
    content: str
    category: WikiCategory
    tags: list[WikiTag] = Field(default_factory=list)

class WikiDocUpdate(BaseModel):
    content: str | None = None
    category: WikiCategory | None = None
    tags: list[WikiTag] | None = None

class WikiDocVersion(SQLModel, table=True):
    wiki_doc_title: str = Field(foreign_key='wikidoc.title', primary_key=True)
    version_number: int = Field(primary_key=True)
    wiki_doc: WikiDoc = Relationship(back_populates='versions')
    content: str
    category: WikiCategory = Field(sa_type=JSON)
    tags: list = Field(default_factory=list, sa_type=JSON)
    updated_at: datetime
    updated_by: str