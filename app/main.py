"""五核智选 FiveCore — standalone service.

Auth: every page and API needs ACCESS_TOKEN (?t= query once -> HttpOnly cookie, or X-Token header), constant-time compare.
Public: /healthz only. Ingest: its own X-Ingest-Token (INGEST_TOKEN), constant-time compare.
No market data is built in; until an ingest arrives the page shows 行情未接入. Missing fields stay null (未取到)."""
import hmac, json, os, sqlite3, datetime as dt
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .engine import analyze_market, analyze_stock, LICENSED_PREFIX

VERSION = '0.2.0'
BJ = dt.timezone(dt.timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'static'
DB_PATH = os.getenv('DB_PATH', str(ROOT / 'data.sqlite3'))
BANNER_PUBLIC = '公开网页行情（非授权 Level-1），仅供研究参考'
PUBLIC_PATHS = {'/healthz', '/api/ingest/market'}   # ingest is guarded by INGEST_TOKEN instead
COOKIE = 'fivecore_t'


def access_token() -> str:
    return os.getenv('ACCESS_TOKEN', '').strip()


def ingest_token() -> str:
    return os.getenv('INGEST_TOKEN', '').strip()


def _eq(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


app = FastAPI(title='五核智选 FiveCore', version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware('http')
async def auth(request: Request, call_next):
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    tok = access_token()
    if not tok:
        return JSONResponse({'detail': '服务器未配置 ACCESS_TOKEN，已拒绝访问。'}, status_code=503)
    given = request.query_params.get('t') or request.headers.get('x-token') or request.cookies.get(COOKIE) or ''
    if not _eq(given, tok):
        if request.url.path.startswith('/api/'):
            return JSONResponse({'detail': '需要访问令牌。'}, status_code=401)
        return HTMLResponse("<h3 style='font-family:sans-serif'>需要访问令牌（请使用带 ?t= 的完整链接打开）</h3>", status_code=401)
    resp = await call_next(request)
    if request.query_params.get('t'):
        resp.set_cookie(COOKIE, tok, max_age=3600 * 24 * 365, httponly=True, secure=True, samesite='lax')
    resp.headers['Cache-Control'] = 'no-store'; resp.headers['X-Robots-Tag'] = 'noindex'
    return resp


app.mount('/static', StaticFiles(directory=STATIC), name='static')


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, kind TEXT, payload TEXT, created_at TEXT)')
    conn.commit()
    return conn


class MarketPayload(BaseModel):
    """Missing numbers must be null — never 0."""
    source: str = Field(min_length=1, max_length=120)
    timestamp: str
    data_quality: str = 'provider_unverified'
    advancers: int | None = None
    decliners: int | None = None
    unchanged: int | None = None
    limit_up: int | None = None
    limit_down: int | None = None
    broken_limit: int | None = None
    yesterday_limit_premium_pct: float | None = None
    turnover_yi: float | None = None
    turnover_vs_prev_pct: float | None = None
    indices: dict[str, Any] | None = None
    sectors: list[dict[str, Any]] | None = None
    field_sources: dict[str, str] | None = None
    field_fetched_at: dict[str, str] | None = None
    trade_date: str | None = None


class StockPayload(BaseModel):
    """Judgment fields are tri-state: true / false / null(未提供)."""
    code: str
    name: str = ''
    price: float | None = None
    change_pct: float | None = None
    is_mainline: bool | None = None
    is_leader_or_capacity_core: bool | None = None
    absorption_improving: bool | None = None
    sector_confirmation: bool | None = None
    entry_trigger: bool | None = None
    unacceptable_t1_risk: bool | None = None


NUM_FIELDS = ['advancers', 'decliners', 'unchanged', 'limit_up', 'limit_down', 'broken_limit',
              'yesterday_limit_premium_pct', 'turnover_yi', 'turnover_vs_prev_pct']


def _latest():
    conn = db()
    try:
        row = conn.execute("SELECT payload, created_at FROM snapshots WHERE kind='market' ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    if not row:
        return None
    d = json.loads(row['payload']); d['_received_at'] = row['created_at']
    return d


def _feed(raw):
    if not raw:
        return 'not_configured'
    q = str(raw.get('data_quality') or '')
    if q.startswith(LICENSED_PREFIX):
        return 'licensed'
    if q == 'public_web_unlicensed':
        return 'public_web_unlicensed'
    return 'ingest_unverified'


@app.get('/healthz')
def healthz():
    return {'ok': True}


@app.get('/')
def home():
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    html = html.replace('<link rel="manifest" href="manifest.webmanifest" crossorigin="use-credentials">', '')
    # inline CSS/JS: the page needs only the one token-checked request
    html = html.replace('<link rel="stylesheet" href="static/style.css">', '<style>' + (STATIC / 'style.css').read_text(encoding='utf-8') + '</style>')
    html = html.replace('<script src="static/app.js"></script>', '<script>' + (STATIC / 'app.js').read_text(encoding='utf-8') + '</script>')
    return HTMLResponse(html)


@app.get('/sw.js', include_in_schema=False)
def sw():
    return Response((STATIC / 'sw.js').read_text(encoding='utf-8'), media_type='application/javascript')


@app.get('/api/health')
def health():
    raw = _latest(); feed = _feed(raw)
    return {'status': 'ok', 'app': 'FiveCore', 'version': VERSION, 'market_feed': feed,
            'market_feed_cn': {'not_configured': '未接入', 'public_web_unlicensed': BANNER_PUBLIC, 'licensed': '授权行情',
                               'ingest_unverified': '外部写入（未标注授权）'}[feed],
            'last_fetch': (raw or {}).get('timestamp'), 'received_at': (raw or {}).get('_received_at'),
            'ingest_enabled': bool(ingest_token()), 'server_time': dt.datetime.now(BJ).isoformat()}


@app.post('/api/ingest/market')
def ingest_market(payload: MarketPayload, x_ingest_token: str = Header(default='')):
    if not _eq(x_ingest_token, ingest_token()):
        raise HTTPException(status_code=401, detail='行情写入接口未授权。请在服务器配置 INGEST_TOKEN，并在请求头提供 X-Ingest-Token。')
    data = payload.model_dump()
    conn = db()
    try:
        conn.execute('INSERT INTO snapshots(kind,payload,created_at) VALUES(?,?,?)',
                     ('market', json.dumps(data, ensure_ascii=False), dt.datetime.now(BJ).isoformat()))
        conn.commit()
    finally:
        conn.close()
    return {'accepted': True, 'source': payload.source, 'timestamp': payload.timestamp, 'data_quality': payload.data_quality}


@app.get('/api/market/latest')
def latest_market():
    raw = _latest()
    if not raw:
        return {'available': False, 'feed': 'not_configured', 'message': '尚未接入行情源。当前没有真实行情，不输出交易结论。'}
    feed = _feed(raw)
    result = analyze_market(raw)
    result.update({'available': True, 'feed': feed, 'source': raw.get('source'), 'trade_date': raw.get('trade_date'),
                   'indices': raw.get('indices'), 'sectors': raw.get('sectors'),
                   'field_sources': raw.get('field_sources') or {k: raw.get('source') for k in NUM_FIELDS if raw.get(k) is not None},
                   'field_fetched_at': raw.get('field_fetched_at') or {k: raw.get('timestamp') for k in NUM_FIELDS if raw.get(k) is not None},
                   'missing_fields': [k for k in NUM_FIELDS + ['indices', 'sectors'] if raw.get(k) is None],
                   'banner': None if feed == 'licensed' else (BANNER_PUBLIC if feed == 'public_web_unlicensed' else '数据源未核验，仅供研究参考')})
    return result


@app.post('/api/analyze/stock')
def stock_analysis(stock: StockPayload):
    m = latest_market()
    if not m.get('available'):
        raise HTTPException(status_code=503, detail='没有可用的真实市场快照，暂不能进行五核判断。')
    s = stock.model_dump()
    res = analyze_stock(s, m)
    res['judgment_source'] = {k: ('用户提供' if s.get(k) is not None else '未提供') for k in
                              ('is_mainline', 'is_leader_or_capacity_core', 'absorption_improving', 'sector_confirmation', 'entry_trigger', 'unacceptable_t1_risk')}
    res['market'] = {k: m.get(k) for k in ('environment', 'sentiment', 'feed', 'data_quality', 'data_quality_cn', 'timestamp', 'banner')}
    return res


@app.get('/api/config')
def config():
    return {'mode': 'private-mvp', 'version': VERSION, 'auth': 'ACCESS_TOKEN', 'live_feed_configured': _latest() is not None,
            'ingest_enabled': bool(ingest_token()), 'decision_policy': 'rules-first; no auto-trading; missing = 未知',
            'market_feed': 'bring-your-own-authorized-provider'}
