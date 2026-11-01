import importlib
import pytest
from fastapi.testclient import TestClient

TOKEN = 'test-access-token'
ING = 'test-ingest-token'


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('ACCESS_TOKEN', TOKEN)
    monkeypatch.setenv('INGEST_TOKEN', ING)
    monkeypatch.setenv('DB_PATH', str(tmp_path / 't.sqlite3'))
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app)


def test_auth_required(client):
    assert client.get('/healthz').status_code == 200
    assert client.get('/api/market/latest').status_code == 401
    assert client.get('/').status_code == 401
    assert client.get('/api/market/latest', headers={'X-Token': 'wrong'}).status_code == 401
    r = client.get('/api/market/latest', headers={'X-Token': TOKEN})
    assert r.status_code == 200 and r.json()['available'] is False
    assert client.get(f'/?t={TOKEN}').status_code == 200


def test_no_access_token_configured_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv('ACCESS_TOKEN', raising=False)
    monkeypatch.setenv('DB_PATH', str(tmp_path / 't.sqlite3'))
    import app.main as main
    importlib.reload(main)
    assert TestClient(main.app).get('/api/market/latest').status_code == 503


def test_ingest_nulls_and_public_label(client):
    body = {'source': '测试夹具', 'timestamp': '2026-11-01T15:00:00+08:00', 'data_quality': 'public_web_unlicensed',
            'advancers': 893, 'decliners': 4602, 'unchanged': 76, 'limit_up': 47, 'limit_down': None, 'broken_limit': 31}
    assert client.post('/api/ingest/market', json=body, headers={'X-Ingest-Token': 'bad'}).status_code == 401
    assert client.post('/api/ingest/market', json=body, headers={'X-Ingest-Token': ING}).status_code == 200
    m = client.get('/api/market/latest', headers={'X-Token': TOKEN}).json()
    assert m['available'] and m['feed'] == 'public_web_unlicensed'
    assert m['banner'] == '公开网页行情（非授权 Level-1），仅供研究参考'
    assert m['limit_down'] is None and 'limit_down' in m['missing_fields']
    assert m['environment'] == '偏弱'          # breadth 16% proves weakness even with 跌停 missing
    assert m['sentiment'] == '数据不足'        # premium/跌停 missing
    h = client.get('/api/health', headers={'X-Token': TOKEN}).json()
    assert h['market_feed'] == 'public_web_unlicensed'
    s = client.post('/api/analyze/stock', json={'code': '002343'}, headers={'X-Token': TOKEN}).json()
    assert s['checks']['sentiment'] is None and s['judgment_source']['is_mainline'] == '未提供'
