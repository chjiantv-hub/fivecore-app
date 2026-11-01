import json, os, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .engine import analyze_market, analyze_stock

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv('DB_PATH', str(ROOT / 'data.sqlite3'))
INGEST_TOKEN = os.getenv('INGEST_TOKEN', '')
app = FastAPI(title='五核智选 FiveCore', version='0.1.0', description='个人使用的A股五核短线决策辅助终端')
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, kind TEXT, payload TEXT, created_at TEXT)')
    conn.commit()
    return conn


class MarketPayload(BaseModel):
    source: str = Field(min_length=1, max_length=80)
    timestamp: str
    data_quality: str = 'provider_unverified'
    advancers: int = 0
    decliners: int = 0
    unchanged: int = 0
    limit_up: int = 0
    limit_down: int = 0
    broken_limit: int = 0
    yesterday_limit_premium_pct: float = 0
    turnover_yi: float = 0
    turnover_vs_prev_pct: float = 0
    indices: dict[str, Any] = Field(default_factory=dict)
    sectors: list[dict[str, Any]] = Field(default_factory=list)

class StockPayload(BaseModel):
    code: str
    name: str = ''
    price: float | None = None
    change_pct: float | None = None
    is_mainline: bool = False
    is_leader_or_capacity_core: bool = False
    absorption_improving: bool = False
    sector_confirmation: bool = False
    entry_trigger: bool = False
    unacceptable_t1_risk: bool = False

@app.get('/')
def home():
    return FileResponse(ROOT / 'static' / 'index.html')

@app.get('/api/health')
def health():
    return {'status':'ok', 'app':'FiveCore', 'version':'0.1.0', 'market_feed':'not_configured', 'server_time':datetime.now(timezone.utc).isoformat()}

@app.post('/api/ingest/market')
def ingest_market(payload: MarketPayload, x_ingest_token: str = Header(default='')):
    if not INGEST_TOKEN or x_ingest_token != INGEST_TOKEN:
        raise HTTPException(status_code=401, detail='行情写入接口未授权。请在服务器配置 INGEST_TOKEN。')
    data = payload.model_dump()
    conn = db()
    conn.execute('INSERT INTO snapshots(kind,payload,created_at) VALUES(?,?,?)', ('market', json.dumps(data, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()
    return {'accepted':True, 'source':payload.source, 'timestamp':payload.timestamp}

@app.get('/api/market/latest')
def latest_market():
    conn = db(); row = conn.execute("SELECT payload FROM snapshots WHERE kind='market' ORDER BY id DESC LIMIT 1").fetchone(); conn.close()
    if not row:
        return {'available':False, 'message':'尚未接入行情源。当前没有真实行情，禁止将演示状态用于交易。'}
    raw = json.loads(row['payload'])
    result = analyze_market(raw)
    result.update({'available':True,'source':raw['source'],'indices':raw.get('indices',{}),'sectors':raw.get('sectors',[])})
    return result

@app.post('/api/analyze/stock')
def stock_analysis(stock: StockPayload):
    m = latest_market()
    if not m.get('available'):
        raise HTTPException(status_code=503, detail='没有可用的真实市场快照，暂不能进行五核判断。')
    return analyze_stock(stock.model_dump(), m)

@app.get('/api/config')
def config():
    return {'mode':'private-mvp','live_feed_configured':bool(INGEST_TOKEN),'decision_policy':'rules-first; no auto-trading','market_feed':'bring-your-own-authorized-provider'}
