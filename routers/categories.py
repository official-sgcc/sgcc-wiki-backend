"""카테고리 트리 CRUD와 카테고리별 문서 조회 엔드포인트."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlmodel import Session, select
from core.config import logger
from core.database import engine
from core.deps import get_current_user
from schemas.categories import WikiCategory, WikiCategoryCreate, WikiCategoryNode, WikiCategoryUpdate
from schemas.wiki_doc import WikiDoc
from schemas.wiki_user import WikiUser

router = APIRouter()

@router.get('/categories')
async def get_categories():
    """전체 카테고리 목록을 조회한다. (인증 불필요)

    Returns:
        list[WikiCategory]: 등록된 모든 카테고리.
    """
    with Session(engine) as session:
        all_cats = session.exec(select(WikiCategory)).all()
        cat_map = {cat.name: cat for cat in all_cats}
        
        def build_node(cat_name: str) -> WikiCategoryNode:
            cat = cat_map[cat_name]
            children = [build_node(c.name) for c in all_cats if c.parent == cat_name]
            return WikiCategoryNode(name=cat.name, parent=cat.parent, children=children)
        
        root_cats = [cat for cat in all_cats if cat.parent is None]
        return [build_node(cat.name) for cat in root_cats]

@router.post('/categories')
async def create_category(category_in: WikiCategoryCreate, current_user: WikiUser = Depends(get_current_user)):
    """새 카테고리를 생성한다. (로그인 필요)

    Args:
        category_in: 생성할 카테고리(name).
        current_user: 인증 사용자. None이면 401.

    Returns:
        dict: `{'message': '...has been created.'}`

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: 같은 이름의 카테고리가 이미 있을 때.
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required to create a category.')
    with Session(engine) as session:
        if session.get(WikiCategory, category_in.name):
            raise HTTPException(status_code=400, detail='Category name already exists.')
        
        category = WikiCategory(**category_in.model_dump())
        session.add(category)
        session.commit()
        session.refresh(category)
        logger.info('category created: %s by %s', category_in.name, current_user.username)
        return {'message': f'The category named {category_in.name} has been created.'}

@router.get('/categories/{name}')
async def get_category(name: str):
    """이름으로 카테고리 하나를 조회한다. (인증 불필요)

    Args:
        name: 조회할 카테고리 이름(PK).

    Returns:
        WikiCategory: 해당 카테고리.

    Raises:
        HTTPException 404: 해당 카테고리가 없을 때.
    """
    with Session(engine) as session:
        category = session.get(WikiCategory, name)
        if not category:
            raise HTTPException(status_code=404, detail='Cannot find the corresponding category.')
        
        all_cats = session.exec(select(WikiCategory)).all()
        cat_map = {cat.name: cat for cat in all_cats}
        
        def build_node(cat_name: str) -> WikiCategoryNode:
            cat = cat_map[cat_name]
            children = [build_node(c.name) for c in all_cats if c.parent == cat_name]
            return WikiCategoryNode(name=cat.name, parent=cat.parent, children=children)
        
        return build_node(name)

@router.get('/categories/{name}/documents')
async def get_documents_by_category(name: str, recursive: bool = False, limit: int | None = None, offset: int = 0):
    """해당 카테고리에 속한 문서를 조회한다. (인증 불필요)

    문서의 category(JSON)의 name이 대상과 일치하는 문서를 반환한다. 기본은 지정한
    카테고리에 정확히 속한 문서만이며, recursive=true면 하위 카테고리(자식·손자…)에
    속한 문서까지 포함한다. `children`(하위 카테고리)을 주는 GET /categories/{name}과
    달리, 이쪽은 카테고리에 담긴 '문서'를 준다.

    Args:
        name: 대상 카테고리 이름. DB에 존재하지 않으면 404.
        recursive: True면 하위 카테고리 문서까지 포함한다.
        limit: 반환 최대 개수. None이면 제한 없음.
        offset: 건너뛸 개수(limit이 있을 때만 적용).

    Returns:
        list[WikiDoc]: 카테고리에 속한 문서 목록(없으면 빈 리스트).

    Raises:
        HTTPException 404: 해당 카테고리가 없을 때.
    """
    with Session(engine) as session:
        if not session.get(WikiCategory, name):
            raise HTTPException(status_code=404, detail='Cannot find the corresponding category.')

        if recursive:
            all_cats = session.exec(select(WikiCategory)).all()
            def get_all_descendant_names(cat_name: str) -> set:
                descendants = {cat_name}
                for cat in all_cats:
                    if cat.parent == cat_name:
                        descendants.update(get_all_descendant_names(cat.name))
                return descendants
            target_names = get_all_descendant_names(name)
        else:
            target_names = {name}

        # JSON 컬럼의 contains는 직렬화된 문자열 LIKE라 {'name': ...}만으로는 매칭되지 않는다.
        statement = select(WikiDoc).where(func.json_extract(WikiDoc.category, '$.name').in_(target_names))

        if limit is not None:
            statement = statement.offset(offset).limit(limit)
        return session.exec(statement).all()

@router.put('/categories/{name}')
async def update_category(name: str, update_data: WikiCategoryUpdate, current_user: WikiUser = Depends(get_current_user)):
    """기존 카테고리를 수정한다. (admin 전용)

    Args:
        name: 수정할 카테고리 이름.
        update_data: 변경할 필드들.
        current_user: 인증 사용자. admin만 가능.

    Returns:
        dict: `{'message': '...has been updated.'}`

    Raises:
        HTTPException 403: admin이 아니거나 비로그인 상태.
        HTTPException 404: 수정할 카테고리가 없을 때.
        HTTPException 400: 부모가 자기 자신이거나 순환 참조가 발생할 때.
    """
    if current_user is None or current_user.permission != 'admin':
        raise HTTPException(status_code=403, detail='Admin permission required to update categories.')
    with Session(engine) as session:
        def would_create_cycle(session, category_name: str, parent_name: str | None) -> bool:
            if parent_name is None:
                return False
            if parent_name == category_name:
                return True

            current_name = parent_name
            while current_name is not None:
                current_category = session.get(WikiCategory, current_name)
                if current_category is None:
                    return False
                if current_category.name == category_name:
                    return True
                current_name = current_category.parent

            return False

        if not (category := session.get(WikiCategory, name)):
            raise HTTPException(status_code=404, detail='Cannot find the corresponding category.')

        update_data_dict = update_data.model_dump(exclude_unset=True)
        if 'parent' in update_data_dict:
            parent_name = update_data_dict['parent']
            if parent_name == name:
                raise HTTPException(status_code=400, detail='Category cannot be its own parent.')
            if parent_name is not None and not session.get(WikiCategory, parent_name):
                raise HTTPException(status_code=400, detail=f'Parent category \'{parent_name}\' does not exist.')
            if would_create_cycle(session, name, parent_name):
                raise HTTPException(status_code=400, detail='Category cannot create a circular parent reference.')

        for key, value in update_data_dict.items():
            setattr(category, key, value)
        session.commit()
        session.refresh(category)
        return {'message': f'The category named {name} has been updated.'}

# Delete category
@router.delete('/categories/{name}')
async def delete_category(name: str, current_user: WikiUser = Depends(get_current_user)):
    """카테고리를 삭제한다. (admin 전용)

    사용 중인 카테고리는 삭제를 거부한다. 이 카테고리를 참조하는 문서가 하나라도
    있으면 409를 반환하며, 먼저 문서들을 다른 카테고리로 옮겨야 한다.

    Args:
        name: 삭제할 카테고리 이름.
        current_user: 인증 사용자. permission이 'admin'이 아니면 403.

    Returns:
        dict: `{'message': '...has been deleted.'}`

    Raises:
        HTTPException 403: 비로그인이거나 admin이 아닐 때.
        HTTPException 404: 삭제할 카테고리가 없을 때.
        HTTPException 409: 이 카테고리를 사용하는 문서가 남아 있을 때.
    """
    if current_user is None or current_user.permission != 'admin':
        raise HTTPException(status_code=403, detail='Admin permission required to delete categories.')
    with Session(engine) as session:
        if not (category := session.get(WikiCategory, name)):
            raise HTTPException(status_code=404, detail='Cannot find category to delete.')

        # Reject if any document still uses this category or its subcategories
        def get_all_descendant_names(cat_name: str) -> set:
            descendants = {cat_name}
            for cat in session.exec(select(WikiCategory)).all():
                if cat.parent == cat_name:
                    descendants.update(get_all_descendant_names(cat.name))
            return descendants
        
        all_descendants = get_all_descendant_names(name)
        in_use = sum(
            1 for doc in session.exec(select(WikiDoc)).all()
            if (doc.category.get('name') if isinstance(doc.category, dict) else getattr(doc.category, 'name', None)) in all_descendants
        )
        if in_use:
            raise HTTPException(status_code=409, detail=f"Category '{name}' or its subcategories are in use by {in_use} document(s). Move them to another category first.")

        # Recursively delete all subcategories
        def delete_recursive(cat_name: str):
            for child_cat in session.exec(select(WikiCategory).where(WikiCategory.parent == cat_name)).all():
                delete_recursive(child_cat.name)
            session.delete(session.get(WikiCategory, cat_name))
        
        delete_recursive(name)
        session.commit()
        logger.info('category deleted (with subcategories): %s by %s', name, current_user.username)
        return {'message': f'The category named {name} and its subcategories have been deleted.'}
