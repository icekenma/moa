"""AI 자동매매: 플랜·매수 조건·청산·손실 한도·분석·개선안 승인·실전 체결 맞추기·API."""
import json
import os
import sys
import time
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import moa.aipick as AP  # noqa: E402
from moa.app import create_app  # noqa: E402
from moa.autotrade import PLAN_KEYS, VARIANTS, _clamp_key, make_plan  # noqa: E402
from moa.db import DB  # noqa: E402
from moa.runtime import Runtime  # noqa: E402


@pytest.fixture
def rt():
    from moa.broker import kiwoom as _kw
    _kw._SHARED.clear()
    r = Runtime(DB(":memory:"), start_thread=False)
    yield r
    r.stop()


class Env:
    """장 시간·시세·날짜를 테스트가 정한다."""

    def __init__(self, rt, mp):
        self.rt, self.at = rt, rt.autotrade
        self.px = {"005930": 70000.0, "000660": 150000.0, "035420": 200000.0, "AAPL": 200.0}
        self.day, self.hm, self.open, self.left = "2026-11-18", "10:30", True, 200
        at = self.at
        mp.setattr(at, "ready", lambda: {"ai": True, "data": True, "real": {"ok": False, "label": None, "why": "키움 주문 계좌가 필요해요"}})
        mp.setattr(at, "_session", lambda mkt: {"open": self.open, "left_min": self.left if self.open else 0})
        mp.setattr(at, "_quote", lambda mkt, sym: {"price": self.px.get(sym), "name": sym, "change_pct": 1.0})
        mp.setattr(at, "_local", lambda mkt: datetime.fromisoformat(f"{self.day}T{self.hm}:00"))
        mp.setattr(AP, "mkt_date", lambda mkt: self.day)
        mp.setattr(AP, "kst_now", lambda: datetime.fromisoformat(f"{self.day}T{self.hm}:00"))
        mp.setattr(rt, "notify", lambda msg: None)
        mp.setattr(rt.ai, "configured", lambda: True)
        self.idx = None                                              # 지수 등락률(없으면 사전 점검을 하지 않는다)
        mp.setattr(at, "index_pct", lambda mkt: self.idx)
        mp.setattr(rt.ai, "fx", lambda: 1400.0)

    def run(self, **kw):
        d = {"budget": 3_000_000, "mkt": "kr", "style": "day", "days": 30, "mode": "paper"}
        new = kw.pop("new_defaults", False)
        d.update(kw)
        rid = self.at.create(d)["run"]["id"]
        if not new:      # 예전 테스트는 '관망이면 쉰다 · 하루 3번 · $1'을 전제로 쓰였다
            self.rt.db.x("UPDATE at_runs SET params=json_set(params,'$.skip_watch',json('true'),'$.cycles_per_day',3,'$.cycle_gap_min',60,'$.ai_daily_usd',1.0,'$.web_search',json('true')) WHERE id=?", (rid,))
        return rid

    def pick(self, sym="005930", **kw):
        x = {"sym": sym, "name": sym, "score": 75, "win_prob": 62, "cal_prob": None, "cal_n": 0, "entry_type": "market",
             "entry_price": self.px[sym], "target_pct": 3.0, "stop_pct": 1.5, "trail_pct": 0, "exit_by": "14:50",
             "reasons": ["거래량 돌파"], "risks": ["지수 약세"], "feat": {"vwap": 0.4}}
        x.update(kw)
        return x

    def enter(self, rid, picks, stance="보통"):
        return self.at._enter(self.at._run(rid), {"id": 1, "stance": stance, "view": "지수 강보합", "picks": picks}, {"usd": 0.05})


@pytest.fixture
def env(rt, monkeypatch):
    return Env(rt, monkeypatch)


# ---------- 플랜 ----------
def test_plan_scales_with_budget_and_style():
    small, big = make_plan(500_000, "kr", "day", 30), make_plan(5_000_000, "kr", "day", 30)
    assert small["max_positions"] < big["max_positions"]
    assert big["per_trade_pct"] * big["max_positions"] <= 100
    sw = make_plan(5_000_000, "kr", "swing", 30)
    assert sw["tp_cap"] > big["tp_cap"] and sw["cycles_per_day"] == 1
    us = make_plan(3000, "us", "day", 30)
    assert us["entry_start"] == "09:40"
    for p in (small, big, sw, us):
        assert p["total_loss_pct"] > p["daily_loss_pct"] and p["skip_watch"] is True


def test_clamp_keys():
    assert _clamp_key("min_win_prob", 999) == 80 and _clamp_key("per_trade_pct", "1") == 5
    assert _clamp_key("max_positions", 2.6) == 3
    assert _clamp_key("entry_start", "10:00") == "10:00"
    with pytest.raises(ValueError):
        _clamp_key("entry_start", "25:00")
    assert _clamp_key("skip_watch", "false") is False
    assert "warmup_trades" not in PLAN_KEYS  # 검증 단계는 개선안으로 못 건드린다


def test_create_validates(env):
    at = env.at
    with pytest.raises(ValueError, match="30만"):
        at.create({"budget": 1000, "mkt": "kr", "style": "day", "days": 10})
    with pytest.raises(ValueError, match="7일"):
        at.create({"budget": 1_000_000, "mkt": "kr", "style": "swing", "days": 3})
    with pytest.raises(ValueError, match="키움"):
        at.create({"budget": 1_000_000, "mkt": "kr", "style": "day", "days": 10, "mode": "real", "confirm": "실전"})
    rid = env.run()
    with pytest.raises(ValueError, match="이미"):
        env.run()
    env.run(mkt="us", budget=3000)  # 다른 시장은 함께 돌 수 있다
    d = at.detail(rid)
    assert d["run"]["status"] == "running" and d["run"]["end_date"] == "2026-12-17" and len(d["plan"]) >= 6


def test_create_needs_ai_and_data(rt, monkeypatch):
    at = rt.autotrade
    with pytest.raises(ValueError, match="API 키"):
        at.create({"budget": 1_000_000, "mkt": "kr", "style": "day", "days": 10})
    monkeypatch.setattr(at, "ready", lambda: {"ai": True, "data": False, "real": {"ok": False, "why": "x", "label": None}})
    with pytest.raises(ValueError, match="키움 계좌"):
        at.create({"budget": 1_000_000, "mkt": "kr", "style": "day", "days": 10})


# ---------- 매수 조건 ----------
def test_enter_filters_and_sizes(env):
    at, rid = env.at, env.run()
    picks = [env.pick("005930"),
             env.pick("000660", win_prob=40),                       # 승률 미달
             env.pick("035420", target_pct=1.0, stop_pct=1.5)]      # 손익비 미달
    assert env.enter(rid, picks) == 1
    t = at._open(rid)[0]
    p = at._run(rid)["params"]
    # 검증 단계: 정해진 비중의 절반만
    assert t["qty"] * t["entry_price"] <= 3_000_000 * p["per_trade_pct"] / 100 * p["warmup_mult"]
    assert t["qty"] >= 1 and t["entry_price"] > 70000  # 슬리피지 반영
    assert t["target_pct"] == 3.0 and t["stop_pct"] == 1.5 and t["exit_by"] == "14:50"
    log = at.detail(rid)["log"]
    dec = next(x for x in log if x["kind"] == "cycle")["data"]["decisions"]
    assert [d["act"] for d in dec] == ["buy", "skip", "skip"]
    assert "승률" in dec[1]["why"] and "손익비" in dec[2]["why"]
    # 같은 종목은 하루에 한 번
    assert env.enter(rid, [env.pick("005930")]) == 0
    m = at.money(at._run(rid))
    assert m["cash"] == pytest.approx(3_000_000 - t["qty"] * t["entry_price"]) and m["cash"] > 0


def test_enter_caps_and_skips(env):
    at, rid = env.at, env.run()
    assert env.enter(rid, [env.pick()], stance="관망") == 0             # 관망이면 쉰다
    assert env.enter(rid, [env.pick(auto=True)]) == 0                   # 앱이 고른 예비 종목은 안 산다
    assert env.enter(rid, [env.pick(entry_price=69000)]) == 0           # 추천가보다 많이 오름
    assert env.enter(rid, [env.pick(target_pct=9, stop_pct=5)]) == 1    # 상한으로 잘라서 매수
    t = at._open(rid)[0]
    p = at._run(rid)["params"]
    assert t["target_pct"] == p["tp_cap"] and t["stop_pct"] == p["sl_cap"]
    at.control(rid, "pause")
    assert env.enter(rid, [env.pick("000660")]) == 0                    # 멈춘 동안은 안 산다


def test_never_spends_more_than_budget(env):
    at, rid = env.at, env.run(budget=400_000)
    env.px.update({"A1": 30000.0, "A2": 30000.0, "A3": 30000.0})
    env.enter(rid, [env.pick("A1"), env.pick("A2"), env.pick("A3"), env.pick("000660")])
    opens = at._open(rid)
    assert len(opens) <= at._run(rid)["params"]["max_positions"]
    assert sum(t["qty"] * t["entry_price"] for t in opens) <= 400_000
    assert at.money(at._run(rid))["cash"] >= 0


# ---------- 청산 ----------
def test_take_profit_stop_and_time_exit(env):
    at, rid = env.at, env.run()
    env.enter(rid, [env.pick("005930"), env.pick("000660"), env.pick("035420")])
    assert len(at._open(rid)) == 3
    env.px["005930"] = 70000 * 1.04     # 익절
    env.px["000660"] = 150000 * 0.97    # 손절
    at.tick(force=True)
    rows = {r["sym"]: r for r in at._closed(rid)}
    assert rows["005930"]["reason"] == "익절" and rows["005930"]["pnl"] > 0
    assert rows["000660"]["reason"] == "손절" and rows["000660"]["pnl"] < 0
    assert rows["005930"]["fees"] > 0 and rows["005930"]["mfe_pct"] > 3 and rows["000660"]["mae_pct"] < -2
    assert len(at._open(rid)) == 1
    env.hm = "14:55"                    # 정리 시각 지남
    at.tick(force=True)
    assert at._closed(rid)[-1]["reason"] == "시간 정리" and not at._open(rid)
    m = at.detail(rid)["run"]["money"]
    assert m["realized"] == pytest.approx(sum(r["pnl"] for r in at._closed(rid)))
    assert m["equity"] == pytest.approx(3_000_000 + m["realized"])


def test_no_selling_when_market_closed(env):
    at, rid = env.at, env.run()
    env.enter(rid, [env.pick()])
    env.open, env.px["005930"] = False, 60000
    at.tick(force=True)
    assert len(at._open(rid)) == 1     # 장이 닫혀 있으면 팔지 않는다


def test_daily_loss_and_streak_block_new_entries(env):
    at, rid = env.at, env.run()
    run = at._run(rid)
    assert at.gate(run)[0] is True
    lim = run["budget"] * run["params"]["daily_loss_pct"] / 100
    rt = env.rt
    rt.db.x("INSERT INTO at_trades(run_id,ver,mkt,sym,name,qty,status,entry_ts,entry_day,entry_hm,entry_price,target_pct,stop_pct,"
            "exit_ts,exit_price,reason,pnl,pnl_pct,exit_day) VALUES(?,1,'kr','X','X',1,'closed',?,?,?,1000,3,1.5,?,900,'손절',?,-10,?)",
            (rid, "2026-11-18T09:30:00", env.day, "09:30", "2026-11-18T09:40:00", -lim - 1, env.day))
    ok, why = at.gate(at._run(rid))
    assert ok is False and "하루 손실 한도" in why
    env.day = "2026-11-19"             # 다음 날은 다시 살 수 있다
    assert at.gate(at._run(rid))[0] is True


def test_gate_windows_and_cycle_limit(env):
    at, rid = env.at, env.run()
    env.hm = "08:50"
    assert "매수 시간" in at.gate(at._run(rid))[1]
    env.hm = "10:30"
    env.open = False
    assert "장이" in at.gate(at._run(rid))[1]
    env.open = True
    n = at._run(rid)["params"]["cycles_per_day"]
    for _ in range(n):
        at.log(rid, "cycle", "추천", {"usd": 0.05})
    assert "추천 횟수" in at.gate(at._run(rid))[1]


def test_total_loss_stops_and_liquidates(env):
    at, rid = env.at, env.run(budget=1_000_000)
    at.edit(rid, {"per_trade_pct": 50, "sl_cap": 20, "tp_cap": 60, "total_loss_pct": 2})
    env.rt.db.x("UPDATE at_runs SET params=json_set(params,'$.warmup_trades',0) WHERE id=?", (rid,))
    assert env.enter(rid, [env.pick("005930", stop_pct=20, target_pct=40)]) == 1
    env.px["005930"] = 70000 * 0.93    # 비중 50% × -7% ≈ -3.5% > 한도 2%
    at.tick(force=True)
    d = at.detail(rid)
    assert d["run"]["status"] == "ended" and "손실 한도" in d["run"]["status_msg"]
    assert not d["open"] and d["closed"][0]["reason"] == "종료 정리"
    with pytest.raises(ValueError):
        at.control(rid, "resume")


def test_end_control_liquidates_and_period_end(env):
    at, rid = env.at, env.run(days=2)
    env.enter(rid, [env.pick()])
    at.control(rid, "end")
    d = at.detail(rid)
    assert d["run"]["status"] == "ended" and d["closed"][0]["reason"] == "종료 정리" and d["equity"]
    rid2 = env.run(days=1)
    env.day = "2026-11-19"             # 기간이 지나면 스스로 끝난다
    at.tick(force=True)
    assert at.detail(rid2)["run"]["status"] == "ended"


def test_swing_holds_overnight_until_exit_date(env):
    at, rid = env.at, env.run(style="swing", days=30)
    env.enter(rid, [env.pick(target_pct=10, stop_pct=5, hold_days=3)])
    t = at._open(rid)[0]
    assert t["exit_date"] > env.day and t["exit_by"] == ""
    env.hm, env.left = "15:15", 5
    at.tick(force=True)
    assert len(at._open(rid)) == 1     # 스윙은 장 마감에 팔지 않는다
    env.day = t["exit_date"]
    at.tick(force=True)
    assert at._closed(rid)[-1]["reason"] == "기간 정리"


# ---------- 분석·개선안 ----------
def _seed(env, rid, n=40):
    """승률 60% 미만은 잃고 그 이상은 버는 기록을 넣는다."""
    db, t0 = env.rt.db, datetime(2026, 11, 2, 9, 30)
    for i in range(n):
        low = i % 2 == 0
        pct = -1.2 if low else 1.6
        ts = t0 + timedelta(hours=i * 5)
        db.x("INSERT INTO at_trades(run_id,ver,mkt,sym,name,qty,status,entry_ts,entry_day,entry_hm,entry_price,target_pct,stop_pct,"
             "exit_ts,exit_price,reason,pnl,pnl_pct,fees,mfe_pct,mae_pct,hold_min,score,win_prob,stance,exit_day,why) "
             "VALUES(?,1,'kr',?,?,10,'closed',?,?,?,10000,3,1.5,?,?,?,?,?,30,?,?,40,75,?,'보통',?,'{}')",
             (rid, f"S{i}", f"S{i}", ts.isoformat(), ts.date().isoformat(), "11:00", (ts + timedelta(minutes=40)).isoformat(),
              10000 * (1 + pct / 100), "손절" if low else "시간 정리", pct * 1000, pct, 0.4 if low else 1.9, -1.4 if low else -0.3,
              56 if low else 66, ts.date().isoformat()))


def test_stats_and_rule_proposal_apply(env):
    at, rid = env.at, env.run()
    with pytest.raises(ValueError, match="10건"):
        at.review(rid)
    _seed(env, rid)
    st = at.stats(rid)
    assert st["all"]["n"] == 40 and st["all"]["win_rate"] == 50 and st["all"]["mdd_pct"] >= 0 and len(st["curve"]) == 40
    assert {g["label"] for g in st["groups"]} >= {"파는 사유", "AI 승률", "산 시간"}
    pr = at.review(rid)
    keys = {c["key"]: c for c in pr["changes"]}
    assert pr["status"] == "pending" and keys["min_win_prob"]["to"] == 60 and "평균" in keys["min_win_prob"]["why"]
    before = at._run(rid)
    d = at.decide(pr["id"], "apply")
    after = at._run(rid)
    assert after["ver"] == before["ver"] + 1 and after["params"]["min_win_prob"] == 60
    assert d["changes"][-1]["source"] == "rule" and d["changes"][-1]["ver"] == 2
    with pytest.raises(ValueError, match="이미"):
        at.decide(pr["id"], "apply")
    # 승인 전에는 아무것도 바뀌지 않는다 / 거절하면 그대로
    pr2 = at._save_proposal(after, "rule", "x", [{"key": "max_positions", "to": 1, "why": "t"}])
    assert at._run(rid)["params"]["max_positions"] == 3
    at.decide(pr2["id"], "reject")
    assert at._run(rid)["params"]["max_positions"] == 3 and at._run(rid)["ver"] == 2


def test_auto_review_after_enough_trades(env, monkeypatch):
    at, rid = env.at, env.run()
    monkeypatch.setattr(at, "_ai_review_bg", lambda rid: None)
    _seed(env, rid, 29)
    env.enter(rid, [env.pick()])
    env.px["005930"] = 80000
    at.tick(force=True)                # 30번째 거래가 끝나면 스스로 분석한다
    props = at.detail(rid)["proposals"]
    assert props and props[0]["source"] == "rule"
    assert at._run(rid)["params"]["min_win_prob"] == 55   # 승인 전이라 그대로


def test_ai_review_parsing(env):
    at, rid = env.at, env.run()
    with pytest.raises(ValueError, match="5건"):
        at.review_context({"run_id": rid})
    _seed(env, rid, 12)
    title, ask, data = at.review_context({"run_id": rid})
    assert "복기" in title and "json" in ask and len(data["끝난 거래(최근 60건)"]) == 12
    text = ("손절이 많아요\n- 낮은 승률대가 문제\n```json\n" + json.dumps({"summary": "승률 기준 상향", "changes": [
        {"key": "min_win_prob", "to": 95, "why": "낮은 승률대 손실"},
        {"key": "warmup_trades", "to": 0, "why": "금지된 값"},
        {"key": "budget", "to": 99999999, "why": "금지된 값"},
        {"key": "entry_start", "to": "bad", "why": "형식 오류"}]}, ensure_ascii=False) + "\n```")
    pr = at.save_review(text, {"run_id": rid}, "claude-sonnet-5-5", 0.03)
    assert pr["source"] == "ai" and pr["status"] == "pending" and pr["summary"] == "승률 기준 상향"
    assert [(c["key"], c["to"]) for c in pr["changes"]] == [("min_win_prob", 80)]   # 범위로 자르고, 허용 안 된 값은 버린다
    assert "```" not in pr["text"]
    pr0 = at.save_review("바꿀 것 없음", {"run_id": rid}, "m", 0.01)
    assert pr0["status"] == "none" and pr0["changes"] == []


def test_ai_context_kind(env):
    rid = env.run()
    _seed(env, rid, 6)
    title, ask, data = env.rt.ai.context("auto", {"run_id": rid})
    assert "자동매매 복기" in title
    assert env.rt.ai.meter("auto")["web"] is False


def test_edit_and_export(env):
    at, rid = env.at, env.run()
    d = at.edit(rid, {"daily_loss_pct": 1.5, "budget": 1, "min_score": ""})
    assert d["params"]["daily_loss_pct"] == 1.5 and d["run"]["ver"] == 2 and d["changes"][-1]["source"] == "user"
    with pytest.raises(ValueError, match="늦어요"):
        at.edit(rid, {"entry_start": "15:00", "entry_end": "10:00"})
    env.enter(rid, [env.pick()])
    csv = at.export_csv(rid).decode("utf-8-sig")
    assert csv.splitlines()[0].startswith("id,ver,mkt,sym") and "005930" in csv


# ---------- 실전 ----------
class FakeClient:
    mock = True

    def __init__(self):
        self.orders, self.fills, self.cancelled, self.usd = [], {}, [], 5000.0

    def fills_today(self):
        return self.fills

    def us_fills_today(self):
        return self.fills

    def us_order(self, side, sym, qty, price=None, exch=None):
        self.orders.append((side, sym, qty, price))
        return f"U{len(self.orders)}", exch or "ND"

    def us_cash(self):
        return {"usd_orderable": self.usd}

    def us_holdings(self):
        return []

    def us_cancel(self, no, sym, exch=None):
        self.cancelled.append(no)


class FakeBroker:
    mode, label = "kiwoom-mock", "키움 모의투자"

    def __init__(self):
        self.client, self.money = FakeClient(), 10_000_000

    def cash(self):
        return self.money

    def positions(self):
        return []

    def buy(self, code, qty, price=None):
        self.client.orders.append(("buy", code, qty, price))
        return {"status": "accepted", "fill_price": 0, "broker_no": f"K{len(self.client.orders)}", "name": code}

    def sell(self, code, qty, price=None):
        self.client.orders.append(("sell", code, qty, price))
        return {"status": "accepted", "fill_price": 0, "broker_no": f"K{len(self.client.orders)}", "name": code}

    def cancel(self, no, code):
        self.client.cancelled.append(no)


def test_real_mode_orders_and_reconcile(env, monkeypatch):
    at, b = env.at, FakeBroker()
    monkeypatch.setattr(at, "real_broker", lambda: (b, ""))
    monkeypatch.setattr(at, "ready", lambda: {"ai": True, "data": True, "real": {"ok": True, "label": b.label, "why": ""}})
    with pytest.raises(ValueError, match="실전"):
        env.run(mode="real")                       # 확인 문구 없이는 시작되지 않는다
    rid = env.run(mode="real", confirm="실전")
    assert env.enter(rid, [env.pick()]) == 1
    t = at._open(rid)[0]
    assert b.client.orders[0][:2] == ("buy", "005930") and t["fill_ok"] == 0 and t["buy_no"] == "K1"
    env.px["005930"] = 80000
    at.tick(force=True)
    assert len(at._open(rid)) == 1                 # 체결 확인 전에는 팔지 않는다
    b.client.fills = {"K1": {"qty": t["qty"], "price": 70100.0}}
    env.px["005930"] = 70100
    at.tick(force=True)
    t = at._open(rid)[0]
    assert t["fill_ok"] == 1 and t["entry_price"] == 70100.0
    env.px["005930"] = 70100 * 1.04
    at.tick(force=True)
    c = at._closed(rid)[0]
    assert c["reason"] == "익절" and c["exit_ok"] == 0 and b.client.orders[-1][:2] == ("sell", "005930")
    b.client.fills["K2"] = {"qty": c["qty"], "price": 72800.0}
    at.tick(force=True)
    c = at._closed(rid)[0]
    assert c["exit_ok"] == 1 and c["exit_price"] == 72800.0 and c["pnl"] > 0
    assert env.rt.db.one("SELECT COUNT(*) n FROM orders WHERE msg LIKE 'AI 자동매매%'")["n"] == 2


def test_real_mode_unfilled_buy_is_cancelled(env, monkeypatch):
    at, b = env.at, FakeBroker()
    monkeypatch.setattr(at, "real_broker", lambda: (b, ""))
    monkeypatch.setattr(at, "ready", lambda: {"ai": True, "data": True, "real": {"ok": True, "label": b.label, "why": ""}})
    rid = env.run(mode="real", confirm="실전")
    env.enter(rid, [env.pick()])
    old = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
    env.rt.db.x("UPDATE at_trades SET entry_ts=? WHERE run_id=?", (old, rid))
    at.tick(force=True)
    assert not at._open(rid) and b.client.cancelled == ["K1"]
    assert at.money(at._run(rid))["cash"] == 3_000_000   # 체결 안 된 주문은 돈에서 빠지지 않는다
    b.money = 1000                                         # 계좌에 돈이 모자라면 주문을 넣지 않는다
    assert env.enter(rid, [env.pick("000660")]) == 0 and len(b.client.orders) == 1


def test_us_paper_and_real(env, monkeypatch):
    at = env.at
    rid = env.run(mkt="us", budget=3000)
    assert env.enter(rid, [env.pick("AAPL", exit_by="")]) == 1
    t = at._open(rid)[0]
    assert t["entry_price"] == pytest.approx(200 * 1.0005) and t["qty"] >= 1
    env.px["AAPL"] = 207
    at.tick(force=True)
    c = at._closed(rid)[0]
    assert c["reason"] == "익절" and c["fees"] > 0 and c["pnl"] == pytest.approx((207 * 0.9995 - t["entry_price"]) * t["qty"] - c["fees"], abs=0.02)
    at.control(rid, "end")
    b = FakeBroker()
    monkeypatch.setattr(at, "real_broker", lambda: (b, ""))
    monkeypatch.setattr(at, "ready", lambda: {"ai": True, "data": True, "real": {"ok": True, "label": b.label, "why": ""}})
    rid2 = env.run(mkt="us", budget=3000, mode="real", confirm="실전")
    env.px["AAPL"] = 200
    env.enter(rid2, [env.pick("AAPL")])
    side, sym, qty, price = b.client.orders[0]
    assert (side, sym) == ("buy", "AAPL") and price == pytest.approx(200.6)   # 시장가 대신 조금 위 지정가


# ---------- 추천 받기(스레드)·API ----------
def test_cycle_uses_ai_stream(env, monkeypatch):
    at, rid = env.at, env.run()
    seen = {}

    def stream(kind, d):
        seen.update(kind=kind, d=d)
        yield {"t": "status", "msg": "..."}
        yield {"t": "picks", "id": 7, "stance": "보통", "view": "v", "picks": [env.pick()]}
        yield {"t": "done", "usd": 0.07}
    monkeypatch.setattr(env.rt.ai, "stream", stream)
    monkeypatch.setattr(env.rt.ai, "configured", lambda: True)
    at.tick(force=True)
    at._busy[rid].join(5)
    assert seen["kind"] == "pick" and seen["d"]["horizon"] == "day" and seen["d"]["mkt"] == "kr"
    assert len(at._open(rid)) == 1 and at._open(rid)[0]["pick_id"] == 7
    assert "다음 추천" in at.gate(at._run(rid))[1]       # 간격을 지킨다
    monkeypatch.setattr(env.rt.ai, "stream", lambda kind, d: iter([{"t": "error", "msg": "예산 초과"}]))
    env.rt.db.x("UPDATE at_runs SET last_cycle=0 WHERE id=?", (rid,))
    at.tick(force=True)
    at._busy[rid].join(5)
    assert "예산 초과" in at.detail(rid)["log"][0]["msg"]


def test_now_tells_what_happens_next_and_what_happened_today(env):
    """지금 상태: 장 시작 전·매수 대기·추천 간격·자리 없음·장 마감 뒤·쉬는 날마다 '다음에 언제 움직이는지'를 알려 준다."""
    import time as _t
    at = env.at
    rid = env.run(budget=1_000_000)
    run = lambda: at._run(rid)
    p = run()["params"]
    assert (p["max_positions"], p["per_trade_pct"], p["cycles_per_day"], p["entry_start"], p["entry_end"]) == (3, 33.0, 3, "09:10", "14:30")
    # 1) 장 시작 전(수요일 08:20): 오늘 09:10에 시작, 50분 뒤
    env.open, env.hm = False, "08:20"
    n = at.now(run())
    assert n["phase"] == "pre" and n["busy"] is False and n["next"]["kind"] == "at" and "장 시작 전" in n["next"]["label"]
    assert (n["next"]["text"], n["next"]["in_min"], n["next"]["at"]) == ("오늘 09:10", 50, "2026-11-18T09:10")
    rows = n["steps"]["rows"]
    assert n["steps"]["today"] and n["steps"]["label"] == "오늘" and [(r["hm"], r["name"]) for r in rows] == [
        ("09:00", "장 시작"), ("09:10", "매수 시작"), ("14:30", "매수 끝"), ("15:12", "전부 정리"), ("15:20", "장 마감")]
    assert not any(r["done"] for r in rows) and all(r["local"] is None for r in rows)
    assert n["today"] == {"cycles": 0, "cycles_max": 3, "buys": 0, "sells": 0, "wins": 0, "pnl": 0, "unreal": 0, "limit": 20000.0, "streak": 0, "streak_max": 3}
    assert n["risk"] == {"loss": 0.0, "limit": 80000.0}
    assert n["size"] == {"alloc": 165000.0, "pct": 33.0, "mult": 0.5, "warm_done": 0, "warm_need": 10, "slots_used": 0, "slots": 3}     # 검증 단계: 비중의 절반
    assert n["period"] == {"day": 1, "days": 30, "left": 29, "end": run()["end_date"]} and n["ai"]["total_usd"] == 0 and n["last_cycle"] is None
    assert n["clock"]["kst"] == "08:20" and n["engine"] == {"sec": None, "ok": False}                                                 # 엔진이 아직 한 번도 돌지 않음
    at.tick(force=True)
    assert at.now(run())["engine"]["ok"] is True
    # 2) 장은 열렸지만 매수 시작 전
    env.open, env.hm = True, "09:05"
    n = at.now(run())
    assert n["phase"] == "open" and n["next"]["text"] == "오늘 09:10" and n["next"]["in_min"] == 5 and "매수 시작 시각을 기다려요" in n["next"]["label"]
    assert [r["done"] for r in n["steps"]["rows"]] == [True, False, False, False, False]
    # 3) 매수 시간: 곧 추천
    env.hm = "09:30"
    assert at.now(run())["next"]["kind"] == "soon"
    # 4) 한 번 추천받아 산 뒤: 간격만큼 기다림, 오늘 한 일이 숫자로 잡힌다
    env.enter(rid, [env.pick(), env.pick("000660", score=10)])
    at.rt.db.x("UPDATE at_runs SET last_cycle=? WHERE id=?", (_t.time(), rid))
    n = at.now(run())
    assert n["next"]["kind"] == "at" and n["next"]["text"] == "오늘 10:30" and 59 <= n["next"]["in_min"] <= 60 and "간격(60분)" in n["next"]["label"]
    assert n["today"]["cycles"] == 1 and n["today"]["buys"] == 1 and n["size"]["slots_used"] == 1 and n["ai"] == {"today_usd": 0.05, "limit_usd": 1.0, "total_usd": 0.05, "cycles_all": 1}
    lc = n["last_cycle"]
    assert lc["bought"] == ["005930"] and lc["picks"] == 2 and lc["skipped"][0]["name"] == "000660" and "점수" in lc["skipped"][0]["why"] and lc["view"] == "지수 강보합"
    env.px["005930"] = 69500.0
    at.tick(force=True)
    n = at.now(run())
    assert n["today"]["unreal"] < 0 and n["today"]["pnl"] == 0 and n["today"]["sells"] == 0
    # 간격이 매수 끝 시각을 넘기면 다음 거래일로
    env.hm = "14:00"
    n = at.now(run())
    assert n["next"]["text"] == "내일 10:10" and "다음 추천 차례가 오지 않아요" in n["next"]["label"]
    # 5) 오늘 추천 횟수를 다 쓰면 다음 거래일
    env.hm = "11:00"
    at.rt.db.x("UPDATE at_runs SET last_cycle=0 WHERE id=?", (rid,))
    for _ in range(2):
        at.log(rid, "cycle", "추천", {"usd": 0.05})
    n = at.now(run())
    assert n["next"]["kind"] == "at" and n["next"]["text"] == "내일 10:10" and "추천 횟수" in n["next"]["label"] and n["today"]["cycles"] == 3
    # 6) 자리가 꽉 차면 시각 없이 '자리가 나면'
    at.rt.db.x("DELETE FROM at_log WHERE run_id=? AND kind='cycle' AND msg='추천'", (rid,))
    at.edit(rid, {"max_positions": 1})
    n = at.now(run())
    assert n["next"]["kind"] == "slot" and "자리가 나면" in n["next"]["label"] and "at" not in n["next"]
    at.edit(rid, {"max_positions": 3})
    # 7) 장 마감 뒤: 시간표는 다음 거래일 것으로 바뀐다(11/19는 수능일이라 10:10). 휴장일(설정)과 주말은 건너뛴다
    env.open, env.hm = False, "16:00"
    n = at.now(run())
    assert n["phase"] == "post" and n["next"]["text"] == "내일 10:10" and n["steps"]["today"] is False and n["steps"]["label"] == "11/19(목)"
    at.rt.settings.update({"holidays": ["2026-11-19"]})
    n = at.now(run())
    assert n["next"]["text"] == "11/20(금) 09:10" and n["steps"]["day"] == "2026-11-20" and n["next"]["in_min"] == (24 + 17) * 60 + 10
    env.day, env.hm = "2026-11-21", "10:00"                                   # 토요일
    n = at.now(run())
    assert n["phase"] == "off" and "장이 쉬어요" in n["next"]["label"] and n["next"]["text"] == "11/23(월) 09:10" and n["period"]["day"] == 4
    # 8) 멈춤·정리·종료
    at.control(rid, "pause")
    assert at.now(run())["next"]["kind"] == "paused"
    at.control(rid, "resume")
    # 기간 마지막 날 뒤로는 새로 사지 않는다고 알려 준다
    at.rt.db.x("UPDATE at_runs SET end_date='2026-11-21' WHERE id=?", (rid,))
    n = at.now(run())
    assert n["next"]["kind"] == "hold" and "새로 사지 않아요" in n["next"]["label"] and n["period"]["left"] == 0
    at.rt.db.x("UPDATE at_runs SET end_date='2026-12-17' WHERE id=?", (rid,))
    # 화면용 묶음: 목록·자세히·홈에 같은 상태가 실린다
    c = create_app(env.rt).test_client()
    o = c.get("/api/auto").get_json()["runs"][0]
    assert o["now"]["next"]["text"] == "11/23(월) 09:10" and o["now"]["size"]["slots"] == 3 and "steps" not in o["now"]
    d = c.get(f"/api/auto/runs/{rid}").get_json()
    assert d["now"]["steps"]["rows"] and d["now"]["last_cycle"]["bought"] == ["005930"] and d["ready"]["ai"] is True
    b = c.get("/api/auto/brief").get_json()
    assert b["ended"] == 0 and b["runs"][0]["id"] == rid and b["runs"][0]["now"]["today"]["buys"] == 0 and b["runs"][0]["money"]["budget"] == 1_000_000
    assert c.get("/api/home").get_json()["auto"]["runs"][0]["name"] == b["runs"][0]["name"]
    env.day, env.hm, env.open = "2026-11-23", "10:00", True
    at.control(rid, "end")
    at.tick(force=True)
    assert at._run(rid)["status"] == "ended"
    d = c.get(f"/api/auto/runs/{rid}").get_json()
    assert d["now"] is None and c.get("/api/auto/brief").get_json() == {"runs": [], "labs": [], "ended": 1} and "now" not in c.get("/api/auto").get_json()["runs"][0]


def test_now_us_shows_korean_time(env, monkeypatch):
    """미국장은 한국 시각으로 알려 주고 뉴욕 시각을 함께 준다."""
    at = env.at
    # 뉴욕 11/18(수) 08:00 = 한국 11/18(수) 22:00 (겨울, 14시간 차이)
    monkeypatch.setattr(at, "_local", lambda mkt: datetime(2026, 11, 18, 8, 0) if mkt == "us" else datetime(2026, 11, 18, 22, 0))
    monkeypatch.setattr(AP, "kst_now", lambda: datetime(2026, 11, 18, 22, 0))
    monkeypatch.setattr(AP, "et_now", lambda: datetime(2026, 11, 18, 8, 0))
    env.open = False
    rid = env.run(mkt="us", budget=3000)
    n = at.now(at._run(rid))
    assert n["phase"] == "pre" and n["next"] == {"kind": "at", "label": n["next"]["label"], "at": "2026-11-18T23:40", "text": "오늘 23:40", "in_min": 100, "in_sec": 6000, "local": "뉴욕 09:40"}
    rows = n["steps"]["rows"]
    assert (rows[0]["hm"], rows[0]["local"], rows[0]["name"]) == ("23:30", "09:30", "장 시작") and (rows[-1]["hm"], rows[-1]["local"]) == ("06:00", "16:00")
    assert n["clock"] == {"kst": "22:00", "local": "08:00", "left_min": 0} and n["today"]["limit"] == 60.0


def _cyc(env, rid, pid, picks, nets=None, stance="보통"):
    """추천 한 번을 흘려보내고, 그 종목들의 '그 뒤 실제 결과'(추천 성적표)를 넣는다."""
    n = env.at._enter(env.at._run(rid), {"id": pid, "stance": stance, "view": "지수 보합", "picks": picks}, {"usd": 0.05})
    for i, x in enumerate(picks):
        if nets and nets[i] is not None:
            env.rt.db.x("INSERT INTO ai_outcomes(pick_id,idx,ts,mkt,model,sym,status,result,net_pct) VALUES(?,?,?,?,?,?,?,?,?)",
                        (pid, i, f"{env.day}T10:00:00", "kr", "m", x["sym"], "done", "익절" if nets[i] > 0 else "손절", nets[i]))
    return n


def test_opportunity_counts_skips_and_joins_what_happened_next(env):
    """기회 분석: 건너뛴 이유를 코드로 남기고, 그 종목들이 그 뒤 실제로 어땠는지 붙인다."""
    at = env.at
    rid = env.run(budget=3_000_000)
    _cyc(env, rid, 11, [env.pick("005930"), env.pick("000660", win_prob=52), env.pick("035420", score=40)], [1.2, 0.8, -1.5])
    env.day = "2026-11-19"
    _cyc(env, rid, 12, [env.pick("000660", win_prob=50), env.pick("035420", target_pct=1.0, stop_pct=1.5)], [None, 0.4])
    _cyc(env, rid, 13, [env.pick("000660"), env.pick("035420")], [0.9, -0.3], stance="관망")
    at.log(rid, "cycle", "추천을 받지 못했어요: 오류")                       # 결과 없는 기록은 세지 않는다
    dec = at.rt.db.q("SELECT data FROM at_log WHERE run_id=? AND kind='cycle' AND data IS NOT NULL ORDER BY id", (rid,))
    d0 = json.loads(dec[0]["data"])["decisions"]
    assert [(q["act"], q.get("code")) for q in d0] == [("buy", None), ("skip", "winprob"), ("skip", "score")] and d0[1]["wp"] == 52
    O = at.opportunity(at._run(rid))
    assert (O["days"], O["cycles"], O["picks"], O["bought"], O["buy_rate"], O["per_day"]) == (2, 3, 7, 1, 14, 0.5)
    R = {r["code"]: r for r in O["reasons"]}
    assert R["winprob"] == {"n": 2, "graded": 1, "avg": 0.8, "win": 100, "code": "winprob", "label": "승률 기준 미달"}
    assert R["score"]["avg"] == -1.5 and R["rr"]["n"] == 1 and R["rr"]["avg"] == 0.4 and R["watch"] == {"n": 2, "graded": 2, "avg": 0.3, "win": 50, "code": "watch", "label": "AI가 관망 권함"}
    assert O["skipped"] == {"n": 6, "graded": 5, "avg": 0.06, "win": 60} and O["reasons"][0]["n"] == 2
    # 예전 기록(코드 없이 글만 있는 것)도 이유를 알아낸다
    from moa.autotrade import skip_code
    assert skip_code({"why": "승률 50% < 기준 55%"}) == "winprob" and skip_code({"why": "배정 금액(165,000)으로 1주를 못 삼"}) == "qty"
    assert skip_code({"why": "추천가보다 0.80% 올라 쫓아가지 않음"}) == "chase" and skip_code({"why": "?"}) == "etc" and skip_code({"code": "full", "why": "승률"}) == "full"
    # 자세히 화면과 AI 복기 자료에 실린다
    d = at.detail(rid)
    assert d["opp"]["picks"] == 7 and "items" not in d["opp"] and isinstance(d["opp"]["notes"], list)


def test_too_few_trades_fixes_money_shortage_first(env):
    """돈이 모자라 1주를 못 사는 경우: 결과를 기다리지 않고 종목 수를 줄여 한 번에 쓰는 돈을 키우자고 한다(거래가 0건이어도)."""
    at = env.at
    rid = env.run(budget=1_000_000)                                        # 3종목 · 33% · 검증 단계 절반 → 한 번에 16.5만
    with pytest.raises(ValueError):
        at.review(rid)                                                    # 추천이 3번도 안 쌓였으면 분석하지 않는다
    for i in range(3):
        if i == 2:
            env.day = "2026-11-19"
        assert _cyc(env, rid, 20 + i, [env.pick("035420"), env.pick("000660", win_prob=52)]) == 0     # 20만 원짜리는 못 삼
    pr = at.review(rid)
    ch = {c["key"]: c for c in pr["changes"]}
    assert pr["status"] == "pending" and ch["max_positions"]["to"] == 2 and ch["per_trade_pct"]["to"] == 50.0 and ch["max_positions"]["loosen"] is True
    assert "1주를 못 사" in ch["max_positions"]["why"] and "200,000원" in ch["max_positions"]["why"] and "min_win_prob" not in ch   # 승률 기준은 결과가 없어 건드리지 않는다
    assert pr["summary"].startswith("추천 6종목 중 0종목 매수") and any("판단하지 않아요" in n for n in pr["stats"]["notes"]) and pr["stats"]["opp"]["buy_rate"] == 0
    at.decide(pr["id"], "apply")
    p = at._run(rid)["params"]
    assert (p["max_positions"], p["per_trade_pct"]) == (2, 50.0)
    assert _cyc(env, rid, 30, [env.pick("035420")]) == 1                    # 이제 25만까지 살 수 있다
    # 더 줄일 수 없으면 '쓸 돈을 늘려야 한다'고만 알린다
    at.edit(rid, {"max_positions": 1})
    env.px["035420"] = 900_000.0
    for i in range(3):
        _cyc(env, rid, 40 + i, [env.pick("035420")])
    at.rt.db.x("DELETE FROM at_trades WHERE run_id=?", (rid,))
    for i in range(3):
        _cyc(env, rid, 50 + i, [env.pick("035420")])
    ch2, notes = at.opp_changes(at._run(rid), at.opportunity(at._run(rid)), [])
    assert ch2 == [] and any("쓸 돈을 늘려야" in n for n in notes)


def test_too_few_trades_loosens_only_with_evidence(env):
    """기준을 낮추는 것은 건너뛴 종목이 실제로 벌었을 때만. 잃었으면 유지하고, 결과가 모자라면 기다리고, 바닥 아래로는 안 내린다."""
    at = env.at
    rid = env.run(budget=3_000_000)
    run = lambda: at._run(rid)
    opp = lambda rows=(): at.opp_changes(run(), at.opportunity(run()), list(rows))

    def feed(base_id, n, nets, **kw):
        for i in range(n):
            env.day = f"2026-11-{18 + i % 3:02d}"
            _cyc(env, rid, base_id + i, [env.pick("000660", **kw)], [nets[i % len(nets)]])
    # 승률 52%라 건너뛴 9건이 그 뒤 꾸준히 벌었다 → 55 → 50
    feed(100, 9, [0.9, 0.6, -0.2], win_prob=52)
    ch, notes = opp()
    assert [(c["key"], c["from"], c["to"]) for c in ch] == [("min_win_prob", 55, 50)] and "9건은 평균 +0.43%" in ch[0]["why"] and "67%가 이익" in ch[0]["why"]
    assert any("추천 9종목 중 0종목만" in n for n in notes)
    # 잃고 있는 중이면 낮추지 않는다
    losing = [{"pnl": -1000.0, "pnl_pct": -1.0, "fees": 10, "mfe_pct": 0.2, "mae_pct": -1.2, "reason": "손절"}] * 8
    ch, notes = opp(losing)
    assert ch == [] and any("잃는 구간" in n for n in notes)
    # 같은 조건인데 건너뛴 종목이 잃었다 → 유지가 맞다고 알린다
    at.rt.db.x("UPDATE ai_outcomes SET net_pct=-0.7, result='손절'")
    ch, notes = opp()
    assert ch == [] and any("유지하는 게 맞아요" in n for n in notes)
    # 결과가 아직 모자라면 기다린다
    at.rt.db.x("DELETE FROM ai_outcomes WHERE pick_id>102")
    ch, notes = opp()
    assert ch == [] and any("결과가 나온 것이 3건이라 아직 판단하지 않아요" in n for n in notes)
    # 기준에서 멀리 떨어진 종목(승률 40%)의 결과는 근거로 쓰지 않는다
    at.rt.db.x("DELETE FROM at_log WHERE run_id=? AND kind='cycle'", (rid,))
    at.rt.db.x("DELETE FROM ai_outcomes")
    feed(200, 9, [1.5], win_prob=40)
    assert opp()[0] == []
    # 바닥: 처음 플랜보다 10%p 넘게는 내리지 않는다
    at.rt.db.x("DELETE FROM at_log WHERE run_id=? AND kind='cycle'", (rid,))
    at.edit(rid, {"min_win_prob": 45})
    feed(300, 9, [1.5], win_prob=44)
    ch, notes = opp()
    assert ch == [] and run()["params"]["min_win_prob"] == 45
    # 점수·손익비·추격·관망도 같은 방식(관망은 문턱이 더 높다)
    at.rt.db.x("DELETE FROM at_log WHERE run_id=? AND kind='cycle'", (rid,))
    at.edit(rid, {"min_win_prob": 55})
    feed(400, 9, [0.8], score=55)
    feed(420, 9, [0.8], target_pct=2.0, stop_pct=1.5)
    feed(440, 6, [0.8], entry_price=140000)
    keys = {c["key"]: c["to"] for c in opp()[0]}
    assert keys == {"min_score": 55, "min_rr": 1.3, "chase_pct": 0.8}
    at.rt.db.x("DELETE FROM at_log WHERE run_id=? AND kind='cycle'", (rid,))
    for i in range(12):
        env.day = f"2026-11-{18 + i % 3:02d}"
        _cyc(env, rid, 500 + i, [env.pick("000660")], [0.2], stance="관망")
    assert opp()[0] == [] and any("쉬는 게 맞았어요" in n for n in opp()[1])              # 조금 번 정도로는 관망을 풀지 않는다
    at.rt.db.x("UPDATE ai_outcomes SET net_pct=0.9 WHERE pick_id>=500")
    assert [(c["key"], c["to"]) for c in opp()[0]] == [("skip_watch", False)]
    # 충분히 사고 있으면 아무것도 낮추지 않는다
    at.rt.db.x("DELETE FROM at_log WHERE run_id=? AND kind='cycle'", (rid,))
    env.day = "2026-11-18"
    _cyc(env, rid, 600, [env.pick("005930"), env.pick("000660", win_prob=52)], [1.0, 1.0])
    _cyc(env, rid, 601, [env.pick("035420"), env.pick("000660", win_prob=52)], [1.0, 1.0])
    _cyc(env, rid, 602, [env.pick("000660", win_prob=52)], [1.0])
    ch, notes = opp()
    assert ch == [] and any("모자란 상태는 아니에요" in n for n in notes)


def test_daily_review_runs_by_days_and_paper_can_apply_itself(env, monkeypatch):
    """거래 건수와 상관없이 정해진 거래일마다(이틀째 한 건도 못 샀으면 바로) 스스로 분석한다. 모의에서 켜 두면 스스로 반영한다."""
    at = env.at
    rid = env.run(budget=1_000_000)
    notes = []
    monkeypatch.setattr(env.rt, "notify", lambda m: notes.append(m))
    close = lambda: (setattr(env, "open", False), setattr(env, "hm", "17:00"), at.tick(force=True), setattr(env, "open", True), setattr(env, "hm", "10:30"))
    props = lambda: at.rt.db.q("SELECT status, source, changes FROM at_proposals WHERE run_id=? ORDER BY id", (rid,))
    # 첫날: 두 번 추천받았지만 돈이 모자라 못 삼 → 아직 하루뿐이라 지켜본다
    _cyc(env, rid, 1, [env.pick("035420")])
    _cyc(env, rid, 2, [env.pick("035420")])
    close()
    assert props() == [] and at._run(rid)["last_review_day"] is None
    # 둘째 날도 0건 → 장 마감 뒤 바로 분석해 개선안을 낸다(승인 대기 + 알림)
    env.day = "2026-11-19"
    _cyc(env, rid, 3, [env.pick("035420")])
    close()
    ps = props()
    assert len(ps) == 1 and ps[0]["status"] == "pending" and "max_positions" in ps[0]["changes"] and at._run(rid)["last_review_day"] == "2026-11-19"
    assert any("개선안이 나왔어요" in m for m in notes) and at._run(rid)["params"]["max_positions"] == 3     # 승인 전에는 그대로
    close()
    assert len(props()) == 1                                                                              # 같은 날 두 번 하지 않는다
    # 모의에서 '스스로 반영'을 켜면 다음 점검부터 바로 반영한다(판이 올라가고 이력·알림이 남는다)
    assert at.edit(rid, {"auto_apply": True})["params"]["auto_apply"] is True
    for d in ("2026-11-20", "2026-11-23"):
        env.day = d
        _cyc(env, rid, 10 + int(d[-2:]), [env.pick("035420")])
        _cyc(env, rid, 40 + int(d[-2:]), [env.pick("035420")])
        close()
    run = at._run(rid)
    assert (run["params"]["max_positions"], run["params"]["per_trade_pct"], run["ver"]) == (2, 50.0, 2) and props()[-1]["status"] == "applied"
    assert any("스스로 반영했어요(모의)" in m for m in notes)
    assert at.rt.db.one("SELECT msg FROM at_log WHERE run_id=? AND msg LIKE '%스스로 반영%'", (rid,)) and at.changes(rid)[-1]["source"] == "rule"
    assert "스스로 반영해요" in at.detail(rid)["plan"][-1]["d"]
    # 정해진 거래일(기본 5일)마다도 돈다: 잘 사고 있으면 '지금 값 유지'로 남는다
    n0 = len(props())
    for i, d in enumerate(("2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27", "2026-11-30")):
        env.day = d
        _cyc(env, rid, 100 + i, [env.pick("005930")])
        at.rt.db.x("UPDATE at_trades SET status='closed', exit_day=?, pnl=100, pnl_pct=0.5 WHERE run_id=? AND status='open'", (d, rid))
        close()
    ps = props()
    assert len(ps) == n0 + 1 and ps[-1]["status"] == "none" and at._run(rid)["last_review_day"] == "2026-11-30"
    # 실전은 스스로 반영을 켤 수 없고, AI 개선안으로도 이 값은 못 바꾼다
    at.rt.db.x("UPDATE at_runs SET mode='real' WHERE id=?", (rid,))
    at.edit(rid, {"auto_apply": False})
    with pytest.raises(ValueError):
        at.edit(rid, {"auto_apply": True})
    at.rt.db.x("UPDATE at_runs SET params=json_set(params,'$.auto_apply',json('true')) WHERE id=?", (rid,))
    assert at._auto_apply(at._run(rid), {"id": 1, "status": "pending", "changes": [{}]}, "x") is False      # 실전이면 켜져 있어도 반영하지 않는다
    assert "auto_apply" not in PLAN_KEYS and "review_days" not in PLAN_KEYS
    assert at.edit(rid, {"review_days": 1})["params"]["review_days"] == 2


def test_ai_review_gets_opportunity_data(env):
    at = env.at
    rid = env.run()
    _seed(env, rid, 8)
    for i in range(3):
        _cyc(env, rid, 900 + i, [env.pick("000660", win_prob=52)], [0.5])
    _, ask, data = at.review_context({"run_id": rid})
    o = data["기회 분석"]
    assert o["추천 종목"] == 3 and o["산 종목"] == 0 and o["건너뛴 이유별(결과 = 그 뒤 실제로 어땠는지, 비용 뺀 수익률)"][0] == {"이유": "승률 기준 미달", "건수": 3, "결과 나온 건수": 3, "평균%": 0.5, "이익 비율%": 100}
    assert "기회 분석" in ask and "잃고 있는 중에는 기준을 낮추지 마" in ask and isinstance(o["앱의 판단"], list)


def test_api(env):
    c = create_app(env.rt).test_client()
    o = c.get("/api/auto").get_json()
    assert o["ready"]["ai"] and o["runs"] == [] and len(o["models"]) >= 3 and len(o["styles"]) == 3
    pv = c.post("/api/auto/plan", json={"budget": "3,000,000", "mkt": "kr", "style": "day", "days": 30}).get_json()
    assert pv["params"]["max_positions"] == 3 and pv["plan"] and pv["ai_cost"]["day_usd"] > 0
    assert c.post("/api/auto/plan", json={"budget": 10, "mkt": "kr"}).status_code == 400
    d = c.post("/api/auto/runs", json={"budget": 3_000_000, "mkt": "kr", "style": "day", "days": 30, "mode": "paper"}).get_json()
    rid = d["run"]["id"]
    assert c.get("/api/auto").get_json()["runs"][0]["id"] == rid
    assert c.post(f"/api/auto/runs/{rid}/control", json={"action": "pause"}).get_json()["run"]["status"] == "paused"
    assert c.post(f"/api/auto/runs/{rid}/control", json={"action": "resume"}).get_json()["run"]["status"] == "running"
    assert c.put(f"/api/auto/runs/{rid}/params", json={"total_loss_pct": 5}).get_json()["params"]["total_loss_pct"] == 5
    assert c.post(f"/api/auto/runs/{rid}/review").status_code == 400
    _seed(env, rid)
    r = c.post(f"/api/auto/runs/{rid}/review").get_json()
    pid = r["proposals"][0]["id"]
    r = c.post(f"/api/auto/proposals/{pid}", json={"action": "apply", "keys": ["min_win_prob"]}).get_json()
    assert r["params"]["min_win_prob"] == 60 and r["proposals"][0]["status"] == "applied"
    x = c.post(f"/api/auto/runs/{rid}/export")
    assert x.status_code == 200 and x.data[:3] == b"\xef\xbb\xbf"
    assert c.post(f"/api/auto/runs/{rid}/control", json={"action": "end"}).get_json()["run"]["status"] == "ended"
    assert c.get("/api/auto/runs/999").status_code == 400


def test_updater_and_restore_respect_autotrade(env):
    rt = env.rt
    assert rt.autotrade.active() is False and rt.autotrade.busy() is False
    env.run()
    assert rt.autotrade.active() is True
    ok, why = rt.backup.can_restore()
    assert ok is False and "자동매매" in why


def test_late_open_days_shift_the_schedule(env, monkeypatch):
    """수능일(개장·마감 1시간 늦음)·새해 첫 거래일(개장만 늦음): 장 열리기 전에 추천을 받지 않고, 시간표와 매수 시각을 그만큼 민다."""
    from datetime import date, datetime as dt
    assert [AP.suneung(y).isoformat() for y in (2021, 2022, 2023, 2024, 2025, 2026)] == ["2021-11-18", "2022-11-17", "2023-11-16", "2024-11-14", "2025-11-13", "2026-11-19"]
    assert AP.kr_shift(date(2026, 11, 19)) == (60, 60) and AP.kr_shift(date(2026, 11, 18)) == (0, 0) and AP.kr_shift(date(2026, 11, 12)) == (0, 0)
    assert AP.kr_shift(date(2027, 1, 4)) == (60, 0) and AP.kr_shift(date(2027, 1, 5)) == (0, 0) and AP.kr_shift(date(2026, 1, 2)) == (60, 0)
    assert "10:00~16:30" in AP.kr_note(date(2026, 11, 19)) and "10:00~15:30" in AP.kr_note(date(2027, 1, 4)) and AP.kr_note(date(2026, 11, 18)) == ""
    assert AP.hm_add("09:10", 60) == "10:10" and AP.hm_add("15:20", 0) == "15:20"
    # 추천 평가 창과 장 구간도 그날 시간에 맞춘다
    assert AP.phase_at("kr", dt(2026, 11, 19, 9, 30))[0] == "pre" and AP.phase_at("kr", dt(2026, 11, 19, 16, 0))[0] == "open" and AP.phase_at("kr", dt(2026, 11, 18, 9, 30))[0] == "open"
    assert AP.window_end("kr", dt(2026, 11, 19, 10, 30)) == dt(2026, 11, 19, 16, 20) and AP.window_end("kr", dt(2026, 11, 18, 10, 30)) == dt(2026, 11, 18, 15, 20)
    assert AP.window_end("kr", dt(2026, 11, 18, 15, 25)) == dt(2026, 11, 19, 16, 20) and AP.window_end("kr", dt(2026, 11, 19, 10, 30), "14:50") == dt(2026, 11, 19, 14, 50)
    monkeypatch.setattr(AP, "kst_now", lambda: dt(2026, 11, 19, 9, 11))
    ss = AP.session("kr")
    assert ss["open"] is False and "수능일" in ss["note"] and ss["close_kst"] == "16:20"
    monkeypatch.setattr(AP, "kst_now", lambda: dt(2026, 11, 19, 16, 15))
    assert AP.session("kr")["open"] is True and AP.session("kr")["left_min"] == 5
    monkeypatch.setattr(AP, "kst_now", lambda: dt(2026, 11, 18, 9, 11))
    assert AP.session("kr")["open"] is True and "note" not in AP.session("kr")
    # 자동매매: 수능일 09:30(평소라면 매수 시간)에는 막고, 10:10부터 받는다
    at = env.at
    env.day = "2026-11-19"
    rid = env.run()
    run = at._run(rid)
    assert at._hours("kr", date(2026, 11, 19)) == ("10:00", "16:20") and at._entry(run, date(2026, 11, 19)) == ("10:10", "15:30") and at._entry(run, date(2026, 11, 20)) == ("09:10", "14:30")
    env.hm = "10:05"
    assert at.gate_code(run)[1] == "time" and "10:10~15:30" in at.gate_code(run)[2]
    env.hm = "10:10"
    assert at.gate_code(run)[0] is True
    env.hm = "15:20"
    assert at.gate_code(run)[0] is True
    n = at.now(run)
    rows = {r["name"]: r["hm"] for r in n["steps"]["rows"]}
    assert rows == {"장 시작": "10:00", "매수 시작": "10:10", "매수 끝": "15:30", "전부 정리": "16:12", "장 마감": "16:20"} and "수능일" in n["steps"]["note"]
    env.day, env.hm = "2026-11-20", "09:30"
    n = at.now(at._run(rid))
    assert {r["name"]: r["hm"] for r in n["steps"]["rows"]}["매수 시작"] == "09:10" and n["steps"]["note"] == ""


def test_scan_progress_while_asking_ai(env, monkeypatch):
    """AI 추천을 받는 동안 어디까지 왔는지(자료 모으기 → AI 분석 → 추천 정리 → 매수 판단)를 화면에 줄 수 있다."""
    import threading
    at, rid = env.at, env.run()
    gate, reached = [threading.Event() for _ in range(4)], [threading.Event() for _ in range(4)]

    def stream(kind, d):
        def stop(i):
            reached[i].set()
            assert gate[i].wait(5)
        stop(0)                                                  # 자료를 모으는 동안(아직 아무 이벤트도 없음)
        yield {"t": "status", "msg": "자료를 정리해서 Claude에게 보내는 중"}
        yield {"t": "status", "msg": "웹에서 찾는 중"}
        yield {"t": "source", "url": "https://example.com/a", "title": "반도체 수출 동향"}
        yield {"t": "text", "text": "확인해 볼게요."}
        stop(1)
        yield {"t": "text", "text": "가" * 200}
        stop(2)
        yield {"t": "picks", "id": 9, "stance": "보통", "view": "v", "picks": [env.pick(), env.pick("000660", win_prob=40)]}
        stop(3)
        yield {"t": "done", "usd": 0.05}
    monkeypatch.setattr(env.rt.ai, "stream", stream)
    monkeypatch.setattr(env.rt.ai, "configured", lambda: True)
    assert at.scan(rid) is None and at.now(at._run(rid))["scan"] is None
    at.tick(force=True)
    assert reached[0].wait(5)
    n = at.now(at._run(rid))
    assert n["busy"] is True and n["scan"]["step"] == 0 and n["scan"]["steps"] == ["시장 자료 모으기", "AI 분석", "추천 정리", "매수 판단"] and "모으는 중" in n["scan"]["msg"]
    assert n["scan"]["elapsed"] >= 0 and n["next"]["kind"] == "busy"
    gate[0].set()
    assert reached[1].wait(5)
    s = at.scan(rid)
    assert (s["step"], s["search"], s["sources"], s["src"]) == (1, 1, 1, "반도체 수출 동향") and s["chars"] == 8 and "읽는 중" in s["msg"]
    gate[1].set()
    assert reached[2].wait(5)
    s = at.scan(rid)
    assert s["step"] == 2 and s["chars"] == 208 and "쓰는 중" in s["msg"]
    gate[2].set()
    assert reached[3].wait(5)
    s = at.scan(rid)
    assert s["step"] == 3 and s["picks"] == 2 and "2종목" in s["msg"]
    assert at.brief()["runs"][0]["now"]["scan"]["step"] == 3            # 홈 카드에도 실린다
    gate[3].set()
    at._busy[rid].join(5)
    assert at.scan(rid) is None and rid not in at._prog and at.now(at._run(rid))["scan"] is None and len(at._open(rid)) == 1
    # 간격을 기다리는 동안은 남은 초와 전체 간격을 준다(화면의 초 단위 카운트다운·진행 막대)
    nx = at.now(at._run(rid))["next"]
    assert nx["kind"] == "at" and nx["span_sec"] == 3600 and 3500 < nx["in_sec"] <= 3600


def test_budget_can_be_changed_while_running(env):
    """돌고 있는 중에 쓸 돈을 늘리고 줄인다. 손익(원)은 그대로, 1회 매수 금액·손실 한도·수익률은 새 금액 기준. 줄이는 데는 바닥이 있다."""
    at = env.at
    rid = env.run(budget=900_000)                                   # 100만 미만: 2종목 · 33%
    run = lambda: at._run(rid)
    assert (run()["params"]["max_positions"], run()["params"]["per_trade_pct"]) == (2, 33.0)
    env.enter(rid, [env.pick("005930")])                            # 검증 단계: 90만 × 33% × 50% = 148,500 → 2주(141,000쯤)
    cost = at.money(run())["open_cost"]
    assert cost > 0
    # 미리 보기(dry): 바꾸지 않고 바뀐 뒤 모습만
    pv = at.budget(rid, {"budget": "1,500,000", "dry": True})
    assert pv["error"] is None and pv["diff"] == 600_000 and pv["cash"] == [900_000 - cost, 1_500_000 - cost] and pv["per_trade"] == [148_500.0, 247_500.0]
    assert pv["total_limit"] == [72_000.0, 120_000.0] and pv["day_limit"] == [18_000.0, 30_000.0] and pv["warmup"] is True
    assert pv["fit"] == {"max_positions": [2, 3], "per_trade_pct": [33.0, 33.0]} and run()["budget"] == 900_000
    # 늘리기 + 종목 수를 새 금액대에 맞추기
    d = at.budget(rid, {"budget": 1_500_000, "fit": True})
    assert d["run"]["money"]["budget"] == 1_500_000 and run()["params"]["max_positions"] == 3 and run()["ver"] == 2
    assert d["now"]["size"]["alloc"] == 247_500.0 and d["now"]["risk"]["limit"] == 120_000.0
    assert "쓸 돈 900,000원 → 1,500,000원(+600,000원)" in [x["msg"] for x in d["log"]]
    # 손익은 그대로, 수익률은 새 금액 기준
    env.px["005930"] = 71_500.0
    at.tick(force=True)
    m = at.money(run(), live=True)
    assert m["unrealized"] > 0 and m["equity"] == 1_500_000 + m["unrealized"] and m["ret_pct"] == round(m["unrealized"] / 1_500_000 * 100, 2)
    # 줄이기: 주식에 들어간 돈·최소 금액 아래로는 안 된다
    pv = at.budget(rid, {"budget": 100_000, "dry": True})
    assert pv["min"] == 300_000 and "최소 금액" in pv["error"]
    with pytest.raises(ValueError, match="최소 금액"):
        at.budget(rid, {"budget": 100_000})
    with pytest.raises(ValueError, match="같은 금액"):
        at.budget(rid, {"budget": 1_500_000})
    d = at.budget(rid, {"budget": 300_000})
    assert d["run"]["money"]["cash"] == 300_000 - cost and at.money(run())["open_cost"] == cost
    # 지금 손실이 새 손실 한도를 넘게 줄이는 것은 막는다(바로 종료되지 않게)
    at.budget(rid, {"budget": 2_000_000})
    env.px["005930"] = 40_000.0                                      # 크게 물린 상태(손절은 다음 tick 전)
    at.rt.db.x("UPDATE at_trades SET last=40000 WHERE run_id=? AND status='open'", (rid,))
    pv = at.budget(rid, {"budget": 400_000, "dry": True})
    loss = -at.money(run())["unrealized"]
    assert loss > 400_000 * 0.08 and "바로 종료" in pv["error"] and pv["min"] >= loss / 0.08
    with pytest.raises(ValueError, match="바로 종료"):
        at.budget(rid, {"budget": 400_000})
    # 실전: 늘릴 때는 '실전' 확인이 필요하고, 줄일 때는 필요 없다
    at.rt.db.x("UPDATE at_trades SET last=70500 WHERE run_id=? AND status='open'", (rid,))
    at.rt.db.x("UPDATE at_runs SET mode='real' WHERE id=?", (rid,))
    assert any("실제 주문" in n for n in at.budget(rid, {"budget": 2_500_000, "dry": True})["notes"])
    with pytest.raises(ValueError, match="실전"):
        at.budget(rid, {"budget": 2_500_000})
    assert at.budget(rid, {"budget": 2_500_000, "confirm": "실전"})["run"]["money"]["budget"] == 2_500_000
    assert at.budget(rid, {"budget": 1_800_000})["run"]["money"]["budget"] == 1_800_000
    # 끝난 뒤에는 바꿀 수 없다
    at.rt.db.x("UPDATE at_runs SET status='ended' WHERE id=?", (rid,))
    with pytest.raises(ValueError, match="바꿀 수 없어요"):
        at.budget(rid, {"budget": 2_000_000})


def test_same_setup_runs_share_one_recommendation_and_cost_limit_is_clear(env, monkeypatch):
    """모의와 실전처럼 조건이 같은 자동매매는 추천을 한 번만 받아 함께 쓴다. 비용 한도에 걸리면 그 이유를 말하고, 한도는 직접 올릴 수 있다."""
    at = env.at
    a = env.run()
    at.rt.db.x("UPDATE at_runs SET mode='real' WHERE id=?", (a,))       # 같은 시장에 모의 하나 더 만들 수 있게
    b = env.run()
    at.rt.db.x("UPDATE at_runs SET mode='paper' WHERE id=?", (a,))
    calls = []

    def stream(kind, d):
        calls.append(d)
        yield {"t": "picks", "id": 31, "stance": "보통", "view": "v", "picks": [env.pick()]}
        yield {"t": "done", "usd": 0.8}
    monkeypatch.setattr(env.rt.ai, "stream", stream)
    monkeypatch.setattr(env.rt.ai, "configured", lambda: True)
    at.tick(force=True)
    for th in list(at._busy.values()):
        th.join(5)
    assert len(calls) == 1 and len(at._open(a)) == 1 and len(at._open(b)) == 1
    usd = sorted(json.loads(r["data"])["usd"] for r in at.rt.db.q("SELECT data FROM at_log WHERE kind='cycle' AND data IS NOT NULL"))
    assert usd == [0.0, 0.8]                                           # 비용은 받은 쪽에만
    # 한도($1)를 넘긴 쪽: 이유를 분명히 말한다(오늘이 마지막 날이어도 '기간' 탓으로 돌리지 않는다)
    paid = a if json.loads(at.rt.db.one("SELECT data FROM at_log WHERE run_id=? AND kind='cycle' AND data IS NOT NULL", (a,))["data"])["usd"] else b
    at.rt.db.x("UPDATE at_log SET data=json_set(data,'$.usd',1.4) WHERE run_id=? AND kind='cycle'", (paid,))
    at.rt.db.x("UPDATE at_runs SET last_cycle=0, end_date=? WHERE id=?", (env.day, paid))
    at.rt.db.x("UPDATE at_trades SET status='closed', exit_day=?, pnl=0, pnl_pct=0 WHERE run_id=?", (env.day, paid))
    n = at.now(at._run(paid))["next"]
    assert n["kind"] == "hold" and n["code"] == "aicost" and "비용 한도($1)" in n["label"] and "마지막 날" in n["label"] and "기간이 끝나 가요" not in n["label"]
    at.rt.db.x("UPDATE at_runs SET end_date='2026-12-31' WHERE id=?", (paid,))
    n = at.now(at._run(paid))["next"]
    assert n["kind"] == "at" and n["code"] == "aicost" and "한도를 올리면" in n["label"]
    # 한도를 올리면 바로 다시 받는다. AI 개선안으로는 못 바꾸는 값이다
    assert at.edit(paid, {"ai_daily_usd": "3"})["params"]["ai_daily_usd"] == 3.0 and "ai_daily_usd" not in PLAN_KEYS
    assert at.gate_code(at._run(paid))[1] != "aicost" and at.edit(paid, {"ai_daily_usd": 999})["params"]["ai_daily_usd"] == 20.0


def test_free_precheck_watch_backoff_and_paper_buys_small_on_watch(env, monkeypatch):
    """돈을 내고 AI에게 묻기 전에 앱이 먼저 장을 본다. 관망 뒤에는 회복될 때까지 다시 묻지 않는다. 모의는 관망에도 작게 산다."""
    at = env.at
    rid = env.run(new_defaults=True)
    run = lambda: at._run(rid)
    p = run()["params"]
    assert p["skip_watch"] is False and p["watch_mult"] == 0.5 and p["idx_floor"] == 0      # 모의는 약한 장도 시험한다(거르지 않는다)
    assert make_plan(3_000_000, "kr", "day", 30)["skip_watch"] is True and make_plan(3_000_000, "kr", "swing", 30)["idx_floor"] == -1.5
    env.idx = -1.46
    assert at.gate_code(run())[0] is True
    at.edit(rid, {"idx_floor": -1.0})                                    # 아래는 실전 기본값(-1%)에서의 동작
    calls = []

    def stream(kind, d):
        calls.append(1)
        yield {"t": "picks", "id": 70 + len(calls), "stance": "관망", "view": "지수 약세", "picks": [env.pick("005930" if len(calls) == 1 else "000660")]}
        yield {"t": "done", "usd": 0.2}
    monkeypatch.setattr(env.rt.ai, "stream", stream)
    # 1) 지수가 기준(-1%)보다 더 내렸으면 AI를 부르지 않는다(비용 0). 기록은 하루 한 번
    env.idx = -1.46
    assert at.gate_code(run())[1] == "market" and "비용 없음" in at.gate_code(run())[2]
    at.tick(force=True)
    at.tick(force=True)
    assert calls == [] and [x["msg"] for x in at.rt.db.q("SELECT msg FROM at_log WHERE run_id=? AND kind='skip'", (rid,))] == ["지수가 -1.46%로 약해 AI를 부르지 않고 쉬어요 · 비용 없음"]
    n = at.now(run())["next"]
    assert n["kind"] == "wait" and n["code"] == "market"
    # 2) 지수가 버티면 부른다. 관망이어도 모의는 절반 크기로 산다(검증 단계 50% × 관망 50%)
    env.idx = -0.4
    at.tick(force=True)
    at._busy[rid].join(5)
    t = at._open(rid)
    assert len(calls) == 1 and len(t) == 1 and t[0]["qty"] == int(3_000_000 * 0.33 * 0.5 * 0.5 // (70000 * 1.002 * 1.0005))
    d = json.loads(at.rt.db.one("SELECT data FROM at_log WHERE run_id=? AND kind='cycle' AND data IS NOT NULL", (rid,))["data"])
    assert d["stance"] == "관망" and d["idx"] == -0.4 and d["mult"] == 0.25
    # 3) 관망 뒤: 간격(60분)이 지나도 지수가 그대로면 다시 묻지 않는다. 0.3%p 넘게 회복되거나 간격의 두 배가 지나면 묻는다
    at.rt.db.x("UPDATE at_runs SET last_cycle=? WHERE id=?", (time.time() - 61 * 60, rid))
    assert at.gate_code(run())[1] == "watchwait" and "120분" in at.gate_code(run())[2]
    at.tick(force=True)
    assert len(calls) == 1
    env.idx = -0.05
    assert at.gate_code(run())[0] is True
    env.idx = -0.4
    at.rt.db.x("UPDATE at_runs SET last_cycle=? WHERE id=?", (time.time() - 121 * 60, rid))
    assert at.gate_code(run())[0] is True
    # 4) 기준은 플랜에서 끌 수 있다(0 = 끔)
    env.idx = -3.0
    assert at.gate_code(run())[1] == "market"
    at.edit(rid, {"idx_floor": 0})
    assert at.gate_code(run())[1] != "market"


def test_ai_cost_fits_the_budget_and_results_count_ai_cost(env, monkeypatch):
    """AI 비용을 쓸 돈에 맞춘다(실전: 월 1% 이내). 넘으면 숨기지 않고 알린다. 손익은 AI 비용을 뺀 값도 보여 준다."""
    at = env.at
    per = {"claude-fable-5-2": 0.8, "claude-opus-5-5": 0.4, "claude-sonnet-5-5": 0.2, "claude-haiku-4-5": 0.1}
    monkeypatch.setattr(at, "_per_usd", lambda style, model, web: (per[model] * (1 if web else 0.6), {"fx": 1400.0}))
    monkeypatch.setattr(env.rt.ai, "conf", lambda: {"model": "claude-fable-5-2", "web_search": True})
    # 실전 60만: 월 1% = 6,000원 = $4.29 → 하루 3번을 지키면서 맞는 조합은 가장 싼 모델 · 웹 검색 없이($3.78)
    pv = at.preview({"budget": 600_000, "mkt": "kr", "style": "day", "days": 20, "mode": "real", "model": "auto"})
    c = pv["cost"]
    assert (pv["model"], pv["params"]["cycles_per_day"], pv["params"]["web_search"]) == ("claude-haiku-4-5", 3, False) and c["ok"] is True and c["auto"] is True
    assert c["month_usd"] == 3.78 and c["pct"] == 0.88 and c["warn"] is None and pv["params"]["cycle_gap_min"] == 60 and pv["params"]["ai_daily_usd"] == 0.3
    # 어떤 조합으로도 못 맞추면 가장 싼 조합으로 하고 숨기지 않고 알린다
    monkeypatch.setitem(per, "claude-haiku-4-5", 0.5)
    monkeypatch.setitem(per, "claude-sonnet-5-5", 0.6)
    pv2 = at.preview({"budget": 600_000, "mkt": "kr", "style": "day", "days": 20, "mode": "real", "model": "auto"})
    assert (pv2["model"], pv2["params"]["cycles_per_day"], pv2["params"]["web_search"]) == ("claude-haiku-4-5", 1, False) and pv2["cost"]["ok"] is False and "본전" in pv2["cost"]["warn"]
    monkeypatch.setitem(per, "claude-haiku-4-5", 0.1)
    monkeypatch.setitem(per, "claude-sonnet-5-5", 0.2)
    assert pv["ready"]["ok"] is False and "아직 실전을 권하지 않아요" in pv["ready"]["text"] and pv["params"]["skip_watch"] is True
    # 실전 3,000만: $214까지 → 가장 좋은 모델로 3번이 들어온다
    pv = at.preview({"budget": 30_000_000, "mkt": "kr", "style": "day", "days": 20, "mode": "real", "model": "auto"})
    assert (pv["model"], pv["params"]["cycles_per_day"], pv["cost"]["ok"], pv["cost"]["warn"]) == ("claude-fable-5-2", 3, True, None) and pv["params"]["ai_daily_usd"] == 3.4
    # 실전 500만: $35.7 → 하루 3번을 지키는 가장 좋은 모델(Opus $25.2)
    pv = at.preview({"budget": 5_000_000, "mkt": "kr", "style": "day", "days": 20, "mode": "real", "model": "auto"})
    assert (pv["model"], pv["params"]["cycles_per_day"], pv["params"]["web_search"]) == ("claude-opus-5-5", 3, True)
    # 모의: 하루 $0.6 목표 → Sonnet 3번
    pv = at.preview({"budget": 1_000_000, "mkt": "kr", "style": "day", "days": 20, "mode": "paper", "model": "auto"})
    assert (pv["model"], pv["params"]["cycles_per_day"], pv["cost"]["ok"]) == ("claude-sonnet-5-5", 3, True) and pv["ready"] is None
    # 모델을 직접 고르면 바꾸지 않고 비용만 알려 준다(한도는 횟수에 맞춘다)
    pv = at.preview({"budget": 600_000, "mkt": "kr", "style": "day", "days": 20, "mode": "real", "model": "claude-fable-5-2"})
    assert pv["model"] == "claude-fable-5-2" and pv["params"]["cycles_per_day"] == 3 and pv["cost"]["auto"] is False and pv["cost"]["pct"] == 11.76 and pv["params"]["ai_daily_usd"] == 3.4
    # 손익에 AI 비용을 뺀 값이 함께 나온다
    rid = env.run()
    at.log(rid, "cycle", "x", {"usd": 0.5})
    at.log(rid, "cycle", "y", {"usd": 0.25})
    m = at.detail(rid)["run"]["money"]
    assert (m["ai_usd"], m["ai_cost"], m["net_pnl"], m["net_ret_pct"], m["ret_pct"]) == (0.75, 1050.0, -1050.0, -0.03, 0.0)
    # 실전 준비도: 한 플랜에서 30건 이상 + AI 비용 빼고 플러스
    _seed(env, rid, 30)
    R = at.readiness("kr", "day")
    pnl = at.rt.db.one("SELECT SUM(pnl) s FROM at_trades WHERE run_id=?", (rid,))["s"]
    assert R["best"]["n"] == 30 and R["ok"] is (pnl - 1050 > 0) and R["best"]["net"] == round(pnl - 1050, 2)


def test_lab_runs_many_plans_on_one_recommendation(env, monkeypatch):
    """모의 실험실: 플랜만 다른 모의 여러 개가 같은 추천을 한 번만 받아 각자 기준으로 사고, 성적표로 비교한다."""
    at = env.at
    solo = env.run()                                                    # 보통 모의가 돌고 있어도 실험은 따로 시작할 수 있다
    monkeypatch.setattr(at, "_per_usd", lambda style, model, web: (0.9, {"fx": 1400.0}))
    with pytest.raises(ValueError, match="두 개 이상"):
        at.lab_create({"budget": 1_000_000, "mkt": "kr", "style": "day", "days": 20, "variants": ["base"]})
    L = at.lab_create({"budget": 3_000_000, "mkt": "kr", "style": "day", "days": 20, "model": "claude-sonnet-5-5"})["labs"][0]
    assert len(L["runs"]) == 9 and L["status"] == "running" and {x["variant"] for x in L["runs"]} == set(VARIANTS)
    with pytest.raises(ValueError, match="이미 돌고"):
        at.lab_create({"budget": 1_000_000, "mkt": "kr", "style": "day", "days": 20})
    ids = {x["variant"]: x["id"] for x in L["runs"]}
    lab = L["id"]
    P = lambda k: at._run(ids[k])["params"]
    assert lab == ids["base"] and P("loose")["min_win_prob"] == 50 and P("strict")["min_score"] == 70 and P("watch_skip")["skip_watch"] is True
    assert (P("tight")["tp_cap"], P("tight")["sl_cap"], P("wide")["tp_cap"]) == (2.0, 1.0, 6.0) and (P("focus")["max_positions"], P("spread")["per_trade_pct"]) == (1, 20)
    at.rt.db.x("UPDATE at_runs SET status='paused' WHERE id=?", (solo,))
    calls = []

    def stream(kind, d):
        calls.append(d)
        yield {"t": "picks", "id": 80 + len(calls), "stance": "관망" if len(calls) == 1 else "보통", "view": "v",
               "picks": [env.pick("005930", win_prob=57, score=65), env.pick("000660", win_prob=52, score=55, target_pct=2.0, stop_pct=1.5)]}
        yield {"t": "done", "usd": 0.9}
    monkeypatch.setattr(env.rt.ai, "stream", stream)
    at.tick(force=True)
    th = at._busy[lab]
    assert all(at._busy[i] is th for i in ids.values()) and at.now(at._run(ids["spread"]))["busy"] is True     # 모두 같은 진행 상태를 본다
    th.join(5)
    assert len(calls) == 1                                               # 추천은 한 번만
    held = {k: sorted(t["sym"] for t in at._open(i)) for k, i in ids.items()}
    assert held["base"] == ["005930"] and held["loose"] == ["000660", "005930"] and held["strict"] == [] and held["watch_skip"] == []
    assert held["watch_full"] == ["005930"] and at._open(ids["watch_full"])[0]["qty"] >= 2 * at._open(ids["base"])[0]["qty"]   # 관망 무시는 두 배 크기
    usd = [json.loads(r["data"])["usd"] for r in at.rt.db.q("SELECT data FROM at_log WHERE kind='cycle' AND data IS NOT NULL AND run_id!=?", (solo,))]
    assert len(usd) == 9 and all(u == 0.1 for u in usd)                  # 비용은 똑같이 나눠 적는다
    # 다음 추천도 묶음 전체에서 한 번: 한 종목 집중은 자리가 없어 건너뛰고, 나머지는 각자 판단
    at.rt.db.x("UPDATE at_runs SET last_cycle=? WHERE lab_id=?", (time.time() - 130 * 60, lab))
    at.tick(force=True)
    at._busy[lab].join(5)
    assert len(calls) == 2 and len(at._open(ids["focus"])) == 1 and sorted(t["sym"] for t in at._open(ids["watch_skip"])) == ["005930"]
    # 맨 앞 실험이 끝나도 다음 실험이 이어받는다
    at.control(ids["base"], "end")
    at._share.clear()                                                   # (10분 안에 받은 같은 조건의 추천은 다시 받지 않고 함께 쓴다)
    at.rt.db.x("UPDATE at_runs SET last_cycle=? WHERE lab_id=?", (time.time() - 61 * 60, lab))
    at.tick(force=True)
    nxt = min(i for k, i in ids.items() if k != "base")
    at._busy[nxt].join(5)
    assert len(calls) == 3
    # 성적표: 끝난 거래가 있는 것부터, AI 비용 뺀 수익률 순. 10건 전에는 순위를 말하지 않는다
    L = at.labs()["labs"][0]
    assert L["cycles"] >= 2 and L["usd"] == round(sum(x["ai_usd"] for x in L["runs"]), 2) and "비교하기 일러요" in L["note"] and L["lead"] is None
    _seed(env, ids["loose"], 12)
    L = at.labs()["labs"][0]
    assert L["runs"][0]["variant"] == "loose" and L["lead"] == "느슨한 기준" and "30건이 쌓여야" in L["note"] and L["runs"][0]["net_ret_pct"] < L["runs"][0]["ret_pct"]
    # 홈 요약과 목록에서는 묶음 하나로 보인다
    b = at.brief()
    assert [r["id"] for r in b["runs"]] == [solo] and b["labs"][0]["n"] == 9 and b["labs"][0]["lead"] == "느슨한 기준"
    assert [r["id"] for r in at.overview()["runs"]] == [solo]
    # 한꺼번에 멈추고 끝낸다
    assert at.lab_control(lab, "pause")["labs"][0]["status"] == "paused"
    assert at.lab_control(lab, "end")["labs"][0]["status"] in ("ended", "paused", "running")


def test_autopilot_only_needs_on_off_and_feeds_a_real_plan(env, monkeypatch):
    """모의 자동 테스트: 켜면 시장·방식별 실험을 앱이 만들고, 끄면 멈춘다. 결과가 쌓이면 실전 후보 플랜을 알려 준다."""
    from moa.autotrade import AUTO_SET
    at = env.at
    monkeypatch.setattr(at, "_per_usd", lambda style, model, web: ({"claude-sonnet-5-5": 0.2, "claude-haiku-4-5": 0.1}.get(model, 0.8) * (1 if web else 0.6), {"fx": 1400.0}))
    S = at.auto_state()
    assert S["on"] is False and S["day_cap_usd"] == 1.0 and len(S["advice"]) == 4 and at.labs()["labs"] == []
    at.tick(force=True)
    assert at.labs()["labs"] == []                                       # 꺼져 있으면 아무것도 만들지 않는다
    S = at.auto_set(True)
    L = at.labs()["labs"]
    assert S["on"] is True and sorted((x["mkt"], x["style"]) for x in L) == sorted(AUTO_SET) and all(len(x["runs"]) == 9 for x in L)
    kd = at._run(next(x["id"] for x in L if (x["mkt"], x["style"]) == ("kr", "day")))
    ks = at._run(next(x["id"] for x in L if (x["mkt"], x["style"]) == ("kr", "swing")))
    # 비용은 앱이 정한다: 단타 하루 $0.35 → Haiku 3번, 스윙 $0.15 → Haiku 1번
    assert (kd["model"], kd["params"]["cycles_per_day"]) == ("claude-haiku-4-5", 3) and (ks["model"], ks["params"]["cycles_per_day"]) == ("claude-haiku-4-5", 1)
    at._auto_at = 0
    at.tick(force=True)
    assert len(at.labs()["labs"]) == 4                                   # 이미 있으면 또 만들지 않는다
    # 끄면 새 매수를 멈추고, 켜면 이어 간다
    assert at.auto_set(False)["on"] is False and all(x["status"] == "paused" for x in at.labs()["labs"])
    assert at.auto_set(True)["on"] is True and all(x["status"] == "running" for x in at.labs()["labs"])
    # 한 묶음이 끝나면 다시 만든다
    at.lab_control(kd["id"], "end")
    at._auto_at = 0
    at.tick(force=True)
    assert len([x for x in at.labs()["labs"] if (x["mkt"], x["style"]) == ("kr", "day") and x["status"] == "running"]) == 1
    # 실전 후보: (시장, 방식, 플랜)별로 모든 실험의 거래를 합쳐 30건 이상 + AI 비용 빼고 플러스
    A = {(a["mkt"], a["style"]): a for a in at.advice()}
    assert A[("kr", "day")]["best"] is None and "모으는 중" in A[("kr", "day")]["text"]
    rid = at.rt.db.one("SELECT id FROM at_runs WHERE lab_id IS NOT NULL AND mkt='kr' AND style='day' AND variant='loose' AND status='running'")["id"]
    _seed(env, rid, 40)
    at.rt.db.x("UPDATE at_trades SET pnl=ABS(pnl)+100, pnl_pct=ABS(pnl_pct)+0.1, entry_day='2026-11-18' WHERE run_id=?", (rid,))
    one = {(a["mkt"], a["style"]): a for a in at.advice()}[("kr", "day")]
    assert one["best"] is None and "1/10거래일" in one["text"]            # 하루에 몰린 40건은 후보로 치지 않는다
    at.rt.db.x("UPDATE at_trades SET entry_day=printf('2026-11-%02d', 2 + id % 12) WHERE run_id=?", (rid,))
    b = {(a["mkt"], a["style"]): a for a in at.advice()}[("kr", "day")]["best"]
    assert b["variant"] == "loose" and b["n"] == 40 and b["net_pct"] > 0 and b["win_rate"] == 100
    # 그 플랜으로 실전 미리보기: 기준은 가져오되, 관망에 쉬는 것은 실전 기본을 지킨다
    pv = at.preview({"budget": 5_000_000, "mkt": "kr", "style": "day", "days": 30, "mode": "real", "model": "auto", "variant": "loose"})
    assert pv["variant"] == {"key": "loose", "label": "느슨한 기준"} and (pv["params"]["min_win_prob"], pv["params"]["min_rr"], pv["params"]["skip_watch"]) == (50, 1.2, True)
    pv = at.preview({"budget": 5_000_000, "mkt": "kr", "style": "day", "days": 30, "mode": "real", "model": "auto", "variant": "watch_full"})
    assert pv["params"]["skip_watch"] is True and pv["ready"]["ok"] is True
