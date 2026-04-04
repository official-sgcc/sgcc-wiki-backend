from sqlmodel import SQLModel, Field
from sqlalchemy.types import JSON

class Permissions(SQLModel, table=True):
    wiki_doc_title: str = Field(foreign_key="wikidoc.title", primary_key=True)
    update: list[str] = Field(sa_type=JSON)
    move: list[str] = Field(sa_type=JSON)
    delete: list[str] = Field(sa_type=JSON)
    comment: list[str] = Field(sa_type=JSON)