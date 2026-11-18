"""AI 자동매매: 플랜·매수 조건·청산·손실 한도·분석·개선안 승인·실전 체결 맞추기·API."""
import json
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import moa.aipick as AP  # noqa: E402
from moa.app import create_app  # noqa: E402
from moa.autotrade import PLAN_KEYS, _clamp_key, make_plan  # noqa: E402
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

    def run(self, **kw):
        d = {"budget": 3_000_000, "mkt": "kr", "style": "day", "days": 30, "mode": "paper"}
        d.update(kw)
        return self.at.create(d)["run"]["id"]

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
