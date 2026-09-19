def test_bootstrap_syncs_password_only_when_changed(client, admin_headers, monkeypatch):
    from sgcc_wiki_backend.core.maintenance import bootstrap_admin
    from sgcc_wiki_backend.core.database import engine
    from sgcc_wiki_backend.core.login_utils import verify_password, create_mfa_token
    from sgcc_wiki_backend.schemas.wiki_user import WikiUser
    from sqlmodel import Session
    headers, username = admin_headers
    monkeypatch.setenv('ADMIN_USERNAME', username)
    monkeypatch.setenv('ADMIN_PASSWORD', 'Password1')
    with Session(engine) as session:
        original_hash = session.get(WikiUser, username).password
    bootstrap_admin()
    with Session(engine) as session:
        user = session.get(WikiUser, username)
        assert user.password == original_hash
        assert user.session_version == 0
    assert client.get('/admin/users', headers=headers).status_code == 200
    pending_mfa = create_mfa_token(username)
    monkeypatch.setenv('ADMIN_PASSWORD', 'ConfiguredPassword2')
    bootstrap_admin()
    with Session(engine) as session:
        user = session.get(WikiUser, username)
        assert verify_password('ConfiguredPassword2', user.password)
        assert user.session_version == 1
        synced_hash = user.password
    assert client.get('/admin/users', headers=headers).status_code == 401
    assert client.post('/login/2fa', json={'mfa_token': pending_mfa, 'code': '000000'}).status_code == 401
    assert client.post('/login', json={'username': username, 'password': 'Password1'}).status_code == 401
    login = client.post('/login', json={'username': username, 'password': 'ConfiguredPassword2'})
    assert login.status_code == 200
    assert client.get('/admin/users', headers={'auth': login.json()['token']}).status_code == 200
    bootstrap_admin()
    with Session(engine) as session:
        user = session.get(WikiUser, username)
        assert user.password == synced_hash
        assert user.session_version == 1


def test_bootstrap_creation_promotion_and_other_accounts(client, auth_headers, monkeypatch):
    from sgcc_wiki_backend.core.maintenance import bootstrap_admin
    from sgcc_wiki_backend.core.database import engine
    from sgcc_wiki_backend.core.login_utils import verify_password
    from sgcc_wiki_backend.schemas.wiki_user import WikiUser
    from sqlmodel import Session
    _, regular = auth_headers('regularbootstrap')
    monkeypatch.setenv('ADMIN_USERNAME', 'configuredadmin')
    monkeypatch.setenv('ADMIN_PASSWORD', 'BootstrapPassword2')
    bootstrap_admin()
    with Session(engine) as session:
        admin = session.get(WikiUser, 'configuredadmin')
        assert admin.permission == 'admin'
        assert verify_password('BootstrapPassword2', admin.password)
        assert admin.session_version == 0
        user = session.get(WikiUser, regular)
        assert user.permission == 'login_user'
        assert verify_password('Password1', user.password)
    monkeypatch.setenv('ADMIN_USERNAME', regular)
    monkeypatch.setenv('ADMIN_PASSWORD', 'Password1')
    bootstrap_admin()
    with Session(engine) as session:
        user = session.get(WikiUser, regular)
        assert user.permission == 'admin'
        assert user.session_version == 0
    monkeypatch.setenv('ADMIN_PASSWORD', '')
    bootstrap_admin()
    with Session(engine) as session:
        assert verify_password('Password1', session.get(WikiUser, regular).password)
