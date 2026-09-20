import pytest


def test_admin_cannot_change_own_role_via_api(client, admin_headers):
    headers, username = admin_headers
    response = client.put(f'/admin/users/{username}/permission', json={'permission': 'login_user'}, headers=headers)
    assert response.status_code == 403
    assert client.get('/permissions', headers=headers).json() == {'is_admin': True}


def test_security_state_is_private_and_profiles_cannot_be_changed_by_others(client, auth_headers, admin_headers):
    owner, username = auth_headers('owner123')
    other, _ = auth_headers('other123')
    admin, _ = admin_headers
    secret_fields = {'password', 'totp_secret', 'totp_last_step', 'session_version'}
    for headers in ({}, other, admin):
        profile = client.get(f'/users/{username}', headers=headers).json()
        assert not (secret_fields | {'totp_enabled', 'email', 'email_verified'}) & profile.keys()
    profile = client.get(f'/users/{username}', headers=owner).json()
    assert not secret_fields & profile.keys()
    assert 'email' in profile and 'totp_enabled' in profile
    for headers in (other, admin):
        assert client.put(f'/users/{username}/bio', json={'bio': 'unauthorized'}, headers=headers).status_code == 403
        assert client.put(f'/users/{username}/profile', json={'nickname': 'bad', 'bio': '', 'github_url': ''}, headers=headers).status_code == 403


def test_demotion_takes_effect_with_existing_access_token(client, auth_headers, admin_headers):
    from sgcc_wiki_backend.core.database import engine
    from sgcc_wiki_backend.schemas.wiki_user import WikiUser
    from sqlmodel import Session
    headers, username = auth_headers('secondadmin')
    admin, _ = admin_headers
    with Session(engine) as session:
        user = session.get(WikiUser, username)
        user.permission = 'admin'
        session.add(user)
        session.commit()
    assert client.get('/admin/users', headers=headers).status_code == 200
    assert client.put(f'/admin/users/{username}/permission', json={'permission': 'login_user'}, headers=admin).status_code == 200
    assert client.get('/admin/users', headers=headers).status_code == 403
    assert client.get('/permissions', headers=headers).json() == {'is_admin': False}


def test_verified_registration_is_bound_to_starting_browser(client):
    from tests.test_email_verification import get_verification_token
    identity = {'username': 'signup123', 'email': 'signup@example.com'}
    assert client.post('/register/verify-email', json={**identity, 'registration_secret': 'owner-random-secret'}).status_code == 200
    token = get_verification_token(identity['username'], identity['email'])
    assert client.post('/email/verify', json={'token': token}).status_code == 200
    for secret in (None, 'attacker-secret'):
        data = {**identity, 'registration_secret': secret}
        assert client.post('/register/verify-status', json=data).json()['verified'] is False
        assert client.post('/register', json={**data, 'password': 'Password1'}).status_code == 400
    data = {**identity, 'registration_secret': 'owner-random-secret'}
    assert client.post('/register/verify-status', json=data).json()['verified'] is True
    assert client.post('/register', json={**data, 'password': 'Password1'}).status_code == 200


@pytest.mark.parametrize('endpoint', ['/2fa/enable', '/2fa/disable'])
def test_2fa_settings_limit_code_guesses(client, auth_headers, endpoint):
    headers, _ = auth_headers('ratelimit123')
    for _ in range(5):
        assert client.post(endpoint, json={'code': '000000'}, headers=headers).status_code == 400
    assert client.post(endpoint, json={'code': '000000'}, headers=headers).status_code == 429


def test_password_reset_revokes_old_access_and_pending_mfa_tokens(client, auth_headers):
    import pyotp
    from sqlmodel import Session
    from sgcc_wiki_backend.core.database import engine
    from sgcc_wiki_backend.core.login_utils import create_password_reset_token
    from sgcc_wiki_backend.schemas.wiki_user import WikiUser
    headers, username = auth_headers('resetsecurity')
    secret = client.post('/2fa/setup', headers=headers).json()['secret']
    assert client.post('/2fa/enable', json={'code': pyotp.TOTP(secret).now()}, headers=headers).status_code == 200
    pending = client.post('/login', json={'username': username, 'password': 'Password1'}).json()['mfa_token']
    with Session(engine) as session:
        reset_token = create_password_reset_token(username, session.get(WikiUser, username).password)
    assert client.post('/password-reset/confirm', json={'token': reset_token, 'new_password': 'NewPassword2'}).status_code == 200
    assert client.get('/permissions', headers=headers).status_code == 401
    assert client.post('/login/2fa', json={'mfa_token': pending, 'code': pyotp.TOTP(secret).now()}).status_code == 401
    new_mfa = client.post('/login', json={'username': username, 'password': 'NewPassword2'}).json()['mfa_token']
    response = client.post('/login/2fa', json={'mfa_token': new_mfa, 'code': pyotp.TOTP(secret).now()})
    assert response.status_code == 200
    assert client.get('/permissions', headers={'auth': response.json()['token']}).status_code == 200


def test_session_version_migration_preserves_existing_users_and_is_idempotent(client, auth_headers):
    from sgcc_wiki_backend.core.database import engine, migrate_legacy_schema
    from sgcc_wiki_backend.core.login_utils import JWT_SECRET_KEY, JWT_ALGORITHM
    import jwt
    headers, username = auth_headers('legacy_session')
    # Existing signed access tokens did not contain a session version.
    claims = jwt.decode(headers['auth'], JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    claims.pop('session_version')
    legacy_headers = {'auth': jwt.encode(claims, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)}
    with engine.begin() as connection:
        connection.exec_driver_sql('ALTER TABLE wikiuser DROP COLUMN session_version')
    migrate_legacy_schema()
    assert client.get('/permissions', headers=legacy_headers).status_code == 200
    with engine.begin() as connection:
        connection.exec_driver_sql('UPDATE wikiuser SET session_version = 3 WHERE username = ?', (username,))
    migrate_legacy_schema()
    with engine.connect() as connection:
        assert connection.exec_driver_sql('SELECT session_version FROM wikiuser WHERE username = ?', (username,)).scalar_one() == 3
    assert client.get('/permissions', headers=legacy_headers).status_code == 401


@pytest.mark.parametrize('method,path,body', [
    ('get', '/admin/users', None),
    ('get', '/admin/permissions', None),
    ('put', '/admin/users/someone/permission', {'permission': 'admin'}),
    ('put', '/categories/Any', {'write_permission': 'login_user'}),
    ('delete', '/categories/Any', None),
    ('delete', '/tags/Any', None),
    ('post', '/email/test', {'email': 'test@example.com'}),
])
def test_all_admin_routes_deny_guest_and_regular_member(client, auth_headers, method, path, body):
    headers, _ = auth_headers('ordinary123')
    for credentials in ({}, headers):
        response = client.request(method, path, json=body, headers=credentials)
        assert response.status_code == 403
