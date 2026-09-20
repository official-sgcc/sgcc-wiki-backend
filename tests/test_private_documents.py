def private_payload(title='PrivateDoc'):
    return {
        'title': title,
        'content': 'secret',
        'category': {'name': 'General'},
        'tags': [{'name': 'Hidden'}],
        'is_private': True,
    }


def test_private_documents_are_admin_only_across_read_paths(client, auth_headers, club_headers, admin_headers):
    regular, _ = auth_headers('privateuser')
    club, _ = club_headers('privateclub')
    admin, _ = admin_headers
    assert client.post('/categories', json={'name': 'General'}, headers=admin).status_code == 200

    assert client.post('/documents', json=private_payload(), headers=club).status_code == 403
    assert client.post('/documents', json=private_payload(), headers=admin).status_code == 200

    assert client.get('/documents/PrivateDoc').status_code == 404
    assert client.get('/documents/PrivateDoc', headers=regular).status_code == 404
    assert client.get('/documents/PrivateDoc', headers=club).status_code == 404
    assert client.get('/documents/PrivateDoc', headers=admin).status_code == 200
    assert client.get('/documents/by-title', params={'title': 'PrivateDoc'}, headers=admin).status_code == 200

    assert client.get('/documents', headers=club).json() == []
    assert client.get('/documents', headers=admin).json()[0]['title'] == 'PrivateDoc'
    assert client.get('/documents/count', headers=club).json() == {'count': 0}
    assert client.get('/documents/count', headers=admin).json() == {'count': 1}
    assert client.get('/search', params={'keyword': 'PrivateDoc'}, headers=club).json() == []
    assert client.get('/search', params={'keyword': 'PrivateDoc'}, headers=admin).json()[0]['title'] == 'PrivateDoc'
    assert client.get('/categories/General/documents', headers=club).json() == []
    assert client.get('/categories/General/documents', headers=admin).json()[0]['title'] == 'PrivateDoc'
    assert client.get('/tags/Hidden/documents', headers=club).json() == []
    assert client.get('/tags/Hidden/documents', headers=admin).json()[0]['title'] == 'PrivateDoc'

    for path in ('/documents/PrivateDoc/versions', '/documents/PrivateDoc/versions/1'):
        assert client.get(path, headers=club).status_code == 404
        assert client.get(path, headers=admin).status_code == 200

    assert client.put('/documents/PrivateDoc', json={'content': 'changed'}, headers=club).status_code == 403
    assert client.delete('/documents/PrivateDoc', headers=club).status_code == 403
    assert client.put('/documents/PrivateDoc', json={'is_private': False}, headers=club).status_code == 403
    assert client.put('/documents/PrivateDoc', json={'is_private': False}, headers=admin).status_code == 200
    assert client.get('/documents/PrivateDoc', headers=club).status_code == 200
