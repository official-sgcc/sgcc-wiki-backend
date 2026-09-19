from sqlmodel import SQLModel, Field
from pydantic import BaseModel

class WikiTag(SQLModel, table=True):
    name: str = Field(primary_key=True)

class WikiTagCreate(BaseModel):
    name: str