"""문서 버전과 별도로 남겨야 하는 제목 변경·삭제 기록."""

from datetime import datetime

from sqlmodel import Field, SQLModel


class DocumentEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    document_title: str = Field(index=True)
    event_type: str
    old_title: str | None = None
    new_title: str | None = None
    updated_by: str = Field(index=True)
    updated_at: datetime
    is_private: bool = False
    deleted: bool = False
