"""Transparent, deterministic Five-Core rules. No LLM-generated trading decisions."""
from datetime import datetime, timezone


def _num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def analyze_market(m: dict) -> dict:
    total = max(_num(m.get('advancers')) + _num(m.get('decliners')), 1)
    breadth = _num(m.get('advancers')) / total
    limit_up = _num(m.get('limit_up'))
    limit_down = _num(m.get('limit_down'))
    broken = _num(m.get('broken_limit'))
    break_rate = broken / max(limit_up + broken, 1)
    prev_premium = _num(m.get('yesterday_limit_premium_pct'))
    amount = _num(m.get('turnover_yi'))

    if breadth >= .62 and prev_premium > 0 and limit_down <= 8:
        environment = '偏强'
    elif breadth <= .38 or limit_down >= 20:
        environment = '偏弱'
    else:
        environment = '震荡'

    if prev_premium > 1 and limit_up >= 50 and break_rate < .30:
        sentiment = '赚钱效应扩散'
    elif prev_premium < -1 or break_rate >= .50 or limit_down >= 15:
        sentiment = '亏钱效应占优'
    else:
        sentiment = '分歧/修复观察'

    if amount <= 0:
        liquidity = '成交额数据缺失'
    else:
        liquidity = '放量' if m.get('turnover_vs_prev_pct', 0) > 3 else ('缩量' if m.get('turnover_vs_prev_pct', 0) < -3 else '量能平稳')

    return {
        'environment': environment,
        'sentiment': sentiment,
        'breadth_pct': round(breadth * 100, 1),
        'limit_up': int(limit_up),
        'limit_down': int(limit_down),
        'broken_limit_rate_pct': round(break_rate * 100, 1),
        'liquidity': liquidity,
        'turnover_yi': amount,
        'timestamp': m.get('timestamp') or datetime.now(timezone.utc).isoformat(),
        'data_quality': m.get('data_quality', 'provider_unverified'),
    }


def analyze_stock(stock: dict, market: dict) -> dict:
    """Only score supplied, timestamped facts. Missing evidence never counts as positive."""
    checks = {
        'market': market.get('environment') != '偏弱',
        'sentiment': market.get('sentiment') != '亏钱效应占优',
        'mainline': bool(stock.get('is_mainline')),
        'leader': bool(stock.get('is_leader_or_capacity_core')),
        'absorption': bool(stock.get('absorption_improving')),
        'sector_validation': bool(stock.get('sector_confirmation')),
        'entry': bool(stock.get('entry_trigger')),
        't1_risk': not bool(stock.get('unacceptable_t1_risk')),
    }
    passed = sum(checks.values())
    if not checks['market'] or not checks['sentiment'] or not checks['t1_risk']:
        status = '风险回避'
    elif passed >= 7:
        status = '允许进入人工决策'
    elif passed >= 4:
        status = '观察等待确认'
    else:
        status = '暂不关注'
    return {
        'code': stock.get('code', ''), 'name': stock.get('name', ''),
        'price': stock.get('price'), 'change_pct': stock.get('change_pct'),
        'score': passed, 'max_score': len(checks), 'status': status,
        'checks': checks,
        'missing_evidence': [k for k, v in checks.items() if not v],
        'reason': '规则引擎仅评估已提供的数据；缺失项按未通过处理，不构成自动买入指令。',
    }
