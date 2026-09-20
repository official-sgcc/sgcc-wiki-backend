def payload(title='Restricted'):
    return {'title': title, 'content': 'original', 'category': {'name': 'General'}, 'tags': []}


def test_default_rejects_regular_member_and_admin_controls_setting(client, auth_headers, club_headers, admin_headers):
    regular, _ = auth_headers('regular123')
    club, _ = club_headers('club123')
    admin, _ = admin_headers
    client.post('/categories', json={'name': 'General'}, headers=admin_headers[0])
    assert client.get('/categories/General', headers=admin).json()['write_permission'] == 'club_member'
    assert client.post('/documents', json=payload(), headers=regular).status_code == 403
    assert client.put('/categories/General', json={'write_permission': 'login_user'}, headers=regular).status_code == 403
    assert client.put('/categories/General', json={'write_permission': 'unknown'}, headers=admin).status_code == 422
    assert client.post('/documents', json=payload(), headers=club).status_code == 200
    assert client.put('/documents/Restricted', json={'content': 'blocked'}, headers=regular).status_code == 403
    assert client.put('/documents/by-title', params={'title': 'Restricted'}, json={'content': 'blocked'}, headers=regular).status_code == 403
    assert client.put('/categories/General', json={'write_permission': 'login_user'}, headers=admin).status_code == 200
    assert client.post('/documents', json=payload('Open'), headers=regular).status_code == 200
    assert client.put('/documents/Restricted', json={'content': 'allowed'}, headers=regular).status_code == 403
    assert client.put('/categories/General', json={'write_permission': 'admin'}, headers=admin).status_code == 200
    assert client.post('/documents', json=payload('ClubBlocked'), headers=club).status_code == 403
    assert client.put('/documents/Restricted', json={'content': 'blocked'}, headers=club).status_code == 403
    assert client.put('/documents/Restricted', json={'content': 'admin'}, headers=admin).status_code == 200


def test_checks_database_setting_instead_of_submitted_category(client, auth_headers, club_headers, admin_headers):
    regular, _ = auth_headers('regular123')
    club, _ = club_headers('club123')
    client.post('/categories', json={'name': 'General'}, headers=admin_headers[0])
    body = payload()
    body['category']['write_permission'] = 'login_user'
    assert client.post('/documents', json=body, headers=regular).status_code == 403


def test_legacy_categories_migrate_once(client):
    from sgcc_wiki_backend.core.database import engine, migrate_legacy_schema
    with engine.begin() as connection:
        connection.exec_driver_sql('DROP TABLE wikicategory')
        connection.exec_driver_sql('CREATE TABLE wikicategory (name VARCHAR PRIMARY KEY, parent VARCHAR)')
        connection.exec_driver_sql("INSERT INTO wikicategory VALUES ('Legacy', NULL)")
    migrate_legacy_schema()
    with engine.begin() as connection:
        assert connection.exec_driver_sql('SELECT write_permission FROM wikicategory').scalar_one() == 'club_member'
        connection.exec_driver_sql("UPDATE wikicategory SET write_permission = 'admin'")
    migrate_legacy_schema()
    with engine.begin() as connection:
        assert connection.exec_driver_sql('SELECT write_permission FROM wikicategory').scalar_one() == 'admin'


def test_app_restart_with_existing_documents_and_old_category_schema(client, auth_headers, club_headers, admin_headers):
    from sgcc_wiki_backend.core.database import engine
    from tests.conftest import reload_app
    from fastapi.testclient import TestClient
    regular, _ = auth_headers('legacyregular')
    club, _ = club_headers('legacyclub')
    admin, _ = admin_headers
    assert client.post('/categories', json={'name': 'Parent'}, headers=admin_headers[0]).status_code == 200
    assert client.post('/categories', json={'name': 'General', 'parent': 'Parent'}, headers=admin_headers[0]).status_code == 200
    assert client.post('/documents', json=payload(), headers=club).status_code == 200
    # Reproduce the deployed schema and pre-change JSON inside documents/versions.
    with engine.begin() as connection:
        connection.exec_driver_sql('ALTER TABLE wikicategory DROP COLUMN write_permission')
        connection.exec_driver_sql("UPDATE wikidoc SET category = '{\"name\":\"General\",\"parent\":\"Parent\"}'")
        connection.exec_driver_sql("UPDATE wikidocversion SET category = '{\"name\":\"General\",\"parent\":\"Parent\"}'")
    engine.dispose()
    app = reload_app()
    with TestClient(app.app) as restarted:
        assert restarted.get('/healthz').status_code == 200
        categories = restarted.get('/categories', headers=admin).json()
        assert categories[0]['name'] == 'Parent'
        assert categories[0]['children'][0]['write_permission'] == 'club_member'
        detail = restarted.get('/documents/Restricted')
        assert detail.status_code == 200
        assert detail.json()['content'] == 'original'
        assert detail.json()['created_by'] == 'legacyclub'
        assert len(restarted.get('/documents/Restricted/versions').json()) == 1
        assert restarted.post('/documents', json=payload('Denied'), headers=regular).status_code == 403
        assert restarted.put('/documents/Restricted', json={'content': 'denied'}, headers=regular).status_code == 403
        assert restarted.put('/documents/Restricted', json={'content': 'updated'}, headers=club).status_code == 200
        assert restarted.put('/categories/General', json={'write_permission': 'admin'}, headers=admin).status_code == 200
    app = reload_app()
    with TestClient(app.app) as restarted_again:
        assert restarted_again.get('/categories/General', headers=admin).json()['write_permission'] == 'admin'
        assert restarted_again.get('/documents/Restricted').json()['content'] == 'updated'


def test_parent_restriction_applies_to_existing_children(client, club_headers, admin_headers):
    club, _ = club_headers('club123')
    admin, _ = admin_headers
    client.post('/categories', json={'name': 'Parent'}, headers=admin)
    client.post('/categories', json={'name': 'General', 'parent': 'Parent'}, headers=admin)
    assert client.post('/documents', json=payload(), headers=club).status_code == 200
    client.put('/categories/Parent', json={'write_permission': 'admin'}, headers=admin)
    assert client.put('/documents/Restricted', json={'content': 'blocked'}, headers=club).status_code == 403
    assert client.post('/documents', json=payload('Blocked'), headers=club).status_code == 403
    child = client.get('/categories/General', headers=club).json()
    assert 'effective_write_permission' not in child
    assert child['can_write'] is False
    assert client.get('/permissions', headers=club).json() == {'is_admin': False}
    assert client.put('/categories/General', json={'write_permission': 'club_member'}, headers=admin).status_code == 400
    assert client.put('/documents/Restricted', json={'content': 'admin'}, headers=admin).status_code == 200
    assert client.delete('/documents/Restricted', headers=club).status_code == 200
