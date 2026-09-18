from sqlmodel import SQLModel, Field
from pydantic import BaseModel
from typing import Literal

WritePermission = Literal['admin', 'club_member', 'login_user']

class WikiCategory(SQLModel, table=True):
    name: str = Field(primary_key=True)
    parent: str | None = Field(default=None, foreign_key='wikicategory.name')
    write_permission: str = Field(default='club_member')

class WikiCategoryCreate(BaseModel):
    name: str
    parent: str | None = None

class WikiCategoryUpdate(BaseModel):
    parent: str | None = None
    write_permission: WritePermission = 'club_member'

class WikiCategoryNode(BaseModel):
    name: str
    parent: str | None
    write_permission: str = 'club_member'
    children: list['WikiCategoryNode'] = []

WikiCategoryNode.model_rebuild()
