"""문서 CRUD, 버전·diff, 검색 엔드포인트."""

from datetime import datetime, timezone
from diff_match_patch import diff_match_patch
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from core.config import logger
from core.database import engine
from core.deps import check_document_permission, get_current_user, validate_tags_and_category
from schemas.permissions import Permissions
from schemas.wiki_doc import WikiDoc, WikiDocCreate, WikiDocUpdate, WikiDocVersion
from schemas.wiki_user import WikiUser

router = APIRouter()

@router.get('/documents')
async def get_documents(keyword: str | None = None, limit: int | None = None, offset: int = 0):
    """문서 목록을 조회한다. (인증 불필요)

    Args:
        keyword: 있으면 제목 또는 본문에 이 문자열을 포함하는 문서만 반환한다.
                 앞뒤 공백은 제거되며, 비면 전체 조회로 처리된다.
        limit: 반환 최대 개수. None이면 제한 없이 전부 반환한다.
        offset: 건너뛸 개수(페이지네이션). limit이 있을 때만 적용된다.

    Returns:
        list[WikiDoc]: 조건에 맞는 문서 목록.
    """
    keyword = keyword.strip() if keyword else None
    with Session(engine) as session:
        if keyword:
            statement = select(WikiDoc).where(
                WikiDoc.title.contains(keyword) | WikiDoc.content.contains(keyword)
            )
        else:
            statement = select(WikiDoc)
        if limit is not None:
            statement = statement.offset(offset).limit(limit)
        return session.exec(statement).all()

@router.post('/documents')
async def create_document(doc_in: WikiDocCreate, current_user: WikiUser = Depends(get_current_user)):
    """새 위키 문서를 생성한다. (로그인 필요)

    문서 본체와 함께 버전 1(WikiDocVersion)과 기본 문서 권한(Permissions)을 한 트랜잭션에
    생성한다. created_by에는 생성자 username이 기록되며, 이후 작성자 삭제 권한의 근거가 된다.
    기본 권한은 update/comment=admin·club_member·login_user, move/delete=admin.

    Args:
        doc_in: 생성할 문서(title, content, category, tags).
        current_user: 인증 사용자. None이면 401.

    Returns:
        dict: `{'message': '...has been created.'}`

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: 같은 제목의 문서가 이미 있거나, 참조 태그·카테고리가 없을 때.
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required to create a document.')
    with Session(engine) as session:
        if session.get(WikiDoc, doc_in.title):
            raise HTTPException(status_code=400, detail='There is already a document with the same name.')

        validate_tags_and_category(session, doc_in.tags, doc_in.category, current_user=current_user, create_missing_tags=True)

        doc = WikiDoc(**doc_in.model_dump())
        doc.created_by = current_user.username
        doc.updated_at = datetime.now(timezone.utc)

        version = WikiDocVersion(
            wiki_doc=doc,
            wiki_doc_title=doc.title,
            version_number=1,
            content=doc.content,
            category=doc.category,
            tags=[{'name': tag.name} if hasattr(tag, 'name') else tag for tag in doc.tags],
            updated_at=doc.updated_at,
            updated_by=current_user.username
        )
        doc.versions.append(version)

        session.add(doc)
        default_permissions = Permissions(
            wiki_doc_title=doc.title,
            update=['admin', 'club_member', 'login_user'],
            move=['admin'],
            delete=['admin'],
            comment=['admin', 'club_member', 'login_user']
        )
        session.add(default_permissions)
        session.commit()
        session.refresh(doc)
        logger.info('document created: %s by %s', doc.title, current_user.username)
        return {'message': f'The document named {doc.title} has been created.'}

@router.get('/documents/{title}')
async def get_document(title: str):
    """제목으로 문서 하나를 조회한다. (인증 불필요)

    Args:
        title: 조회할 문서 제목(PK).

    Returns:
        WikiDoc: 본문·카테고리·태그·버전 관계를 포함한 문서.

    Raises:
        HTTPException 404: 해당 제목의 문서가 없을 때.
    """
    with Session(engine) as session:
        doc = session.get(WikiDoc, title)
        if not doc:
            raise HTTPException(status_code=404, detail='Cannot find a document with the corresponding name.')
        return doc

@router.put('/documents/{title}')
async def update_document(title: str, update_data: WikiDocUpdate, current_user: WikiUser = Depends(get_current_user)):
    """문서를 수정하고 새 버전을 추가한다. (문서별 `update` 권한 필요)

    content/tags/category 중 None이 아닌 필드만 갱신한다. 매 수정마다 새
    WikiDocVersion(version_number = 기존 버전 수 + 1)을 남긴다.

    동시성: 두 요청이 같은 다음 version_number를 만들면 PK 충돌(IntegrityError)이
    난다. 이 경우 롤백 후 문서를 다시 읽어 최대 3회 재시도하고, 그래도 실패하면 409.

    Args:
        title: 수정할 문서 제목.
        update_data: 부분 수정 페이로드(content/category/tags, 모두 선택).
        current_user: 인증 사용자(권한 검사 및 버전의 updated_by 기록에 사용).

    Returns:
        WikiDoc: 갱신된 문서.

    Raises:
        HTTPException 404: 대상 문서가 없을 때.
        HTTPException 403: 문서별 `update` 권한이 없을 때.
        HTTPException 400: 참조 태그·카테고리가 없을 때.
        HTTPException 409: 동시 수정 충돌로 3회 재시도 후에도 저장 실패.
    """
    with Session(engine) as session:
        if not (doc := session.get(WikiDoc, title)):
            raise HTTPException(status_code=404, detail='Cannot find document to update')

        check_document_permission(session, current_user, title, 'update')

        validate_tags_and_category(session, update_data.tags, update_data.category, current_user=current_user, create_missing_tags=True)

        for _ in range(3):
            if update_data.content is not None:
                doc.content = update_data.content

            if update_data.tags is not None:
                doc.tags = [tag.model_dump() if hasattr(tag, 'model_dump') else tag for tag in update_data.tags]

            if update_data.category is not None:
                doc.category = (update_data.category.model_dump() if hasattr(update_data.category, 'model_dump') else update_data.category)

            doc.updated_at = datetime.now(timezone.utc)

            version = WikiDocVersion(
                wiki_doc=doc,
                wiki_doc_title=doc.title,
                version_number=len(doc.versions) + 1,
                content=doc.content,
                category=doc.category,
                tags=doc.tags,
                updated_at=doc.updated_at,
                updated_by=current_user.username
            )
            doc.versions.append(version)

            try:
                session.add(doc)
                session.commit()
                break
            except IntegrityError:
                session.rollback()
                doc = session.get(WikiDoc, title)
        else:
            logger.warning('document update gave up after retries: %s', title)
            raise HTTPException(status_code=409, detail='Could not save document version due to concurrent updates. Try again.')

        session.refresh(doc)
        logger.info('document updated: %s by %s (version %d)', title, current_user.username, len(doc.versions))
        return doc

@router.delete('/documents/{title}')
async def delete_document(title: str, current_user: WikiUser = Depends(get_current_user)):
    """문서를 삭제한다. (작성자 본인 또는 문서별 `delete` 권한)

    작성자 본인(current_user.username == doc.created_by)이면 권한 검사 없이 삭제할 수
    있다. 이 예외는 삭제에만 적용되며 update/move 등 다른 동작에는 적용되지 않는다.
    작성자가 아니면 문서별 `delete` 권한을 검사한다. 문서 삭제 시 연결된
    버전·권한 레코드도 cascade로 함께 제거된다.

    Args:
        title: 삭제할 문서 제목.
        current_user: 인증 사용자(작성자 판별 및 권한 검사에 사용).

    Returns:
        dict: `{'message': '...has been deleted.'}`

    Raises:
        HTTPException 404: 대상 문서가 없을 때.
        HTTPException 403: 작성자가 아니고 문서별 `delete` 권한도 없을 때.
    """
    with Session(engine) as session:
        if not (doc := session.get(WikiDoc, title)):
            raise HTTPException(status_code=404, detail='Cannot find document to delete')

        is_creator = (
            current_user is not None
            and doc.created_by is not None
            and doc.created_by == current_user.username
        )
        if not is_creator:
            check_document_permission(session, current_user, title, 'delete')

        session.delete(doc)
        session.commit()
        logger.info('document deleted: %s by %s', title, current_user.username if current_user else 'unknown')
        return {'message': f'The document named {title} has been deleted.'}

@router.get('/search')
async def search_documents(keyword: str, search_type: str = 'title', limit: int | None = None, offset: int = 0):
    """문서를 검색한다. (인증 불필요)

    Args:
        keyword: 검색어(필수). strip 후 빈 문자열이면 400.
        search_type: 검색 방식.
            - 'title'(기본): 제목 부분 일치.
            - 'title_content': 제목 또는 본문 부분 일치.
            - 'tag': 태그명 정확 일치(JSON 부분검색으로 후보를 좁힌 뒤 파이썬에서 정확 매칭).
        limit: 반환 최대 개수. None이면 제한 없음.
        offset: 건너뛸 개수(limit이 있을 때만 적용).

    Returns:
        list[WikiDoc]: 검색 결과 문서 목록.

    Raises:
        HTTPException 400: keyword가 비었거나, search_type이 위 세 값이 아닐 때.
    """
    keyword = keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail='Search keyword cannot be empty.')
    with Session(engine) as session:
        if search_type == 'title':
            statement = select(WikiDoc).where(WikiDoc.title.contains(keyword))
        elif search_type == 'title_content':
            statement = select(WikiDoc).where(
                WikiDoc.title.contains(keyword) | WikiDoc.content.contains(keyword)
            )
        elif search_type == 'tag':
            statement = select(WikiDoc).where(WikiDoc.tags.contains(f'"{keyword}"'))
            docs = session.exec(statement).all()
            docs = [
                d for d in docs
                if any((t.get('name') if isinstance(t, dict) else getattr(t, 'name', None)) == keyword for t in (d.tags or []))
            ]
            if limit is not None:
                docs = docs[offset:offset + limit]
            return docs
        else:
            raise HTTPException(status_code=400, detail='Invalid search type.')
        if limit is not None:
            statement = statement.offset(offset).limit(limit)
        return session.exec(statement).all()

@router.get('/documents/{title}/versions')
async def get_document_versions(title: str):
    """문서의 전체 버전 이력을 조회한다. (인증 불필요)

    Args:
        title: 대상 문서 제목.

    Returns:
        list[WikiDocVersion]: 해당 문서의 모든 버전.

    Raises:
        HTTPException 404: 해당 문서가 없을 때.
    """
    with Session(engine) as session:
        doc = session.get(WikiDoc, title)
        if not doc:
            raise HTTPException(status_code=404, detail='Cannot find document with the corresponding name.')
        return doc.versions

@router.get('/documents/{title}/versions/{version_number}')
async def get_document_version(title: str, version_number: int):
    """문서의 특정 버전을 조회한다. (인증 불필요)

    Args:
        title: 대상 문서 제목.
        version_number: 조회할 버전 번호(1부터 시작).

    Returns:
        WikiDocVersion: 해당 (문서, 버전) 스냅샷.

    Raises:
        HTTPException 404: 해당 (문서, 버전) 조합이 없을 때.
    """
    with Session(engine) as session:
        version = session.get(WikiDocVersion, (title, version_number))
        if not version:
            raise HTTPException(status_code=404, detail='Cannot find the corresponding document version.')
        return version

@router.get('/documents/{title}/diff/{version_number}')
async def get_document_update_diff(title: str, version_number: int):
    """지정 버전과 직전 버전(version_number - 1)의 본문 diff를 반환한다. (인증 불필요)

    diff-match-patch로 두 버전의 content를 비교한 뒤 diff_cleanupSemantic으로 사람이
    읽기 좋게 정리한다.

    Args:
        title: 대상 문서 제목.
        version_number: 비교 기준이 되는 최신 쪽 버전(2 이상이어야 함).

    Returns:
        list[tuple[int, str]]: (op, text) 형태의 diff 목록.
            op는 -1(삭제) / 0(유지) / 1(추가).

    Raises:
        HTTPException 400: version_number가 1 이하라 비교할 이전 버전이 없을 때.
        HTTPException 404: 두 버전 중 하나라도 없을 때.
    """
    if version_number <= 1:
        raise HTTPException(status_code=400, detail='No previous version to compare with.')
    with Session(engine) as session:
        original = session.get(WikiDocVersion, (title, version_number - 1))
        updated = session.get(WikiDocVersion, (title, version_number))
        if not original or not updated:
            raise HTTPException(status_code=404, detail='Cannot find the corresponding document versions.')
        dmp = diff_match_patch()
        diffs = dmp.diff_main(original.content, updated.content)
        dmp.diff_cleanupSemantic(diffs)
        return diffs
