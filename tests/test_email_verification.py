def test_register_with_server_side_verified_record(client):
    from core.login_utils import create_email_verification_token

    # Send verification email which creates a server-side EmailVerification record
    resp = client.post('/register/verify-email', json={'username': 'alice123', 'email': 'alice@example.com'})
    assert resp.status_code == 200

    # Simulate the user clicking the link on another device by calling /email/verify
    token = create_email_verification_token('alice123', 'alice@example.com')
    assert client.post('/email/verify', json={'token': token}).status_code == 200

    # Now attempt to register without supplying the token (other device already verified)
    resp = client.post('/register', json={
        'username': 'alice123',
        'password': 'Password1',
        'email': 'alice@example.com',
    })
    assert resp.status_code == 200

    # Login should work
    resp = client.post('/login', json={'username': 'alice123', 'password': 'Password1'})
    assert resp.status_code == 200
    assert 'token' in resp.json()


def test_register_verify_status_endpoint(client):
    from core.login_utils import create_email_verification_token

    # Create server-side record
    resp = client.post('/register/verify-email', json={'username': 'bob123', 'email': 'bob@example.com'})
    assert resp.status_code == 200

    # Initially not verified
    resp = client.post('/register/verify-status', json={'username': 'bob123', 'email': 'bob@example.com'})
    assert resp.status_code == 200
    assert resp.json() == {'verified': False}

    # After clicking link (token verification) the status should be true
    token = create_email_verification_token('bob123', 'bob@example.com')
    assert client.post('/email/verify', json={'token': token}).status_code == 200
    resp = client.post('/register/verify-status', json={'username': 'bob123', 'email': 'bob@example.com'})
    assert resp.status_code == 200
    assert resp.json() == {'verified': True}
