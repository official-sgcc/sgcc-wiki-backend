import pytest


def test_legacy_lists_use_global_floor(client, auth_headers, club_headers, admin_headers):
    from core.database import engine
    regular, _ = auth_headers('aclregular')
    club, _ = club_headers('aclclub')
    admin, _ = admin_headers
    client.post('/categories', json={'name': 'Open'}, headers=admin)
    client.put('/categories/Open', json={'write_permission': 'login_user'}, headers=admin)
    body = {'title': 'Legacy', 'content': 'body', 'category': {'name': 'Open'}, 'tags': []}
    assert client.post('/documents', json=body, headers=club).status_code == 200
    with engine.begin() as connection:
        connection.exec_driver_sql("UPDATE permissions SET \"update\" = '[\"login_user\"]', move = '[\"login_user\"]', \"delete\" = '[\"login_user\"]'")
    regular_caps = client.get('/documents/by-title/permissions', params={'title': 'Legacy'}, headers=regular).json()
    assert not any(regular_caps.values())
    club_caps = client.get('/documents/by-title/permissions', params={'title': 'Legacy'}, headers=club).json()
    assert club_caps['document_update']
    assert not club_caps['document_move'] and club_caps['document_delete']
    assert client.put('/documents/Legacy/move', json={'new_title': 'Renamed'}, headers=club).status_code == 200
    assert all(client.get('/documents/by-title/permissions', params={'title': 'Renamed'}, headers=admin).json().values())
