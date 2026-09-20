from enum import Enum
from types import SimpleNamespace
import pytest


def user(role, username='someone'):
    return SimpleNamespace(permission=role, username=username)


@pytest.mark.parametrize('role,minimum,expected', [
    ('login_user', 'club_member', False),
    ('club_member', 'login_user', True),
    ('admin', 'club_member', True),
    ('club_member', 'admin', False),
    ('admin', 'admin', True),
    ('unknown', 'login_user', False),
    ('admin', 'unknown', True),
    (None, 'login_user', False),
])
def test_role_thresholds(role, minimum, expected):
    from sgcc_wiki_backend.core.permissions import has_minimum_role
    assert has_minimum_role(user(role) if role else None, minimum) is expected


def test_new_enum_roles_inherit_grades_but_not_admin_access(monkeypatch):
    import sgcc_wiki_backend.core.permissions as policy
    original = policy.Role
    expanded = Enum('ExpandedRole', {**{role.name: role.value for role in original},
        'SENIOR_MEMBER': 'senior_member', 'HIGH_GRADE_MEMBER': 'high_grade_member'}, type=str)
    for role in expanded:
        existing = next((item for item in original if item.value == role.value), None)
        role.grade = existing.grade if existing else (75 if role.value == 'senior_member' else 150)
        role.label = existing.label if existing else role.name
    monkeypatch.setattr(policy, 'Role', expanded)
    assert policy.can_perform(user('senior_member'), policy.Action.DOCUMENT_RENAME)
    assert policy.has_minimum_role(user('high_grade_member'), 'club_member')
    assert not policy.can_perform(user('high_grade_member'), policy.Action.ADMIN)
    assert not policy.can_perform(user('high_grade_member'), policy.Action.DOCUMENT_MOVE)
    assert not policy.has_minimum_role(user('high_grade_member'), 'admin')
    assert policy.has_minimum_role(user('admin'), 'high_grade_member')
    assert 'senior_member' in policy.role_names()
    assert any(role['name'] == 'senior_member' for role in policy.permission_context(user('senior_member'))['roles'])


@pytest.mark.parametrize('roles', [[], ['unknown'], ['login_user', 'unknown'], None, 'login_user'])
def test_admin_bypasses_invalid_or_empty_document_settings(roles):
    from sgcc_wiki_backend.core.permissions import meets_document_roles
    assert meets_document_roles(user('admin'), roles)


def seed(client, club_headers, admin_headers):
    club, username = club_headers('clubpolicy')
    admin, _ = admin_headers
    assert client.post('/categories', json={'name': 'General'}, headers=admin).status_code == 200
    assert client.post('/categories', json={'name': 'Other'}, headers=admin).status_code == 200
    assert client.post('/documents', json={'title': 'Old/Title', 'content': 'original',
        'category': {'name': 'General'}, 'tags': []}, headers=club).status_code == 200
    return club, admin, username


def test_document_capabilities_match_enforcement_and_rename_preserves_data(client, club_headers, auth_headers, admin_headers):
    club, admin, username = seed(client, club_headers, admin_headers)
    regular, _ = auth_headers('regularpolicy')
    assert client.get('/permissions', headers=club).json() == {'is_admin': False}
    assert client.get('/categories/General', headers=club).json()['can_write'] is True
    assert client.get('/categories/General', headers=regular).json()['can_write'] is False
    caps = client.get('/documents/by-title/permissions', params={'title': 'Old/Title'}, headers=club).json()
    assert caps == {'document_update': True, 'document_rename': True, 'document_move': False, 'document_delete': True}
    assert not any(client.get('/documents/by-title/permissions', params={'title': 'Old/Title'}, headers=regular).json().values())
    assert client.put('/documents/by-title/move', params={'title': 'Old/Title'}, json={'new_title': 'Denied'}, headers=regular).status_code == 403
    assert client.put('/documents/by-title', params={'title': 'Old/Title'}, json={'category': {'name': 'Other'}}, headers=club).status_code == 403
    assert client.put('/documents/by-title/move', params={'title': 'Old/Title'}, json={'new_title': 'New/Title'}, headers=club).status_code == 200
    detail = client.get('/documents/by-title', params={'title': 'New/Title'}).json()
    assert detail['content'] == 'original' and detail['created_by'] == username
    versions = client.get('/documents/by-title/versions', params={'title': 'New/Title'}).json()
    assert len(versions) == 1
    assert client.put('/documents/by-title', params={'title': 'New/Title'}, json={'category': {'name': 'Other'}}, headers=admin).status_code == 200
    assert client.get('/admin/users', headers=club).status_code == 403
    assert client.put('/categories/General', json={'write_permission': 'login_user'}, headers=club).status_code == 403


def test_reading_capabilities_does_not_increment_views(client, club_headers, admin_headers):
    from sgcc_wiki_backend.core.database import engine
    from sgcc_wiki_backend.schemas.wiki_doc import WikiDoc
    from sqlmodel import Session
    club, _, _ = seed(client, club_headers, admin_headers)
    for _ in range(2):
        assert client.get('/documents/by-title/permissions', params={'title': 'Old/Title'}, headers=club).status_code == 200
    with Session(engine) as session:
        assert session.get(WikiDoc, 'Old/Title').view_count == 0


def test_document_lists_combine_with_category_and_admin_bypass(client, club_headers, admin_headers):
    from sgcc_wiki_backend.core.database import engine
    club, admin, _ = seed(client, club_headers, admin_headers)
    with engine.begin() as connection:
        connection.exec_driver_sql("UPDATE permissions SET \"update\" = '[]', rename = '[]', move = '[]'")
    for headers in (club, admin):
        caps = client.get('/documents/by-title/permissions', params={'title': 'Old/Title'}, headers=headers).json()
        assert caps['document_update'] is (headers == admin)
        assert caps['document_rename'] is (headers == admin)
    assert client.put('/documents/by-title', params={'title': 'Old/Title'}, json={'content': 'allowed'}, headers=club).status_code == 403
    assert client.put('/documents/by-title', params={'title': 'Old/Title'}, json={'category': {'name': 'Other'}}, headers=admin).status_code == 200


def test_category_restriction_blocks_rename_and_missing_auth_denies(client, club_headers, admin_headers):
    club, admin, _ = seed(client, club_headers, admin_headers)
    assert client.put('/categories/General', json={'write_permission': 'admin'}, headers=admin).status_code == 200
    assert client.get('/categories/General', headers=club).json()['can_write'] is False
    for headers in (club, {}):
        caps = client.get('/documents/by-title/permissions', params={'title': 'Old/Title'}, headers=headers).json()
        assert caps['document_rename'] is False
        assert client.put('/documents/by-title/move', params={'title': 'Old/Title'}, json={'new_title': 'Denied'}, headers=headers).status_code == 403


def test_legacy_permissions_migrate_on_app_restart_and_retain_custom_settings(client, club_headers, admin_headers):
    from sgcc_wiki_backend.core.database import engine
    from tests.conftest import reload_app
    from fastapi.testclient import TestClient
    club, admin, _ = seed(client, club_headers, admin_headers)
    with engine.begin() as connection:
        connection.exec_driver_sql("UPDATE permissions SET \"update\" = '[\"admin\"]'")
        connection.exec_driver_sql('ALTER TABLE permissions DROP COLUMN rename')
    engine.dispose()
    app = reload_app()
    with TestClient(app.app) as restarted:
        caps = restarted.get('/documents/by-title/permissions', params={'title': 'Old/Title'}, headers=club).json()
        assert caps['document_rename'] is False and caps['document_update'] is False
        assert caps['document_move'] is False
        assert restarted.put('/documents/by-title', params={'title': 'Old/Title'}, json={'content': 'category policy'}, headers=club).status_code == 403
        assert restarted.put('/documents/by-title/move', params={'title': 'Old/Title'}, json={'new_title': 'Renamed'}, headers=admin).status_code == 200
        from sgcc_wiki_backend.core.database import engine, migrate_legacy_schema
        from sgcc_wiki_backend.schemas.permissions import Permissions
        from sqlmodel import Session
        with Session(engine) as session:
            permissions = session.get(Permissions, 'Renamed')
            assert permissions.update == ['admin'] and permissions.move == ['admin']
            assert permissions.rename == ['club_member']
            permissions.rename = ['admin']
            permissions.update = ['login_user']
            session.add(permissions)
            session.commit()
        migrate_legacy_schema()
        assert restarted.get('/documents/by-title/permissions', params={'title': 'Renamed'}, headers=club).json()['document_rename'] is False
        assert restarted.get('/documents/by-title/permissions', params={'title': 'Renamed'}, headers=admin).json()['document_rename'] is True
