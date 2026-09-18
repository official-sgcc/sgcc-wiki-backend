def test_author_delete_survives_demotion_and_restrictions(client, club_headers, auth_headers, admin_headers):
    from core.database import engine
    owner, username = club_headers('deleteowner')
    other, _ = auth_headers('deleteother')
    admin, _ = admin_headers
    client.post('/categories', json={'name': 'Locked'}, headers=admin)
    body = {'title': 'Own', 'content': 'body', 'category': {'name': 'Locked'}, 'tags': []}
    assert client.post('/documents', json=body, headers=owner).status_code == 200
    assert client.put('/categories/Locked', json={'write_permission': 'admin'}, headers=admin).status_code == 200
    assert client.put(f'/admin/users/{username}/permission', json={'permission': 'login_user'}, headers=admin).status_code == 200
    with engine.begin() as connection:
        connection.exec_driver_sql('DELETE FROM permissions WHERE wiki_doc_title = ?', ('Own',))
    for headers in ({}, other):
        caps = client.get('/documents/by-title/permissions', params={'title': 'Own'}, headers=headers).json()
        assert caps['document_delete'] is False
        assert client.delete('/documents/by-title', params={'title': 'Own'}, headers=headers).status_code == 403
    caps = client.get('/documents/by-title/permissions', params={'title': 'Own'}, headers=owner).json()
    assert caps == {'document_update': False, 'document_rename': False, 'document_move': False, 'document_delete': True}
    assert client.delete('/documents/by-title', params={'title': 'Own'}, headers=owner).status_code == 200
    assert client.get('/documents/by-title', params={'title': 'Own'}).status_code == 404
