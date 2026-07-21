from sqlmodel import SQLModel, Field
from pydantic import BaseModel

class WikiCategory(SQLModel, table=True):
    name: str = Field(primary_key=True)
    parent: str | None = Field(default=None, foreign_key='wikicategory.name')

class WikiCategoryCreate(BaseModel):
    name: str
    parent: str | None = None

class WikiCategoryUpdate(BaseModel):
    name: str | None = None
    parent: str | None = None

class WikiCategoryNode(BaseModel):
    name: str
    parent: str | None
    children: list['WikiCategoryNode'] = []

WikiCategoryNode.model_rebuild()