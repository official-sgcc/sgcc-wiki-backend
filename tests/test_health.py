from sqlmodel import create_engine


def test_healthz_reports_ok(client):
    resp = client.get('/healthz')
    assert resp.status_code == 200
    assert resp.json() == {'status': 'ok'}


def test_healthz_returns_503_when_db_is_unreachable(client, monkeypatch):
    import sgcc_wiki_backend.routers as routers

    monkeypatch.setattr(routers.health, 'engine', create_engine('sqlite:////nonexistent-dir/x.db'))
    resp = client.get('/healthz')
    assert resp.status_code == 503
    assert resp.json()['detail'] == 'Database unavailable.'
