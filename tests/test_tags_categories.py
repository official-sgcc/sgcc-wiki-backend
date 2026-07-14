def test_create_tag_requires_auth(client):
    resp = client.post('/tags', json={'name': 'Python'})
    assert resp.status_code == 401


def test_create_and_list_tag(client, auth_headers):
    headers, _ = auth_headers('alice123')
    resp = client.post('/tags', json={'name': 'Python'}, headers=headers)
    assert resp.status_code == 200

    resp = client.get('/tags')
    names = [t['name'] for t in resp.json()]
    assert 'Python' in names


def test_delete_tag_admin_only(client, auth_headers, admin_headers):
    user_headers, _ = auth_headers('alice123')
    client.post('/tags', json={'name': 'Python'}, headers=user_headers)

    resp = client.delete('/tags/Python', headers=user_headers)
    assert resp.status_code == 403

    admin, _ = admin_headers
    resp = client.delete('/tags/Python', headers=admin)
    assert resp.status_code == 200


def test_delete_tag_cascades_from_documents(client, auth_headers, admin_headers):
    user_headers, _ = auth_headers('alice123')
    client.post('/tags', json={'name': 'Python'}, headers=user_headers)
    client.post('/categories', json={'name': 'General'}, headers=user_headers)
    client.post('/documents', json={
        'title': 'Doc1',
        'content': 'hello',
        'category': {'name': 'General'},
        'tags': [{'name': 'Python'}],
    }, headers=user_headers)

    admin, _ = admin_headers
    resp = client.delete('/tags/Python', headers=admin)
    assert resp.status_code == 200

    doc = client.get('/documents/Doc1').json()
    tag_names = [(t.get('name') if isinstance(t, dict) else t) for t in doc['tags']]
    assert 'Python' not in tag_names


def test_get_documents_by_tag(client, auth_headers):
    headers, _ = auth_headers('alice123')
    client.post('/tags', json={'name': 'Python'}, headers=headers)
    client.post('/tags', json={'name': 'Rust'}, headers=headers)
    client.post('/categories', json={'name': 'General'}, headers=headers)
    for title, tags in [('Doc1', ['Python']), ('Doc2', ['Python', 'Rust']), ('Doc3', ['Rust'])]:
        client.post('/documents', json={
            'title': title,
            'content': 'hello',
            'category': {'name': 'General'},
            'tags': [{'name': t} for t in tags],
        }, headers=headers)

    resp = client.get('/tags/Python/documents')
    assert resp.status_code == 200
    titles = sorted(d['title'] for d in resp.json())
    assert titles == ['Doc1', 'Doc2']

    resp = client.get('/tags/Nope/documents')
    assert resp.status_code == 404


def test_create_category_requires_auth(client):
    resp = client.post('/categories', json={'name': 'General'})
    assert resp.status_code == 401


def test_delete_category_in_use_rejected(client, auth_headers, admin_headers):
    user_headers, _ = auth_headers('alice123')
    client.post('/categories', json={'name': 'General'}, headers=user_headers)
    client.post('/documents', json={
        'title': 'Doc1',
        'content': 'hello',
        'category': {'name': 'General'},
        'tags': [],
    }, headers=user_headers)

    admin, _ = admin_headers
    resp = client.delete('/categories/General', headers=admin)
    assert resp.status_code == 409


def test_delete_unused_category_succeeds(client, admin_headers):
    admin, _ = admin_headers
    client.post('/categories', json={'name': 'Orphan'}, headers=admin)
    resp = client.delete('/categories/Orphan', headers=admin)
    assert resp.status_code == 200
