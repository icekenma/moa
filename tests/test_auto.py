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
        if not new:      # 예전 테스트는 '관망이면 쉰다 · 하루 3번 · $1 · 배정 금액으로 1주를 못 사면 건너뛴다'를 전제로 쓰였다
            self.rt.db.x("UPDATE at_runs SET params=json_set(params,'$.skip_watch',json('true'),'$.cycles_per_day',3,'$.cycle_gap_min',60,'$.ai_daily_usd',1.0,'$.web_search',json('true'),'$.min_one',json('false'),'$.quick_gap_min',0) WHERE id=?", (rid,))
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
    assert n["today"] == {"cycles": 0, "cycles_max": 3, "full": 0, "quick": 0, "quick_gap": 0, "buys": 0, "sells": 0, "wins": 0, "pnl": 0, "unreal": 0, "limit": 20000.0, "streak": 0, "streak_max": 3}
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
    assert at.gate_code(at._run(paid))[1] != "aicost" and at.edit(paid, {"ai_daily_usd": 999})["params"]["ai_daily_usd"] == 200.0


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
    at.edit(rid, {"idx_floor": -1.0, "quick_gap_min": 0, "cycles_per_day": 3, "cycle_gap_min": 60})   # 아래는 실전 기본값(-1%, 빠른 재추천 없음)에서의 동작
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
    # 하루 한도 = 시드에 비례(월 1%)하되 최소 $3. 실전 60만은 월 1%가 $4.29뿐이라 최소 $3/일 → 가장 좋은 모델 3번 · 웹 검색($2.4/일)
    assert at.day_cap(600_000, "kr") == 3.0 and at.day_cap(30_000_000, "kr") == 10.2 and at.day_cap(100_000, "us") == 47.62
    pv = at.preview({"budget": 600_000, "mkt": "kr", "style": "day", "days": 20, "mode": "real", "model": "auto"})
    c = pv["cost"]
    assert (pv["model"], pv["params"]["cycles_per_day"], pv["params"]["web_search"]) == ("claude-fable-5-2", 3, True) and c["ok"] is True and c["auto"] is True
    assert c["month_usd"] == 50.4 and c["pct"] == 11.76 and c["cap_day"] == 3.0 and pv["params"]["ai_daily_usd"] == 3.0
    assert "최소 $3" in c["warn"] and "본전" in c["warn"]                  # 시드 대비 비중이 크면 숨기지 않고 알린다
    # 어떤 조합으로도 못 맞추면 가장 싼 조합으로 하고 알린다
    for k in per:
        monkeypatch.setitem(per, k, 6.0)
    pv2 = at.preview({"budget": 600_000, "mkt": "kr", "style": "day", "days": 20, "mode": "real", "model": "auto"})
    assert (pv2["model"], pv2["params"]["cycles_per_day"], pv2["params"]["web_search"]) == ("claude-haiku-4-5", 1, False) and pv2["cost"]["ok"] is False and "본전" in pv2["cost"]["warn"]
    per.update({"claude-fable-5-2": 0.8, "claude-opus-5-5": 0.4, "claude-sonnet-5-5": 0.2, "claude-haiku-4-5": 0.1})
    assert pv["ready"]["ok"] is False and "아직 실전을 권하지 않아요" in pv["ready"]["text"] and pv["params"]["skip_watch"] is True
    # 실전 3,000만: 월 1% = 하루 $10.2 → 한도도 $10.2
    pv = at.preview({"budget": 30_000_000, "mkt": "kr", "style": "day", "days": 20, "mode": "real", "model": "auto"})
    assert (pv["model"], pv["params"]["cycles_per_day"], pv["cost"]["ok"], pv["cost"]["warn"]) == ("claude-fable-5-2", 3, True, None) and pv["params"]["ai_daily_usd"] == 10.2
    # 설정한 엔진이 Sonnet이면 거기까지만(성과가 나빠 올린 단계가 있으면 Opus·Fable까지)
    monkeypatch.setattr(env.rt.ai, "conf", lambda: {"model": "claude-sonnet-5-5", "web_search": True})
    pv = at.preview({"budget": 5_000_000, "mkt": "kr", "style": "day", "days": 20, "mode": "real", "model": "auto"})
    assert (pv["model"], pv["params"]["cycles_per_day"], pv["params"]["web_search"]) == ("claude-sonnet-5-5", 3, True)
    m, patch, cc = at.fit_cost(5_000_000, "kr", "day", "real", make_plan(5_000_000, "kr", "day", 20, "real"), "auto", ceil="claude-opus-5-5")
    assert (m, patch["cycles_per_day"]) == ("claude-opus-5-5", 3)
    m, patch, cc = at.fit_cost(5_000_000, "kr", "day", "real", make_plan(5_000_000, "kr", "day", 20, "real"), "auto", ceil="claude-fable-5-2")
    assert (m, patch["cycles_per_day"]) == ("claude-fable-5-2", 3)
    monkeypatch.setitem(per, "claude-fable-5-2", 2.0)                    # 3번이면 한도($3)를 넘는다 → 횟수·검색을 줄여서라도 Fable
    m, patch, cc = at.fit_cost(5_000_000, "kr", "day", "real", make_plan(5_000_000, "kr", "day", 20, "real"), "auto", ceil="claude-fable-5-2")
    assert (m, patch["cycles_per_day"], patch["web_search"]) == ("claude-fable-5-2", 2, False)
    per["claude-fable-5-2"] = 0.8
    monkeypatch.setattr(env.rt.ai, "conf", lambda: {"model": "claude-fable-5-2", "web_search": True})
    # 직접 만든 모의도 최소 하루 $3 → 20분마다 빠른 재추천 + 정밀 추천 Fable 2번
    pv = at.preview({"budget": 1_000_000, "mkt": "kr", "style": "day", "days": 20, "mode": "paper", "model": "auto"})
    c = pv["cost"]
    assert (pv["model"], pv["params"]["cycles_per_day"], c["ok"]) == ("claude-fable-5-2", 2, True) and pv["ready"] is None and pv["params"]["ai_daily_usd"] == 3.0
    assert (pv["params"]["quick_gap_min"], c["quick_n"], pv["params"]["cycle_gap_min"], pv["params"]["web_search"]) == (20, 14, 160, True)
    assert 1.6 < c["day_usd"] <= 3.0 and c["quick_usd"] < 0.02 and "빠른 재추천" in next(x["d"] for x in pv["plan"] if x["t"] == "추천 받기")
    assert "Opus → Fable" in next(x["d"] for x in pv["plan"] if x["t"] == "추천 엔진")
    # 모델을 직접 고르면 바꾸지 않고 비용만 알려 준다(한도는 횟수에 맞춘다)
    pv = at.preview({"budget": 600_000, "mkt": "kr", "style": "day", "days": 20, "mode": "real", "model": "claude-fable-5-2"})
    assert pv["model"] == "claude-fable-5-2" and pv["params"]["cycles_per_day"] == 3 and pv["cost"]["auto"] is False and pv["cost"]["pct"] == 11.76 and pv["params"]["ai_daily_usd"] == 3.0
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
    assert len(L["runs"]) == 10 and L["status"] == "running" and {x["variant"] for x in L["runs"]} == set(VARIANTS)
    with pytest.raises(ValueError, match="이미 돌고"):
        at.lab_create({"budget": 1_000_000, "mkt": "kr", "style": "day", "days": 20})
    ids = {x["variant"]: x["id"] for x in L["runs"]}
    lab = L["id"]
    P = lambda k: at._run(ids[k])["params"]
    assert lab == ids["base"] and (P("loose")["min_win_prob"], P("loose")["min_rr"], P("all")["min_win_prob"]) == (48, 1.2, 40) and P("strict")["min_score"] == 70 and P("watch_skip")["skip_watch"] is True
    assert (P("tight")["exit_mult"], P("wide")["exit_mult"], P("wide")["tp_cap"], P("base")["exit_mult"]) == (0.5, 1.5, 6.0, 1.0) and (P("focus")["max_positions"], P("spread")["per_trade_pct"]) == (1, 20)
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
    T = lambda k: at._open(ids[k])[0]
    assert (T("base")["target_pct"], T("base")["stop_pct"]) == (3.0, 1.5) and (T("tight")["target_pct"], T("tight")["stop_pct"]) == (1.5, 0.75)   # 짧게·길게는 AI가 낸 폭 자체를 바꾼다
    assert (T("wide")["target_pct"], T("wide")["stop_pct"]) == (4.5, 2.25)
    B = at.labs()["labs"][0]
    g = next(x for x in B["positions"] if x["sym"] == "005930")
    assert len(g["plans"]) == len([k for k in held if "005930" in held[k]]) and g["why"]["reasons"] == ["거래량 돌파"] and g["why"]["risks"] == ["지수 약세"] and "장 마감 전" in g["hold"]
    pb = next(x for x in g["plans"] if x["label"] == "기본")
    assert pb["target"] == round(pb["price"] * 1.03, 2) and pb["stop"] == round(pb["price"] * 0.985, 2) and B["last"]["stance"] == "관망"
    lp = {x["sym"]: x for x in B["last"]["picks"]}
    assert lp["005930"]["bought"] == len(g["plans"]) and any(k["why"] == "AI가 관망 권함" for k in lp["005930"]["skips"])
    usd = [json.loads(r["data"])["usd"] for r in at.rt.db.q("SELECT data FROM at_log WHERE kind='cycle' AND data IS NOT NULL AND run_id!=?", (solo,))]
    assert len(usd) == 10 and all(u == 0.09 for u in usd)                  # 비용은 똑같이 나눠 적는다
    rows = {x["variant"]: x for x in B["runs"]}
    assert rows["base"]["open_qty"] == at._open(ids["base"])[0]["qty"] and rows["watch_full"]["open_pct"] > rows["base"]["open_pct"] > 0 and rows["strict"]["open_qty"] == 0
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
    assert [r["id"] for r in b["runs"]] == [solo] and b["labs"][0]["n"] == 10 and b["labs"][0]["lead"] == "느슨한 기준"
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
    assert S["on"] is False and S["day_cap_usd"] == 3.0 and len(S["advice"]) == 4 and at.labs()["labs"] == []
    at.tick(force=True)
    assert at.labs()["labs"] == []                                       # 꺼져 있으면 아무것도 만들지 않는다
    S = at.auto_set(True)
    L = at.labs()["labs"]
    assert S["on"] is True and sorted((x["mkt"], x["style"]) for x in L) == sorted(AUTO_SET) and all(len(x["runs"]) == 10 for x in L)
    kd = at._run(next(x["id"] for x in L if (x["mkt"], x["style"]) == ("kr", "day")))
    ks = at._run(next(x["id"] for x in L if (x["mkt"], x["style"]) == ("kr", "swing")))
    # 비용은 앱이 정한다(전체 하루 $3): 단타 하루 $1.05 → 20분마다 빠른 재추천 + 정밀 추천 Sonnet 2번(뉴스 검색), 스윙 $0.45 → 60분마다 + Sonnet 1번
    assert (kd["model"], kd["params"]["cycles_per_day"], kd["params"]["quick_gap_min"], kd["params"]["web_search"]) == ("claude-sonnet-5-5", 2, 20, True)
    assert (ks["model"], ks["params"]["cycles_per_day"], ks["params"]["quick_gap_min"], ks["params"]["web_search"]) == ("claude-sonnet-5-5", 1, 60, True)
    assert kd["params"]["lab_day_usd"] == 1.05 and ks["params"]["lab_day_usd"] == 0.45
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
    assert pv["variant"] == {"key": "loose", "label": "느슨한 기준"} and (pv["params"]["min_win_prob"], pv["params"]["min_rr"], pv["params"]["skip_watch"]) == (48, 1.2, True)
    pv = at.preview({"budget": 5_000_000, "mkt": "kr", "style": "day", "days": 30, "mode": "real", "model": "auto", "variant": "watch_full"})
    assert pv["params"]["skip_watch"] is True and pv["ready"]["ok"] is True


def test_running_experiments_pick_up_new_and_fixed_plans(env, monkeypatch):
    """돌고 있는 실험에도 새 플랜(가리지 않고 사기)이 붙고, 느슨·엄격은 기본 대비 값으로 고쳐진다(스윙에서 기본과 같아지던 문제)."""
    at = env.at
    monkeypatch.setattr(at, "_per_usd", lambda style, model, web: (0.1, {"fx": 1400.0}))
    at.auto_set(True)
    lab = next(x for x in at.labs()["labs"] if (x["mkt"], x["style"]) == ("kr", "swing"))
    ids = {x["variant"]: x["id"] for x in lab["runs"]}
    assert at._run(ids["base"])["params"]["min_win_prob"] == 50 and at._run(ids["loose"])["params"]["min_win_prob"] == 43
    # 예전 판 상태로 되돌려 놓고(느슨 = 고정 50, '가리지 않고 사기' 없음, 길게 끌기에 배수 없음) 다시 점검
    at.rt.db.x("DELETE FROM at_runs WHERE id=?", (ids["all"],))
    at.rt.db.x("UPDATE at_runs SET params=json_set(params,'$.min_win_prob',50,'$.idx_floor',-1.5) WHERE id=?", (ids["loose"],))
    at.rt.db.x("UPDATE at_runs SET params=json_remove(params,'$.exit_mult') WHERE id=?", (ids["wide"],))
    at._auto_at = 0
    at.tick(force=True)
    runs = {r["variant"]: r for r in at._runs("WHERE lab_id=?", (lab["id"],))}
    assert len(runs) == 10 and runs["all"]["params"]["min_win_prob"] == 40 and runs["all"]["status"] == "running"
    assert runs["loose"]["params"]["min_win_prob"] == 43 and runs["loose"]["params"]["idx_floor"] == 0 and runs["wide"]["params"]["exit_mult"] == 1.5
    at._auto_at = 0
    at.tick(force=True)
    assert len(at._runs("WHERE lab_id=?", (lab["id"],))) == 10                # 두 번 붙이지 않는다


def test_paper_never_skips_for_lot_size_and_real_only_sees_affordable_stocks(env, monkeypatch):
    """모의는 '돈이 모자라 1주를 못 삼'으로 시험을 날리지 않는다. 실전은 살 수 있는 값의 종목만 AI에게 보여 준다."""
    from moa.autotrade import AUTO_SET
    at = env.at
    # 1) 모의: 배정 금액(100만 × 33% × 검증 50% = 16.5만)보다 비싼 20만 원짜리도 현금이 되면 1주는 산다
    rid = env.run(budget=1_000_000, new_defaults=True)
    assert env.enter(rid, [env.pick("035420")]) == 1 and at._open(rid)[0]["qty"] == 1
    d = json.loads(at.rt.db.one("SELECT data FROM at_log WHERE run_id=? AND kind='cycle' AND data IS NOT NULL", (rid,))["data"])["decisions"][0]
    assert d["act"] == "buy" and d["min1"] is True
    env.px["000660"] = 900_000.0                                         # 현금(약 80만)으로도 못 사면 그때만 건너뛴다
    assert env.enter(rid, [env.pick("000660")]) == 0
    # 2) 추천을 받을 때 값 상한을 함께 보낸다: 모의 = 남은 현금, 실전 = 지금 한 번에 쓰는 돈
    cash = at.money(at._run(rid))["cash"]
    assert at._price_cap(at._run(rid)) == round(cash / 1.003, 2)
    at.rt.db.x("UPDATE at_runs SET mode='real' WHERE id=?", (rid,))
    assert at._price_cap(at._run(rid)) == round(min((1_000_000) * 0.33 * 0.5, cash) / 1.003, 2)
    sent = []

    def stream(kind, d):
        sent.append(d)
        yield {"t": "picks", "id": 5, "stance": "보통", "view": "v", "picks": []}
        yield {"t": "done", "usd": 0.1}
    monkeypatch.setattr(env.rt.ai, "stream", stream)
    at._ask_ai(at._run(rid))
    assert sent[0]["max_price"] == at._price_cap(at._run(rid))
    # 3) AI 추천 후보를 고를 때 그 상한보다 비싼 종목은 뺀다
    inv = env.rt.invest
    items = [{"rank": 1, "sym": "000660", "name": "SK하이닉스", "price": 600_000, "change_pct": 2.0, "value": 9e11, "volume": 1e6},
             {"rank": 2, "sym": "005930", "name": "삼성전자", "price": 70_000, "change_pct": 1.0, "value": 8e11, "volume": 9e6}]
    monkeypatch.setattr(inv, "kr", lambda: object())
    monkeypatch.setattr(inv, "ranking", lambda mkt, k: {"items": items})
    monkeypatch.setattr(inv, "candles", lambda mkt, sym, tf: {"bars": []})
    ap = env.rt.aipick
    for h in ("day", "swing"):
        try:
            ap.context({"mkt": "kr", "horizon": h, "max_price": 164_000})
        except Exception:
            pass
        names = [c["sym"] for c in ap._cands.get(AP._ck("kr", h), [])]
        assert names == ["005930"], (h, names)
    # 4) 실험 묶음: 가상 돈 1,000만 · 검증 단계 없이 처음부터 정해진 크기로. 예전 묶음(300만)은 스스로 늘린다
    monkeypatch.setattr(at, "_per_usd", lambda style, model, web: (0.1, {"fx": 1400.0}))
    at.auto_set(True)
    lab = next(x for x in at.labs()["labs"] if (x["mkt"], x["style"]) == ("kr", "day"))
    base = at._run(lab["id"])
    assert base["budget"] == AUTO_SET[("kr", "day")][1] == 10_000_000 and base["params"]["warmup_trades"] == 0 and at._price_cap(base) is None
    assert at._enter(base, {"id": 9, "stance": "보통", "view": "v", "picks": [env.pick("000660")]}, {"usd": 0}) == 1 and at._open(base["id"])[0]["qty"] == 3   # 330만 ÷ 90만
    at.rt.db.x("UPDATE at_runs SET budget=3000000, params=json_set(params,'$.warmup_trades',10) WHERE lab_id=?", (lab["id"],))
    at._auto_at = 0
    at.tick(force=True)
    r = at._run(lab["id"])
    assert r["budget"] == 10_000_000 and r["params"]["warmup_trades"] == 0 and any("가상 돈을" in x["msg"] for x in at.rt.db.q("SELECT msg FROM at_log WHERE run_id=?", (lab["id"],)))


def test_marks_for_chart_split_real_and_paper(env):
    """종목 차트에 찍을 자동매매 기록: 추천한 때, 플랜별로 산 때·판 때. 실전과 모의를 가른다."""
    at = env.at
    a = env.run()
    at.rt.db.x("UPDATE at_runs SET mode='real' WHERE id=?", (a,))
    b = env.run()
    at.rt.db.x("UPDATE at_runs SET mode='paper' WHERE id=?", (a,))       # (가상 장부로 사고판 뒤, 아래에서 실전으로 표시만 바꾼다)
    at.rt.db.x("INSERT INTO ai_picks(id,ts,mkt,model,usd,stance,view,picks) VALUES(77,?,?,?,?,?,?,?)",
               (f"{env.day}T10:20:00", "kr", "m", 0.1, "보통", "v", json.dumps([dict(env.pick("005930"), entry_price=70000), env.pick("000660")])))
    env.enter(a, [env.pick("005930")])
    env.enter(b, [env.pick("005930")])
    env.px["005930"] = 73000.0
    at.tick(force=True)
    at.rt.db.x("UPDATE at_runs SET mode='real' WHERE id=?", (a,))
    M = at.marks("kr", "005930")
    kinds = [(e["kind"], e.get("mode")) for e in M["events"]]
    assert kinds.count(("rec", None)) == 1 and kinds.count(("buy", "real")) == 1 and kinds.count(("buy", "paper")) == 1 and kinds.count(("sell", "real")) == 1 and kinds.count(("sell", "paper")) == 1
    buy = next(e for e in M["events"] if e["kind"] == "buy" and e["mode"] == "real")
    sell = next(e for e in M["events"] if e["kind"] == "sell" and e["mode"] == "real")
    assert buy["target"] == round(buy["price"] * 1.03, 2) and sell["reason"] == "익절" and sell["pct"] > 0 and M["has"] == {"real": True, "paper": True, "rec": True}
    assert at.marks("kr", "035420")["events"] == [] and [e["kind"] for e in at.marks("kr", "000660")["events"]] == ["rec"]


# ---------- 빠른 재추천(자주, 싸게) ----------
def test_quick_rescans_between_full_scans(env, monkeypatch):
    """모의는 정밀 추천(좋은 엔진·뉴스) 사이사이에 20분마다 가장 싼 엔진으로 다시 묻는다. 이미 다룬 종목은 빼고 묻고, 비용·횟수는 따로 센다."""
    at = env.at
    rid = env.run(new_defaults=True)
    run = lambda: at._run(rid)
    p = run()["params"]
    assert p["quick_gap_min"] == 20 and make_plan(3_000_000, "kr", "day", 30)["quick_gap_min"] == 0     # 실전은 사용자가 켠다
    assert _clamp_key("quick_gap_min", 3) == 10 and _clamp_key("quick_gap_min", 0) == 0 and _clamp_key("quick_gap_min", 999) == 120
    sent = []

    def stream(kind, d):
        sent.append(dict(d))
        sym = ["005930", "000660", "035420"][len(sent) - 1]
        yield {"t": "picks", "id": 40 + len(sent), "stance": "보통", "view": "v", "picks": [env.pick(sym)]}
        yield {"t": "done", "usd": 0.008 if d.get("lite") else 0.1}
    monkeypatch.setattr(env.rt.ai, "stream", stream)

    def go():
        at.tick(force=True)
        at._busy[rid].join(5)
    # 1) 그날 첫 추천은 정밀
    assert at._due(run())["kind"] == "full"
    go()
    assert len(sent) == 1 and not sent[0].get("lite") and "exclude" not in sent[0] and run()["last_full"] > 0
    # 2) 20분이 안 됐으면 기다린다(남은 시간을 알려 준다)
    at.rt.db.x("UPDATE at_runs SET last_cycle=? WHERE id=?", (time.time() - 5 * 60, rid))
    ok, code, _ = at.gate_code(run())
    n = at.now(run())["next"]
    assert (ok, code) == (False, "gap") and n["kind"] == "at" and "20분마다" in n["label"] and 14 * 60 < n["in_sec"] <= 15 * 60 and n["span_sec"] == 1200
    at.tick(force=True)
    assert len(sent) == 1
    # 3) 20분이 지나면 빠른 재추천: 가장 싼 엔진 · 검색 없이 · 이미 산 종목은 빼고
    at.rt.db.x("UPDATE at_runs SET last_cycle=? WHERE id=?", (time.time() - 21 * 60, rid))
    assert at._due(run())["kind"] == "quick" and "빠른 재추천" in at.now(run())["next"]["label"]
    go()
    d = sent[1]
    assert (d["lite"], d["model"], d["web_search"], d["exclude"]) == (True, "claude-haiku-4-5", False, ["005930"])
    t = at._open(rid)
    assert [x["sym"] for x in t] == ["005930", "000660"] and json.loads(t[1]["why"])["lite"] is True and json.loads(t[0]["why"])["lite"] is False
    N = at.now(run())
    assert (N["today"]["cycles"], N["today"]["full"], N["today"]["quick"], N["today"]["quick_gap"]) == (2, 1, 1, 20) and N["last_cycle"]["lite"] is True
    assert N["ai"]["today_usd"] == 0.108 and "빠른 재추천" in at.detail(rid)["log"][0]["msg"]
    assert "20분마다 빠른 재추천" in next(r["sub"] for r in N["steps"]["rows"] if r["name"] == "매수 시작")
    # 4) 정밀 추천 간격이 지나면 다시 정밀로(하루 횟수 안에서). 횟수를 다 쓰면 빠른 재추천만 이어 간다
    gap = p["cycle_gap_min"] * 60
    at.rt.db.x("UPDATE at_runs SET last_cycle=?, last_full=? WHERE id=?", (time.time() - 21 * 60, time.time() - gap - 60, rid))
    assert at._due(run())["kind"] == ("full" if p["cycles_per_day"] > 1 else "quick")
    at.rt.db.x("UPDATE at_runs SET params=json_set(params,'$.cycles_per_day',1) WHERE id=?", (rid,))
    assert at._due(run())["kind"] == "quick"
    go()
    assert sent[2]["lite"] is True and sent[2]["exclude"] == ["000660", "005930"]
    # 5) 관망이어도(작게 사 보는 플랜) 빠른 재추천은 쉬지 않는다. 관망에 쉬는 플랜은 예전처럼 두 배를 기다린다
    at.log(rid, "cycle", "x", {"lite": True, "stance": "관망", "idx": -0.5, "usd": 0.008, "pick_id": 99})
    env.idx = -0.5
    at.rt.db.x("UPDATE at_runs SET last_cycle=? WHERE id=?", (time.time() - 21 * 60, rid))
    at.rt.db.x("UPDATE at_trades SET status='closed', exit_ts=entry_ts, exit_price=entry_price, pnl=0, pnl_pct=0 WHERE run_id=?", (rid,))
    assert at.gate_code(run())[0] is True
    at.rt.db.x("UPDATE at_runs SET params=json_set(params,'$.skip_watch',json('true')) WHERE id=?", (rid,))
    assert at.gate_code(run())[1] == "watchwait" and "40분" in at.gate_code(run())[2]
    # 6) 하루 비용 한도는 정밀·빠른 재추천을 합쳐서 본다
    at.rt.db.x("UPDATE at_runs SET params=json_set(params,'$.skip_watch',json('false'),'$.ai_daily_usd',0.12) WHERE id=?", (rid,))
    assert at.gate_code(run())[1] == "aicost"


def test_quick_rescan_sends_a_short_table_and_keeps_the_morning_news(env, monkeypatch):
    """빠른 재추천은 같은 후보를 표 한 덩어리로 줄여 보내고, 앞선 정밀 추천이 찾은 재료를 메모로 이어받는다. 추천 탭 기록에는 섞지 않는다."""
    rt, ap, inv = env.rt, env.rt.aipick, env.rt.invest
    items = [{"rank": i + 1, "sym": s, "name": n, "price": px, "change_pct": 2.0 + i, "value": 9e11, "volume": 1e6}
             for i, (s, n, px) in enumerate([("005930", "삼성전자", 70_000), ("000660", "SK하이닉스", 150_000), ("035420", "NAVER", 200_000)])]
    bars = [{"t": f"11/18 09:{5 * i:02d}", "o": 100 + i, "h": 102 + i, "l": 99 + i, "c": 101 + i, "v": 1000 + 10 * i} for i in range(12)]
    monkeypatch.setattr(inv, "kr", lambda: object())
    monkeypatch.setattr(inv, "ranking", lambda mkt, k: {"items": items})
    monkeypatch.setattr(inv, "candles", lambda mkt, sym, tf: {"bars": bars})
    monkeypatch.setattr(inv, "orderbook", lambda mkt, sym: {"tot_bid": 300, "tot_ask": 200})
    monkeypatch.setattr(rt.market, "kr_indices", lambda: [{"name": "코스피", "price": 2700, "change_pct": 0.4}])
    monkeypatch.setattr(ap, "_cash", lambda mkt: (1_000_000, {}))
    monkeypatch.setattr(AP, "session", lambda mkt: {"open": True, "kst": "2026-11-18 10:30", "local": "10:30", "left_min": 290, "close_kst": "15:30", "phase": "mid"})
    full = ap.parse('판단 글: 반도체 수출 호조 기사 확인\n```json\n{"stance":"보통","market_view":"반도체 강세","picks":[{"sym":"005930","name":"삼성전자","score":70,"win_prob":60,'
                    '"entry_price":70000,"target_pct":3,"stop_pct":1.5,"reasons":["수출 호조 뉴스"],"risks":["지수"]}]}\n```', "kr", "claude-sonnet-5-5", 0.1)
    _, ask_f, data_f = ap.context({"mkt": "kr", "horizon": "day"})
    _, ask_q, data_q = ap.context({"mkt": "kr", "horizon": "day", "lite": True, "exclude": ["005930"]})
    tbl = data_q["후보"].split("\n")
    assert "실시간 순위" in data_f and "실시간 순위" not in data_q and "계좌(참고)" not in data_q
    assert tbl[0].startswith("sym|이름|현재가|등락률%|시가대비%|VWAP대비%") and [r.split("|")[0] for r in tbl[1:]] == ["000660", "035420"]      # 이미 다룬 종목은 뺀다
    assert data_q["이미 다룬 종목(빼고 골라)"] == ["005930"] and "표" in ask_q
    memo = data_q["앞선 정밀 추천 메모"]
    assert memo["시장 판단"] == "반도체 강세" and "수출 호조" in memo["판단 글"] and memo["그때 고른 종목"] == ["삼성전자(005930): 수출 호조 뉴스"]
    size = lambda d: len(json.dumps(d, ensure_ascii=False))
    assert size(data_q) < size(data_f) * 0.6
    # 빠른 재추천의 결과는 따로 표시해 두고, 추천 탭의 기록·오늘 횟수에는 넣지 않는다. 다음 메모도 정밀 추천에서만 가져온다
    q = ap.parse('```json\n{"stance":"보통","market_view":"눌림 뒤 재상승","picks":[{"sym":"000660","name":"SK하이닉스","score":66,"win_prob":58,"entry_price":150000,'
                 '"target_pct":2.4,"stop_pct":1.2,"reasons":["VWAP 위 재상승"],"risks":["지수"]}]}\n```', "kr", "claude-haiku-4-5", 0.008, lite=True)
    assert q["lite"] is True and q["picks"][0]["sym"] == "000660" and full["lite"] is False
    assert [r["id"] for r in ap.history()] == [full["id"]] and ap.memo("kr", "day")["시장 판단"] == "반도체 강세"
    assert rt.db.one("SELECT lite FROM ai_picks WHERE id=?", (q["id"],))["lite"] == 1
    # 프롬프트: 빠른 재추천은 표(JSON)만 받는다. 스윙도 같은 방식
    assert "설명 글 없이" in AP.system_prompt("day", True) and "설명 글 없이" in AP.system_prompt("swing", True) and "5거래일" in AP.system_prompt("swing", True)
    assert AP.system_prompt("day") == AP.PICK_SYSTEM
    monkeypatch.setattr(ap, "index_state", lambda mkt, sym="": {"price": 2700, "sym": "KOSPI"})
    monkeypatch.setattr(rt.market, "watch", lambda: {})
    dbars = [{"t": f"2026-{8 + i // 30:02d}-{1 + i % 28:02d}", "o": 100 + i, "h": 103 + i, "l": 98 + i, "c": 101 + i, "v": 1000 + i} for i in range(70)]
    monkeypatch.setattr(inv, "candles", lambda mkt, sym, tf: {"bars": dbars})
    _, ask_s, data_s = ap.context({"mkt": "kr", "horizon": "swing", "lite": True, "exclude": ["035420"]})
    rows = data_s["후보"].split("\n")
    assert "실시간 순위" not in data_s and rows[0].startswith("sym|이름|현재가|등락률%|") and "20일선대비%" in rows[0] and [r.split("|")[0] for r in rows[1:]] == ["005930", "000660"]


def test_quick_rescan_api_call_is_cheap(rt):
    """빠른 재추천은 가장 싼 엔진으로, 검색 없이, 짧은 답만 받고 비용을 따로 센다."""
    from moa.ai import LITE_MODEL
    rt.settings.update({"ai": {"api_key": "sk-ant-test", "model": "claude-fable-5-2", "web_search": True}})
    sent = []

    def fake_post(body):
        sent.append(body)
        yield {"type": "message_start", "message": {"usage": {"input_tokens": 3000, "output_tokens": 0}}}
        yield {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
        yield {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": '```json\n{"stance":"관망","market_view":"v","picks":[]}\n```'}}
        yield {"type": "content_block_stop", "index": 0}
        yield {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 400}}
    rt.ai._post = fake_post
    rt.ai.context = lambda kind, d: ("t", "ask", {"후보": "sym|이름"})
    rt.market.yh_quote = lambda s: {"price": 1400.0}
    out = list(rt.ai.stream("pick", {"mkt": "kr", "horizon": "day", "lite": True, "web_search": True}))
    b = sent[0]
    assert b["model"] == LITE_MODEL and "tools" not in b and b["max_tokens"] == 1500 and "설명 글 없이" in b["system"]
    done = out[-1]
    assert done["t"] == "done" and done["model"] == LITE_MODEL and abs(done["usd"] - (3000 * 1 + 400 * 5) / 1e6) < 1e-9
    assert rt.db.one("SELECT kind, model FROM ai_usage ORDER BY id DESC LIMIT 1") == {"kind": "pickq", "model": LITE_MODEL}
    m = rt.ai.meter("pickq", LITE_MODEL)
    assert m["web"] is False and m["avg_usd"] == 0.005 and rt.autotrade._quick_usd() == 0.005


def test_old_runs_get_quick_rescans_once(env, monkeypatch):
    """예전 판에서 시작한 모의·실험은 빠른 재추천을 스스로 켠다(하루 비용 상한 안에서). 실전은 그대로 둔다."""
    at = env.at
    monkeypatch.setattr(at, "_per_usd", lambda style, model, web: ({"claude-sonnet-5-5": 0.2, "claude-haiku-4-5": 0.1}.get(model, 0.8) * (1 if web else 0.6), {"fx": 1400.0}))
    paper, us = env.run(), env.run(mkt="us", budget=3000)
    at.rt.db.x("UPDATE at_runs SET mode='real' WHERE id=?", (us,))
    at.auto_set(True)
    lab = next(x["id"] for x in at.labs()["labs"] if (x["mkt"], x["style"]) == ("kr", "day"))
    at.rt.db.x("UPDATE at_runs SET params=json_remove(params,'$.quick_gap_min')")
    at.rt.db.x("UPDATE at_runs SET params=json_set(params,'$.cycles_per_day',3,'$.cycle_gap_min',60) WHERE lab_id=?", (lab,))
    env.open = False
    at.tick(force=True)
    P = lambda i: at._run(i)["params"]
    assert P(paper)["quick_gap_min"] == 20 and P(paper)["cycles_per_day"] == 3 and P(us)["quick_gap_min"] == 0       # 직접 만든 모의: 간격만 붙인다. 실전: 끈 채로
    sibs = at._runs("WHERE lab_id=?", (lab,))
    assert all((r["params"]["quick_gap_min"], r["params"]["cycles_per_day"], r["params"]["cycle_gap_min"]) == (20, 2, 160) for r in sibs) and len(sibs) == 10
    assert sum(1 for x in at.rt.db.q("SELECT msg FROM at_log WHERE kind='control'") if "더 자주" in x["msg"]) == 5       # 직접 만든 모의 1 + 실험 묶음 4
    at.tick(force=True)
    assert sum(1 for x in at.rt.db.q("SELECT msg FROM at_log WHERE kind='control'") if "더 자주" in x["msg"]) == 5       # 한 번만
    assert at.detail(us)["params"]["quick_gap_min"] == 0 and at.edit(us, {"quick_gap_min": 30})["params"]["quick_gap_min"] == 30   # 실전은 플랜에서 켠다


# ---------- 매매 기록 ----------
def test_history_by_day_strategy_and_angles(env, monkeypatch):
    """지난 기록을 날짜별·전략별·기준별로. 실험은 묶음이 바뀌어도 같은 플랜끼리 이어 보고, 돈은 원화(미국은 환율로)로 맞춘다."""
    at = env.at
    monkeypatch.setattr(at, "_per_usd", lambda style, model, web: (0.1, {"fx": 1400.0}))
    mine, us = env.run(), env.run(mkt="us", budget=3000)
    _seed(env, mine, 10)                                                  # 11/02~11/04, 반은 -1.2%, 반은 +1.6%
    env.rt.db.x("UPDATE at_trades SET entry_day='2026-11-17', exit_day='2026-11-17', why=? WHERE run_id=? AND sym='S1'", (json.dumps({"lite": True, "catalyst": "수주"}), mine))
    env.rt.db.x("INSERT INTO at_trades(run_id,ver,mkt,sym,name,qty,status,entry_ts,entry_day,entry_hm,entry_price,target_pct,stop_pct,exit_ts,exit_price,reason,pnl,pnl_pct,exit_day,hold_min) "
                "VALUES(?,1,'us','NVDA','엔비디아',2,'closed','2026-11-17T23:40:00','2026-11-17','10:40',100,3,1.5,'2026-11-18T01:10:00',102,'익절',4,2.0,'2026-11-17',90)", (us,))
    at.log(mine, "cycle", "x", {"usd": 0.5, "pick_id": 1, "decisions": []})
    at.auto_set(True)
    lab = next(x for x in at.labs()["labs"] if (x["mkt"], x["style"]) == ("kr", "day"))
    loose = next(r["id"] for r in lab["runs"] if r["variant"] == "loose")
    _seed(env, loose, 4)
    # 30일(오늘 11/18 기준 10/20부터): 모든 기록을 원화로(미국 4달러 = 5,600원)
    H = at.history({"days": 30})
    T = H["total"]
    assert T["n"] == 15 and T["pnl_krw"] == 5 * 1600 - 5 * 1200 + 4 * 1400 + 2 * 1600 - 2 * 1200
    keys = {s["key"]: s for s in H["strategies"]}
    assert f"run:{mine}" in keys and f"run:{us}" in keys and "lab:kr:day:loose" in keys
    s = keys["lab:kr:day:loose"]
    assert (s["label"], s["group"], s["mode"], s["stats"]["n"]) == ("느슨한 기준", "국내 단타 실험", "paper", 4) and s["by_day"][0]["n"] >= 1
    assert keys[f"run:{mine}"]["ai_cost"] == 700 and keys[f"run:{mine}"]["net"] == keys[f"run:{mine}"]["stats"]["pnl_krw"] - 700
    d17 = next(x for x in H["days"] if x["day"] == "2026-11-17")
    assert d17["sells"] == 2 and d17["dow"] == "화" and d17["stats"]["pnl_krw"] == 1600 + 5600 and d17["mkts"] == ["kr", "us"]
    G = {g["label"]: {r["key"]: r for r in g["rows"]} for g in H["groups"]}
    assert G["추천 종류"]["빠른 재추천"]["n"] == 1 and G["시장 · 방식"]["미국 단타"]["n"] == 1 and G["파는 사유"]["익절"]["n"] == 1
    assert H["symbols"] and all(t["strat_label"] for t in H["trades"]) and H["trades"][0]["exit_ts"] >= H["trades"][-1]["exit_ts"]
    # 걸러 보기: 시장·구분·전략, 그날 자세히(받은 추천 포함)
    assert at.history({"days": 30, "mkt": "us"})["total"]["n"] == 1
    assert at.history({"days": 30, "strat": "lab:kr:day:loose"})["total"]["n"] == 4
    assert at.history({"days": 30, "mode": "real"})["total"]["n"] == 0
    one = at.history({"days": 30, "day": "2026-11-17"})
    assert {t["sym"] for t in one["trades"]} == {"S1", "NVDA"} and one["day"]["day"] == "2026-11-17"
    assert at.history({"days": 7})["total"]["n"] == 2                     # 11/12~11/18
    # API
    c = create_app(env.rt).test_client()
    assert c.get("/api/auto/history?days=30&mkt=us").get_json()["total"]["n"] == 1


def test_history_shows_each_days_index(env, monkeypatch):
    """날짜별 기록에 그날 지수(국내 코스피·코스닥, 미국 S&P 500·나스닥)를 붙이고, 받은 값은 저장해 두고 쓴다."""
    at = env.at
    px = {"^KS11": [2500, 2525, 2500], "^KQ11": [800, 792, 800], "^GSPC": [6000, 6060, 5940], "^IXIC": [20000, 20000, 20200]}
    calls = []

    def candles(mkt, sym, tf):
        calls.append(sym)
        days = ["2026-11-14", "2026-11-17", "2026-11-18"]
        return {"bars": [{"t": d, "o": c, "h": c * 1.01, "l": c * 0.99, "c": c} for d, c in zip(days, px[sym])]}
    monkeypatch.setattr(env.rt.invest, "candles", candles)
    rid = env.run()
    _seed(env, rid, 2)
    env.rt.db.x("UPDATE at_trades SET entry_day='2026-11-17', exit_day='2026-11-17' WHERE run_id=?", (rid,))
    H = at.history({"days": 30})
    d17 = next(x for x in H["days"] if x["day"] == "2026-11-17")
    assert [(i["name"], i["pct"]) for i in d17["idx"]] == [("코스피", 1.0), ("코스닥", -1.0)]           # 그날 거래한 시장(국내)만
    assert d17["idx"][0]["hi"] == 2.01 and d17["idx"][0]["close"] == 2525
    G = {g["label"]: g for g in H["groups"]}
    assert "그날 지수(코스피·S&P 500)" not in G                                    # 모두 같은 칸(0~+1%)이면 비교할 게 없어 빼 둔다
    assert all(t["idx_pct"] == 1.0 for t in H["trades"])
    # 시장을 고르면 그 시장 지수만, 10분 안에는 다시 받지 않고 저장해 둔 값을 쓴다
    n = len(calls)
    U = at.history({"days": 30, "mkt": "us"})
    assert len(calls) == n
    env.rt.db.x("INSERT INTO at_trades(run_id,ver,mkt,sym,name,qty,status,entry_ts,entry_day,entry_hm,entry_price,target_pct,stop_pct,exit_ts,exit_price,reason,pnl,pnl_pct,exit_day) "
                "VALUES(?,1,'us','NVDA','엔비디아',1,'closed','x','2026-11-18','10:00',100,3,1.5,'y',101,'익절',1,1.0,'2026-11-18')", (rid,))
    U = at.history({"days": 30, "mkt": "kr"})
    assert all(i["mkt"] == "kr" for x in U["days"] for i in x["idx"])
    A = at.history({"days": 30})
    d18 = next(x for x in A["days"] if x["day"] == "2026-11-18")
    assert [(i["name"], i["pct"]) for i in d18["idx"]] == [("S&P 500", -1.98), ("나스닥", 1.0)]
    monkeypatch.setattr(env.rt.invest, "candles", lambda *a: (_ for _ in ()).throw(RuntimeError("off")))
    at._idx_at.clear()
    assert next(x for x in at.history({"days": 30})["days"] if x["day"] == "2026-11-17")["idx"][0]["pct"] == 1.0   # 받기 실패해도 저장해 둔 값


def test_old_runs_show_full_plan_in_order(env):
    """예전 판 자동매매에 없던 플랜 값은 그때 동작과 같은 값으로 채워 보여 준다. 세부 값은 정해 둔 순서대로."""
    at, rid = env.at, env.run()
    env.rt.db.x("UPDATE at_runs SET params=json_remove(params,'$.exit_mult','$.watch_mult','$.idx_floor','$.quick_gap_min') WHERE id=?", (rid,))
    d = at.detail(rid)
    assert (d["params"]["exit_mult"], d["params"]["watch_mult"], d["params"]["idx_floor"], d["params"]["quick_gap_min"]) == (1.0, 0.5, 0.0, 0)
    assert [k for k, _ in d["keys"]] == list(PLAN_KEYS)
    assert "quick_gap_min" not in json.loads(env.rt.db.one("SELECT params FROM at_runs WHERE id=?", (rid,))["params"])   # 저장 값은 그대로(_upgrade가 알아보게)


def test_lab_refits_when_daily_ai_budget_changes(env, monkeypatch):
    """하루 AI 비용 목표가 바뀌면(예전 $1 → $3) 돌고 있는 실험의 엔진·횟수를 묶음마다 한 번 다시 맞춘다."""
    at = env.at
    monkeypatch.setattr(at, "_per_usd", lambda style, model, web: ({"claude-sonnet-5-5": 0.2, "claude-haiku-4-5": 0.1}.get(model, 0.8) * (1 if web else 0.6), {"fx": 1400.0}))
    at.auto_set(True)
    lab = next(x for x in at.labs()["labs"] if (x["mkt"], x["style"]) == ("kr", "day"))
    env.rt.db.x("UPDATE at_runs SET model='claude-haiku-4-5', params=json_set(params,'$.lab_day_usd',0.35,'$.cycles_per_day',2) WHERE lab_id=?", (lab["id"],))
    env.open = False
    at._auto_at = 0
    at.tick(force=True)
    rs = at._runs("WHERE lab_id=?", (lab["id"],))
    assert all((r["model"], r["params"]["lab_day_usd"], r["params"]["cycles_per_day"]) == ("claude-sonnet-5-5", 1.05, 2) for r in rs)
    msgs = [x["msg"] for x in env.rt.db.q("SELECT msg FROM at_log WHERE kind='control' AND run_id=?", (lab["id"],))]
    assert any("$0.35 → $1.05" in m and "Sonnet" in m for m in msgs)
    at._auto_at = 0
    at.tick(force=True)
    assert len([m for m in (x["msg"] for x in env.rt.db.q("SELECT msg FROM at_log WHERE kind='control' AND run_id=?", (lab["id"],))) if "비용 목표" in m]) == 1


def test_learning_uses_full_scans_only(env):
    """빠른 재추천의 성적은 성적표에 따로 보이지만, 자체 학습(통계·승률 보정·복기)에는 넣지 않는다."""
    ap, db = env.rt.aipick, env.rt.db
    from moa import aipick as AP2
    AP2._ensure_cols(db)
    for i, lite in enumerate([0, 0, 1, 1, 1]):
        pid = db.x("INSERT INTO ai_picks(ts,mkt,model,usd,stance,view,picks,text,horizon,lv,lite) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (f"2026-11-1{i}T10:00:00", "kr", "claude-haiku-4-5" if lite else "claude-sonnet-5-5", 0.1, "보통", "v", "[]", "", "day", 0, lite))
        db.x("INSERT INTO ai_outcomes(pick_id,idx,ts,mkt,model,sym,name,stance,auto,score,win_prob,entry_type,rec_price,entry_price,target_pct,stop_pct,end_ts,"
             "cost_usd,feat,horizon,status,result,net_pct,ret_pct,filled,lite) VALUES(?,0,?,?,?,?,?,?,0,70,60,'limit',100,100,2,1,?,0.1,'{}','day','done','익절',1.8,2,1,0)",
             (pid, f"2026-11-1{i}T10:00:00", "kr", "m", f"S{i}", f"종목{i}", "보통", f"2026-11-1{i}T15:00:00"))
    db._lite_marked = False
    AP2._ensure_cols(db)                                                  # 예전 판에 들어간 줄도 한 번 표시한다
    assert [r["lite"] for r in db.q("SELECT lite FROM ai_outcomes ORDER BY pick_id")] == [0, 0, 1, 1, 1]
    assert len(AP2._done_rows(ap, None, "day")) == 2
    st = ap.stats(0, "", "", "day")
    assert st["all"]["n"] == 5 and {x["key"]: x["n"] for x in st["by_kind"]} == {"정밀 추천": 2, "빠른 재추천": 3}
    assert ap.stats(0, "", "", "day", "lite")["all"]["n"] == 3 and ap.stats(0, "", "", "day", "full")["all"]["n"] == 2
    c = create_app(env.rt).test_client()
    assert c.get("/api/ai/stats?h=day&kind=full").get_json()["all"]["n"] == 2


# ---------- AI 비용 한도는 시드에 비례(최소 $3) · 성과가 나쁘면 엔진을 올린다 ----------
def test_cost_cap_follows_seed_and_old_runs_get_it_once(env, monkeypatch):
    at = env.at
    per = {"claude-fable-5-2": 0.8, "claude-opus-5-5": 0.4, "claude-sonnet-5-5": 0.2, "claude-haiku-4-5": 0.1}
    monkeypatch.setattr(at, "_per_usd", lambda style, model, web: (per[model] * (1 if web else 0.6), {"fx": 1400.0}))
    monkeypatch.setattr(env.rt.ai, "conf", lambda: {"model": "claude-sonnet-5-5", "web_search": True})
    rid = env.run(mode="paper")
    env.rt.db.x("UPDATE at_runs SET model='claude-haiku-4-5', params=json_remove(json_set(params,'$.ai_daily_usd',0.4,'$.cycles_per_day',1),'$.cap_v') WHERE id=?", (rid,))
    env.open = False
    at.tick(force=True)
    r = at._run(rid)
    assert (r["params"]["ai_daily_usd"], r["params"]["cap_v"], r["model"]) == (3.0, 2, "claude-sonnet-5-5")
    assert any("$0.4 → $3" in x["msg"] for x in env.rt.db.q("SELECT msg FROM at_log WHERE run_id=? AND kind='control'", (rid,)))
    at.tick(force=True)
    assert sum("$0.4 → $3" in x["msg"] for x in env.rt.db.q("SELECT msg FROM at_log WHERE run_id=? AND kind='control'", (rid,))) == 1
    # 시드를 늘리면 한도도 따라 오른다(내리지는 않는다)
    at.budget(rid, {"budget": 30_000_000})
    assert at._run(rid)["params"]["ai_daily_usd"] == 10.2
    at.budget(rid, {"budget": 3_000_000})
    assert at._run(rid)["params"]["ai_daily_usd"] == 10.2


def test_engine_moves_up_when_results_are_poor_and_back_if_worse(env, monkeypatch):
    """정밀 추천으로 산 거래가 20건 쌓일 때마다 본다. 나쁘면 Sonnet → Opus → Fable, 올렸는데 더 나빠지면 되돌리고 멈춘다."""
    at = env.at
    per = {"claude-fable-5-2": 0.8, "claude-opus-5-5": 0.4, "claude-sonnet-5-5": 0.2, "claude-haiku-4-5": 0.1}
    monkeypatch.setattr(at, "_per_usd", lambda style, model, web: (per[model] * (1 if web else 0.6), {"fx": 1400.0}))
    monkeypatch.setattr(env.rt.ai, "conf", lambda: {"model": "claude-sonnet-5-5", "web_search": True})
    notes = []
    monkeypatch.setattr(env.rt, "notify", lambda m: notes.append(m))
    rid = env.run(mode="paper")
    env.rt.db.x("UPDATE at_runs SET model='claude-sonnet-5-5' WHERE id=?", (rid,))
    env.open = False
    day = [0]

    def trades(n, pct, lite=False):
        for i in range(n):
            env.rt.db.x("INSERT INTO at_trades(run_id,ver,mkt,sym,name,qty,status,entry_ts,entry_day,entry_hm,entry_price,target_pct,stop_pct,exit_ts,exit_price,reason,pnl,pnl_pct,exit_day,why) "
                        "VALUES(?,1,'kr','S','s',1,'closed',?, '2026-11-18','10:00',100,2,1,?,100,'손절',?,?,'2026-11-18',?)",
                        (rid, "2099-01-01T10:00:00", "2099-01-01T11:00:00", pct, pct, json.dumps({"lite": lite})))

    def check():
        day[0] += 1
        m0 = at._run(rid)["model"]
        env.rt.db.x("UPDATE at_runs SET params=json_set(params,'$.engine_day','x') WHERE id=?", (rid,))
        at._engine_watch()
        r = at._run(rid)
        if r["model"] != m0:                                              # 바꾼 뒤부터 새로 센다: 앞 거래는 바꾸기 전 시각으로 옮겨 둔다
            env.rt.db.x("UPDATE at_trades SET exit_ts='2000-01-01T00:00:00' WHERE run_id=?", (rid,))
        return r
    trades(19, -0.4)
    trades(10, -2.0, lite=True)                                           # 빠른 재추천 거래는 엔진 판단에 넣지 않는다
    assert check()["model"] == "claude-sonnet-5-5"
    trades(1, -0.4)
    r = check()
    assert r["model"] == "claude-opus-5-5" and r["params"]["engine_ceil"] == "claude-opus-5-5" and r["params"]["engine_hist"][-1]["avg"] == -0.4
    assert "Opus" in notes[-1] and "성과가 나빠" in notes[-1]
    n = len(notes)
    env.rt.db.x("UPDATE at_runs SET params=json_set(params,'$.engine_day',?) WHERE id=?", (AP.mkt_date("kr"), rid))
    at._engine_watch()
    assert len(notes) == n                                                # 하루에 한 번만 본다
    # Opus에서도 나쁘지만 덜 나쁨 → Fable
    trades(20, -0.1)
    r = check()
    assert r["model"] == "claude-fable-5-2" and len(r["params"]["engine_hist"]) == 2
    # Fable에서 더 나빠짐 → Opus로 되돌리고 멈춘다
    trades(20, -0.9)
    r = check()
    assert r["model"] == "claude-opus-5-5" and r["params"]["engine_lock"] is True and "되돌렸어요" in notes[-1]
    trades(20, -3.0)
    assert check()["model"] == "claude-opus-5-5"
    # 다른 설정(비용 한도 다시 맞추기)이 돌아도 올린 엔진을 지킨다
    m, patch, _ = at.fit_cost(3_000_000, "kr", "day", "paper", at._run(rid)["params"], "auto", ceil=at._run(rid)["params"]["engine_ceil"])
    assert m == "claude-opus-5-5"


def test_flow_shows_whole_pipeline_and_where_it_is(env):
    """흐름도: 점검(앞에서부터 처음 막힌 곳) → AI 추천 → 거르기(기준별로 몇 개가 빠졌나) → 크기 → 주문 → 보유(손절~익절 사이) → 팔기 → 배우기."""
    at = env.at
    rid = env.run()
    picks = [env.pick("005930"), env.pick("000660", win_prob=40), env.pick("035420", score=50)]
    assert env.enter(rid, picks) == 1
    F = at.flow(rid)
    assert F["ok"] and all(g["st"] == "pass" for g in F["gates"]) and F["gates"][0]["code"] == "closed"
    fu = F["funnel"]
    assert (fu["picks"], fu["buy"]) == (3, 1)
    st = {x["code"]: x for x in fu["steps"]}
    assert st["winprob"]["out"] == 1 and st["score"]["out"] == 1 and st["score"]["in"] == 2 and "이상" in st["winprob"]["rule"]
    assert F["ai"]["full"] == 1 and F["ai"]["exclude"] == 1 and F["ai"]["engine"]["ladder"][0]["label"]
    assert len(F["hold"]) == 1 and F["hold"][0]["tp"] == 3.0 and F["hold"][0]["sl"] == 1.5
    assert F["size"]["pct"] > 0 and F["learn"]["review"]["every"] == 30 and F["learn"]["engine"]["need"] > 0
    # 막힌 곳: 그 앞은 통과, 막힌 곳 표시, 그 뒤는 아직
    env.hm = "15:10"
    F = at.flow(rid)
    S = [g["st"] for g in F["gates"]]
    i = next(k for k, g in enumerate(F["gates"]) if g["code"] == "time")
    assert not F["ok"] and S[:i] == ["pass"] * i and S[i] == "stop" and set(S[i + 1:]) == {"wait"} and F["gates"][i]["why"]
    # 팔면 그 사유로 묶어 보여 준다
    env.hm = "10:40"
    env.px["005930"] = 72_500.0
    at._manage(at._run(rid))
    F = at.flow(rid)
    assert F["exits"] and F["exits"][0]["reason"] == "익절" and F["exits"][0]["win"] == 1 and not F["hold"]
    # 멈춘 자동매매는 맨 앞에서 멈춤
    at.control(rid, "pause")
    assert at.flow(rid)["gates"][0]["st"] == "stop"


def test_flow_route(rt):
    from moa.app import create_app
    c = create_app(rt).test_client()
    assert c.get("/api/auto/runs/999/flow").status_code == 404


def test_review_uses_its_own_engine(env):
    """복기(자동매매 복기·추천 성적 복기)는 따로 정한 엔진(기본 Opus)으로. 추천 엔진 설정과는 별개."""
    ai = env.rt.ai
    assert ai.meter("auto")["model"] == "claude-opus-5-5" and ai.meter("learn")["model"] == "claude-opus-5-5"
    assert ai.meter("chat")["model"] == "claude-sonnet-5-5"
    env.rt.settings.update({"ai": {"review_model": "claude-fable-5-2"}})
    assert ai.meter("learn")["model"] == "claude-fable-5-2" and ai.status()["review_model"] == "claude-fable-5-2"
    env.rt.settings.update({"ai": {"review_model": "없는 엔진"}})
    assert ai.review_model() == "claude-opus-5-5"
    rid = env.run()
    assert env.at.flow(rid)["learn"]["review"]["model"].startswith("Opus")
