from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class DocumentLike(SQLModel, table=True):
    document_title: str = Field(primary_key=True)
    username: str = Field(primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
