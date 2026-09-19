"""역할 등급과 행동별 최소 권한. DB/API 문자열은 이전 버전과 호환한다."""

from enum import Enum
from fastapi import HTTPException


class Role(str, Enum):
    def __new__(cls, value, grade, label):
        instance = str.__new__(cls, value)
        instance._value_ = value
        instance.grade = grade
        instance.label = label
        return instance

    ADMIN = ('admin', 100, '관리자')
    CLUB_MEMBER = ('club_member', 50, '동아리 회원')
    LOGIN_USER = ('login_user', 10, '일반 회원')


class Action(str, Enum):
    ADMIN = 'admin'
    DOCUMENT_CREATE = 'document_create'
    DOCUMENT_UPDATE = 'document_update'
    DOCUMENT_RENAME = 'document_rename'
    DOCUMENT_MOVE = 'document_move'
    DOCUMENT_DELETE = 'document_delete'
    CATEGORY_CREATE = 'category_create'


MINIMUM_ROLES = {
    Action.ADMIN: Role.ADMIN,
    Action.DOCUMENT_CREATE: Role.LOGIN_USER,
    Action.DOCUMENT_UPDATE: Role.CLUB_MEMBER,
    Action.DOCUMENT_RENAME: Role.CLUB_MEMBER,
    Action.DOCUMENT_MOVE: Role.ADMIN,
    Action.DOCUMENT_DELETE: Role.ADMIN,
    Action.CATEGORY_CREATE: Role.ADMIN,
}

DOCUMENT_FIELDS = {
    Action.DOCUMENT_UPDATE: 'update',
    Action.DOCUMENT_RENAME: 'rename',
    Action.DOCUMENT_MOVE: 'move',
    Action.DOCUMENT_DELETE: 'delete',
}


def role_names():
    return [role.value for role in Role]


def role_rank(role):
    try:
        return Role(role).grade
    except (ValueError, TypeError):
        return None


def is_admin(user):
    return getattr(user, 'permission', None) == Role.ADMIN.value


def has_minimum_role(user, minimum):
    if is_admin(user):
        return True
    if minimum == Role.ADMIN:
        return is_admin(user)
    rank = role_rank(getattr(user, 'permission', None))
    required = role_rank(minimum)
    return rank is not None and required is not None and (is_admin(user) or rank >= required)


def can_perform(user, action):
    if action in (Action.ADMIN, Action.DOCUMENT_MOVE, Action.DOCUMENT_DELETE) and not is_admin(user):
        return False
    minimum = MINIMUM_ROLES.get(action)
    return minimum is not None and has_minimum_role(user, minimum)


def require_action(user, action, *, anonymous_status=403):
    if not can_perform(user, action):
        raise HTTPException(status_code=anonymous_status if user is None else 403,
            detail=f"Requires '{getattr(action, 'value', action)}' permission.")


def meets_document_roles(user, roles):
    """기존 JSON 목록의 최소 등급을 검사. 관리자는 항상 통과한다."""
    if is_admin(user):
        return True
    if not isinstance(roles, list) or not roles:
        return False
    ranks = [role_rank(role) for role in roles]
    if any(rank is None for rank in ranks):
        return False
    minimum = min(roles, key=role_rank)
    return has_minimum_role(user, minimum)


def category_name(category):
    return category.get('name') if isinstance(category, dict) else getattr(category, 'name', None)


def effective_category_role(category, lookup=None):
    """Apply every ancestor's floor; malformed trees fail closed."""
    if category is None:
        return None
    roles, seen = [], set()
    while category is not None:
        if category.name in seen or role_rank(category.write_permission) is None:
            return None
        seen.add(category.name)
        roles.append(category.write_permission)
        parent = getattr(category, 'parent', None)
        if not parent:
            break
        if lookup is None:
            return None
        category = lookup(parent)
        if category is None:
            return None
    if Role.ADMIN in roles:
        return Role.ADMIN.value
    return max(roles, key=role_rank)


def can_write_category(user, category, lookup=None):
    return has_minimum_role(user, effective_category_role(category, lookup))


def can_read_document(user, document):
    """비공개 문서는 관리자만 읽을 수 있는지 검사한다."""
    return document is not None and (not document.is_private or is_admin(user))


def can_perform_document(user, action, document, permissions, category, lookup=None):
    if action not in DOCUMENT_FIELDS or document is None:
        return False
    if document.is_private and not is_admin(user):
        return False
    if is_admin(user):
        return True
    if action == Action.DOCUMENT_DELETE:
        return has_minimum_role(user, Role.LOGIN_USER) and user.username == document.created_by
    if not can_perform(user, action):
        return False
    field = DOCUMENT_FIELDS[action]
    if not meets_document_roles(user, getattr(permissions, field, None)):
        return False
    if action in (Action.DOCUMENT_RENAME, Action.DOCUMENT_MOVE):
        if not can_perform_document(user, Action.DOCUMENT_UPDATE, document, permissions, category, lookup):
            return False
    return can_write_category(user, category, lookup)



def permission_context(user):
    return {
        'role': getattr(user, 'permission', None),
        'roles': [{'name': role.value, 'grade': role.grade, 'label': role.label} for role in Role],
        'actions': {action.value: can_perform(user, action) for action in Action},
    }
