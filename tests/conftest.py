import os
import sys
import tempfile
import pytest

# 엔진·설정은 임포트 시점에 만들어지므로, 캐시된 채로 두면 임시 DB가 아니라
# 실제 wiki.db를 쓰게 된다. schemas는 지우지 않는다(테이블 재등록 에러 방지).
APP_MODULES = ('main', 'core', 'routers')


def reload_app():
    """현재 환경변수로 앱 모듈 전체를 새로 임포트해 반환한다."""
    for name in list(sys.modules):
        if name in APP_MODULES or name.startswith(('core.', 'routers.')):
            del sys.modules[name]
    import main
    return main


@pytest.fixture
def client(monkeypatch):
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(db_fd)

    monkeypatch.setenv('DB_PATH', db_path)
    monkeypatch.setenv('ADMIN_USERNAME', '')
    monkeypatch.setenv('ADMIN_PASSWORD', '')
    monkeypatch.setenv('JWT_SECRET_KEY', 'testsecretkey')

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
    def _create(username='alice123', password='Password1'):
        client.post('/register', json={'username': username, 'password': password})
        resp = client.post('/login', json={'username': username, 'password': password})
        token = resp.json()['token']
        return {'auth': token}, username
    return _create


@pytest.fixture
def admin_headers(client, monkeypatch):
    from core.login_utils import hash_password
    from core.database import engine
    from schemas.wiki_user import WikiUser
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
