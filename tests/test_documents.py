def _prep_tag_and_category(client, headers):
    client.post('/tags', json={'name': 'Python'}, headers=headers)
    client.post('/categories', json={'name': 'General'}, headers=headers)


import sqlite3


def test_create_document_requires_auth(client):
    resp = client.post('/documents', json={
        'title': 'Doc1',
        'content': 'hello',
        'category': {'name': 'General'},
        'tags': [],
    })
    assert resp.status_code == 401


def test_create_and_get_document(client, auth_headers):
    headers, _ = auth_headers('alice123')
    _prep_tag_and_category(client, headers)

    resp = client.post('/documents', json={
        'title': 'Doc1',
        'content': 'hello',
        'category': {'name': 'General'},
        'tags': [{'name': 'Python'}],
    }, headers=headers)
    assert resp.status_code == 200

    resp = client.get('/documents/Doc1')
    assert resp.status_code == 200
    assert resp.json()['content'] == 'hello'


def test_move_document_updates_title_and_versions(client, admin_headers):
    headers, _ = admin_headers
    client.post('/categories', json={'name': 'General'}, headers=headers)

    client.post('/documents', json={
        'title': 'Doc1',
        'content': 'hello',
        'category': {'name': 'General'},
        'tags': [],
    }, headers=headers)

    resp = client.put('/documents/Doc1/move', json={'new_title': 'DocRenamed'}, headers=headers)
    assert resp.status_code == 200

    assert client.get('/documents/Doc1').status_code == 404

    moved = client.get('/documents/DocRenamed')
    assert moved.status_code == 200
    assert moved.json()['content'] == 'hello'

    versions = client.get('/documents/DocRenamed/versions').json()
    assert len(versions) == 1


def test_create_document_auto_creates_missing_tag(client, auth_headers):
    headers, _ = auth_headers('alice123')
    client.post('/categories', json={'name': 'General'}, headers=headers)

    resp = client.post('/documents', json={
        'title': 'DocX',
        'content': 'hi',
        'category': {'name': 'General'},
        'tags': [{'name': 'NonExisting'}],
    }, headers=headers)
    assert resp.status_code == 200

    tags = client.get('/tags').json()
    assert any(tag['name'] == 'NonExisting' for tag in tags)


def test_update_document_auto_creates_missing_tag(client, auth_headers):
    headers, _ = auth_headers('alice123')
    client.post('/categories', json={'name': 'General'}, headers=headers)
    client.post('/documents', json={
        'title': 'DocY',
        'content': 'before',
        'category': {'name': 'General'},
        'tags': [],
    }, headers=headers)

    resp = client.put('/documents/DocY', json={
        'content': 'after',
        'tags': [{'name': 'NewTag'}],
    }, headers=headers)
    assert resp.status_code == 200

    tags = client.get('/tags').json()
    assert any(tag['name'] == 'NewTag' for tag in tags)


def test_create_document_rejects_missing_category(client, auth_headers):
    headers, _ = auth_headers('alice123')

    resp = client.post('/documents', json={
        'title': 'DocX',
        'content': 'hi',
        'category': {'name': 'NoSuch'},
        'tags': [],
    }, headers=headers)
    assert resp.status_code == 400


def test_create_document_does_not_create_comment_permission(client, auth_headers):
    headers, _ = auth_headers('alice123')
    client.post('/categories', json={'name': 'General'}, headers=headers)

    resp = client.post('/documents', json={
        'title': 'DocComment',
        'content': 'hello',
        'category': {'name': 'General'},
        'tags': [],
    }, headers=headers)
    assert resp.status_code == 200

    from core.database import engine
    from schemas.permissions import Permissions
    from sqlmodel import Session

    with Session(engine) as session:
        permissions = session.get(Permissions, 'DocComment')

    assert permissions is not None
    assert permissions.update == ['admin', 'club_member', 'login_user']
    assert permissions.move == ['admin']
    assert permissions.delete == ['admin']
    assert not hasattr(permissions, 'comment')


def test_update_document_category_move_requires_admin(client, auth_headers, admin_headers):
    # 카테고리 변경(이동)은 move 권한(기본 admin) 필요. 일반 사용자는 403, admin은 성공.
    headers, _ = auth_headers('alice123')
    _prep_tag_and_category(client, headers)
    client.post('/categories', json={'name': 'Other'}, headers=headers)
    client.post('/documents', json={
        'title': 'Doc1',
        'content': 'v1',
        'category': {'name': 'General'},
        'tags': [],
    }, headers=headers)

    # 같은 카테고리 재지정은 이동이 아니므로 update 권한만으로 통과
    same = client.put('/documents/Doc1', json={'category': {'name': 'General'}}, headers=headers)
    assert same.status_code == 200

    # 다른 카테고리로 변경은 move 권한 필요 → 일반 사용자 403
    resp = client.put('/documents/Doc1', json={'category': {'name': 'Other'}}, headers=headers)
    assert resp.status_code == 403

    admin, _ = admin_headers
    resp = client.put('/documents/Doc1', json={'category': {'name': 'Other'}}, headers=admin)
    assert resp.status_code == 200


def test_update_document_creates_version(client, auth_headers):
    headers, _ = auth_headers('alice123')
    _prep_tag_and_category(client, headers)

    client.post('/documents', json={
        'title': 'Doc1',
        'content': 'v1',
        'category': {'name': 'General'},
        'tags': [],
    }, headers=headers)

    resp = client.put('/documents/Doc1', json={'content': 'v2'}, headers=headers)
    assert resp.status_code == 200

    versions = client.get('/documents/Doc1/versions').json()
    assert len(versions) == 2


def test_delete_document_other_users_forbidden(client, auth_headers, admin_headers):
    alice_headers, _ = auth_headers('alice123')
    _prep_tag_and_category(client, alice_headers)
    client.post('/documents', json={
        'title': 'Doc1',
        'content': 'v1',
        'category': {'name': 'General'},
        'tags': [],
    }, headers=alice_headers)

    bob_headers, _ = auth_headers('bob456')
    resp = client.delete('/documents/Doc1', headers=bob_headers)
    assert resp.status_code == 403

    admin, _ = admin_headers
    resp = client.delete('/documents/Doc1', headers=admin)
    assert resp.status_code == 200


def test_delete_document_creator_can_delete(client, auth_headers):
    headers, _ = auth_headers('alice123')
    _prep_tag_and_category(client, headers)
    client.post('/documents', json={
        'title': 'Doc1',
        'content': 'v1',
        'category': {'name': 'General'},
        'tags': [],
    }, headers=headers)

    resp = client.delete('/documents/Doc1', headers=headers)
    assert resp.status_code == 200


def test_get_document_diff(client, auth_headers):
    headers, _ = auth_headers('alice123')
    _prep_tag_and_category(client, headers)
    client.post('/documents', json={
        'title': 'Doc1',
        'content': 'hello',
        'category': {'name': 'General'},
        'tags': [],
    }, headers=headers)
    client.put('/documents/Doc1', json={'content': 'hello world'}, headers=headers)

    resp = client.get('/documents/Doc1/diff/2')
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_document_diff_rejects_first_version(client, auth_headers):
    headers, _ = auth_headers('alice123')
    _prep_tag_and_category(client, headers)
    client.post('/documents', json={
        'title': 'Doc1',
        'content': 'v1',
        'category': {'name': 'General'},
        'tags': [],
    }, headers=headers)

    resp = client.get('/documents/Doc1/diff/1')
    assert resp.status_code == 400


def test_slash_title_query_parameter_lifecycle(client, auth_headers, admin_headers):
    headers, _ = auth_headers('alice123')
    admin, _ = admin_headers
    _prep_tag_and_category(client, headers)
    title = 'React/Router 사용법'
    renamed_title = 'React/Router 심화'

    created = client.post('/documents', json={
        'title': title,
        'content': 'v1',
        'category': {'name': 'General'},
        'tags': [],
    }, headers=headers)
    assert created.status_code == 200

    detail = client.get('/documents/by-title', params={'title': title})
    assert detail.status_code == 200
    assert detail.json()['title'] == title

    updated = client.put(
        '/documents/by-title',
        params={'title': title},
        json={'content': 'v2'},
        headers=headers,
    )
    assert updated.status_code == 200

    versions = client.get('/documents/by-title/versions', params={'title': title})
    assert versions.status_code == 200
    assert len(versions.json()) == 2

    version = client.get('/documents/by-title/version', params={
        'title': title,
        'version_number': 2,
    })
    assert version.status_code == 200
    assert version.json()['content'] == 'v2'

    diff = client.get('/documents/by-title/diff', params={
        'title': title,
        'version_number': 2,
    })
    assert diff.status_code == 200

    moved = client.put(
        '/documents/by-title/move',
        params={'title': title},
        json={'new_title': renamed_title},
        headers=admin,
    )
    assert moved.status_code == 200
    assert client.get(
        '/documents/by-title',
        params={'title': renamed_title},
    ).status_code == 200

    deleted = client.delete(
        '/documents/by-title',
        params={'title': renamed_title},
        headers=admin,
    )
    assert deleted.status_code == 200


def test_legacy_document_schema_adds_view_count(tmp_path, monkeypatch):
    db_path = tmp_path / 'legacy-documents.db'
    monkeypatch.setenv('DB_PATH', str(db_path))
    monkeypatch.setenv('ADMIN_USERNAME', '')
    monkeypatch.setenv('ADMIN_PASSWORD', '')
    monkeypatch.setenv('JWT_SECRET_KEY', 'test-jwt-secret-key-32-bytes-long')

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            'CREATE TABLE wikidoc ('
            'title VARCHAR PRIMARY KEY, '
            'content VARCHAR NOT NULL, '
            'category JSON NOT NULL, '
            'tags JSON NOT NULL, '
            'created_by VARCHAR, '
            'updated_at DATETIME NOT NULL'
            ')'
        )
        connection.execute(
            "INSERT INTO wikidoc VALUES "
            "('legacy/document', 'content', '{}', '[]', NULL, CURRENT_TIMESTAMP)"
        )
        connection.commit()

    from tests.conftest import reload_app
    reload_app()

    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]: row
            for row in connection.execute('PRAGMA table_info(wikidoc)')
        }
        stored_count = connection.execute(
            'SELECT view_count FROM wikidoc WHERE title = ?',
            ('legacy/document',),
        ).fetchone()[0]

    assert columns['view_count'][3] == 1
    assert stored_count == 0
