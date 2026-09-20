"""Attach aggregate like counts to document list responses without per-document queries."""

from sqlalchemy import func
from sqlmodel import Session, select

from sgcc_wiki_backend.schemas.document_like import DocumentLike
from sgcc_wiki_backend.schemas.wiki_doc import WikiDoc


def with_like_counts(session: Session, documents: list[WikiDoc]) -> list[dict]:
    if not documents:
        return []

    counts = dict(session.exec(
        select(DocumentLike.document_title, func.count())
        .where(DocumentLike.document_title.in_([document.title for document in documents]))
        .group_by(DocumentLike.document_title)
    ).all())
    return [
        {**document.model_dump(), 'like_count': int(counts.get(document.title, 0))}
        for document in documents
    ]
