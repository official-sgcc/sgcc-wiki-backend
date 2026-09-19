"""문서 CRUD, 버전·diff, 검색 엔드포인트."""

from datetime import datetime, timezone
from diff_match_patch import diff_match_patch
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from sqlmodel import Session, select
from core.config import logger
from core.database import engine
from core.deps import check_category_write_permission, check_document_permission, check_document_read_permission, get_current_user, validate_tags_and_category
from schemas.permissions import Permissions
from schemas.wiki_doc import WikiDocMove, WikiDoc, WikiDocCreate, WikiDocUpdate, WikiDocVersion
from schemas.wiki_user import WikiUser
from schemas.categories import WikiCategory
from core.permissions import Action, Role, require_action, can_perform_document, category_name, is_admin

router = APIRouter()


@router.get('/documents')
async def get_documents(keyword: str | None = None, limit: int | None = None, offset: int = 0, current_user: WikiUser = Depends(get_current_user)):
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
        if not is_admin(current_user):
            statement = statement.where(WikiDoc.is_private == False)
        if limit is not None:
            statement = statement.offset(offset).limit(limit)
        return session.exec(statement).all()


@router.get('/documents/count')
async def get_documents_count(category: str | None = None, tag: str | None = None, current_user: WikiUser = Depends(get_current_user)):
    """총 문서 개수를 반환한다. (인증 불필요)

    Args:
        category: 특정 카테고리명으로 필터링하여 개수 반환.
        tag: 특정 태그명으로 필터링하여 개수 반환.

    Returns:
        dict: {'count': <int>} 총 문서 개수
    """
    with Session(engine) as session:
        if category is not None or tag is not None:
            statement = select(WikiDoc)
            if not is_admin(current_user):
                statement = statement.where(WikiDoc.is_private == False)
            if category is not None:
                statement = statement.where(func.json_extract(WikiDoc.category, '$.name') == category)
            if tag is not None:
                statement = statement.where(WikiDoc.tags.contains(f'"{tag}"'))
            docs = session.exec(statement).all()
            if tag is not None:
                docs = [
                    d for d in docs
                    if any((t.get('name') if isinstance(t, dict) else getattr(t, 'name', None)) == tag for t in (d.tags or []))
                ]
            return {'count': len(docs)}

        statement = select(func.count(WikiDoc.title))
        if not is_admin(current_user):
            statement = statement.where(WikiDoc.is_private == False)
        total = session.exec(statement).one()
        return {'count': int(total)}

@router.post('/documents')
async def create_document(doc_in: WikiDocCreate, current_user: WikiUser = Depends(get_current_user)):
    """새 위키 문서를 생성한다. (로그인 필요)

    문서 본체와 함께 버전 1(WikiDocVersion)과 기본 문서 권한(Permissions)을 한 트랜잭션에
    생성한다. created_by에는 생성자 username을 기록한다.
    기본 최소 권한은 update=club_member, rename=club_member, move/delete=admin.

    Args:
        doc_in: 생성할 문서(title, content, category, tags).
        current_user: 인증 사용자. None이면 401.

    Returns:
        dict: `{'message': '...has been created.'}`

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: 같은 제목의 문서가 이미 있거나, 참조 태그·카테고리가 없을 때.
    """
    require_action(current_user, Action.DOCUMENT_CREATE, anonymous_status=401)
    if doc_in.is_private and not is_admin(current_user):
        raise HTTPException(status_code=403, detail='Only administrators can create private documents.')
    with Session(engine) as session:
        if session.get(WikiDoc, doc_in.title):
            raise HTTPException(status_code=400, detail='There is already a document with the same name.')
        if not doc_in.title:
            raise HTTPException(status_code=400, detail='Document title cannot be empty.')

        check_category_write_permission(session, current_user, doc_in.category)

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
            update=[Role.CLUB_MEMBER.value],
            move=[Role.ADMIN.value],
            rename=[Role.CLUB_MEMBER.value],
            delete=[Role.ADMIN.value]
        )
        session.add(default_permissions)
        session.commit()
        session.refresh(doc)
        logger.info('document created: %s by %s', doc.title, current_user.username)
        return {'message': f'The document named {doc.title} has been created.'}


# 제목에 '/' 같은 경로 구분자가 포함되어도 안전하게 접근할 수 있도록
# 기존 path parameter API와 동일한 동작을 query parameter API로도 제공한다.
@router.get('/documents/by-title')
async def get_document_by_title(title: str, current_user: WikiUser = Depends(get_current_user)):
    return await get_document(title, current_user)


@router.get('/documents/by-title/edit')
async def get_document_for_edit_by_title(title: str, current_user: WikiUser = Depends(get_current_user)):
    """편집할 문서를 조회한다. 조회수는 올리지 않고 수정 권한을 확인한다."""
    with Session(engine) as session:
        doc = session.get(WikiDoc, title)
        if doc is None:
            raise HTTPException(status_code=404, detail='Cannot find a document with the corresponding name.')
        check_document_read_permission(current_user, doc)
        check_document_permission(session, current_user, title, 'update')
        return doc


@router.put('/documents/by-title')
async def update_document_by_title(
    update_data: WikiDocUpdate,
    title: str,
    current_user: WikiUser = Depends(get_current_user),
):
    return await update_document(title, update_data, current_user)


@router.delete('/documents/by-title')
async def delete_document_by_title(
    title: str,
    current_user: WikiUser = Depends(get_current_user),
):
    return await delete_document(title, current_user)


@router.put('/documents/by-title/move')
async def move_document_by_title(
    move_data: WikiDocMove,
    title: str,
    current_user: WikiUser = Depends(get_current_user),
):
    return await move_document(title, move_data, current_user)


@router.get('/documents/by-title/permissions')
async def get_document_permissions(title: str, current_user: WikiUser = Depends(get_current_user)):
    with Session(engine) as session:
        document = session.get(WikiDoc, title)
        if document is None:
            raise HTTPException(status_code=404, detail='Cannot find document')
        check_document_read_permission(current_user, document)
        permissions = session.get(Permissions, title)
        category = session.get(WikiCategory, category_name(document.category))
        return {action.value: can_perform_document(current_user, action, document, permissions, category, lambda name: session.get(WikiCategory, name))
                for action in (Action.DOCUMENT_UPDATE, Action.DOCUMENT_RENAME, Action.DOCUMENT_MOVE, Action.DOCUMENT_DELETE)}


@router.get('/documents/by-title/versions')
async def get_document_versions_by_title(title: str, current_user: WikiUser = Depends(get_current_user)):
    return await get_document_versions(title, current_user)


@router.get('/documents/by-title/version')
async def get_document_version_by_title(title: str, version_number: int, current_user: WikiUser = Depends(get_current_user)):
    return await get_document_version(title, version_number, current_user)


@router.get('/documents/by-title/diff')
async def get_document_update_diff_by_title(title: str, version_number: int, current_user: WikiUser = Depends(get_current_user)):
    return await get_document_update_diff(title, version_number, current_user)


@router.get('/documents/{title}')
async def get_document(title: str, current_user: WikiUser = Depends(get_current_user)):
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
        check_document_read_permission(current_user, doc)
        doc.view_count = (doc.view_count or 0) + 1
        session.add(doc)
        session.commit()
        session.refresh(doc)
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
        HTTPException 403: 문서별 `update` 권한이 없거나, 카테고리를 바꾸는데 `move` 권한이 없을 때.
        HTTPException 400: 참조 태그·카테고리가 없을 때.
        HTTPException 409: 동시 수정 충돌로 3회 재시도 후에도 저장 실패.
    """
    with Session(engine) as session:
        if not (doc := session.get(WikiDoc, title)):
            raise HTTPException(status_code=404, detail='Cannot find document to update')

        check_document_permission(session, current_user, title, 'update')
        if update_data.is_private is not None and update_data.is_private != doc.is_private and not is_admin(current_user):
            raise HTTPException(status_code=403, detail='Only administrators can change document privacy.')
        check_category_write_permission(session, current_user, doc.category)
        if update_data.category is not None:
            check_category_write_permission(session, current_user, update_data.category)

        # 카테고리를 실제로 바꾸는 것은 이동이므로 admin 기본인 'move' 권한을 요구한다.
        # 같은 카테고리 재지정은 이동이 아니라 그냥 통과시킨다.
        if update_data.category is not None:
            new_name = update_data.category.name if hasattr(update_data.category, 'name') else update_data.category.get('name')
            old_name = doc.category.get('name') if isinstance(doc.category, dict) else getattr(doc.category, 'name', None)
            if new_name != old_name:
                check_document_permission(session, current_user, title, 'move')

        validate_tags_and_category(session, update_data.tags, update_data.category, current_user=current_user, create_missing_tags=True)

        for _ in range(3):
            if update_data.content is not None:
                doc.content = update_data.content

            if update_data.tags is not None:
                doc.tags = [tag.model_dump() if hasattr(tag, 'model_dump') else tag for tag in update_data.tags]

            if update_data.category is not None:
                doc.category = (update_data.category.model_dump() if hasattr(update_data.category, 'model_dump') else update_data.category)

            if update_data.is_private is not None:
                doc.is_private = update_data.is_private

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

@router.put('/documents/{title}/move')
async def move_document(title: str, move_data: WikiDocMove, current_user: WikiUser = Depends(get_current_user)):
    """문서 제목을 변경한다. (동아리 회원 이상 + 문서별 `rename` + 카테고리 작성 권한)

    문서 PK가 제목이므로 이동은 실제로 제목을 새 값으로 바꾸는 rename 처리다.
    WikiDoc.title 변경에 따라 WikiDocVersion.wiki_doc_title도 함께 갱신하고,
    같은 새 제목의 문서가 이미 있으면 400으로 거부한다.

    Args:
        title: 현재 문서 제목.
        move_data: 새 제목.
        current_user: 인증 사용자(권한 검사에 사용).

    Returns:
        dict: `{'message': '...has been moved.'}`

    Raises:
        HTTPException 400: 새 제목이 비었거나, 같은 이름의 문서가 이미 있을 때.
        HTTPException 404: 대상 문서가 없을 때.
        HTTPException 403: 제목 변경 정책 또는 문서별 `rename` 또는 카테고리 작성 권한이 없을 때.
    """
    new_title = move_data.title.strip()
    if not new_title:
        raise HTTPException(status_code=400, detail='New title cannot be empty.')
    if new_title == title:
        raise HTTPException(status_code=400, detail='New title must be different from the current title.')

    with Session(engine) as session:
        if not (doc := session.get(WikiDoc, title)):
            raise HTTPException(status_code=404, detail='Cannot find document to move')

        check_document_permission(session, current_user, title, 'rename')

        if session.get(WikiDoc, new_title):
            raise HTTPException(status_code=400, detail='There is already a document with the same name.')

        moved_doc = WikiDoc(
            title=new_title,
            content=doc.content,
            category=doc.category,
            tags=doc.tags,
            is_private=doc.is_private,
            view_count=(doc.view_count or 0),
            created_by=doc.created_by,
            updated_at=doc.updated_at,
        )
        session.add(moved_doc)
        session.flush()

        for version in list(doc.versions):
            session.add(WikiDocVersion(
                wiki_doc=moved_doc,
                wiki_doc_title=new_title,
                version_number=version.version_number,
                content=version.content,
                category=version.category,
                tags=version.tags,
                updated_at=version.updated_at,
                updated_by=version.updated_by,
            ))

        if doc.permissions is not None:
            session.add(Permissions(
                wiki_doc_title=new_title,
                update=doc.permissions.update,
                move=doc.permissions.move,
                rename=doc.permissions.rename,
                delete=doc.permissions.delete,
            ))

        session.delete(doc)
        session.commit()
        logger.info('document moved: %s -> %s by %s', title, new_title, current_user.username if current_user else 'unknown')
        return {'message': f'The document named {title} has been moved to {new_title}.'}


@router.delete('/documents/{title}')
async def delete_document(title: str, current_user: WikiUser = Depends(get_current_user)):
    """문서를 삭제한다. (관리자 또는 작성자)

    실제 admin은 모든 문서를, 인증된 작성자는 자기 문서를 삭제할 수 있다.
    문서 삭제 시 연결된 버전·권한 레코드도 cascade로 함께 제거된다.

    Args:
        title: 삭제할 문서 제목.
        current_user: 인증 사용자(관리자 권한 검사에 사용).

    Returns:
        dict: `{'message': '...has been deleted.'}`

    Raises:
        HTTPException 404: 대상 문서가 없을 때.
        HTTPException 403: 관리자 또는 해당 문서 작성자가 아닐 때.
    """
    with Session(engine) as session:
        if not (doc := session.get(WikiDoc, title)):
            raise HTTPException(status_code=404, detail='Cannot find document to delete')

        permissions = session.get(Permissions, title)
        category = session.get(WikiCategory, category_name(doc.category))
        if not can_perform_document(current_user, Action.DOCUMENT_DELETE, doc, permissions, category, lambda name: session.get(WikiCategory, name)):
            raise HTTPException(status_code=403, detail='Document delete permission required.')

        session.delete(doc)
        session.commit()
        logger.info('document deleted: %s by %s', title, current_user.username if current_user else 'unknown')
        return {'message': f'The document named {title} has been deleted.'}

@router.get('/search')
async def search_documents(keyword: str, search_type: str = 'title', limit: int | None = None, offset: int = 0, current_user: WikiUser = Depends(get_current_user)):
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
        visibility = [] if is_admin(current_user) else [WikiDoc.is_private == False]
        if search_type == 'title':
            statement = select(WikiDoc).where(WikiDoc.title.contains(keyword), *visibility)
        elif search_type == 'title_content':
            statement = select(WikiDoc).where(
                WikiDoc.title.contains(keyword) | WikiDoc.content.contains(keyword), *visibility
            )
        elif search_type == 'tag':
            statement = select(WikiDoc).where(WikiDoc.tags.contains(f'"{keyword}"'), *visibility)
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
async def get_document_versions(title: str, current_user: WikiUser = Depends(get_current_user)):
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
        check_document_read_permission(current_user, doc)
        return doc.versions

@router.get('/documents/{title}/versions/{version_number}')
async def get_document_version(title: str, version_number: int, current_user: WikiUser = Depends(get_current_user)):
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
        check_document_read_permission(current_user, session.get(WikiDoc, title))
        return version

@router.get('/documents/{title}/diff/{version_number}')
async def get_document_update_diff(title: str, version_number: int, current_user: WikiUser = Depends(get_current_user)):
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
        check_document_read_permission(current_user, session.get(WikiDoc, title))
        dmp = diff_match_patch()
        diffs = dmp.diff_main(original.content, updated.content)
        dmp.diff_cleanupSemantic(diffs)
        return diffs
