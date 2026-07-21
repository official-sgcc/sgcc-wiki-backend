"""핸들러가 공유하는 인증 의존성과 권한·입력 검증 헬퍼."""

from fastapi import Header, HTTPException, status
from sqlmodel import Session
from core.database import engine
from core.login_utils import verify_jwt_token
from schemas.categories import WikiCategory
from schemas.permissions import Permissions
from schemas.tags import WikiTag
from schemas.wiki_user import WikiUser

async def get_current_user(
    auth: str | None = Header(None),
    authorization: str | None = Header(None),
):
    """요청 헤더의 JWT를 해석해 현재 로그인 사용자를 반환하는 인증 의존성.

    토큰을 두 헤더에서 찾는다(우선순위대로):
      1. `Authorization: Bearer <token>` — 표준 방식
      2. `auth: <token>` — 구버전 프론트엔드 호환용. 프론트를 깨지 않기 위해 유지.

    토큰이 아예 없으면 예외 대신 None을 반환한다. 따라서 이 의존성을 쓰는 핸들러는
    "비로그인 허용"이 기본이며, 로그인 필수 여부는 각 핸들러에서 `current_user is None`을
    직접 검사해 결정한다.

    Args:
        auth: `auth` 헤더 값(raw 토큰). 없으면 None.
        authorization: `Authorization` 헤더 값(`Bearer ` 접두사 포함). 없으면 None.

    Returns:
        WikiUser | None: 유효한 토큰이면 해당 사용자, 토큰이 없으면 None.

    Raises:
        HTTPException 401: 토큰은 있으나 가리키는 사용자가 DB에 없을 때.
                           (토큰 자체가 잘못된 경우는 verify_jwt_token에서 처리)
    """
    token = None
    if authorization and authorization.lower().startswith('bearer '):
        token = authorization[7:].strip()
    elif auth:
        token = auth

    if not token:
        return None

    username = verify_jwt_token(token)

    with Session(engine) as session:
        user = session.get(WikiUser, username)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='User not found')
        return user

def check_document_permission(session: Session, current_user: WikiUser, title: str, action: str):
    """문서별 권한(Permissions 테이블)으로 특정 동작 수행 가능 여부를 검사한다.

    Permissions는 문서마다 action별 허용 권한 등급 리스트를 JSON으로 갖는다
    (예: update=['admin', 'club_member', 'login_user']). current_user의 권한 등급이
    해당 action의 허용 목록에 들어 있어야 통과한다.

    Args:
        session: 활성 DB 세션.
        current_user: 현재 사용자. None(비로그인)이면 권한 등급을 None으로 취급해 거부된다.
        title: 대상 문서 제목(Permissions PK).
        action: 검사할 동작. 'update' / 'move' / 'delete' / 'comment' 중 하나.

    Raises:
        HTTPException 403: 문서 권한 설정이 없거나, 허용 목록이 비었거나,
                           current_user의 권한 등급이 목록에 없을 때.
    """
    permission = session.get(Permissions, title)
    if not permission:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Document permissions not configured')
    allowed = getattr(permission, action, None)
    current_user_permission = current_user.permission if current_user else None
    if not allowed or current_user_permission not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f'Requires document-specific \'{action}\' permission')

def validate_tags_and_category(session: Session, tags, category, current_user=None, create_missing_tags=False):
    """문서에 지정된 태그·카테고리가 DB에 실제로 존재하는지 검증한다.

    문서 생성/수정 시 존재하지 않는 태그·카테고리 참조를 막기 위한 가드.
    입력은 pydantic 모델(.name 속성) 또는 dict({'name': ...}) 둘 다 허용한다.

    태그가 없을 때의 동작은 create_missing_tags로 갈린다. 로그인 사용자가 문서를
    만들며 create_missing_tags=True를 주면 없는 태그를 그 자리에서 생성한다(세션에
    add만 하고 커밋은 호출자 몫). 그 외에는 400으로 거부한다. 카테고리는 항상 존재를
    요구한다(자동 생성 없음).

    Args:
        session: 활성 DB 세션.
        tags: WikiTag 유사 객체들의 리스트(또는 None).
        category: WikiCategory 유사 객체(또는 None). None이면 카테고리 검사를 건너뛴다.
        current_user: 인증 사용자. None(비로그인)이면 태그 자동 생성을 하지 않는다.
        create_missing_tags: True이고 로그인 상태일 때만 없는 태그를 자동 생성한다.

    Raises:
        HTTPException 400: 카테고리가 없거나, 없는 태그를 자동 생성할 수 없을 때.
    """
    if category is not None:
        cat_name = category.name if hasattr(category, 'name') else category.get('name')
        if not session.get(WikiCategory, cat_name):
            raise HTTPException(status_code=400, detail=f"Category '{cat_name}' does not exist.")

    for tag in tags or []:
        tag_name = tag.name if hasattr(tag, 'name') else tag.get('name')
        if session.get(WikiTag, tag_name):
            continue
        if create_missing_tags and current_user is not None:
            session.add(WikiTag(name=tag_name))
            continue
        raise HTTPException(status_code=400, detail=f"Tag '{tag_name}' does not exist.")
