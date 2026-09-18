from tests.seed_data import seed_tag

def _seed(client, headers, admin_headers):
    seed_tag('Python')
    seed_tag('PythonDev')
    client.post('/categories', json={'name': 'General'}, headers=admin_headers[0])

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


def test_search_by_title(client, club_headers, admin_headers):
    headers, _ = club_headers('alice123')
    _seed(client, headers, admin_headers)
    resp = client.get('/search?keyword=Apple&search_type=title')
    assert resp.status_code == 200
    titles = [d['title'] for d in resp.json()]
    assert titles == ['Apple']


def test_search_tag_exact_match(client, club_headers, admin_headers):
    headers, _ = club_headers('alice123')
    _seed(client, headers, admin_headers)

    resp = client.get('/search?keyword=Python&search_type=tag')
    assert resp.status_code == 200
    titles = [d['title'] for d in resp.json()]
    assert titles == ['PythonGuide']


def test_pagination_on_documents(client, club_headers, admin_headers):
    headers, _ = club_headers('alice123')
    _seed(client, headers, admin_headers)

    full = client.get('/documents').json()
    assert len(full) == 3

    page1 = client.get('/documents?limit=2&offset=0').json()
    page2 = client.get('/documents?limit=2&offset=2').json()
    assert len(page1) == 2
    assert len(page2) == 1
