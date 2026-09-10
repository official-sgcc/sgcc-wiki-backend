"""메일 발송 경로(provider·재시도·한도·테스트 엔드포인트) 회귀 테스트.

실제 네트워크는 쓰지 않는다. httpx.post / smtplib.SMTP를 가짜로 바꾸고,
백그라운드 스레드 대신 동기 실행하도록 _run_in_background를 교체한다.
"""

import httpx


class FakeResponse:
    def __init__(self, status_code, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _use_resend(monkeypatch, responses):
    """provider를 resend로 고정하고 httpx.post를 가짜로 바꾼다. 호출 기록을 반환한다."""
    import core.maintenance as maintenance

    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({'url': url, 'headers': headers, 'json': json, 'timeout': timeout})
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(maintenance, 'EMAIL_PROVIDER', 'resend')
    monkeypatch.setattr(maintenance, 'RESEND_API_KEY', 're_test_key')
    monkeypatch.setattr(maintenance, 'EMAIL_FROM', 'no-reply@sgcc.test')
    monkeypatch.setattr(maintenance, 'EMAIL_RETRY_DELAYS', (0,))
    monkeypatch.setattr(maintenance, '_run_in_background', lambda func, *args: func(*args))
    monkeypatch.setattr(maintenance.httpx, 'post', fake_post)
    return calls


def _count_verifications(email):
    from sqlmodel import Session, select
    from core.database import engine
    from schemas.wiki_user import EmailVerification
    with Session(engine) as session:
        return len(session.exec(select(EmailVerification).where(EmailVerification.email == email)).all())


def test_verification_email_goes_through_resend_api(client, monkeypatch):
    calls = _use_resend(monkeypatch, [FakeResponse(200, {'id': 'msg_123'})])

    resp = client.post('/register/verify-email', json={'username': 'alice123', 'email': 'alice@example.com'})
    assert resp.status_code == 200

    assert len(calls) == 1
    call = calls[0]
    assert call['url'] == 'https://api.resend.com/emails'
    assert call['headers']['Authorization'] == 'Bearer re_test_key'
    assert call['headers']['Idempotency-Key'].startswith('sgcc-wiki/')
    assert call['timeout'] == 10
    assert call['json']['from'] == 'no-reply@sgcc.test'
    assert call['json']['to'] == ['alice@example.com']
    assert '/verify-email?token=' in call['json']['text']
    assert _count_verifications('alice@example.com') == 1


def test_transient_failure_is_retried(client, monkeypatch):
    calls = _use_resend(monkeypatch, [
        httpx.ConnectError('connection refused'),
        FakeResponse(200, {'id': 'msg_after_retry'}),
    ])

    resp = client.post('/register/verify-email', json={'username': 'alice123', 'email': 'alice@example.com'})
    assert resp.status_code == 200
    assert len(calls) == 2
    # 재시도에도 같은 Idempotency-Key를 써야 중복 발송이 안 된다.
    assert calls[0]['headers']['Idempotency-Key'] == calls[1]['headers']['Idempotency-Key']


def test_permanent_failure_is_not_retried_and_request_still_succeeds(client, monkeypatch):
    calls = _use_resend(monkeypatch, [FakeResponse(422, text='domain is not verified')])

    # 발송 실패는 로그에만 남고 사용자 응답은 영향받지 않는다.
    resp = client.post('/register/verify-email', json={'username': 'alice123', 'email': 'alice@example.com'})
    assert resp.status_code == 200
    assert len(calls) == 1


def test_same_recipient_is_throttled_by_cooldown(client):
    first = client.post('/register/verify-email', json={'username': 'alice123', 'email': 'alice@example.com'})
    assert first.status_code == 200

    second = client.post('/register/verify-email', json={'username': 'alice123', 'email': 'alice@example.com'})
    assert second.status_code == 429
    # 한도에 걸리면 EmailVerification 레코드도 남기지 않는다.
    assert _count_verifications('alice@example.com') == 1


def test_daily_limit_blocks_further_sends(client, monkeypatch):
    import core.maintenance as maintenance
    monkeypatch.setattr(maintenance, 'EMAIL_DAILY_LIMIT', 1)

    assert client.post('/register/verify-email', json={'username': 'alice123', 'email': 'a@example.com'}).status_code == 200
    assert client.post('/register/verify-email', json={'username': 'bob123', 'email': 'b@example.com'}).status_code == 429


def test_password_reset_request_stays_200_when_throttled(client, auth_headers, monkeypatch):
    import core.maintenance as maintenance

    dispatched = []
    monkeypatch.setattr(maintenance, '_run_in_background', lambda func, *args: dispatched.append(args[1]))

    # auth_headers가 방금 alice123@example.com으로 인증 메일을 보냈으므로 쿨다운 상태다.
    _, username = auth_headers('alice123')
    dispatched.clear()

    resp = client.post('/password-reset/request', json={'username': username})
    assert resp.status_code == 200
    assert resp.json() == {'message': 'If the account exists, a password reset link has been sent.'}
    assert dispatched == []

    # 쿨다운이 풀리면 같은 요청이 실제로 발송된다(응답은 동일).
    monkeypatch.setattr(maintenance, 'EMAIL_COOLDOWN_SECONDS', 0)
    resp = client.post('/password-reset/request', json={'username': username})
    assert resp.status_code == 200
    assert resp.json() == {'message': 'If the account exists, a password reset link has been sent.'}
    assert dispatched == ['alice123@example.com']


def test_email_test_endpoint_is_admin_only(client, auth_headers, admin_headers):
    headers, _ = auth_headers('alice123')
    admin, _ = admin_headers

    assert client.post('/email/test', json={'email': 'x@example.com'}).status_code == 401
    assert client.post('/email/test', json={'email': 'x@example.com'}, headers=headers).status_code == 403

    # log provider: 실제 발송 없이 성공, message_id는 null
    resp = client.post('/email/test', json={'email': 'x@example.com'}, headers=admin)
    assert resp.status_code == 200
    assert resp.json() == {'message': 'Test email sent.', 'provider': 'log', 'message_id': None}


def test_email_test_endpoint_reports_provider_result(client, admin_headers, monkeypatch):
    admin, _ = admin_headers
    calls = _use_resend(monkeypatch, [
        FakeResponse(200, {'id': 'msg_test'}),
        FakeResponse(403, text='API key is invalid'),
    ])

    ok = client.post('/email/test', json={'email': 'ok@example.com'}, headers=admin)
    assert ok.status_code == 200
    assert ok.json() == {'message': 'Test email sent.', 'provider': 'resend', 'message_id': 'msg_test'}
    assert calls[0]['json']['subject'] == 'SGCC Wiki 메일 테스트'

    # 설정 오류는 로그를 안 봐도 응답에서 바로 보이도록 502 + provider 메시지로 돌려준다.
    bad = client.post('/email/test', json={'email': 'bad@example.com'}, headers=admin)
    assert bad.status_code == 502
    assert 'API key is invalid' in bad.json()['detail']
    assert len(calls) == 2


def test_smtp_provider_uses_timeout_starttls_and_login(client, monkeypatch):
    import core.maintenance as maintenance

    events = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            events.append(('connect', host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            events.append(('starttls',))

        def login(self, user, password):
            events.append(('login', user, password))

        def send_message(self, msg):
            events.append(('send', msg['From'], msg['To'], msg['Message-ID']))

    monkeypatch.setattr(maintenance, 'EMAIL_PROVIDER', 'smtp')
    monkeypatch.setattr(maintenance, 'SMTP_HOST', 'smtp.example.com')
    monkeypatch.setattr(maintenance, 'SMTP_PORT', 587)
    monkeypatch.setattr(maintenance, 'SMTP_USER', 'club@example.com')
    monkeypatch.setattr(maintenance, 'SMTP_PASSWORD', 'app-password')
    monkeypatch.setattr(maintenance, 'EMAIL_FROM', 'club@example.com')
    monkeypatch.setattr(maintenance.smtplib, 'SMTP', FakeSMTP)

    result = maintenance.send_email_now('to@example.com', 'subject', 'body')
    assert result['provider'] == 'smtp'
    message_id = result['message_id']

    assert events[0] == ('connect', 'smtp.example.com', 587, 10)
    assert events[1] == ('starttls',)
    assert events[2] == ('login', 'club@example.com', 'app-password')
    assert events[3][:3] == ('send', 'club@example.com', 'to@example.com')
    assert events[3][3] == message_id
