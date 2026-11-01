"""Five-Core pre-market / intraday selection rules (observation pool only).

Pure functions, no I/O, no external dependencies, so the same module drops
into either codebase (repo v0.2 or uploaded zip v0.1) unchanged.

Semantics (shared with the v0.2 engine):
- Evidence is tri-state: True = supports, False = contradicts, None = not
  provided / unknown. None never counts as a pass.
- Prior-day classification is an INPUT, never derived here from daily OHLC:
  a failed-board morphology, a long upper shadow, seal quality, diffusion
  and afternoon absorption cannot be established from day high/low alone,
  and a board-open claim needs an intraday sequence or an open count.
  Whoever builds the input must supply the prior classification from a
  verified prior-day review (or mark it None / background).
- Output is an observation shortlist, never a buy signal. Candidates are
  capped at 3 with 0 allowed; if more than 3 qualify, all are returned with
  an over_cap flag and a note that a human must cut to 3 -- this module
  invents no ranking rule.
- Standing rules encoded here:
  (1) 9:27 output is a PRE-signal only; a full trigger needs 9:36 too.
  (2) An opening rebound does NOT reverse an auction failure.
  (3) Seal continuity, volume and sector diffusion must each be separately
      verified before a candidate counts as confirmed; a closing limit
      price alone never confirms.
"""

MAX_CANDIDATES = 3
VERIFY_AT_0936 = ["封板连续性", "量能", "板块扩散"]


def _stock(code, name, prior, auction_signal, **kw):
    d = {"code": code, "name": name, "prior": prior,
         "auction_signal": auction_signal}
    d.update(kw)
    return d


def select_premarket(stocks):
    """9:27 auction screen.

    Input per stock:
      prior: "weak" | "strong" | "background" | "excluded" | None
        weak = prior-day weak (failed board at any count, or gap-up long
          upper shadow, mainline intact); strong = prior-day strong
          (well-sealed limit-up or clearly stronger rise, leader with
          diffusion + afternoon absorption, incl. highest board);
          background = e.g. a plain decline (never a weak candidate);
          excluded = mainline invalidated / leader structure broken.
      auction_signal: "加强" | "不变" | "减弱" | "失效" | None
        Directional auction judgment vs the stock's prior state.
      prior_evidence: str, optional free-text evidence for the prior label.
    """
    candidates, rejected = [], []
    for s in stocks:
        code, name = s.get("code", ""), s.get("name", "")
        prior, sig = s.get("prior"), s.get("auction_signal")
        ev = s.get("prior_evidence", "")
        if prior == "excluded":
            rejected.append({"code": code, "name": name,
                             "reason": "主线失效 / 龙头结构破坏，已排除（失效 ≠ 弱）",
                             "evidence": ev})
        elif prior not in ("weak", "strong"):
            rejected.append({"code": code, "name": name,
                             "reason": "前日未分类为弱或强（一般收跌仅作背景，不构成弱转强候选）"
                             if prior == "background" else "前日弱 / 强未分类",
                             "evidence": ev})
        elif sig != "加强":
            rejected.append({"code": code, "name": name,
                             "reason": "竞价未转强（信号：%s）" % (sig or "未提供"),
                             "evidence": ev})
        else:
            kind = "弱转强预信号" if prior == "weak" else "强更强预信号"
            candidates.append({"code": code, "name": name, "kind": kind,
                               "status": "预信号（需9:36确认）",
                               "evidence": ev,
                               "verify_at_0936": list(VERIFY_AT_0936)})
    over_cap = len(candidates) > MAX_CANDIDATES
    notes = []
    if over_cap:
        notes.append("预信号 %d 只，超出 %d 只上限，需人工筛选至 ≤3 只（本模块不设排序规则）"
                     % (len(candidates), MAX_CANDIDATES))
    return {"candidates": candidates, "rejected": rejected,
            "over_cap": over_cap, "notes": notes}


def select_intraday(premarket, intraday):
    """9:36 opening-structure confirmation (window 09:30-09:40).

    premarket: the return value of select_premarket.
    intraday: dict code -> {
        sealed_0936: bool/None, seal_continuous: bool/None,
        opens_count: int/None, volume_ok: bool/None, diffusion_ok: bool/None,
        note: str }
    A candidate confirms only if: sealed AND seal continuous AND diffusion
    verified AND volume verified, with no conflicting opens count. Any
    unverified required item blocks confirmation. A stock without a
    pre-signal never confirms, even if it seals or rebounds at the open.
    """
    pre_codes = {c["code"] for c in premarket.get("candidates", [])}
    confirmed, not_confirmed, rebound = [], [], []
    for c in premarket.get("candidates", []):
        code, name = c["code"], c["name"]
        d = intraday.get(code) or {}
        sealed, cont = d.get("sealed_0936"), d.get("seal_continuous")
        vol, diff = d.get("volume_ok"), d.get("diffusion_ok")
        opens = d.get("opens_count")
        if sealed is not True:
            reason = "9:36 未封板"
        elif cont is not True:
            reason = "封板连续性未核实 / 不连续"
        elif diff is not True:
            reason = "板块扩散未核实"
        elif vol is not True:
            reason = "量能未核实"
        elif opens is not None and opens > 0:
            reason = "开板 %d 次，与连续封板矛盾" % opens
        else:
            confirmed.append({**c, "status": "9:36确认候选（仍为观察候选，非买入指令）",
                              "intraday": {"sealed_0936": sealed,
                                           "seal_continuous": cont,
                                           "opens_count": opens,
                                           "volume_ok": vol,
                                           "diffusion_ok": diff}})
            continue
        not_confirmed.append({**c, "status": "未确认", "reason": reason,
                              "intraday_note": d.get("note", "")})
    for code, d in intraday.items():
        if code in pre_codes:
            continue
        if d.get("sealed_0936") is True:
            rebound.append({"code": code,
                            "reason": "开盘封板 / 翻红，但 9:27 未通过预信号，不逆转竞价失败"})
    over_cap = len(confirmed) > MAX_CANDIDATES
    notes = []
    if over_cap:
        notes.append("确认候选 %d 只，超出 %d 只上限，需人工筛选至 ≤3 只"
                     % (len(confirmed), MAX_CANDIDATES))
    if not confirmed:
        notes.append("确认候选 0 只（空仓 / 等待亦为有效结果）")
    return {"confirmed": confirmed, "not_confirmed": not_confirmed,
            "rebound_not_reversed": rebound, "over_cap": over_cap,
            "notes": notes}
