def test_logout_revokes_only_presented_token_and_survives_restart(client, auth_headers):
    from tests.conftest import reload_app
    from fastapi.testclient import TestClient
    first, username = auth_headers('logout123')
    second = client.post('/login', json={'username': username, 'password': 'Password1'}).json()['token']
    assert first['auth'] != second
    assert client.post('/logout', headers={'Authorization': 'Bearer ' + first['auth']}).status_code == 200
    assert client.get('/permissions', headers=first).status_code == 401
    assert client.get('/permissions', headers={'auth': second}).status_code == 200
    with TestClient(reload_app().app) as restarted:
        assert restarted.get('/permissions', headers=first).status_code == 401
        assert restarted.get('/permissions', headers={'auth': second}).status_code == 200
    assert client.post('/logout').status_code == 401


def test_category_creation_and_tag_creation_are_scoped(client, auth_headers, club_headers, admin_headers):
    regular, _ = auth_headers('regularscope')
    club, _ = club_headers('clubscope')
    admin, _ = admin_headers
    for headers in (regular, club):
        assert client.post('/categories', json={'name': 'Blocked'}, headers=headers).status_code == 403
    for headers in ({}, regular, club, admin):
        assert client.post('/tags', json={'name': 'Detached'}, headers=headers).status_code == 405
    assert client.post('/categories', json={'name': 'General'}, headers=admin).status_code == 200
    data = {'title': 'Scoped', 'content': 'body', 'category': {'name': 'General'}, 'tags': [{'name': 'Inline'}]}
    assert client.post('/documents', json=data, headers=regular).status_code == 403
    assert client.get('/tags').json() == []
    assert client.post('/documents', json=data, headers=club).status_code == 200
    assert any(tag['name'] == 'Inline' for tag in client.get('/tags').json())


def test_reparent_inherits_all_ancestors_and_admin_bypasses_legacy_acl(client, club_headers, admin_headers):
    from sgcc_wiki_backend.core.database import engine
    club, _ = club_headers('inherit123')
    admin, _ = admin_headers
    for name, parent in [('Locked', None), ('Root', None), ('Child', 'Root'), ('Leaf', 'Child')]:
        assert client.post('/categories', json={'name': name, 'parent': parent}, headers=admin).status_code == 200
    client.put('/categories/Locked', json={'write_permission': 'admin'}, headers=admin)
    body = {'title': 'Old', 'content': 'body', 'category': {'name': 'Leaf'}, 'tags': []}
    assert client.post('/documents', json=body, headers=club).status_code == 200
    with engine.begin() as connection:
        connection.exec_driver_sql('DELETE FROM permissions')
    assert client.put('/documents/Old', json={'content': 'category only'}, headers=club).status_code == 403
    assert client.put('/categories/Root', json={'parent': 'Locked'}, headers=admin).status_code == 200
    assert client.get('/categories/Leaf', headers=club).json()['effective_write_permission'] == 'admin'
    caps = client.get('/documents/by-title/permissions', params={'title': 'Old'}, headers=club).json()
    assert not caps['document_update'] and not caps['document_rename']
    assert client.put('/documents/Old/move', json={'new_title': 'Denied'}, headers=club).status_code == 403
    caps = client.get('/documents/by-title/permissions', params={'title': 'Old'}, headers=admin).json()
    assert all(caps.values())
    assert client.put('/documents/Old/move', json={'new_title': 'Renamed'}, headers=admin).status_code == 200
    assert client.get('/documents/Renamed/versions').status_code == 200
    assert client.put('/categories/Root', json={'parent': None}, headers=admin).status_code == 200
    assert client.get('/categories/Leaf', headers=club).json()['can_write'] is True
