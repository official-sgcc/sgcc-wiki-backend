import sqlite3


def test_register_and_login(client):
    resp = client.post('/register', json={'username': 'alice123', 'password': 'Password1'})
    assert resp.status_code == 200

    resp = client.post('/login', json={'username': 'alice123', 'password': 'Password1'})
    assert resp.status_code == 200
    assert 'token' in resp.json()


def test_register_duplicate_username(client):
    client.post('/register', json={'username': 'alice123', 'password': 'Password1'})
    resp = client.post('/register', json={'username': 'alice123', 'password': 'Password1'})
    assert resp.status_code == 400


def test_register_reserved_username(client):
    resp = client.post('/register', json={'username': 'admin', 'password': 'Password1'})
    assert resp.status_code == 400


def test_register_weak_password_too_short(client):
    resp = client.post('/register', json={'username': 'alice123', 'password': 'short1'})
    assert resp.status_code == 400


def test_register_password_missing_digit(client):
    resp = client.post('/register', json={'username': 'alice123', 'password': 'onlyletters'})
    assert resp.status_code == 400


def test_register_invalid_username(client):
    resp = client.post('/register', json={'username': 'ab', 'password': 'Password1'})
    assert resp.status_code == 400


def test_login_unknown_user_returns_same_message_as_wrong_password(client):
    client.post('/register', json={'username': 'alice123', 'password': 'Password1'})

    miss_user = client.post('/login', json={'username': 'nobody', 'password': 'Password1'})
    wrong_pw = client.post('/login', json={'username': 'alice123', 'password': 'WrongPass1'})

    assert miss_user.status_code == 401
    assert wrong_pw.status_code == 401
    assert miss_user.json()['detail'] == wrong_pw.json()['detail']


def test_register_works_with_migrated_legacy_schema(tmp_path, monkeypatch):
    # 과거 email이 NOT NULL이던 구버전 DB를 README의 수동 마이그레이션으로 따라잡은
    # 상태(email nullable + 신규 컬럼 추가)를 재현한다. create_all은 기존 테이블을
    # 건드리지 않으므로, 이 마이그레이션이 선행돼야 신규 가입(email=None)이 된다.
    db_path = tmp_path / 'legacy.db'
    monkeypatch.setenv('DB_PATH', str(db_path))
    monkeypatch.setenv('ADMIN_USERNAME', '')
    monkeypatch.setenv('ADMIN_PASSWORD', '')
    monkeypatch.setenv('JWT_SECRET_KEY', 'testsecretkey')

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            'CREATE TABLE wikiuser ('
            'username TEXT PRIMARY KEY, '
            'password TEXT NOT NULL, '
            'permission TEXT NOT NULL, '
            'bio TEXT NOT NULL, '
            'email TEXT, '
            'email_verified BOOLEAN NOT NULL DEFAULT 0, '
            'totp_secret VARCHAR, '
            'totp_enabled BOOLEAN NOT NULL DEFAULT 0, '
            'totp_last_step INTEGER'
            ')'
        )
        conn.commit()

    from tests.conftest import reload_app
    main = reload_app()

    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        resp = client.post('/register', json={'username': 'alice123', 'password': 'Password1'})
        assert resp.status_code == 200


def test_get_user_info_hides_email_to_others(client, auth_headers):
    headers, username = auth_headers('alice123')
    resp = client.get(f'/users/{username}')
    body = resp.json()
    assert 'password' not in body
    assert 'email' not in body

    resp_self = client.get(f'/users/{username}', headers=headers)
    body_self = resp_self.json()
    assert 'password' not in body_self
    assert 'totp_secret' not in body_self
    assert 'email' in body_self


def test_password_reset_request_is_generic(client):
    client.post('/register', json={'username': 'alice123', 'password': 'Password1'})
    existing = client.post('/password-reset/request', json={'username': 'alice123'})
    missing = client.post('/password-reset/request', json={'username': 'nobody'})
    assert existing.status_code == 200 and missing.status_code == 200
    assert existing.json() == missing.json()


def test_password_reset_full_flow(client):
    from sqlmodel import Session
    from core.database import engine
    from schemas.wiki_user import WikiUser
    from core.login_utils import create_password_reset_token

    client.post('/register', json={'username': 'alice123', 'password': 'Password1'})
    with Session(engine) as session:
        token = create_password_reset_token('alice123', session.get(WikiUser, 'alice123').password)

    resp = client.post('/password-reset/confirm', json={'token': token, 'new_password': 'NewPass123'})
    assert resp.status_code == 200
    assert client.post('/login', json={'username': 'alice123', 'password': 'Password1'}).status_code == 401
    assert client.post('/login', json={'username': 'alice123', 'password': 'NewPass123'}).status_code == 200


def test_password_reset_token_is_single_use(client):
    from sqlmodel import Session
    from core.database import engine
    from schemas.wiki_user import WikiUser
    from core.login_utils import create_password_reset_token

    client.post('/register', json={'username': 'alice123', 'password': 'Password1'})
    with Session(engine) as session:
        token = create_password_reset_token('alice123', session.get(WikiUser, 'alice123').password)

    assert client.post('/password-reset/confirm', json={'token': token, 'new_password': 'NewPass123'}).status_code == 200
    # reuse fails: the hash changed, so the token no longer verifies
    assert client.post('/password-reset/confirm', json={'token': token, 'new_password': 'Another123'}).status_code == 400


def test_2fa_setup_enable_and_two_step_login(client, auth_headers):
    import pyotp

    headers, username = auth_headers('alice123')
    resp = client.post('/2fa/setup', headers=headers)
    assert resp.status_code == 200
    secret = resp.json()['secret']

    resp = client.post('/2fa/enable', json={'code': pyotp.TOTP(secret).now()}, headers=headers)
    assert resp.status_code == 200

    step1 = client.post('/login', json={'username': username, 'password': 'Password1'})
    assert step1.status_code == 200
    body = step1.json()
    assert body.get('mfa_required') is True and 'token' not in body

    step2 = client.post('/login/2fa', json={'mfa_token': body['mfa_token'], 'code': pyotp.TOTP(secret).now()})
    assert step2.status_code == 200
    assert 'token' in step2.json()


def test_2fa_enable_rejects_wrong_code(client, auth_headers):
    headers, _ = auth_headers('alice123')
    client.post('/2fa/setup', headers=headers)
    resp = client.post('/2fa/enable', json={'code': '000000'}, headers=headers)
    assert resp.status_code == 400


def test_set_email_requires_auth(client):
    resp = client.put('/email', json={'email': 'a@b.com'})
    assert resp.status_code == 401


def test_set_email_rejects_invalid_format(client, auth_headers):
    headers, _ = auth_headers('alice123')
    resp = client.put('/email', json={'email': 'not-an-email'}, headers=headers)
    assert resp.status_code == 400


def test_set_email_and_verify_flow(client, auth_headers):
    from core.login_utils import create_email_verification_token

    headers, username = auth_headers('alice123')
    resp = client.put('/email', json={'email': 'alice@example.com'}, headers=headers)
    assert resp.status_code == 200

    me = client.get(f'/users/{username}', headers=headers).json()
    assert me['email'] == 'alice@example.com'
    assert me['email_verified'] is False

    token = create_email_verification_token(username, 'alice@example.com')
    assert client.post('/email/verify', json={'token': token}).status_code == 200

    me = client.get(f'/users/{username}', headers=headers).json()
    assert me['email_verified'] is True


def test_email_uniqueness_conflict(client, auth_headers):
    a_headers, _ = auth_headers('alice123')
    b_headers, _ = auth_headers('bob12345')
    assert client.put('/email', json={'email': 'dup@example.com'}, headers=a_headers).status_code == 200
    assert client.put('/email', json={'email': 'dup@example.com'}, headers=b_headers).status_code == 409


def test_verify_token_stale_after_email_change(client, auth_headers):
    from core.login_utils import create_email_verification_token

    headers, username = auth_headers('alice123')
    client.put('/email', json={'email': 'first@example.com'}, headers=headers)
    stale = create_email_verification_token(username, 'first@example.com')
    client.put('/email', json={'email': 'second@example.com'}, headers=headers)
    # the token's email no longer matches the account's current email
    assert client.post('/email/verify', json={'token': stale}).status_code == 400


def test_password_reset_requires_verified_email(client, auth_headers, monkeypatch):
    import routers.users
    from core.login_utils import create_email_verification_token

    # send_email을 가로채 실제 발송(링크) 여부를 관찰한다. 응답은 항상 200이라
    # 발송 여부는 이 훅으로만 확인할 수 있다.
    sent = []
    monkeypatch.setattr(routers.users, 'send_email', lambda to, subject, body: sent.append(to))

    headers, username = auth_headers('alice123')
    client.put('/email', json={'email': 'alice@example.com'}, headers=headers)  # unverified
    sent.clear()  # PUT /email이 보낸 인증 메일은 관심 밖 — 재설정 발송만 관찰한다.

    # 미인증 이메일: request는 200이지만 재설정 링크는 발송되지 않아야 한다.
    assert client.post('/password-reset/request', json={'username': username}).status_code == 200
    assert sent == []

    # 이메일 인증 후에는 링크가 발송된다.
    token = create_email_verification_token(username, 'alice@example.com')
    client.post('/email/verify', json={'token': token})
    assert client.post('/password-reset/request', json={'username': username}).status_code == 200
    assert sent == ['alice@example.com']


def test_mfa_token_cannot_be_used_as_access_token(client, auth_headers):
    # 회귀 방지: /login이 2FA 1단계에서 주는 mfa_token은 정식 세션 토큰으로 통과하면 안 된다.
    import pyotp

    headers, username = auth_headers('alice123')
    secret = client.post('/2fa/setup', headers=headers).json()['secret']
    client.post('/2fa/enable', json={'code': pyotp.TOTP(secret).now()}, headers=headers)

    body = client.post('/login', json={'username': username, 'password': 'Password1'}).json()
    mfa_token = body['mfa_token']
    # mfa_token으로 인증이 필요한 엔드포인트(문서 생성)를 호출하면 401이어야 한다.
    resp = client.post('/documents', json={'title': 'X', 'content': 'y'}, headers={'auth': mfa_token})
    assert resp.status_code == 401


def test_totp_code_cannot_be_replayed_on_2fa_login(client, auth_headers):
    # 회귀 방지: 같은 TOTP 코드로 두 번 로그인할 수 없어야 한다(single-use).
    import pyotp

    headers, username = auth_headers('alice123')
    secret = client.post('/2fa/setup', headers=headers).json()['secret']
    client.post('/2fa/enable', json={'code': pyotp.TOTP(secret).now()}, headers=headers)
    code = pyotp.TOTP(secret).now()

    body1 = client.post('/login', json={'username': username, 'password': 'Password1'}).json()
    assert client.post('/login/2fa', json={'mfa_token': body1['mfa_token'], 'code': code}).status_code == 200

    body2 = client.post('/login', json={'username': username, 'password': 'Password1'}).json()
    replay = client.post('/login/2fa', json={'mfa_token': body2['mfa_token'], 'code': code})
    assert replay.status_code == 400
