from sqlmodel import SQLModel, Field
from pydantic import BaseModel
from sgcc_wiki_backend.core.permissions import Role

WritePermission = Role

class WikiCategory(SQLModel, table=True):
    name: str = Field(primary_key=True)
    parent: str | None = Field(default=None, foreign_key='wikicategory.name')
    write_permission: str = Field(default=Role.CLUB_MEMBER.value)

class WikiCategoryCreate(BaseModel):
    name: str
    parent: str | None = None

class WikiCategoryUpdate(BaseModel):
    parent: str | None = None
    write_permission: WritePermission = Role.CLUB_MEMBER

class WikiCategoryNode(BaseModel):
    name: str
    parent: str | None
    write_permission: str = Role.CLUB_MEMBER.value
    effective_write_permission: str | None = None
    inherited_write_permission: str | None = None
    can_write: bool = False
    children: list['WikiCategoryNode'] = []

WikiCategoryNode.model_rebuild()
