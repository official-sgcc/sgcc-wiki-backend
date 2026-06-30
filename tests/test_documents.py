def _prep_tag_and_category(client, headers):
    client.post('/tags', json={'name': 'Python'}, headers=headers)
    client.post('/categories', json={'name': 'General'}, headers=headers)


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
