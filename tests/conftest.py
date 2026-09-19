import os
import sys
import tempfile
import pytest

# 엔진·설정은 임포트 시점에 만들어지므로, 캐시된 채로 두면 임시 DB가 아니라
# 실제 wiki.db를 쓰게 된다. schemas는 지우지 않는다(테이블 재등록 에러 방지).
APP_MODULES = ('main', 'core', 'routers', 'sgcc_wiki_backend.main', 'sgcc_wiki_backend.core', 'sgcc_wiki_backend.routers')


def reload_app():
    """현재 환경변수로 앱 모듈 전체를 새로 임포트해 반환한다."""
    for name in list(sys.modules):
        if name in APP_MODULES or name.startswith(('core.', 'routers.', 'sgcc_wiki_backend.core.', 'sgcc_wiki_backend.routers.')):
            del sys.modules[name]
    import sgcc_wiki_backend.main as main
    return main


@pytest.fixture
def client(monkeypatch):
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(db_fd)

    monkeypatch.setenv('DB_PATH', db_path)
    monkeypatch.setenv('ADMIN_USERNAME', '')
    monkeypatch.setenv('ADMIN_PASSWORD', '')
    monkeypatch.setenv('JWT_SECRET_KEY', 'test-jwt-secret-key-32-bytes-long')
    monkeypatch.setenv('EMAIL_PROVIDER', 'log')
    monkeypatch.setenv('EMAIL_COOLDOWN_SECONDS', '60')
    monkeypatch.setenv('EMAIL_DAILY_LIMIT', '90')
    monkeypatch.setenv('EMAIL_FROM', 'SGCC Wiki <no-reply@example.com>')

    main = reload_app()

    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c

    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture
def auth_headers(client):
    def _create(username='alice123', password='Password1', email: str | None = None):
        # Default email is derived from username to avoid collisions when the
        # helper is called multiple times within the same test.
        if email is None:
            email = f'{username}@example.com'
        client.post('/register/verify-email', json={'username': username, 'email': email})
        token = __import__('sgcc_wiki_backend.core.login_utils', fromlist=['create_email_verification_token']).create_email_verification_token(username, email)
        client.post('/register', json={'username': username, 'password': password, 'email': email, 'verification_token': token})
        resp = client.post('/login', json={'username': username, 'password': password})
        token = resp.json()['token']
        return {'auth': token}, username
    return _create


@pytest.fixture
def club_headers(auth_headers):
    def create(*args, **kwargs):
        headers, username = auth_headers(*args, **kwargs)
        from sgcc_wiki_backend.core.database import engine
        from sgcc_wiki_backend.schemas.wiki_user import WikiUser
        from sqlmodel import Session
        with Session(engine) as session:
            user = session.get(WikiUser, username)
            user.permission = 'club_member'
            session.add(user)
            session.commit()
        return headers, username
    return create


@pytest.fixture
def admin_headers(client, monkeypatch):
    from sgcc_wiki_backend.core.login_utils import hash_password
    from sgcc_wiki_backend.core.database import engine
    from sgcc_wiki_backend.schemas.wiki_user import WikiUser
    from sqlmodel import Session

    username, password = 'rootadmin', 'Password1'
    with Session(engine) as session:
        user = WikiUser(
            username=username,
            password=hash_password(password),
            permission='admin',
            bio='',
            email=None,
        )
        session.add(user)
        session.commit()

    resp = client.post('/login', json={'username': username, 'password': password})
    return {'auth': resp.json()['token']}, username
