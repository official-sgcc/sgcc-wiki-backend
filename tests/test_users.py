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


def test_get_user_info_hides_email_to_others(client, auth_headers):
    headers, username = auth_headers('alice123')
    resp = client.get(f'/users/{username}')
    body = resp.json()
    assert 'password' not in body
    assert 'email' not in body

    resp_self = client.get(f'/users/{username}', headers=headers)
    body_self = resp_self.json()
    assert 'password' not in body_self
    assert 'email' in body_self
