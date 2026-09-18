from sqlmodel import SQLModel, Field
from sqlalchemy.types import JSON
from core.permissions import Role

class Permissions(SQLModel, table=True):
    wiki_doc_title: str = Field(foreign_key='wikidoc.title', primary_key=True)
    update: list[str] = Field(sa_type=JSON)
    move: list[str] = Field(sa_type=JSON)
    rename: list[str] = Field(default_factory=lambda: [Role.CLUB_MEMBER.value], sa_type=JSON)
    delete: list[str] = Field(sa_type=JSON)
