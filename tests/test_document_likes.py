def test_document_likes_follow_document_and_are_idempotent(client, club_headers, admin_headers):
    author, _ = club_headers('likeauthor')
    other, _ = club_headers('likeother')
    admin, _ = admin_headers
    assert client.post('/categories', json={'name': 'General'}, headers=admin).status_code == 200
    assert client.post('/documents', json={
        'title': 'Like/Doc', 'content': 'hello', 'category': {'name': 'General'},
        'tags': [{'name': 'Shared'}],
    }, headers=author).status_code == 200

    path = '/documents/by-title/likes'
    params = {'title': 'Like/Doc'}
    assert client.get(path, params=params).json() == {'count': 0, 'liked': False}
    assert client.post(path, params=params).status_code == 401
    assert client.post(path, params=params, headers=author).json() == {'count': 1, 'liked': True}
    assert client.post(path, params=params, headers=author).json() == {'count': 1, 'liked': True}
    assert client.post(path, params=params, headers=other).json() == {'count': 2, 'liked': True}
    assert client.get(path, params=params).json() == {'count': 2, 'liked': False}
    for list_path in ('/documents', '/search?keyword=Like',
                      '/categories/General/documents', '/tags/Shared/documents'):
        assert client.get(list_path).json()[0]['like_count'] == 2
    assert client.delete(path, params=params, headers=other).json() == {'count': 1, 'liked': False}

    assert client.put('/documents/by-title/move', params=params,
        json={'new_title': 'Renamed'}, headers=author).status_code == 200
    assert client.get(path, params=params).status_code == 404
    assert client.get(path, params={'title': 'Renamed'}, headers=author).json() == {
        'count': 1, 'liked': True,
    }
    assert client.delete('/documents/by-title', params={'title': 'Renamed'}, headers=author).status_code == 200
    assert client.get(path, params={'title': 'Renamed'}).status_code == 404
