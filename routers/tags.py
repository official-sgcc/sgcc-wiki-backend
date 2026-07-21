"""태그 CRUD와 태그별 문서 조회 엔드포인트."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlmodel import Session, select
from core.config import logger
from core.database import engine
from core.deps import get_current_user
from schemas.tags import WikiTag, WikiTagCreate
from schemas.wiki_doc import WikiDoc
from schemas.wiki_user import WikiUser

router = APIRouter()

@router.get('/tags')
async def get_tags():
    """전체 태그 목록을 조회한다. (인증 불필요)

    Returns:
        list[WikiTag]: 등록된 모든 태그.
    """
    with Session(engine) as session:
        return session.exec(select(WikiTag)).all()

@router.post('/tags')
async def create_tag(tag_in: WikiTagCreate, current_user: WikiUser = Depends(get_current_user)):
    """새 태그를 생성한다. (로그인 필요)

    Args:
        tag_in: 생성할 태그(name).
        current_user: 인증 사용자. None이면 401.

    Returns:
        dict: `{'message': '...has been created.'}`

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: 같은 이름의 태그가 이미 있을 때.
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required to create a tag.')
    with Session(engine) as session:
        if session.get(WikiTag, tag_in.name):
            raise HTTPException(status_code=400, detail='Tag name already exists.')

        tag = WikiTag(**tag_in.model_dump())
        session.add(tag)
        session.commit()
        session.refresh(tag)
        logger.info('tag created: %s by %s', tag_in.name, current_user.username)
        return {'message': f'The tag named {tag_in.name} has been created.'}

@router.get('/tags/{name}/documents')
async def get_documents_by_tag(name: str, limit: int | None = None, offset: int = 0):
    """해당 태그가 달린 모든 문서를 조회한다. (인증 불필요)

    JSON 부분검색으로 후보를 좁힌 뒤, 태그명이 정확히 일치하는 문서만 남긴다
    (검색의 search_type='tag'와 동일한 매칭 규칙).

    Args:
        name: 대상 태그 이름. DB에 존재하지 않으면 404.
        limit: 반환 최대 개수. None이면 제한 없음.
        offset: 건너뛸 개수(limit이 있을 때만 적용).

    Returns:
        list[WikiDoc]: 태그가 달린 문서 목록(없으면 빈 리스트).

    Raises:
        HTTPException 404: 해당 태그가 없을 때.
    """
    with Session(engine) as session:
        if not session.get(WikiTag, name):
            raise HTTPException(status_code=404, detail='Cannot find the corresponding tag.')

        # tags는 [{'name': ...}] JSON 배열이라 원소 단위로 풀어서 정확 일치를 본다.
        # (contains는 직렬화 문자열 LIKE라 태그가 여러 개면 매칭되지 않는다)
        tag_entries = func.json_each(WikiDoc.tags).table_valued('value', joins_implicitly=True)
        statement = select(WikiDoc).where(
            select(1).select_from(tag_entries).where(func.json_extract(tag_entries.c.value, '$.name') == name).exists()
        )
        if limit is not None:
            statement = statement.offset(offset).limit(limit)
        return session.exec(statement).all()

@router.delete('/tags/{name}')
async def delete_tag(name: str, current_user: WikiUser = Depends(get_current_user)):
    """태그를 삭제한다. (admin 전용)

    삭제와 동시에 이 태그를 참조하는 모든 문서의 tags 목록에서도 해당 태그를 제거해
    끊어진 참조가 남지 않게 한다.

    Args:
        name: 삭제할 태그 이름.
        current_user: 인증 사용자. permission이 'admin'이 아니면 403.

    Returns:
        dict: `{'message': '...has been deleted.'}`

    Raises:
        HTTPException 403: 비로그인이거나 admin이 아닐 때.
        HTTPException 404: 삭제할 태그가 없을 때.
    """
    if current_user is None or current_user.permission != 'admin':
        raise HTTPException(status_code=403, detail='Admin permission required to delete tags.')
    with Session(engine) as session:
        if not (tag := session.get(WikiTag, name)):
            raise HTTPException(status_code=404, detail='Cannot find tag to delete.')

        # Remove this tag from every document that references it
        for doc in session.exec(select(WikiDoc)).all():
            new_tags = [t for t in (doc.tags or []) if (t.get('name') if isinstance(t, dict) else getattr(t, 'name', None)) != name]
            if len(new_tags) != len(doc.tags or []):
                doc.tags = new_tags
                session.add(doc)

        session.delete(tag)
        session.commit()
        logger.info('tag deleted: %s by %s', name, current_user.username)
        return {'message': f'The tag named {name} has been deleted.'}
