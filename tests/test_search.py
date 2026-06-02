def _seed(client, headers):
    client.post('/tags', json={'name': 'Python'}, headers=headers)
    client.post('/tags', json={'name': 'PythonDev'}, headers=headers)
    client.post('/categories', json={'name': 'General'}, headers=headers)

    for i, title in enumerate(['Apple', 'Banana', 'PythonGuide']):
        client.post('/documents', json={
            'title': title,
            'content': f'body {i}',
            'category': {'name': 'General'},
            'tags': [{'name': 'Python'}] if title == 'PythonGuide' else [{'name': 'PythonDev'}],
        }, headers=headers)


def test_search_empty_keyword_rejected(client):
    resp = client.get('/search?keyword=&search_type=title')
    assert resp.status_code == 400


def test_search_by_title(client, auth_headers):
    headers, _ = auth_headers('alice123')
    _seed(client, headers)
    resp = client.get('/search?keyword=Apple&search_type=title')
    assert resp.status_code == 200
    titles = [d['title'] for d in resp.json()]
    assert titles == ['Apple']


def test_search_tag_exact_match(client, auth_headers):
    headers, _ = auth_headers('alice123')
    _seed(client, headers)

    resp = client.get('/search?keyword=Python&search_type=tag')
    assert resp.status_code == 200
    titles = [d['title'] for d in resp.json()]
    assert titles == ['PythonGuide']


def test_pagination_on_documents(client, auth_headers):
    headers, _ = auth_headers('alice123')
    _seed(client, headers)

    full = client.get('/documents').json()
    assert len(full) == 3

    page1 = client.get('/documents?limit=2&offset=0').json()
    page2 = client.get('/documents?limit=2&offset=2').json()
    assert len(page1) == 2
    assert len(page2) == 1
