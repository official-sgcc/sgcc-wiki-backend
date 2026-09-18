def payload(title='Restricted'):
    return {'title': title, 'content': 'original', 'category': {'name': 'General'}, 'tags': []}


def test_default_rejects_regular_member_and_admin_controls_setting(client, auth_headers, club_headers, admin_headers):
    regular, _ = auth_headers('regular123')
    club, _ = club_headers('club123')
    admin, _ = admin_headers
    client.post('/categories', json={'name': 'General'}, headers=club)
    assert client.get('/categories/General').json()['write_permission'] == 'club_member'
    assert client.post('/documents', json=payload(), headers=regular).status_code == 403
    assert client.put('/categories/General', json={'write_permission': 'login_user'}, headers=regular).status_code == 403
    assert client.put('/categories/General', json={'write_permission': 'unknown'}, headers=admin).status_code == 422
    assert client.post('/documents', json=payload(), headers=club).status_code == 200
    assert client.put('/documents/Restricted', json={'content': 'blocked'}, headers=regular).status_code == 403
    assert client.put('/documents/by-title', params={'title': 'Restricted'}, json={'content': 'blocked'}, headers=regular).status_code == 403
    assert client.put('/categories/General', json={'write_permission': 'login_user'}, headers=admin).status_code == 200
    assert client.post('/documents', json=payload('Open'), headers=regular).status_code == 200
    assert client.put('/documents/Restricted', json={'content': 'allowed'}, headers=regular).status_code == 200
    assert client.put('/categories/General', json={'write_permission': 'admin'}, headers=admin).status_code == 200
    assert client.post('/documents', json=payload('ClubBlocked'), headers=club).status_code == 403
    assert client.put('/documents/Restricted', json={'content': 'blocked'}, headers=club).status_code == 403
    assert client.put('/documents/Restricted', json={'content': 'admin'}, headers=admin).status_code == 200


def test_checks_database_setting_instead_of_submitted_category(client, auth_headers, club_headers):
    regular, _ = auth_headers('regular123')
    club, _ = club_headers('club123')
    client.post('/categories', json={'name': 'General'}, headers=club)
    body = payload()
    body['category']['write_permission'] = 'login_user'
    assert client.post('/documents', json=body, headers=regular).status_code == 403


def test_legacy_categories_migrate_once(client):
    from core.database import engine, migrate_legacy_schema
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
    from core.database import engine
    from tests.conftest import reload_app
    from fastapi.testclient import TestClient
    regular, _ = auth_headers('legacyregular')
    club, _ = club_headers('legacyclub')
    admin, _ = admin_headers
    assert client.post('/categories', json={'name': 'Parent'}, headers=admin).status_code == 200
    assert client.post('/categories', json={'name': 'General', 'parent': 'Parent'}, headers=admin).status_code == 200
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
        categories = restarted.get('/categories').json()
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
        assert restarted_again.get('/categories/General').json()['write_permission'] == 'admin'
        assert restarted_again.get('/documents/Restricted').json()['content'] == 'updated'


def test_parent_settings_are_independent_and_document_restrictions_remain(client, club_headers, admin_headers):
    from core.database import engine
    from schemas.permissions import Permissions
    from sqlmodel import Session
    club, _ = club_headers('club123')
    admin, _ = admin_headers
    client.post('/categories', json={'name': 'Parent'}, headers=admin)
    client.post('/categories', json={'name': 'General', 'parent': 'Parent'}, headers=admin)
    client.put('/categories/Parent', json={'write_permission': 'admin'}, headers=admin)
    assert client.post('/documents', json=payload(), headers=club).status_code == 200
    with Session(engine) as session:
        permissions = session.get(Permissions, 'Restricted')
        permissions.update = ['admin']
        session.add(permissions)
        session.commit()
    assert client.put('/documents/Restricted', json={'content': 'blocked'}, headers=club).status_code == 403
    # Moving content into an admin-only destination also requires its write permission.
    with Session(engine) as session:
        permissions = session.get(Permissions, 'Restricted')
        permissions.update = ['admin', 'club_member']
        permissions.move = ['admin', 'club_member']
        session.add(permissions)
        session.commit()
    assert client.put('/documents/Restricted', json={'category': {'name': 'Parent'}}, headers=club).status_code == 403
    client.put('/categories/General', json={'write_permission': 'admin'}, headers=admin)
    assert client.put('/documents/by-title', params={'title': 'Restricted'},
        json={'category': {'name': 'Parent'}}, headers=admin).status_code == 200
