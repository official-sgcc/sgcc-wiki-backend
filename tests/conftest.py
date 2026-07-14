import os
import tempfile
import pytest


@pytest.fixture
def client(monkeypatch):
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(db_fd)

    monkeypatch.setenv('DB_PATH', db_path)
    monkeypatch.setenv('ADMIN_USERNAME', '')
    monkeypatch.setenv('ADMIN_PASSWORD', '')
    monkeypatch.setenv('JWT_SECRET_KEY', 'testsecretkey')

    import importlib
    import main
    importlib.reload(main)

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
    from login_utils import hash_password
    from main import engine, WikiUser
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
