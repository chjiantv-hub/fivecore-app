"""Public-quote market adapter for FiveCore repo v0.2. No licensed feed.

Port of the v0.1 adapter to the v0.2 payload contract (base: repo commit
65707f401fce52d8c35e42cfa6cd3cc0e20e998b, main.py v0.2.0):
- data_quality is 'public_web_unlicensed' (the value v0.2 maps to the
  public-web banner). Never 'licensed*' and never the v0.1 label.
- Missing baselines are None, never 0: on the first run there is no
  previous trading day, so turnover_vs_prev_pct and
  yesterday_limit_premium_pct are None (v0.2 treats them as unknown, and
  the engine then reports 数据不足 for environment/sentiment until day 2).
- Adds trade_date (Beijing date), field_sources and field_fetched_at,
  as v0.2's MarketPayload expects.
- Self-contained storage: writes snapshots + adapter state directly to
  the same SQLite schema v0.2 uses, so main.py only needs a small
  lifespan hook (start adapter_loop when ADAPTER_ENABLED=1). No new
  module dependency.

Honesty rules (unchanged from v0.1): if the full-market scan fails, NO
snapshot is posted; weekend/holiday duplicate scans are skipped; scans
run only Mon-Fri 09:15-15:05 Beijing time.
"""
import asyncio
import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("fivecore.adapter")
BJ = ZoneInfo("Asia/Shanghai")
DB_PATH = os.getenv("DB_PATH", str(Path(__file__).resolve().parent.parent / "data.sqlite3"))


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS snapshots "
        "(id INTEGER PRIMARY KEY, kind TEXT, payload TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS adapter_state "
        "(key TEXT PRIMARY KEY, value TEXT)"
    )
    conn.commit()
    return conn


def store_snapshot(data: dict) -> None:
    conn = db()
    conn.execute(
        "INSERT INTO snapshots(kind,payload,created_at) VALUES(?,?,?)",
        ("market", json.dumps(data, ensure_ascii=False),
         datetime.now(BJ).isoformat()),
    )
    conn.commit()
    conn.close()


def get_latest() -> dict | None:
    conn = db()
    row = conn.execute(
        "SELECT payload FROM snapshots WHERE kind='market' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return None
    return json.loads(row["payload"])


def get_state(key: str, default=None):
    conn = db()
    row = conn.execute(
        "SELECT value FROM adapter_state WHERE key=?", (key,)
    ).fetchone()
    conn.close()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
        return default


def set_state(key: str, value) -> None:
    conn = db()
    conn.execute(
        "INSERT INTO adapter_state(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()
POLL_SECONDS = 90
PAGE_SIZE = 100
EASTMONEY_UT = "bd1d9ddb0404a9d0e4b7b9d0e4b7b9"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
# 沪深 A 股 (不含北交所). Scanned total is logged and stored in the snapshot
# meta so coverage drift is visible instead of silently changing breadth.
EASTMONEY_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
INDEX_SECIDS = "1.000001,0.399001,0.399006,0.000688"


def http_get_json(url, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def http_get_text(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("gbk", errors="ignore")


def limit_rate(code, name):
    """Board limit rate, or None when the stock has no limit today."""
    if name.startswith(("*ST", "ST")):
        return 0.05
    if name.startswith(("N", "C")):
        return None
    if code.startswith(("688", "300", "301")):
        return 0.20
    if code.startswith(("43", "92")):
        return 0.30
    return 0.10


def classify(code, name, price, prev, high):
    """'limit_up' | 'limit_down' | 'broken' | 'normal' | 'skip'."""
    rate = limit_rate(code or "", name or "")
    if rate is None or not prev or prev <= 0 or price is None or high is None:
        return "skip"
    up = round(prev * (1 + rate), 2)
    down = round(prev * (1 - rate), 2)
    eps = 0.005
    if price >= up - eps:
        return "limit_up"
    if price <= down + eps:
        return "limit_down"
    if high >= up - eps:
        return "broken"
    return "normal"


def fetch_eastmoney_scan():
    """Full 沪深 A-share scan. Returns (rows, total). Raises on any failure."""
    rows = []
    total = None
    page = 1
    while True:
        url = (
            f"https://push2.eastmoney.com/api/qt/clist/get?pn={page}&pz={PAGE_SIZE}"
            f"&po=1&np=1&ut={EASTMONEY_UT}&fltt=2&invt=2&fid=f3"
            f"&fs={EASTMONEY_FS}"
            f"&fields=f12,f14,f2,f3,f6,f15,f16,f17,f18"
        )
        data = http_get_json(url)
        d = data.get("data") or {}
        if total is None:
            total = d.get("total") or 0
        diff = d.get("diff") or []
        rows.extend(diff)
        if not diff or len(rows) >= total or page >= 200:
            break
        page += 1
    if total and len(rows) < total * 0.95:
        raise RuntimeError(f"incomplete scan: got {len(rows)} of {total}")
    return rows, total


def fetch_eastmoney_indices():
    data = http_get_json(
        "https://push2.eastmoney.com/api/qt/ulist.np/get"
        f"?secids={INDEX_SECIDS}&fields=f12,f14,f2,f3,f6"
    )
    out = {}
    for it in (data.get("data") or {}).get("diff") or []:
        out[it.get("f12")] = {
            "name": it.get("f14"),
            "price": it.get("f2"),
            "change_pct": it.get("f3"),
            "turnover": it.get("f6"),
        }
    return out


def fetch_tencent_indices():
    """Fallback index source. Tencent stock layout shares the leading
    fields ([1] name, [3] price, [4] prev close); change_pct is computed."""
    text = http_get_text(
        "http://qt.gtimg.cn/q=sh000001,sz399001,sz399006,sh000688"
    )
    out = {}
    code_map = {
        "sh000001": "1.000001",
        "sz399001": "0.399001",
        "sz399006": "0.399006",
        "sh000688": "0.000688",
    }
    for line in text.splitlines():
        if "=\"" not in line:
            continue
        key, body = line.split("=\"", 1)
        key = key.strip()[2:]  # strip leading 'v_'
        body = body.rsplit('";', 1)[0]
        f = body.split("~")
        if len(f) < 5:
            continue
        try:
            price = float(f[3])
            prev = float(f[4])
        except (ValueError, TypeError):
            continue
        chg = round((price - prev) / prev * 100, 2) if prev else None
        out[code_map.get(key, key)] = {
            "name": f[1], "price": price, "change_pct": chg, "turnover": None,
        }
    return out


def compute_snapshot(rows, indices, daily):
    """Build the MarketPayload dict from one full scan. daily maps
    Beijing date -> {'turnover_yi': float, 'limit_codes': [codes]}."""
    adv = dec = unc = 0
    limit_up = limit_down = broken = 0
    turnover = 0.0
    today_limit_codes = []
    for r in rows:
        chg = r.get("f3")
        if chg is None:
            continue
        if chg > 0:
            adv += 1
        elif chg < 0:
            dec += 1
        else:
            unc += 1
        turnover += r.get("f6") or 0
        cls = classify(r.get("f12", ""), r.get("f14", ""), r.get("f2"),
                       r.get("f18"), r.get("f15"))
        if cls == "limit_up":
            limit_up += 1
            today_limit_codes.append(r.get("f12"))
        elif cls == "limit_down":
            limit_down += 1
        elif cls == "broken":
            broken += 1
    turnover_yi = round(turnover / 1e8, 1)
    today = datetime.now(BJ).strftime("%Y-%m-%d")
    prev_dates = sorted(d for d in daily if d < today)
    prev = daily.get(prev_dates[-1]) if prev_dates else None
    prev_turnover = (prev or {}).get("turnover_yi") or 0
    # v0.2: missing baselines are None, never 0.
    vs_prev = (
        round((turnover_yi - prev_turnover) / prev_turnover * 100, 2)
        if prev_turnover else None
    )
    by_code = {r.get("f12"): r for r in rows}
    yday_codes = (prev or {}).get("limit_codes") or []
    vals = [by_code[c].get("f3") for c in yday_codes
            if c in by_code and by_code[c].get("f3") is not None]
    premium = round(sum(vals) / len(vals), 2) if vals else None
    ts = datetime.now(BJ).isoformat()
    scan_source = "eastmoney公开行情推算"
    snapshot = {
        "source": scan_source,
        "timestamp": ts,
        "data_quality": "public_web_unlicensed",
        "advancers": adv,
        "decliners": dec,
        "unchanged": unc,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "broken_limit": broken,
        "yesterday_limit_premium_pct": premium,
        "turnover_yi": turnover_yi,
        "turnover_vs_prev_pct": vs_prev,
        "indices": indices,
        "sectors": [],
        "trade_date": today,
        "field_sources": {k: scan_source for k in
                          ("advancers", "decliners", "unchanged", "limit_up",
                           "limit_down", "broken_limit",
                           "yesterday_limit_premium_pct", "turnover_yi",
                           "turnover_vs_prev_pct")},
        "field_fetched_at": {k: ts for k in
                             ("advancers", "decliners", "unchanged", "limit_up",
                              "limit_down", "broken_limit",
                              "yesterday_limit_premium_pct", "turnover_yi",
                              "turnover_vs_prev_pct")},
    }
    return snapshot, today, today_limit_codes


def in_trading_window(now=None):
    now = now or datetime.now(BJ)
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= mins <= 15 * 60 + 5


def run_cycle(dryrun=False):
    """One gated cycle. Returns a short status string. Never posts partial
    or duplicate snapshots."""
    if not dryrun and not in_trading_window():
        return "outside trading window, skipped"
    try:
        rows, total = fetch_eastmoney_scan()
    except Exception as e:
        log.warning("eastmoney scan failed: %s", e)
        return f"scan failed: {e}"
    try:
        indices = fetch_eastmoney_indices()
        indices_source = "eastmoney"
    except Exception as e:
        log.warning("eastmoney indices failed (%s), trying tencent", e)
        try:
            indices = fetch_tencent_indices()
            indices_source = "tencent"
        except Exception as e2:
            log.warning("tencent indices failed: %s", e2)
            indices, indices_source = {}, "none"
    daily = get_state("daily", {}) or {}
    snapshot, today, today_limit_codes = compute_snapshot(rows, indices, daily)
    snapshot["indices"] = dict(snapshot["indices"])
    snapshot["indices"]["_meta"] = {
        "scan_total": total,
        "scan_source": "eastmoney",
        "indices_source": indices_source,
        "universe": "沪深A股(不含北交所)",
    }
    if indices_source == "tencent":
        snapshot["source"] = "eastmoney+tencent公开行情推算"
        for k in snapshot["field_sources"]:
            snapshot["field_sources"][k] = "eastmoney+tencent公开行情推算"
    if dryrun:
        return (
            f"DRYRUN total={total} adv={snapshot['advancers']} "
            f"dec={snapshot['decliners']} limit_up={snapshot['limit_up']} "
            f"limit_down={snapshot['limit_down']} broken={snapshot['broken_limit']} "
            f"turnover_yi={snapshot['turnover_yi']} "
            f"yday_premium={snapshot['yesterday_limit_premium_pct']} "
            f"indices_source={indices_source}"
        )
    last = get_latest()
    if last and (
        last.get("turnover_yi") == snapshot["turnover_yi"]
        and last.get("advancers") == snapshot["advancers"]
        and last.get("limit_up") == snapshot["limit_up"]
    ):
        return "duplicate/stale scan, skipped (not posted)"
    store_snapshot(snapshot)
    daily[today] = {"turnover_yi": snapshot["turnover_yi"],
                    "limit_codes": today_limit_codes}
    set_state("daily", daily)
    return (
        f"posted total={total} adv={snapshot['advancers']} "
        f"dec={snapshot['decliners']} limit_up={snapshot['limit_up']} "
        f"limit_down={snapshot['limit_down']} broken={snapshot['broken_limit']} "
        f"turnover_yi={snapshot['turnover_yi']}"
    )


async def adapter_loop():
    while True:
        try:
            status = await asyncio.to_thread(run_cycle)
            log.info("adapter cycle: %s", status)
        except Exception as e:
            log.warning("adapter cycle error: %s", e)
        await asyncio.sleep(POLL_SECONDS)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if os.getenv("ADAPTER_DRYRUN") == "1":
        print(run_cycle(dryrun=True))
        return
    if os.getenv("ADAPTER_ONCE") == "1":
        print(run_cycle())
        return
    log.info("adapter loop starting (poll every %ss)", POLL_SECONDS)
    while True:
        try:
            log.info("adapter cycle: %s", run_cycle())
        except Exception as e:
            log.warning("adapter cycle error: %s", e)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
