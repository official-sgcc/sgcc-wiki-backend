from sqlmodel import SQLModel, Field
from pydantic import BaseModel

class WikiCategory(SQLModel, table=True):
    name: str = Field(primary_key=True)

class WikiCategoryCreate(BaseModel):
    name: str