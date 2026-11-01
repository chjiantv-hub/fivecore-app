from app.engine import analyze_market, analyze_stock


def test_market_missing_data_is_not_falsely_bullish():
    result = analyze_market({'advancers': 0, 'decliners': 0, 'limit_up': 0, 'limit_down': 0, 'broken_limit': 0})
    assert result['environment'] == '震荡'
    assert result['breadth_pct'] == 0


def test_unacceptable_t1_risk_blocks_candidate():
    market = {'environment': '偏强', 'sentiment': '赚钱效应扩散'}
    stock = {'code': '600000', 'is_mainline': True, 'is_leader_or_capacity_core': True,
             'absorption_improving': True, 'sector_confirmation': True,
             'entry_trigger': True, 'unacceptable_t1_risk': True}
    result = analyze_stock(stock, market)
    assert result['status'] == '风险回避'


def test_missing_evidence_does_not_count_as_pass():
    result = analyze_stock({'code': '000001'}, {'environment': '震荡', 'sentiment': '分歧/修复观察'})
    assert result['score'] == 0
    assert result['status'] == '暂不关注'
