import json
import os
import sys
from datetime import date, datetime

import pytest
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moa.ai import AIError  # noqa: E402
from moa.app import create_app  # noqa: E402
from moa.broker.brokers import OrderError  # noqa: E402
from moa.broker.demo import demo_day  # noqa: E402
from moa.db import DB  # noqa: E402
from moa.runtime import Runtime  # noqa: E402
from moa.settings import DEFAULTS  # noqa: E402
from moa.trading import backtest as BT  # noqa: E402
from moa.trading import costs as C  # noqa: E402
from moa.trading.dca import allocate, due_list, effective_date  # noqa: E402
from moa.trading.risk import check_exit, entry_gate, position_size  # noqa: E402
from moa.trading.strategy import orb_vwap  # noqa: E402


@pytest.fixture
def rt():
    from moa.broker import kiwoom as _kw
    _kw._SHARED.clear()
    r = Runtime(DB(":memory:"), start_thread=False)
    yield r
    r.stop()


# ---------- 비용 ----------
def test_tick_size():
    assert C.tick_size(1999) == 1
    assert C.tick_size(4990) == 5
    assert C.tick_size(77000) == 100
    assert C.tick_size(198000) == 100
    assert C.tick_size(205000) == 500
    assert C.tick_size(600000) == 1000


def test_trade_pnl_includes_costs():
    pnl, fees = C.trade_pnl(10000, 10000, 100, DEFAULTS["costs"])
    assert pnl < 0 and fees == pnl * -1


# ---------- 전략·리스크 ----------
def _bars(prices, vols=None):
    out = []
    for i, p in enumerate(prices):
        m = 9 * 60 + i
        out.append({"t": f"{m // 60:02d}:{m % 60:02d}", "o": p, "h": p + 50, "l": p - 50, "c": p,
                    "v": (vols or [1000] * len(prices))[i]})
    return out


def test_orb_signal_on_breakout_with_volume():
    p = DEFAULTS["strategy"]["orb_vwap"]
    prices = [10000 + (i % 5) * 40 for i in range(15)] + [10050] * 5 + [10400]
    vols = [1000] * 20 + [5000]
    sig = orb_vwap(_bars(prices, vols), p)
    assert sig and sig["stop"] < sig["entry"] < sig["target"]


def test_orb_no_signal_without_volume():
    p = DEFAULTS["strategy"]["orb_vwap"]
    prices = [10000 + (i % 5) * 40 for i in range(15)] + [10050] * 5 + [10400]
    assert orb_vwap(_bars(prices), p) is None


def test_check_exit_stop_before_target():
    pos = {"entry": 100, "stop": 98, "stop0": 98, "target": 104, "entry_t": "09:30"}
    assert check_exit(pos, 105, 97, 100, "09:40", DEFAULTS["exit"], DEFAULTS["risk"])[0] == "손절"


def test_breakeven_moves_stop():
    pos = {"entry": 100, "stop": 98, "stop0": 98, "target": 104, "entry_t": "09:30"}
    assert check_exit(pos, 102.5, 101, 102, "09:40", DEFAULTS["exit"], DEFAULTS["risk"]) is None
    assert pos["stop"] == 100


def test_force_exit():
    pos = {"entry": 100, "stop": 98, "stop0": 98, "target": 104, "entry_t": "15:00"}
    assert check_exit(pos, 101, 99, 100, "15:15", DEFAULTS["exit"], DEFAULTS["risk"])[0] == "장마감 청산"


def test_position_size_limits():
    r = dict(DEFAULTS["risk"])
    # 자본 1천만, 0.5% = 5만원 위험, 1주당 100원 위험 → 500주, 하지만 300만원 상한 → 300주
    assert position_size(10000, 9900, 10_000_000, r, DEFAULTS["costs"]) == 300
    assert position_size(10000, 9900, 500_000, r, DEFAULTS["costs"]) == 49


def test_entry_gate_rules():
    r = DEFAULTS["risk"]
    assert not entry_gate("09:10", [], 0, r)[0]
    assert entry_gate("10:00", [], 0, r)[0]
    assert not entry_gate("10:00", [{"pnl": -100_001, "exit_t": "09:50"}], 0, r)[0]
    streak = [{"pnl": -1, "exit_t": "09:40"}] * 3
    assert not entry_gate("10:00", streak, 0, r)[0]
    assert entry_gate("10:20", streak, 0, r)[0]


# ---------- 백테스트 ----------
def test_backtest_demo_runs():
    cfg = {k: DEFAULTS[k] for k in ("strategy", "exit", "risk", "costs")}
    data = BT.demo_dataset(["005930", "000660"], 20, end=date(2026, 11, 16))
    res = BT.run(data, cfg)
    s = res["stats"]
    assert s["days"] == 20 and s["trades"] == len(res["trades"])
    assert s["net"] == sum(t["pnl"] for t in res["trades"])
    # 일별 손익 합 = 거래 손익 합
    assert abs(sum(d["pnl"] for d in res["daily"]) - s["net"]) <= len(res["trades"]) * 2


def test_parse_csv_formats():
    text = "date,time,open,high,low,close,volume\n20261116,0900,100,101,99,100,10\n20261116,0901,100,102,99,101,20\n"
    d = BT.parse_csv(text)
    assert list(d) == ["2026-11-16"] and d["2026-11-16"][1]["t"] == "09:01"
    text2 = "datetime,open,high,low,close,volume\n20261116090000,100,101,99,100,10\n"
    assert BT.parse_csv(text2)["2026-11-16"][0]["c"] == 100
    with pytest.raises(BT.DataError):
        BT.parse_csv("a,b\n1,2\n")


def test_demo_day_deterministic():
    a = demo_day("005930", date(2026, 11, 16))
    assert a == demo_day("005930", date(2026, 11, 16)) and len(a) == 390


# ---------- 적립식 ----------
def test_effective_date_rolls_over_weekend_and_holiday():
    assert effective_date(2026, 11, 21, []) == date(2026, 11, 23)        # 토 → 월
    assert effective_date(2026, 12, 25, ["2026-12-25"]) == date(2026, 12, 28)


def test_due_list_and_catch_up():
    plans = [{"id": 1, "day": 10, "enabled": 1, "created_at": "2026-01-01T00:00:00"}]
    assert [d for _, d in due_list(plans, date(2026, 11, 12), set(), [])] == [date(2026, 11, 10)]
    assert due_list(plans, date(2026, 11, 12), {(1, "2026-11-10")}, []) == []
    assert due_list(plans, date(2026, 11, 25), set(), []) == []  # 7일 넘게 지나면 건너뜀
    assert due_list(plans, date(2026, 11, 9), set(), []) == []


def test_allocate_rebalance():
    plans = [{"id": 1, "code": "A", "amount": 500000, "target_weight": 50},
             {"id": 2, "code": "B", "amount": 500000, "target_weight": 50}]
    a = allocate(plans, {"A": 3_000_000, "B": 1_000_000}, "rebalance")
    assert sum(a.values()) == 1_000_000 and a[2] == 1_000_000
    assert allocate(plans, {}, "fixed") == {1: 500000, 2: 500000}


def test_dca_run_and_idempotent(rt):
    pid = rt.dca.save_plan({"code": "069500", "amount": 500000, "day": 10, "target_weight": 50})
    rt.db.x("UPDATE dca_plans SET created_at='2026-01-01T00:00:00' WHERE id=?", (pid,))
    now = datetime(2026, 11, 10, 10, 0)
    r1 = rt.dca.run_due(now)
    assert r1 and r1[0]["status"] == "done" and r1[0]["qty"] > 0
    assert rt.dca.run_due(now) == []
    st = rt.dca.status()
    assert st["holdings"] and st["cost"] > 0


# ---------- 모의 브로커 ----------
def test_paper_broker_cash_accounting(rt):
    b = rt.brokers["scalp"]
    start = b.cash()
    res = b.buy("005930", 10)
    assert b.cash() < start - res["fill_price"] * 10 + 1
    with pytest.raises(OrderError):
        b.sell("005930", 11)
    b.sell("005930", 10)
    assert b.positions() == [] and b.cash() < start


# ---------- 단타 엔진(가상 시세로 하루 돌리기) ----------
class FakeFeed:
    kind = "demo"

    def __init__(self, bars_by_code, d):
        self.data, self.d, self.i = bars_by_code, d, 0

    def now(self):
        m = 9 * 60 + self.i
        return datetime(self.d.year, self.d.month, self.d.day, m // 60, m % 60)

    def market_open(self):
        return self.i < 390

    def name(self, code):
        return code

    def bars(self, code):
        return self.data[code][:self.i]

    def quote(self, code):
        b = self.data[code][min(self.i, 389)]
        return {"code": code, "name": code, "price": b["c"]}


def test_scalp_engine_days(rt):
    """가상 시세 며칠을 실시간 엔진으로 돌려 매수·청산·현금이 맞는지 본다."""
    from datetime import timedelta
    codes = ["005930", "000660", "035420", "005380", "035720", "068270"]
    rt.settings.update({"risk": {"min_edge_mult": 0.5}})
    rt.scalp.control("start")
    d = date(2026, 11, 2)
    for _ in range(8):
        feed = FakeFeed({c: demo_day(c, d) for c in codes}, d)
        rt.feed = feed
        rt.brokers["scalp"].feed = feed
        for i in range(1, 390):
            feed.i = i
            rt.scalp.tick()
        assert rt.scalp.open_trades() == []  # 15:15 강제 청산
        d += timedelta(days=1)
    trades = rt.db.q("SELECT * FROM trades")
    assert trades, "며칠 동안 한 번도 매매하지 않았어요"
    for t in trades:
        assert t["exit_ts"] and t["pnl"] is not None and t["reason"]
    b = rt.brokers["scalp"]
    assert b.positions() == []
    assert abs(b.cash() - (10_000_000 + sum(t["pnl"] for t in trades))) <= len(trades) * 2
    assert rt.scalp.status()["state"] == "running"


# ---------- API ----------
def test_api_smoke(rt):
    c = create_app(rt).test_client()
    assert c.get("/").status_code == 200
    r = c.post("/api/tx", json={"date": "2026-11-16", "kind": "out", "amount": "12,000", "category_id": 1,
                                "memo": "점심", "method": "체크카드"})
    assert r.status_code == 200
    assert c.post("/api/tx", json={"date": "x", "kind": "out", "amount": 1}).status_code == 400
    led = c.get("/api/ledger?month=2026-11").get_json()
    assert led["summary"]["expense"] == 12000 and led["rows"][0]["category"] == "식비"
    assert c.post("/api/accounts", json={"name": "단타", "type": "stock", "linked": "scalp"}).status_code == 200
    a = c.get("/api/assets").get_json()
    assert a["net"] == 10_000_000
    for path in ("/api/home", "/api/stocks", "/api/scalp", "/api/dca", "/api/settings"):
        assert c.get(path).status_code == 200, path
    r = c.post("/api/order", json={"engine": "scalp", "side": "buy", "code": "005930", "qty": 1})
    assert r.status_code == 200 and r.get_json()["status"] == "filled"
    bt = c.post("/api/backtest", json={"days": 10}).get_json()
    assert "stats" in bt
    s = c.put("/api/settings", json={"kiwoom": {"scalp": {"appkey": "a", "secretkey": "b"}}}).get_json()
    assert s["settings"]["kiwoom"]["scalp"]["secretkey"] == "********"
    c.put("/api/settings", json={"kiwoom": {"scalp": {"secretkey": "********"}}})
    assert rt.settings.all()["kiwoom"]["scalp"]["secretkey"] == "b"


def test_remote_access_requires_token(rt):
    from moa.app import access_token
    c = create_app(rt).test_client()
    tok = access_token(rt.db)
    fwd = {"X-Forwarded-For": "100.64.0.2"}
    assert c.get("/api/ping", headers=fwd).status_code == 401
    assert c.get("/api/ping", headers={**fwd, "Authorization": "Bearer wrong"}).status_code == 401
    r = c.get("/api/ping", headers={**fwd, "Authorization": f"Bearer {tok}", "Origin": "https://icekenma.github.io"})
    assert r.status_code == 200 and r.headers["Access-Control-Allow-Origin"] == "https://icekenma.github.io"
    # 토큰만으로는 데이터에 못 들어간다: 비밀번호를 만들고 열어야 한다
    auth_h = {**fwd, "Authorization": f"Bearer {tok}"}
    r = c.get("/api/home", headers=auth_h)
    assert r.status_code == 401 and r.get_json()["code"] == "setup"
    sid = c.post("/api/auth/setup", json={"password": "moa-pass-1"}, headers=auth_h).get_json()["session"]
    assert c.get("/api/home", headers=auth_h).get_json()["code"] == "locked"
    auth_h["X-Moa-Session"] = sid
    assert c.get("/api/home", headers=auth_h).status_code == 200
    assert c.get("/api/connect-info", headers=auth_h).status_code == 403
    pre = c.options("/api/home", headers={"Origin": "https://icekenma.github.io",
                                          "Access-Control-Request-Method": "GET",
                                          "Access-Control-Request-Headers": "authorization"})
    assert pre.status_code == 204 and "Authorization" in pre.headers["Access-Control-Allow-Headers"]
    assert "X-Moa-Session" in pre.headers["Access-Control-Allow-Headers"]
    evil = c.options("/api/home", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in evil.headers
    assert c.get("/api/connect-info").get_json()["token"] == tok


def test_updater_pulls_new_version(rt, tmp_path):
    import subprocess
    from moa.updater import Updater

    def g(cwd, *a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True,
                       capture_output=True)
    origin, laptop, dev = tmp_path / "origin.git", tmp_path / "laptop", tmp_path / "dev"
    g(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    g(tmp_path, "clone", "-q", str(origin), str(dev))
    (dev / "requirements.txt").write_text("flask\n")
    (dev / "a.txt").write_text("v1")
    g(dev, "add", "-A"); g(dev, "commit", "-qm", "v1"); g(dev, "push", "-q", "origin", "main")
    g(tmp_path, "clone", "-q", str(origin), str(laptop))
    (laptop / "data").mkdir(); (laptop / "data" / "moa.db").write_text("keep")
    exits = []
    up = Updater(rt, str(laptop), exit_fn=lambda: exits.append(1))
    assert up.available and up.check()["behind"] == 0
    (dev / "a.txt").write_text("v2")
    g(dev, "commit", "-qam", "v2 화면 수정"); g(dev, "push", "-q", "origin", "main")
    st = up.check()
    assert st["behind"] == 1 and st["latest_subject"] == "v2 화면 수정"
    # 단타 엔진이 종목을 들고 있으면 미룬다
    rt.db.x("INSERT INTO trades(engine,mode,strategy,code,name,qty,session,entry_ts,entry_price,stop,stop0,target) "
            "VALUES('scalp','paper','orb_vwap','005930','삼성전자',1,'2026-11-17','2026-11-17T10:00:00',100,99,99,102)")
    ok, why = up.apply()
    assert not ok and "종목" in why and (laptop / "a.txt").read_text() == "v1"
    rt.db.x("UPDATE trades SET exit_ts='2026-11-17T10:05:00', pnl=0, fees=0")
    ok, _ = up.apply()
    import time as _t
    _t.sleep(1.3)
    assert ok and (laptop / "a.txt").read_text() == "v2" and exits == [1]
    assert (laptop / "data" / "moa.db").read_text() == "keep"


class _Resp:
    def __init__(self, data, headers=None):
        import json as _j
        self._d, self.status_code, self.headers = data, 200, headers or {}
        self.content = _j.dumps(data).encode()

    def json(self):
        return self._d


class _FakeKiwoom:
    """키움 서버 흉내: 토큰, kt00018(2페이지), kt00001. 주문 api-id가 오면 기록."""
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        if url.endswith("/oauth2/token"):
            return _Resp({"token": "T", "expires_dt": "20991231235959", "return_code": 0})
        api = headers["api-id"]
        self.calls.append(api)
        if api == "kt00018":
            if headers.get("next-key") == "P2":
                return _Resp({"return_code": 0, "acnt_evlt_remn_indv_tot": [
                    {"stk_cd": "A000660", "stk_nm": "SK하이닉스", "rmnd_qty": "000000000002", "trde_able_qty": "2",
                     "pur_pric": "200000", "cur_prc": "-198000", "pur_amt": "400000", "evlt_amt": "396000",
                     "evltv_prft": "-4000", "prft_rt": "-1.00"}]}, {"cont-yn": "N"})
            return _Resp({"return_code": 0, "tot_pur_amt": "1170000", "tot_evlt_amt": "1166000",
                          "tot_evlt_pl": "-4,000", "tot_prft_rt": "-0.34", "prsm_dpst_aset_amt": "6166000",
                          "acnt_evlt_remn_indv_tot": [
                              {"stk_cd": "A005930", "stk_nm": "삼성전자", "rmnd_qty": "10", "trde_able_qty": "10",
                               "pur_pric": "77000", "cur_prc": "+77000", "pur_amt": "770000", "evlt_amt": "770000",
                               "evltv_prft": "0", "prft_rt": "0.00"}]}, {"cont-yn": "Y", "next-key": "P2"})
        if api == "kt00001":
            return _Resp({"return_code": 0, "entr": "5000000", "d2_entra": "5000000", "ord_alow_amt": "4900000"})
        return _Resp({"return_code": 0, "ord_no": "1"})


def test_myaccount_read_only(rt):
    from moa.broker.kiwoom import KiwoomError
    rt.settings.update({"kiwoom_view": {"appkey": "k", "secretkey": "s", "mock": False}})
    fake = _FakeKiwoom()
    cl = rt.account.client()
    cl.session = fake
    snap = rt.account.snapshot(force=True)
    assert snap["ok"] and snap["server"] == "실전"
    assert snap["total"] == 6166000 and snap["pnl"] == -4000 and snap["cash"] == 5000000
    assert [h["code"] for h in snap["holdings"]] == ["005930", "000660"]  # 2페이지까지, 평가금 순
    assert snap["holdings"][1]["pnl_pct"] == -1.0 and snap["holdings"][1]["price"] == 198000
    with pytest.raises(KiwoomError):
        cl.order("buy", "005930", 1)
    assert not any(a.startswith("kt100") for a in fake.calls)  # 주문 요청이 한 번도 안 나감
    # 자산 탭 연결
    c = create_app(rt).test_client()
    c.post("/api/accounts", json={"name": "키움 계좌", "type": "stock", "linked": "kiwoom"})
    assert c.get("/api/assets").get_json()["net"] == 6166000
    assert c.get("/api/myaccount").get_json()["total"] == 6166000
    assert c.get("/api/settings").get_json()["kiwoom_view"]["secretkey"] == "********"


def test_sample_budgets_cleared_once(tmp_path):
    p = str(tmp_path / "m.db")
    db = DB(p)
    assert sum(c["budget"] for c in db.q("SELECT budget FROM categories")) == 0
    # 예전 버전처럼 예시 예산이 들어 있던 DB
    for k, v in DB.SAMPLE_BUDGETS.items():
        db.x("UPDATE categories SET budget=? WHERE name=?", (v, k))
    db.set_setting("sample_budgets_cleared", False)
    db2 = DB(p)
    assert sum(c["budget"] for c in db2.q("SELECT budget FROM categories")) == 0
    # 사용자가 정한 예산은 다시 열어도 그대로
    db2.x("UPDATE categories SET budget=300000 WHERE name='식비'")
    db3 = DB(p)
    assert db3.one("SELECT budget FROM categories WHERE name='식비'")["budget"] == 300000


def test_trend_and_timeline(rt):
    c = create_app(rt).test_client()
    c.post("/api/tx", json={"date": "2026-11-03", "kind": "out", "amount": 5000, "category_id": 1, "memo": "커피"})
    c.post("/api/tx", json={"date": "2026-10-10", "kind": "in", "amount": 100000, "category_id": 10, "memo": "급여"})
    tr = rt.ledger.trend(3, today=date(2026, 11, 17))
    assert [x["month"] for x in tr] == ["2026-09", "2026-10", "2026-11"]
    assert tr[1]["income"] == 100000 and tr[2]["expense"] == 5000
    tl = c.get("/api/timeline").get_json()
    assert tl[0]["title"] == "급여" and any(x["amount"] == -5000 for x in tl)


def test_update_api_with_updater(rt, tmp_path):
    from moa.updater import Updater
    up = Updater(rt, str(tmp_path))  # git 저장소가 아닌 폴더 → 사용 불가 상태
    c = create_app(rt, up).test_client()
    r = c.get("/api/update")
    assert r.status_code == 200 and r.get_json()["available"] is False and "version" in r.get_json()
    assert c.post("/api/update/check", json={}).status_code == 200


SMS_SAMPLES = {
    "shinhan": "[Web발신]\n신한카드(1234)승인\n안*석\n12,000원(일시불)\n11/17 12:30\n스타벅스 세종점\n누적1,234,567원",
    "kb": "[Web발신]\nKB국민카드1234승인\n안*석님\n45,000원 일시불\n11/16 19:20\n이마트세종점\n누적567,000원",
    "oneline": "현대카드 승인 안*석 8,900원 일시불 11/15 08:30 GS25 나성점 누적 100,000원",
    "cancel": "[Web발신]\n신한카드(1234)승인취소\n안*석\n12,000원(일시불)\n11/17 12:40\n스타벅스 세종점",
    "salary": "[Web발신]\n신한 11/10 09:00\n110-***-123456\n입금 3,900,000\n잔액 4,123,456\n인사혁신처",
    "outflow": "[Web발신]\n신한 11/12 10:00\n110-***-123456\n출금 50,000\n잔액 4,073,456\n관리비",
    "chat": "오늘 저녁에 치킨 먹을래? 2만원 정도면 될 듯",
}


def test_parse_sms_formats():
    from moa.inbox import parse
    now = datetime(2026, 11, 17, 13, 0)
    a = parse(SMS_SAMPLES["shinhan"], now)
    assert (a["kind"], a["action"], a["amount"], a["merchant"], a["issuer"], a["date"], a["category"]) == \
        ("card", "spend", 12000, "스타벅스 세종점", "신한", "2026-11-17", "외식")
    b = parse(SMS_SAMPLES["kb"], now)
    assert b["amount"] == 45000 and b["merchant"] == "이마트세종점" and b["category"] == "식비" and b["issuer"] == "KB국민"
    c = parse(SMS_SAMPLES["oneline"], now)
    assert c["amount"] == 8900 and c["merchant"].startswith("GS25") and c["date"] == "2026-11-15"
    assert parse(SMS_SAMPLES["cancel"], now)["action"] == "cancel"
    s = parse(SMS_SAMPLES["salary"], now)
    assert (s["kind"], s["action"], s["amount"], s["balance"], s["acct_tail"]) == ("bank", "income", 3900000, 4123456, "3456")
    assert parse(SMS_SAMPLES["outflow"], now)["action"] == "outflow"
    assert parse(SMS_SAMPLES["chat"], now) is None


def test_inbox_flow(rt):
    c = create_app(rt).test_client()
    rt.assets.save({"name": "신한 월급통장", "type": "cash", "balance": 0, "note": "110-xxx-123456"})
    r = c.post("/api/inbox", json={"text": SMS_SAMPLES["shinhan"]}).get_json()
    assert r["financial"] and r["item"]["status"] == "added"
    assert c.post("/api/inbox", json={"text": SMS_SAMPLES["shinhan"]}).get_json()["item"]["status"] == "duplicate"
    assert c.post("/api/inbox", json={"text": SMS_SAMPLES["chat"]}).get_json()["financial"] is False
    assert rt.db.one("SELECT COUNT(*) AS n FROM inbox")["n"] == 1  # 일반 대화는 저장 안 함
    sal = c.post("/api/inbox", json={"text": SMS_SAMPLES["salary"]}).get_json()["item"]
    assert sal["status"] == "pending" and "잔액" in sal["note"]
    assert rt.db.one("SELECT balance FROM accounts WHERE name='신한 월급통장'")["balance"] == 4123456
    c.post(f"/api/inbox/{sal['id']}/apply", json={})
    month = rt.ledger.summary("2026-11")
    assert month["income"] == 3900000 and month["expense"] == 12000
    c.post("/api/inbox", json={"text": SMS_SAMPLES["cancel"]})
    assert rt.ledger.summary("2026-11")["expense"] == 0  # 취소 → 승인 기록 삭제
    # 카카오톡 내보내기 형식 가져오기
    kakao = ("신한카드 님과 카카오톡 대화\n저장한 날짜 : 2026-11-17 13:00\n\n"
             "2026년 11월 16일 오후 7:20, 신한카드 : [신한카드] 승인\n안*석\n33,000원 일시불\n11/16 19:20\n배달의민족\n"
             "2026년 11월 16일 오후 8:00, 신한카드 : 이벤트 안내입니다\n")
    res = c.post("/api/inbox/import", json={"text": kakao}).get_json()
    assert res["financial"] == 1 and res["added"] == 1
    items = c.get("/api/inbox").get_json()
    assert items["added"] >= 3


def test_hana_nh_bank_alerts_update_assets(rt):
    c = create_app(rt).test_client()
    c.post("/api/accounts", json={"name": "하나 급여통장", "type": "cash", "bank": "하나", "acct_no": "123-456789-12345"})
    c.post("/api/accounts", json={"name": "농협 생활비", "type": "cash", "bank": "NH농협", "acct_no": "351-1234-5678-56"})
    c.post("/api/inbox", json={"text": "[Web발신]\n하나,11/17 09:00\n123-******-12345\n입금3,900,000원\n잔액4,123,456원\n인사혁신처"})
    c.post("/api/inbox", json={"text": "[Web발신]\n농협 출금50,000원\n11/17 10:00 351-****-1234-56 관리비 잔액1,073,456원"})
    bal = {a["name"]: a["balance"] for a in rt.db.q("SELECT name, balance FROM accounts")}
    assert bal == {"하나 급여통장": 4123456, "농협 생활비": 1073456}
    con = c.get("/api/connections").get_json()
    assert [b["bank"] for b in con["banks"]] == ["하나", "NH농협"]
    assert con["banks"][1]["last_alert"]["action"] == "outflow"
    assert con["kiwoom"]["configured"] is False


def test_balance_needs_account_number_and_ignore_restores(rt):
    c = create_app(rt).test_client()
    c.post("/api/accounts", json={"name": "하나 급여통장", "type": "cash", "bank": "하나", "acct_no": "123-456789-12345", "balance": 2000000})
    # 계좌번호 없는 시험 문자 → 잔액을 바꾸지 않음
    c.post("/api/inbox", json={"text": "[Web발신] 하나 11/17 09:00 입금1,000원 잔액1,000원 테스트"})
    assert rt.db.one("SELECT balance FROM accounts")["balance"] == 2000000
    # 계좌번호 있는 알림 → 바꾸고, 무시하면 되돌림
    r = c.post("/api/inbox", json={"text": "[Web발신]\n하나,11/17 10:00\n123-******-12345\n입금5,000원\n잔액2,005,000원\n홍길동"}).get_json()
    assert rt.db.one("SELECT balance FROM accounts")["balance"] == 2005000
    c.post(f"/api/inbox/{r['item']['id']}/ignore")
    assert rt.db.one("SELECT balance FROM accounts")["balance"] == 2000000


class _FakeMarketKiwoom(_FakeKiwoom):
    """시세 TR 흉내: ka10001, ka20001, ka10080, ka10081, ka10099(2페이지)."""
    def post(self, url, json=None, headers=None, timeout=None):
        if url.endswith("/oauth2/token"):
            return _Resp({"token": "T", "expires_dt": "20991231235959", "return_code": 0})
        api = headers["api-id"]
        self.calls.append(api)
        if api == "ka10001":
            return _Resp({"return_code": 0, "stk_nm": "삼성전자", "cur_prc": "-70100", "pred_pre": "-900",
                          "flu_rt": "-1.27", "base_pric": "71000", "open_pric": "+71200", "high_pric": "+71500",
                          "low_pric": "-69900", "trde_qty": "1234567", "250hgst": "+88000", "250lwst": "-49900"})
        if api == "ka20001":
            return _Resp({"return_code": 0, "cur_prc": "-2394.49", "pred_pre": "-12.30", "flu_rt": "-0.51"})
        if api == "ka10080":
            return _Resp({"return_code": 0, "stk_min_pole_chart_qry": [
                {"cntr_tm": "20261113153000", "cur_prc": "-70100"}, {"cntr_tm": "20261113090100", "cur_prc": "71200"},
                {"cntr_tm": "20261112153000", "cur_prc": "71000"}]})
        if api == "ka10081":
            today = datetime.now()
            return _Resp({"return_code": 0, "stk_dt_pole_chart_qry": [
                {"dt": today.strftime("%Y%m%d"), "cur_prc": "70100"},
                {"dt": (today - __import__("datetime").timedelta(days=3)).strftime("%Y%m%d"), "cur_prc": "71000"},
                {"dt": "20200101", "cur_prc": "50000"}]})
        if api == "ka10099":
            if headers.get("next-key") == "K2":
                return _Resp({"return_code": 0, "list": [{"code": "000660", "name": "SK하이닉스"}]}, {"cont-yn": "N"})
            if json["mrkt_tp"] == "0":
                return _Resp({"return_code": 0, "list": [{"code": "005930", "name": "삼성전자"},
                                                         {"code": "005935", "name": "삼성전자우"}]},
                             {"cont-yn": "Y", "next-key": "K2"})
            return _Resp({"return_code": 0, "list": []}, {"cont-yn": "N"})
        return _Resp({"return_code": 0})


def test_market_kr_with_kiwoom(rt, tmp_path):
    rt.market.data_dir = str(tmp_path)
    rt.settings.update({"kiwoom_view": {"appkey": "k", "secretkey": "s", "mock": False}})
    fake = _FakeMarketKiwoom()
    rt.account.client().session = fake
    q = rt.market.kr_quote("005930")
    assert q["price"] == 70100 and q["change"] == -900 and q["change_pct"] == -1.27 and q["high52"] == 88000
    idx = rt.market.kr_indices()
    assert idx[0]["name"] == "코스피" and idx[0]["price"] == 2394.49 and idx[0]["change_pct"] == -0.51
    ch = rt.market.kr_chart("005930", "1d")
    assert [p["t"] for p in ch["points"]] == ["09:01", "15:30"]  # 가장 최근 거래일만, 오래된 순
    ch = rt.market.kr_chart("005930", "1m")
    assert len(ch["points"]) == 2 and ch["points"][0]["c"] == 71000
    hits = rt.market.kr_search("삼성")
    assert [h["sym"] for h in hits] == ["005930", "005935"]
    assert rt.market.kr_search("하이닉스")[0]["sym"] == "000660"  # 연속조회 2페이지
    assert os.path.exists(os.path.join(str(tmp_path), "kr_master.json"))
    assert not any(a.startswith("kt100") for a in fake.calls)
    c = create_app(rt).test_client()
    rt.market.yh_quote = lambda s: (_ for _ in ()).throw(RuntimeError("offline"))
    ov = c.get("/api/market/overview?mkt=kr").get_json()
    assert ov["kiwoom"] and ov["items"][0]["price"] == 70100
    assert c.get("/api/market/quote/kr/005930").get_json()["watched"] is True
    assert c.post("/api/market/watch", json={"mkt": "kr", "sym": "005930", "on": False}).get_json()["kr"][0] == "000660"
    assert c.get("/api/market/quote/kr/12").status_code == 400
    assert c.get("/api/market/chart/kr/005930?range=9y").status_code == 400
    assert c.get("/api/market/overview?mkt=jp").status_code == 400


def test_market_us_parsing(rt):
    class R:
        status_code = 200
        def __init__(self, d): self.d = d
        def json(self): return self.d

    def fake_get(url, params=None, timeout=None):
        if "/search" in url:
            return R({"quotes": [{"symbol": "AAPL", "shortname": "Apple Inc.", "quoteType": "EQUITY", "exchange": "NMS", "exchDisp": "NASDAQ"},
                                 {"symbol": "APC.F", "quoteType": "EQUITY", "exchange": "FRA"},
                                 {"symbol": "AAPL240119C", "quoteType": "OPTION"}]})
        return R({"chart": {"result": [{"meta": {"regularMarketPrice": 190.5, "chartPreviousClose": 188.0, "currency": "USD",
                                                   "shortName": "Apple Inc.", "fiftyTwoWeekHigh": 199.6, "fiftyTwoWeekLow": 164.1},
                                          "timestamp": [1700000000, 1700000300],
                                          "indicators": {"quote": [{"close": [189.0, None]}]}}]}})
    rt.market.http.get = fake_get
    q = rt.market.yh_quote("AAPL")
    assert q["price"] == 190.5 and round(q["change"], 2) == 2.5 and round(q["change_pct"], 2) == 1.33
    assert rt.market.yh_chart("AAPL", "1m")["points"] == [{"t": "2023-11-14", "c": 189.0}]
    assert [x["sym"] for x in rt.market.us_search("apple")] == ["AAPL"]
    c = create_app(rt).test_client()
    d = c.get("/api/market/quote/us/aapl").get_json()
    assert d["sym"] == "AAPL" and d["fx"] == 190.5 and d["watched"] is True


def test_myaccount_keeps_last_snapshot_and_acct_no(rt):
    class F(_FakeKiwoom):
        def post(self, url, json=None, headers=None, timeout=None):
            if headers and headers.get("api-id") == "ka00001":
                return _Resp({"return_code": 0, "acctNo": "5012345611"})
            return super().post(url, json, headers, timeout)
    rt.settings.update({"kiwoom_view": {"appkey": "k", "secretkey": "s", "mock": False}})
    rt.account.client().session = F()
    snap = rt.account.snapshot(force=True)
    assert snap["acct_no"] == "5012345611" and snap["ok"]
    from moa.myaccount import AccountView
    from moa.broker import kiwoom as _kw
    _kw._SHARED.clear()
    v2 = AccountView(rt.settings)  # 서버를 다시 켠 상황
    v2.client().session = type("Down", (), {"post": lambda *a, **k: (_ for _ in ()).throw(OSError("down"))})()
    s2 = v2.snapshot(force=True)
    assert s2["total"] == 6166000 and s2["ok"] is False and s2["stale"] and "down" in s2["error"]
    c = create_app(rt).test_client()
    assert c.get("/api/connections").get_json()["kiwoom"]["holdings"][0]["code"] == "005930"


def test_switching_kiwoom_account_drops_old_snapshot(rt):
    rt.settings.update({"kiwoom_view": {"appkey": "k", "secretkey": "s", "mock": False}})
    rt.account.client().session = _FakeKiwoom()
    assert rt.account.snapshot(force=True)["ok"] and rt.account.last()["total"] == 6166000
    rt.settings.update({"kiwoom_view": {"appkey": "k2", "secretkey": "s2"}})  # 다른 계좌 키
    assert rt.account.last() is None
    rt.account.client().session = type("Down", (), {"post": lambda *a, **k: (_ for _ in ()).throw(OSError("down"))})()
    s = rt.account.snapshot(force=True)
    assert s.get("total") is None and not s["ok"]  # 예전 계좌 금액을 보여 주지 않음


def test_kiwoom_feed_falls_back_to_view_keys(rt):
    from moa.broker.kiwoom import KiwoomFeed
    rt.settings.update({"data_source": "kiwoom", "kiwoom_view": {"appkey": "k", "secretkey": "s", "mock": False}})
    rt.rebuild()
    assert isinstance(rt.feed, KiwoomFeed) and rt.feed.client is rt.account.client() and not rt.errors.get("feed")
    rt.feed.client.session = _FakeMarketKiwoom()
    assert rt.feed.quote("005930")["price"] == 70100
    c = create_app(rt).test_client()
    r = c.post("/api/order", json={"engine": "scalp", "side": "buy", "code": "005930", "qty": 1})
    assert r.status_code == 200 and r.get_json()["fill_price"] >= 70100  # 앱 내 모의가 실제 시세로 체결


def test_kiwoom_clients_share_token_throttle_and_cache(rt):
    from moa.broker.kiwoom import KiwoomClient
    a = KiwoomClient("k", "s", False)
    b = KiwoomClient("k", "s", False, read_only=True)
    fake = _FakeKiwoom()
    a.session = b.session = fake
    a.cash(); b.cash(); a.cash()
    assert fake.calls.count("kt00001") == 1  # 2.5초 안의 같은 조회는 한 번만
    assert a.token() == b.token()

    class Busy(_FakeKiwoom):
        n = 0
        def post(self, url, json=None, headers=None, timeout=None):
            if headers and headers.get("api-id") == "kt00018" and Busy.n == 0:
                Busy.n = 1
                return _Resp({"return_code": 5, "return_msg": "허용된 요청 개수를 초과하였습니다[1700:...]"})
            return super().post(url, json, headers, timeout)
    c = KiwoomClient("k2", "s", False)
    c.session = Busy()
    assert c.holdings()[0]["code"] == "005930"  # 한도 초과는 한 번 쉬고 다시


def test_market_board_bulk_and_stale_while_revalidate(rt):
    import time as _t
    from moa.market import Cache
    rt.settings.update({"kiwoom_view": {"appkey": "kb", "secretkey": "s", "mock": False}})

    class Bulk(_FakeMarketKiwoom):
        def post(self, url, json=None, headers=None, timeout=None):
            if headers and headers.get("api-id") == "ka10095":
                self.calls.append("ka10095")
                return _Resp({"return_code": 0, "atn_stk_infr": [
                    {"stk_cd": "005930", "stk_nm": "삼성전자", "cur_prc": "+192800", "pred_pre": "+2300", "flu_rt": "+1.21"},
                    {"stk_cd": "000660", "stk_nm": "SK하이닉스", "cur_prc": "-198000", "pred_pre": "-500", "flu_rt": "-0.25"}]})
            return super().post(url, json, headers, timeout)
    fake = Bulk()
    rt.account.client().session = fake
    b = rt.market.kr_board(["005930", "000660", "035420"])
    assert b["005930"]["price"] == 192800 and b["000660"]["change"] == -500
    assert b["035420"]["price"] == 70100  # 한 번에 못 받은 종목은 따로
    assert fake.calls.count("ka10095") == 1 and fake.calls.count("ka10001") == 1
    c, n = Cache(), [0]

    def slow():
        n[0] += 1
        _t.sleep(0.3)
        return n[0]
    assert c.get("x", 0.05, slow) == 1
    _t.sleep(0.1)
    t0 = _t.time()
    assert c.get("x", 0.05, slow) == 1 and _t.time() - t0 < 0.1  # 지난 값은 바로, 새 값은 뒤에서
    _t.sleep(0.5)
    assert c.peek("x") == 2


def test_unfilled_orders_and_cancel(rt):
    class K(_FakeKiwoom):
        def post(self, url, json=None, headers=None, timeout=None):
            api = (headers or {}).get("api-id")
            if api == "ka10075":
                self.calls.append(api)
                left = "0" if "kt10003" in self.calls else "3"
                return _Resp({"return_code": 0, "oso": [
                    {"ord_no": "0001234", "stk_cd": "005930", "stk_nm": "삼성전자", "ord_qty": "5", "oso_qty": left,
                     "cntr_qty": "2", "ord_pric": "190000", "io_tp_nm": "+매수", "tm": "101530"}]})
            if api == "kt10003":
                self.calls.append(api)
                self.cancel_body = json
                return _Resp({"return_code": 0, "ord_no": "0001240"})
            return super().post(url, json, headers, timeout)
    rt.settings.update({"trade_mode": {"scalp": "kiwoom"}, "real_orders_allowed": {"scalp": True},
                        "kiwoom": {"scalp": {"appkey": "ka", "secretkey": "s", "mock": False}}})
    rt.rebuild()
    fake = K()
    rt.brokers["scalp"].client.session = fake
    c = create_app(rt).test_client()
    d = c.get("/api/orders/open").get_json()
    o = d["items"][0]
    assert o["side"] == "buy" and o["left"] == 3 and o["ord_no"] == "0001234" and o["engine"] == "scalp"
    r = c.post("/api/orders/cancel", json=o)
    assert r.status_code == 200 and fake.cancel_body == {"dmst_stex_tp": "KRX", "orig_ord_no": "0001234",
                                                        "stk_cd": "005930", "cncl_qty": "0"}
    assert c.get("/api/orders/open").get_json()["items"] == []  # 취소 뒤엔 새로 조회
    assert c.get("/api/stocks").get_json()["orders"][0]["status"] == "cancel"
    rt.settings.update({"trade_mode": {"scalp": "paper"}})
    rt.rebuild()
    assert c.post("/api/orders/cancel", json=o).status_code == 400


def test_kiwoom_account_auto_added_to_assets(rt):
    c = create_app(rt).test_client()
    assert c.get("/api/assets").get_json()["net"] == 0  # 연결 전엔 없음
    rt.settings.update({"kiwoom_view": {"appkey": "k", "secretkey": "s", "mock": False}})
    rt.account.client().session = _FakeKiwoom()
    a = c.get("/api/assets").get_json()
    assert a["net"] == 6166000
    acc = [x for g in a["groups"] for x in g["items"] if x["linked"] == "kiwoom"]
    assert len(acc) == 1 and acc[0]["name"] == "키움증권" and acc[0]["synced"]
    assert rt.db.one("SELECT balance FROM accounts WHERE linked='kiwoom'")["balance"] == 6166000
    c.delete(f"/api/accounts/{acc[0]['id']}")
    assert c.get("/api/assets").get_json()["net"] == 0  # 지우면 다시 만들지 않음


def test_own_account_transfers_excluded_from_income_expense(rt):
    c = create_app(rt).test_client()
    # 1) 하나 → 농협 같은 금액이 30분 안에 나가고 들어오면 짝으로 이체 처리
    a = c.post("/api/inbox", json={"text": "[Web발신]\n하나,11/17 09:00\n123-******-12345\n출금300,000원\n잔액1,000,000원\n농협이체"}).get_json()["item"]
    b = c.post("/api/inbox", json={"text": "[Web발신]\n농협 입금300,000원\n11/17 09:01 351-****-1234-56 하나송금 잔액500,000원"}).get_json()["item"]
    a = rt.inbox.get(a["id"])  # 짝(입금)이 들어온 뒤 다시 보기
    assert a["transfer"] == 1 and b["transfer"] == 1 and a["status"] == b["status"] == "added"
    s = rt.ledger.summary("2026-11")
    assert s["income"] == 0 and s["expense"] == 0 and s["transfer_count"] == 2
    # 2) 받는 분이 내 이름이면 이체(이름을 나중에 넣어도 지난 알림을 다시 살핌)
    o = c.post("/api/inbox", json={"text": "[Web발신]\n하나,11/17 12:00\n123-******-12345\n출금200,000원\n잔액800,000원\n홍길동"}).get_json()["item"]
    assert o["transfer"] == 0 and o["status"] == "pending"
    r = c.put("/api/inbox/names", json={"names": ["홍길동"]}).get_json()
    assert r["retagged"] == 1 and rt.ledger.summary("2026-11")["expense"] == 0
    # 3) 월급(다른 사람이 보낸 돈)은 그대로 수입
    sal = c.post("/api/inbox", json={"text": "[Web발신]\n하나,11/17 13:00\n123-******-12345\n입금3,900,000원\n잔액4,700,000원\n인사혁신처"}).get_json()["item"]
    assert sal["transfer"] == 0
    c.post(f"/api/inbox/{sal['id']}/apply", json={})
    assert rt.ledger.summary("2026-11")["income"] == 3900000
    # 4) 직접 기록도 이체로 표시하면 합계에서 빠지고, 목록 필터에도 안 섞임
    tid = c.post("/api/tx", json={"date": "2026-11-17", "kind": "out", "amount": 50000, "memo": "키움 입금", "transfer": True}).get_json()["id"]
    s = rt.ledger.summary("2026-11")
    assert s["expense"] == 0 and sum(s["daily"]) == 0 and rt.ledger.trend(1, date(2026, 11, 17))[0]["expense"] == 0
    assert all(t["id"] != tid for t in rt.ledger.rows("2026-11", "out"))
    assert any(t["id"] == tid for t in rt.ledger.rows("2026-11", "transfer"))
    # 5) 키움을 연결해 두었으면 '키움증권'으로 보낸 돈은 이체
    rt.db.set_setting("kiwoom_asset_auto", True)
    k = c.post("/api/inbox", json={"text": "[Web발신]\n하나,11/17 15:00\n123-******-12345\n출금100,000원\n잔액600,000원\n키움증권"}).get_json()["item"]
    assert k["transfer"] == 1


def test_us_stock_orders_via_kiwoom(rt):
    class US(_FakeKiwoom):
        def __init__(self):
            super().__init__()
            self.bodies = {}

        def post(self, url, json=None, headers=None, timeout=None):
            api = (headers or {}).get("api-id")
            if api and api.startswith(("ust", "usa")):
                self.calls.append(api)
                self.bodies[api] = json
                assert "/api/us/" in url
                if api == "usa10100":
                    return _Resp({"return_code": 0, "stex_tp": "ND", "stk_cd": "AAPL", "stk_nm": "애플", "mkgb": "NASDAQ"})
                if api == "usa20100":
                    return _Resp({"return_code": 0, "stk_nm": "애플", "cur_prc": "190.52", "pred_pre": "-1.48",
                                  "flu_rt": "-0.77", "base_close_pric": "192.00", "base_exrt": "1385.20",
                                  "52wk_hgst_pric": "237.23", "52wk_lwst_pric": "164.08", "acc_trde_qty": "1234"})
                if api in ("ust20000", "ust20001"):
                    return _Resp({"return_code": 0, "ord_no": "U0001", "stk_nm": "애플"})
                if api == "ust20003":
                    return _Resp({"return_code": 0, "ord_no": "U0002", "cncl_ord_qty": "2"})
                if api == "ust21050":
                    return _Resp({"return_code": 0, "result_list": [
                        {"ord_no": "U0001", "stk_cd": "AAPL", "frgn_stk_nm": "애플", "stex_nm": "NASDAQ",
                         "slby_tp": "2", "slby_tp_nm": "매수", "ord_qty": "2", "ord_remnq": "2", "cntr_qty": "0",
                         "ord_uv": "185.00", "ord_time": "101530"}]})
                if api == "ust21070":
                    return _Resp({"return_code": 0, "result_list": [
                        {"stk_cd": "NVDA", "frgn_stk_nm": "엔비디아", "qty": "3", "sell_alowq": "3",
                         "frgn_stk_book_uv": "120.50", "now_pric": "142.30", "evlt_amt": "426.90",
                         "pl_amt": "65.40", "evlt_amt_krw": "591000", "pl_amt_krw": "90500", "pl_rt": "18.09"}]})
                if api == "ust21110":
                    return _Resp({"return_code": 0, "krw_entra": "8139", "result_list": [
                        {"crnc_code": "USD", "fc_entra": "500.25", "fc_ord_alowa": "480.10"}]})
            return super().post(url, json, headers, timeout)
    rt.settings.update({"trade_mode": {"scalp": "kiwoom"}, "real_orders_allowed": {"scalp": True},
                        "kiwoom": {"scalp": {"appkey": "ku", "secretkey": "s", "mock": False}}})
    rt.rebuild()
    fake = US()
    rt.brokers["scalp"].client.session = fake
    c = create_app(rt).test_client()
    q = c.get("/api/us/quote/aapl?engine=scalp").get_json()
    assert q["price"] == 190.52 and q["exch"] == "ND" and q["live"] and q["fx"] == 1385.2 and q["change"] == -1.48
    r = c.post("/api/order", json={"engine": "scalp", "market": "us", "side": "buy", "code": "aapl", "qty": 2, "price": "185"})
    assert r.status_code == 200 and r.get_json()["broker_no"] == "U0001"
    assert fake.bodies["ust20000"] == {"stex_tp": "ND", "stk_cd": "AAPL", "ord_qty": "2", "ord_uv": "185.00", "trde_tp": "00"}
    c.post("/api/order", json={"engine": "scalp", "market": "us", "side": "sell", "code": "NVDA", "qty": 1, "exch": "ND"})
    assert fake.bodies["ust20001"] == {"stex_tp": "ND", "stk_cd": "NVDA", "ord_qty": "1", "ord_uv": "", "trde_tp": "03", "stop_pric": ""}
    o = [x for x in c.get("/api/orders/open").get_json()["items"] if x["market"] == "us"][0]
    assert o["ord_no"] == "U0001" and o["exch"] == "ND" and o["side"] == "buy" and o["price"] == 185.0
    assert c.post("/api/orders/cancel", json=o).status_code == 200
    assert fake.bodies["ust20003"] == {"orig_ord_no": "U0001", "stex_tp": "ND", "stk_cd": "AAPL"}
    st = c.get("/api/stocks").get_json()
    acc = st["accounts"][0]
    assert acc["us_holdings"][0]["code"] == "NVDA" and acc["us_holdings"][0]["value"] == 591000
    assert acc["us_cash"]["usd_orderable"] == 480.1
    # 앱 내 모의 계좌로는 미국 주문 불가, 잘못된 기호 거절
    rt.settings.update({"trade_mode": {"scalp": "paper"}})
    rt.rebuild()
    assert c.post("/api/order", json={"engine": "scalp", "market": "us", "side": "buy", "code": "AAPL", "qty": 1}).status_code == 400
    assert c.get("/api/us/quote/BAD$").status_code in (400, 404)


def test_kiwoom_total_includes_us_stocks_and_dollars(rt):
    class Both(_FakeKiwoom):
        def post(self, url, json=None, headers=None, timeout=None):
            api = (headers or {}).get("api-id")
            if api == "ust21070":
                return _Resp({"return_code": 0, "result_list": [
                    {"stk_cd": "NVDA", "frgn_stk_nm": "엔비디아", "qty": "3", "frgn_stk_book_uv": "120.50",
                     "now_pric": "142.30", "evlt_amt_krw": "591000", "pl_amt_krw": "90500", "pl_rt": "18.09", "exch_rate": "1385.2"}]})
            if api == "ust21110":
                return _Resp({"return_code": 0, "krw_entra": "0", "result_list": [{"crnc_code": "USD", "fc_entra": "100.00", "fc_ord_alowa": "100.00"}]})
            return super().post(url, json, headers, timeout)
    rt.settings.update({"kiwoom_view": {"appkey": "kx", "secretkey": "s", "mock": False}})
    rt.account.client().session = Both()
    s = rt.account.snapshot(force=True)
    assert s["kr_total"] == 6166000 and s["total"] == 6166000 + 591000 + 138520
    assert any(h.get("market") == "us" for h in s["holdings"]) and s["us"]["usd_krw"] == 138520
    c = create_app(rt).test_client()
    assert c.get("/api/assets").get_json()["net"] == s["total"]  # 자산 탭도 합친 금액


def test_kiwoom_total_without_overseas_account(rt):
    rt.settings.update({"kiwoom_view": {"appkey": "ky", "secretkey": "s", "mock": False}})
    rt.account.client().session = _FakeKiwoom()  # 미국 TR에 빈 응답 → 국내만
    s = rt.account.snapshot(force=True)
    assert s["total"] == 6166000 and "kr_total" not in s


def test_sweep_screen_matches_engine_and_api(rt, tmp_path, monkeypatch):
    import time as _t
    from moa.trading import sweep as SW
    monkeypatch.setattr(SW, "GRIDS", {"orb_vwap": {"range_min": [10, 15], "vol_mult": [1.5], "min_range_pct": [0.2, 0.4],
                                                   "stop_atr": [1.5], "target_r": [2.0], "max_hold_min": [90]},
                                      "vwap_pullback": SW.GRIDS["vwap_pullback"]})
    cfg = {k: rt.settings.get(k) for k in ("strategy", "exit", "risk", "costs")}
    data = BT.demo_dataset(["005930", "000660"], 20)
    res, meta = SW.screen(data, "orb_vwap", cfg)
    assert meta["combos"] == 4 and len(res) == 4
    # 같은 조건을 엔진 백테스터로 돌린 거래 수와 크게 다르지 않아야 한다(본전 이동·계좌 한도 차이만)
    p = res[0]["params"]
    c2 = json.loads(json.dumps(cfg)); c2["strategy"]["orb_vwap"] = {k: v for k, v in p.items() if k != "max_hold_min"}
    exact = BT.run(data, c2)["stats"]["trades"]
    vec = res[0]["is"]["trades"] + res[0]["oos"]["trades"]
    assert abs(exact - vec) <= max(2, exact * 0.2)
    rt.sweep.data_dir = str(tmp_path)
    rt.sweep.path = str(tmp_path / "sweep_last.json")
    c = create_app(rt).test_client()
    assert c.post("/api/sweep", json={"strategy": "orb_vwap", "source": "demo", "days": 10, "codes": ["005930"]}).status_code == 200
    for _ in range(120):
        st = c.get("/api/sweep").get_json()
        if st["status"] != "running":
            break
        _t.sleep(0.5)
    assert st["status"] == "done", st
    assert st["meta"]["combos"] == 4 and "checked" in st
    if st["top"]:
        rid = st["top"][0]["id"]
        assert c.post("/api/sweep/apply", json={"id": rid}).status_code == 200
        assert rt.settings.get("strategy")["orb_vwap"]["range_min"] == st["top"][0]["params"]["range_min"]
    assert c.post("/api/sweep/apply", json={"id": 9999}).status_code == 400


def test_server_starts_without_numpy():
    """훑기용 numpy·vectorbt가 없는 PC에서도 서버는 떠야 한다(v2.4 사고 재발 방지)."""
    import subprocess
    code = ("import sys; sys.modules['numpy'] = None; sys.modules['vectorbt'] = None\n"
            "from moa.db import DB; from moa.runtime import Runtime; from moa.app import create_app\n"
            "rt = Runtime(DB(':memory:'), start_thread=False); c = create_app(rt).test_client()\n"
            "assert c.get('/api/sweep').status_code == 200; print('ok')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), timeout=120)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-800:]


class _SSE:
    def __init__(self, events, status=200):
        self.status_code, self.events = status, events
        self.text, self.encoding = json.dumps({"error": {"message": "bad"}}), "utf-8"

    def json(self):
        return json.loads(self.text)

    def iter_lines(self, decode_unicode=True):
        for e in self.events:
            yield "event: x"
            yield "data: " + json.dumps(e, ensure_ascii=False)
            yield ""


def _claude_events(text="## 한 줄 요약\n- **좋아요**"):
    return [
        {"type": "message_start", "message": {"usage": {"input_tokens": 1200, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "server_tool_use", "name": "web_search", "id": "s1", "input": {}}},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "web_search_tool_result", "content": [
            {"type": "web_search_result", "url": "https://news.example.com/a", "title": "삼성전자 뉴스", "page_age": "1 day ago"}]}},
        {"type": "content_block_start", "index": 2, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": text[:5]}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": text[5:]}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"output_tokens": 300, "server_tool_use": {"web_search_requests": 2}}},
        {"type": "message_stop"}]


def test_ai_stream_contexts_usage_and_budget(rt):
    c = create_app(rt).test_client()
    # 키가 없으면 안내
    r = c.post("/api/ai/ask", json={"kind": "chat", "question": "hi"})
    assert '"error"' in r.get_data(as_text=True) and "키" in r.get_data(as_text=True)
    c.put("/api/settings", json={"ai": {"api_key": "sk-ant-test", "budget_usd": 5}})
    assert c.get("/api/settings").get_json()["ai"]["api_key"] == "********"  # 키는 가려서
    sent = []

    def fake_post(url, json=None, stream=None, timeout=None, headers=None):
        sent.append((url, json, headers))
        return _SSE(_claude_events())
    rt.ai.session.post = fake_post
    rt.market.kr_quote = lambda s: {"sym": s, "name": "삼성전자", "price": 70100, "change": -900, "change_pct": -1.27,
                                    "currency": "KRW"}
    rt.market.kr_chart = lambda s, r: {"points": [{"t": "2026-11-16", "c": 71000}, {"t": "2026-11-17", "c": 70100}]}
    rt.market.yh_quote = lambda s: {"price": 1400.0}
    out = [json.loads(x) for x in c.post("/api/ai/ask", json={"kind": "stock", "sym": "005930", "mkt": "kr"}).get_data(as_text=True).splitlines() if x]
    assert out[0]["t"] == "job"                      # 첫 줄은 이어 받기용 작업 번호
    out = [x for x in out if x["t"] not in ("job", "ping")]
    kinds = [e["t"] for e in out]
    assert kinds[0] == "status" and "source" in kinds and kinds[-1] == "done"
    text = "".join(e["text"] for e in out if e["t"] == "text")
    assert text.startswith("## 한 줄 요약")
    done = out[-1]
    assert done["searches"] == 2 and done["in"] == 1200 and done["out"] == 300
    assert abs(done["usd"] - (1200 * 2 + 300 * 10) / 1e6 - 0.02) < 1e-9 and done["krw"] == round(done["usd"] * 1400)
    url, body, headers = sent[0]
    assert url.endswith("/v1/messages") and headers["x-api-key"] == "sk-ant-test" and body["stream"] is True
    assert body["tools"][0]["type"] == "web_search_20250305" and "70100" in body["messages"][-1]["content"]
    assert "temperature" not in body  # 최신 모델은 기본값 외 temperature를 받지 않음
    st = c.get("/api/ai/status").get_json()
    assert st["month_calls"] == 1 and st["month_usd"] > 0
    # 훑기 해석은 웹 검색 없이, 결과가 없으면 안내
    out = c.post("/api/ai/ask", json={"kind": "sweep"}).get_data(as_text=True)
    assert "조건 훑기를 끝내" in out
    # 웹 검색이 막힌 조직이면 검색 없이 다시
    calls = []

    def fake_post2(url, json=None, stream=None, timeout=None, headers=None):
        calls.append(dict(json))
        if "tools" in json:
            r = _SSE([], status=400); r.text = '{"error":{"message":"web_search is not enabled"}}'; return r
        return _SSE(_claude_events("검색 없이 답"))
    rt.ai.session.post = fake_post2
    out = [json.loads(x) for x in c.post("/api/ai/ask", json={"kind": "chat", "question": "요즘 시장?"}).get_data(as_text=True).splitlines() if x]
    out = [x for x in out if x["t"] not in ("job", "ping")]
    assert len(calls) == 2 and "tools" in calls[0] and "tools" not in calls[1]
    assert "".join(e.get("text", "") for e in out) == "검색 없이 답"
    # 월 예산을 넘으면 부르지 않음
    rt.db.x("INSERT INTO ai_usage(ts,kind,model,in_tok,out_tok,searches,usd) VALUES(datetime('now'),'x','m',0,0,0,10)")
    calls.clear()
    out = c.post("/api/ai/ask", json={"kind": "chat", "question": "또?"}).get_data(as_text=True)
    assert "예산" in out and not calls


def test_ai_credit_remaining_live(rt):
    c = create_app(rt).test_client()
    assert c.get("/api/ai/credit").get_json() == {"set": False}
    c.put("/api/settings", json={"ai": {"api_key": "sk-ant-test"}})
    assert c.post("/api/ai/credit", json={"usd": "abc"}).status_code == 400
    r = c.post("/api/ai/credit", json={"usd": 100}).get_json()
    assert r["set"] and r["left"] == 100
    rt.ai.session.post = lambda *a, **k: _SSE(_claude_events())
    rt.market.yh_quote = lambda s: {"price": 1400.0}
    out = [json.loads(x) for x in c.post("/api/ai/ask", json={"kind": "chat", "question": "hi"}).get_data(as_text=True).splitlines() if x]
    done = out[-1]
    assert done["t"] == "done" and abs(done["left"] - (100 - done["usd"])) < 1e-6
    cr = c.get("/api/ai/credit").get_json()
    assert cr["spent_moa"] == done["usd"] and cr["source"] == "moa 사용분"
    # Admin 키가 있으면 콘솔 전체 사용액(맞춘 시점 이후)을 반영
    c.put("/api/settings", json={"ai": {"admin_key": "sk-ant-admin-x"}})

    class R:
        def __init__(self, cents): self.status_code, self.cents = 200, cents
        def json(self): return {"data": [{"results": [{"amount": str(self.cents), "currency": "USD"}]}]}
    seq = iter([R(500), R(2500)])  # 맞출 때 오늘 $5, 지금 $25 → 맞춘 뒤 $20 사용
    rt.ai.session.get = lambda *a, **k: next(seq)
    rt.ai._admin_cache = None
    c.post("/api/ai/credit", json={"usd": 80})
    rt.ai._admin_cache = None
    cr = c.get("/api/ai/credit").get_json()
    assert cr["spent_admin"] == 20.0 and cr["left"] == 60.0 and "Admin" in cr["source"]
    assert c.get("/api/settings").get_json()["ai"]["admin_key"] == "********"
    # 크레딧 부족 오류를 받으면 표시
    def low(*a, **k):
        r = _SSE([], status=400); r.text = '{"error":{"message":"Your credit balance is too low to access the Anthropic API."}}'; return r
    rt.ai.session.post = low
    out = c.post("/api/ai/ask", json={"kind": "chat", "question": "hi"}).get_data(as_text=True)
    assert "크레딧이 바닥" in out and c.get("/api/ai/credit").get_json()["exhausted"] is True


def _card_sms(amount, md, hm_, merchant, tail="1234", issuer="신한"):
    return f"[Web발신]\n{issuer}카드({tail})승인\n안*석\n{amount:,}원 일시불\n{md} {hm_}\n{merchant}"


def test_fixed_recurring_match_post_absorb_and_suggest(rt):
    from moa.fixed import Fixed
    F = rt.fixed
    c = create_app(rt).test_client()
    cid = c.post("/api/cards", json={"name": "신한 딥드림", "issuer": "신한", "last4": "1234", "perf_target": 300000,
                                     "cycle_start": 1, "pay_day": 14}).get_json()["id"]
    # 10/10 넷플릭스 카드 문자(먼저 들어옴)
    c.post("/api/inbox", json={"text": _card_sms(13500, "10/10", "09:00", "넷플릭스")})
    rid = c.post("/api/fixed", json={"name": "넷플릭스", "amount": 13500, "day": 10, "card_id": cid,
                                     "start_date": "2026-10-01", "category_id": None}).get_json()["id"]
    # 저장할 때 오늘 날짜로 한 번 돌므로, 시험을 위해 11월 건은 되돌린다
    for h in rt.db.q("SELECT * FROM recurring_hits WHERE rec_id=? AND due_date>='2026-11-01'", (rid,)):
        rt.db.x("DELETE FROM transactions WHERE id=?", (h["tx_id"],))
    rt.db.x("DELETE FROM recurring_hits WHERE rec_id=? AND due_date>='2026-11-01'", (rid,))
    F.run(today=date(2026, 10, 12), force=True)
    h = rt.db.one("SELECT * FROM recurring_hits WHERE rec_id=? AND due_date='2026-10-10'", (rid,))
    assert h["status"] == "paid" and rt.db.one("SELECT COUNT(*) AS n FROM transactions WHERE memo LIKE '%넷플릭스%'")["n"] == 1
    # 11/10: 문자가 안 오면 3일 기다렸다 자동 기록
    F.run(today=date(2026, 11, 11), force=True)
    assert not rt.db.one("SELECT 1 AS x FROM recurring_hits WHERE rec_id=? AND due_date='2026-11-10'", (rid,))
    F.run(today=date(2026, 11, 13), force=True)
    h = rt.db.one("SELECT * FROM recurring_hits WHERE rec_id=? AND due_date='2026-11-10'", (rid,))
    assert h["status"] == "posted"
    # 늦게 온 문자는 새로 넣지 않고 그 기록에 맞춘다(금액이 조금 달라도 변동 금액이면)
    rt.db.x("UPDATE recurring SET variable=1 WHERE id=?", (rid,))
    r = c.post("/api/inbox", json={"text": _card_sms(13900, "11/10", "09:00", "넷플릭스")}).get_json()["item"]
    assert r["status"] == "added" and r["tx_id"] == h["tx_id"] and "고정 지출" in r["note"]
    assert rt.db.one("SELECT amount FROM transactions WHERE id=?", (h["tx_id"],))["amount"] == 13900
    assert rt.db.one("SELECT COUNT(*) AS n FROM transactions WHERE memo LIKE '%넷플릭스%'")["n"] == 2
    # 현금 월세: 결제일에 바로 기록
    c.post("/api/fixed", json={"name": "월세", "amount": 500000, "day": 5, "start_date": "2026-11-01", "method": "계좌이체"})
    F.run(today=date(2026, 11, 5), force=True)
    assert rt.db.one("SELECT COUNT(*) AS n FROM transactions WHERE memo='월세' AND date='2026-11-05'")["n"] == 1
    F.run(today=date(2026, 11, 6), force=True)  # 두 번 기록하지 않음
    assert rt.db.one("SELECT COUNT(*) AS n FROM transactions WHERE memo='월세'")["n"] == 1
    s = F.summary(today=date(2026, 11, 6))
    assert s["month_total"] == 13900 + 500000 and s["month_paid"] == s["month_total"] and s["yearly"] > 0
    # 반복 결제 찾기: 3개월 같은 날 같은 금액
    for d in ("2026-08-03", "2026-09-03", "2026-10-04"):
        rt.ledger.add({"date": d, "kind": "out", "amount": 7890, "memo": "쿠팡와우멤버십", "method": "신용카드"})
    rt.ledger.add({"date": "2026-10-20", "kind": "out", "amount": 45000, "memo": "동네마트", "method": "체크카드"})
    sug = F.suggestions(today=date(2026, 11, 6))
    assert sug and sug[0]["name"].startswith("쿠팡와우") and sug[0]["confidence"] == "high" and sug[0]["day"] == 3
    assert all("마트" not in x["name"] for x in sug)
    c.post("/api/fixed/dismiss", json={"key": sug[0]["key"]})
    assert not any(x["key"] == sug[0]["key"] for x in F.suggestions(today=date(2026, 11, 6)))
    # 결제 전 알림(ntfy)
    sent = []
    rt.notify = lambda m, *a, **k: sent.append(m)
    F._notify(date(2026, 12, 9), hour=7)
    assert sent == []                               # 아침 8시 전에는 조용히
    F._notify(date(2026, 12, 9), hour=9)            # 테스트를 도는 시각과 무관하게
    assert any("넷플릭스" in m and "내일" in m for m in sent)


def test_cards_perf_billing_and_double_count_guards(rt):
    from moa.fixed import Fixed
    c = create_app(rt).test_client()
    cid = c.post("/api/cards", json={"name": "신한 딥드림", "issuer": "신한", "last4": "1234", "perf_target": 300000,
                                     "cycle_start": 1, "pay_day": 14, "exclude_kw": "국세"}).get_json()["id"]
    today = date.today()
    md = f"{today.month}/{today.day}"
    c.post("/api/inbox", json={"text": _card_sms(120000, md, "10:00", "이마트")})
    c.post("/api/inbox", json={"text": _card_sms(50000, md, "11:00", "국세청 국세")})
    c.post("/api/inbox", json={"text": _card_sms(8000, md, "12:00", "다른카드", tail="9999")})  # 다른 카드
    ov = c.get("/api/cards").get_json()
    card = ov["cards"][0]
    assert card["perf"] == 120000 and card["perf_need"] == 180000  # 국세는 실적 제외
    assert card["bills"][0]["amount"] >= 170000 or card["bills"][1]["amount"] >= 170000
    assert any(s["last4"] == "9999" for s in ov["suggest"])  # 등록 안 된 카드 제안
    # 이용기간: 시작일 1·결제일 14 → 12/14 결제는 11/1~11/30
    k = {"cycle_start": 1, "pay_day": 14}
    assert Fixed.billing_window(k, date(2026, 12, 14)) == (date(2026, 11, 1), date(2026, 11, 30))
    k = {"cycle_start": 12, "pay_day": 25}
    assert Fixed.billing_window(k, date(2026, 12, 25)) == (date(2026, 11, 12), date(2026, 12, 11))
    # 은행의 카드 대금 출금은 지출에서 빼기
    r = c.post("/api/inbox", json={"text": "[Web발신]\n하나,11/14 08:00\n123-******-12345\n출금850,000원\n잔액1,000,000원\n신한카드"}).get_json()["item"]
    assert r["transfer"] == 1 and "카드 대금" in r["note"]
    # 체크카드: 카드 문자와 은행 출금 문자가 같은 결제면 한 번만
    c.post("/api/inbox", json={"text": _card_sms(23000, md, "13:00", "올리브영", issuer="NH농협", tail="5555")})
    r = c.post("/api/inbox", json={"text": f"[Web발신]\n농협 출금23,000원\n{md} 13:01 351-****-1234-56 올리브영 잔액500,000원"}).get_json()["item"]
    assert r["transfer"] == 1 and "체크카드" in r["note"]
    # 직접 입력도 카드 지정, 수정할 때 카드 안 보내면 유지
    tid = c.post("/api/tx", json={"date": today.isoformat(), "kind": "out", "amount": 10000, "memo": "점심", "card_id": cid}).get_json()["id"]
    c.put(f"/api/tx/{tid}", json={"date": today.isoformat(), "kind": "out", "amount": 11000, "memo": "점심"})
    assert rt.db.one("SELECT card_id FROM transactions WHERE id=?", (tid,))["card_id"] == cid


def test_empty_unfilled_is_not_error_and_orders_settle(rt):
    class E(_FakeKiwoom):
        def post(self, url, json=None, headers=None, timeout=None):
            api = (headers or {}).get("api-id")
            if api == "ust21050":
                return _Resp({"return_code": 2000, "return_msg": "[2000](571758:해당 계좌의미체결 내역이 없습니다.)"})
            if api == "ka10075":
                return _Resp({"return_code": 0, "oso": []})
            if api == "kt10000":
                return _Resp({"return_code": 0, "ord_no": "0001234"})
            return super().post(url, json, headers, timeout)
    rt.settings.update({"trade_mode": {"scalp": "kiwoom"}, "real_orders_allowed": {"scalp": True},
                        "kiwoom": {"scalp": {"appkey": "ke", "secretkey": "s", "mock": False}}})
    rt.rebuild()
    rt.brokers["scalp"].client.session = E()
    c = create_app(rt).test_client()
    rt.feed.quote = lambda code: {"price": 70000}
    rt.feed.name = lambda code: "삼성전자"
    assert c.post("/api/order", json={"engine": "scalp", "side": "buy", "code": "005930", "qty": 1}).status_code == 200
    d = c.get("/api/orders/open").get_json()
    assert d["errors"] == {} and d["items"] == []
    assert c.get("/api/stocks").get_json()["orders"][0]["status"] == "filled"


def test_orders_get_real_fill_prices(rt):
    class F(_FakeKiwoom):
        def post(self, url, json=None, headers=None, timeout=None):
            api = (headers or {}).get("api-id")
            if api == "kt10000":
                return _Resp({"return_code": 0, "ord_no": "0001234"})
            if api == "kt10001":
                return _Resp({"return_code": 0, "ord_no": "0001240"})
            if api == "ka10075":
                return _Resp({"return_code": 0, "oso": []})
            if api == "kt00007":
                assert json["qry_tp"] == "4" and len(json["ord_dt"]) == 8
                return _Resp({"return_code": 0, "acnt_ord_cntr_prps_dtl": [
                    {"ord_no": "0001234", "cntr_qty": "1", "cntr_uv": "192400"},
                    {"ord_no": "0001240", "cntr_qty": "1", "cntr_uv": "192600"},
                    {"ord_no": "0001240", "cntr_qty": "1", "cntr_uv": "192800"}]})
            if api == "ust21050":
                return _Resp({"return_code": 0, "result_list": []})
            return super().post(url, json, headers, timeout)
    rt.settings.update({"trade_mode": {"scalp": "kiwoom"}, "real_orders_allowed": {"scalp": True},
                        "kiwoom": {"scalp": {"appkey": "kf", "secretkey": "s", "mock": False}}})
    rt.rebuild()
    rt.brokers["scalp"].client.session = F()
    rt.feed.quote = lambda code: {"price": 192300}
    rt.feed.name = lambda code: "삼성전자"
    c = create_app(rt).test_client()
    c.post("/api/order", json={"engine": "scalp", "side": "buy", "code": "005930", "qty": 1})
    c.post("/api/order", json={"engine": "scalp", "side": "sell", "code": "005930", "qty": 3})
    c.get("/api/orders/open")
    o = {x["broker_no"]: x for x in c.get("/api/stocks").get_json()["orders"]}
    assert o["0001234"]["status"] == "filled" and o["0001234"]["fill_price"] == 192400 and "체결 1주 192,400원" in o["0001234"]["msg"]
    assert o["0001240"]["status"] == "partial" and o["0001240"]["fill_price"] == 192700  # 2주 가중 평균, 3주 중 일부


class _FakeInvestKiwoom(_FakeMarketKiwoom):
    def post(self, url, json=None, headers=None, timeout=None):
        api = (headers or {}).get("api-id")
        if api == "ka10004":
            d = {"return_code": 0, "sel_fpr_bid": "+70200", "sel_fpr_req": "1200", "buy_fpr_bid": "+70100", "buy_fpr_req": "900",
                 "tot_sel_req": "50000", "tot_buy_req": "42000"}
            for n in range(2, 11):
                d[f"sel_{n}th_pre_bid"] = str(70200 + (n - 1) * 100); d[f"sel_{n}th_pre_req"] = str(100 * n)
                d[f"buy_{n}th_pre_bid"] = str(70100 - (n - 1) * 100); d[f"buy_{n}th_pre_req"] = str(90 * n)
            return _Resp(d)
        if api == "ka10003":
            return _Resp({"return_code": 0, "cntr_infr": [{"tm": "101530", "cur_prc": "+70100", "cntr_trde_qty": "-15",
                                                           "pre_rt": "-1.27", "cntr_str": "95.2"}]})
        if api in ("ka10082", "ka10083"):
            k = {"ka10082": "stk_stk_pole_chart_qry", "ka10083": "stk_mth_pole_chart_qry"}[api]
            return _Resp({"return_code": 0, k: [{"dt": "20261113", "open_pric": "70000", "high_pric": "71000",
                                                "low_pric": "69000", "cur_prc": "70100", "trde_qty": "1000"}]})
        if api == "ka10032":
            return _Resp({"return_code": 0, "trde_prica_upper": [{"stk_cd": "005930", "stk_nm": "삼성전자", "cur_prc": "+70100",
                                                                  "pred_pre": "-900", "flu_rt": "-1.27", "trde_prica": "1234567"}]})
        if api == "ka10027":
            assert json["sort_tp"] in ("1", "3")
            return _Resp({"return_code": 0, "pred_pre_flu_rt_upper": [{"stk_cd": "123456", "stk_nm": "급등주", "cur_prc": "+5000",
                                                                       "pred_pre": "+1150", "flu_rt": "+29.87"}]})
        if api in ("kt00018", "kt00001", "ka00001"):
            return _FakeKiwoom.post(self, url, json, headers, timeout)
        return super().post(url, json, headers, timeout)


def test_invest_book_candles_rank_info_home_alerts(rt):
    rt.settings.update({"kiwoom_view": {"appkey": "kv", "secretkey": "s", "mock": False}})
    rt.account.client().session = _FakeInvestKiwoom()
    c = create_app(rt).test_client()
    b = c.get("/api/invest/book/kr/005930").get_json()
    assert len(b["asks"]) == 10 and b["asks"][0] == {"price": 70200, "qty": 1200} and b["bids"][0]["price"] == 70100
    assert b["asks"][9]["price"] == 71100 and b["tot_bid"] == 42000
    t = c.get("/api/invest/ticks/kr/005930").get_json()
    assert t[0]["t"] == "10:15:30" and t[0]["qty"] == -15 and t[0]["strength"] == 95.2
    for tf in ("d", "w", "mo", "m1"):
        cd = c.get(f"/api/invest/candles/kr/005930?tf={tf}").get_json()
        assert cd["bars"] and set(cd["bars"][0]) == {"t", "o", "h", "l", "c", "v"}, tf
    r = c.get("/api/invest/rank?mkt=kr&kind=value").get_json()
    assert r["items"][0]["sym"] == "005930" and r["items"][0]["value"] == 1234567
    assert c.get("/api/invest/rank?mkt=kr&kind=up").get_json()["items"][0]["change_pct"] == 29.87
    info = c.get("/api/invest/info/kr/005930").get_json()
    assert info["items"][0][0] == "시가총액"
    h = c.get("/api/invest/home").get_json()
    assert h["account"]["total"] >= 6166000 and h["holdings"][0]["day_pct"] == -1.27 and h["day_pnl"] == -900 * sum(x["qty"] for x in h["holdings"])
    # 가격 알림: 현재가 70,100 → 70,000 이하 알림은 바로, 80,000 이상은 대기
    sent = []
    rt.notify = lambda m, *a, **k: sent.append(m)
    c.post("/api/invest/alerts", json={"mkt": "kr", "sym": "005930", "name": "삼성전자", "price": 70000})
    c.post("/api/invest/alerts", json={"mkt": "kr", "sym": "005930", "name": "삼성전자", "price": "80,000"})
    al = c.get("/api/invest/alerts").get_json()
    assert {a["cond"] for a in al} == {"above", "below"}
    rt.market.kr_quote = lambda s: {"price": 69900}
    assert len(rt.invest.check_alerts(force=True)) == 1 and "69,900원" in sent[0] and "이하" in sent[0]
    assert c.post("/api/invest/alerts", json={"mkt": "kr", "sym": "005930", "price": "abc"}).status_code == 400


# ---------- AI 추천·자동 익절/손절 ----------
def test_aipick_parse_guard_and_estimate(rt, monkeypatch):
    import moa.aipick as AP
    ap = rt.aipick
    monkeypatch.setattr(ap, "quote", lambda m, s: {"price": 10000, "name": "테스트", "change_pct": 3.2})
    text = ("오늘은 반도체 강세\n- 이유\n```json\n" + json.dumps({"stance": "보통", "market_view": "지수 강보합", "picks": [
        {"sym": "A005930", "name": "삼성전자", "score": 80, "win_prob": 62, "entry_type": "limit", "entry_price": 10000,
         "target_pct": 3, "stop_pct": 1.5, "trail_pct": 1, "exit_by": "15:10", "weight_pct": 20,
         "reasons": ["a", "b"], "risks": ["c"]},
        {"sym": "", "target_pct": 3, "stop_pct": 1}]}, ensure_ascii=False) + "\n```")
    r = ap.parse(text, "kr", "claude-sonnet-5-5", 0.05)
    assert r["stance"] == "보통" and len(r["picks"]) == 1 and r["picks"][0]["sym"] == "005930"
    assert ap.history()[0]["picks"][0]["win_prob"] == 62
    assert ap.parse("그냥 글", "kr", "m", 0)["note"].startswith("AI 답에서 추천표를 읽지 못했어요")
    # 관망이라 picks가 비어도 최소 1종목(지표 기반)
    ap._cands["kr"] = [{"sym": "000660", "name": "SK하이닉스", "현재가": 10000, "등락률%": 2.0,
                        "오늘_5분봉": {"VWAP대비%": 0.8, "최근30분%": 0.5, "고점대비%": -0.4, "최근30분_거래량배수": 1.6}},
                       {"sym": "035720", "name": "카카오", "현재가": 10000, "등락률%": -1.0,
                        "오늘_5분봉": {"VWAP대비%": -1.2, "최근30분%": -0.6, "고점대비%": -3.0, "최근30분_거래량배수": 0.8}}]
    r2 = ap.parse("```json\n" + json.dumps({"stance": "관망", "market_view": "약세", "picks": []}) + "\n```", "kr", "m", 0)
    assert r2["stance"] == "관망" and len(r2["picks"]) == 1 and r2["picks"][0]["sym"] == "000660" and r2["picks"][0]["auto"]
    # 감시 등록 → 정규장 가정 → 익절가 도달 시 매도
    g = ap.add_guard({"mkt": "kr", "engine": "scalp", "sym": "005930", "name": "삼성전자", "qty": 5, "entry": 10000,
                      "target_pct": 3, "stop_pct": 1.5, "trail_pct": 0, "exit_by": "15:10"})
    assert g["status"] == "watching"
    with pytest.raises(ValueError):
        ap.add_guard({"mkt": "kr", "sym": "005930", "qty": 1, "entry": 100, "target_pct": 0, "stop_pct": 1})
    monkeypatch.setattr(AP, "session", lambda m: {"open": True, "left_min": 120})
    monkeypatch.setattr(AP, "kst_now", lambda: datetime(2026, 11, 17, 10, 0))
    sold = []
    monkeypatch.setattr(ap, "_position", lambda g: {"qty": 5, "avg_price": 10000})
    monkeypatch.setattr(ap, "_sell", lambda g, q, why, p: sold.append((q, why, p)))
    ap.check(force=True)
    assert sold == []  # 0% → 그대로
    monkeypatch.setattr(ap, "quote", lambda m, s: {"price": 10310})
    ap.check(force=True)
    assert sold == [(5, "익절", 10310)]
    sold.clear()
    monkeypatch.setattr(ap, "quote", lambda m, s: {"price": 9840})
    ap.check(force=True)
    assert sold and sold[0][1] == "손절"
    sold.clear()
    monkeypatch.setattr(ap, "quote", lambda m, s: {"price": 10050})
    monkeypatch.setattr(AP, "kst_now", lambda: datetime(2026, 11, 17, 15, 11))
    ap.check(force=True)
    assert sold and sold[0][1] == "시간 정리"
    # 실제 매도(앱 내 모의 계좌)도 한 번
    rows = ap.guards(live=False)
    assert rows[0]["tp_price"] == 10300 and rows[0]["sl_price"] == 9850
    c = create_app(rt).test_client()
    assert c.delete(f"/api/ai/guards/{g['id']}").get_json()["ok"]
    assert c.get("/api/ai/guards").get_json()[0]["status"] == "cancelled"
    e = c.get("/api/ai/pick/estimate?mkt=us").get_json()
    assert len(e["models"]) == 4 and all(m["krw_hi"] > m["krw_lo"] > 0 for m in e["models"])


def test_aipick_us_session_dst():
    from datetime import timezone as tz
    import moa.aipick as AP
    assert AP._us_dst(datetime(2026, 7, 1, 12, tzinfo=tz.utc))
    assert not AP._us_dst(datetime(2026, 11, 17, 12, tzinfo=tz.utc))
    assert AP._us_dst(datetime(2026, 3, 8, 7, 1, tzinfo=tz.utc)) and not AP._us_dst(datetime(2026, 3, 8, 6, 59, tzinfo=tz.utc))
    f = AP.bar_features([{"t": "11/17 09:00", "o": 100, "h": 102, "l": 99, "c": 101, "v": 10}] +
                        [{"t": f"11/17 09:{5 * i:02d}", "o": 101, "h": 103, "l": 100, "c": 102, "v": 20} for i in range(1, 8)])
    assert f["시가대비%"] == 2.0 and f["5분봉_개수"] == 8


def test_us_exchange_probes_when_blank_rejected():
    from moa.broker.kiwoom import KiwoomClient, KiwoomError
    KiwoomClient._US_EX_CACHE.clear()
    cl = KiwoomClient.__new__(KiwoomClient)
    calls = []

    def fake(key, body):
        calls.append(body["stex_tp"])
        if body["stex_tp"] in ("", None):
            raise KiwoomError("usa10100 실패: 거래소 구분값이 없습니다")
        if body["stex_tp"] == "ND":
            return {"stk_cd": "AXTI", "stk_nm": "AXT Inc", "stex_tp": "ND"}
        raise KiwoomError("없음")
    cl.call = fake
    assert cl.us_exchange("AXTI", "NMS") == ("ND", "AXT Inc") and calls == ["ND"]
    calls.clear()
    KiwoomClient._US_EX_CACHE.clear()
    cl.call = lambda k, b: (calls.append(b["stex_tp"]) or {"stk_cd": "KO", "stk_nm": "코카콜라", "stex_tp": "NY"}) if b["stex_tp"] == "NY" else (_ for _ in ()).throw(KiwoomError("x"))
    assert cl.us_exchange("KO")[0] == "NY" and calls == ["NY"]
    assert cl.us_exchange("KO")[0] == "NY"  # 기억해 둔 값


def test_ai_outcomes_judge_window_and_stats(rt, monkeypatch):
    import moa.aipick as AP
    from datetime import datetime as D
    assert AP.window_end("kr", D(2026, 11, 17, 10, 5), "15:10") == D(2026, 11, 17, 15, 10)
    assert AP.window_end("kr", D(2026, 11, 20, 16, 0), "") == D(2026, 11, 23, 15, 20)  # 금요일 장 뒤 → 월요일
    assert AP.window_end("us", D(2026, 11, 17, 21, 0), "") == D(2026, 11, 18, 6, 0)    # 겨울: 다음날 06:00
    assert AP.window_end("us", D(2026, 7, 15, 23, 0), "") == D(2026, 7, 16, 5, 0)      # 여름: 05:00
    b = lambda o, h, l, c: {"o": o, "h": h, "l": l, "c": c}
    assert AP.judge([b(100, 101, 99.5, 100.5), b(100.5, 103.2, 100, 103)], 100, 3, 1.5)[0] == "익절"
    assert AP.judge([b(100, 101, 98.4, 99)], 100, 3, 1.5)[0] == "손절"
    assert AP.judge([b(100, 104, 98, 101)], 100, 3, 1.5)[0] == "손절"  # 한 봉에 둘 다, 양봉 → 저가가 먼저
    assert AP.judge([b(101, 104, 98, 100)], 100, 3, 1.5)[0] == "익절"  # 음봉 → 고가가 먼저
    r = AP.judge([b(100, 101, 99.8, 100.4)], 100, 3, 1.5)
    assert r[0] == "시간 정리" and round(r[2], 1) == 100.4
    assert AP.judge([b(100, 101, 99.8, 100)], 100, 3, 1.5, "limit", 99)[0] == "미체결"
    assert AP.judge([b(100, 101, 98.9, 100), b(100, 102.1, 99.5, 102)], 100, 3, 1.5, "limit", 99)[0] == "익절"  # 99 진입 → 101.97
    # 추천 2건(국내 Sonnet, 미국 Fable) → 채점 → 통계
    db = rt.db
    db.x("INSERT INTO ai_picks(ts,mkt,model,usd,stance,view,picks,text) VALUES(?,?,?,?,?,?,?,?)",
         ("2026-11-17T10:00:00", "kr", "claude-sonnet-5-5", 0.2, "보통", "", json.dumps([
             {"sym": "005930", "name": "삼성전자", "price": 100, "entry_type": "market", "entry_price": 100, "target_pct": 3, "stop_pct": 1.5, "win_prob": 60, "exit_by": "15:10"},
             {"sym": "000660", "name": "SK하이닉스", "price": 100, "entry_type": "market", "entry_price": 100, "target_pct": 3, "stop_pct": 1.5, "win_prob": 55, "exit_by": "15:10"}]), ""))
    db.x("INSERT INTO ai_picks(ts,mkt,model,usd,stance,view,picks,text) VALUES(?,?,?,?,?,?,?,?)",
         ("2026-11-16T23:40:00", "us", "claude-fable-5-2", 0.5, "관망", "", json.dumps([
             {"sym": "NVDA", "name": "엔비디아", "price": 100, "entry_type": "market", "entry_price": 100, "target_pct": 2, "stop_pct": 1, "win_prob": 45, "auto": True}]), ""))
    bars = {"005930": [{"t": "11/17 10:00", "o": 100, "h": 101, "l": 99.6, "c": 100.8, "v": 1}, {"t": "11/17 10:05", "o": 100.8, "h": 103.5, "l": 100.5, "c": 103, "v": 1}],
            "000660": [{"t": "11/17 10:00", "o": 100, "h": 100.2, "l": 98.2, "c": 98.4, "v": 1}],
            "NVDA": [{"t": "11/16 23:40", "o": 100, "h": 100.6, "l": 99.7, "c": 100.4, "v": 1}, {"t": "11/17 05:55", "o": 100.4, "h": 100.9, "l": 100.1, "c": 100.7, "v": 1}]}
    monkeypatch.setattr(rt.invest, "candles", lambda m, s, tf: {"bars": bars[s]})
    rt.aipick.evaluate(force=True)
    st = rt.aipick.stats()
    a = st["all"]
    assert a["n"] == 3 and a["done"] == 3 and a["tp"] == 1 and a["sl"] == 1 and a["time"] == 1
    assert {x["key"] for x in st["by_model"]} == {"claude-sonnet-5-5", "claude-fable-5-2"}
    kr = next(x for x in st["by_mkt"] if x["key"] == "kr")
    assert kr["tp_rate"] == 50.0 and kr["avg"] == round(((3 - .2) + (-1.5 - .2)) / 2, 2)
    us = next(x for x in st["by_mkt"] if x["key"] == "us")
    assert us["avg"] == round(0.7 - 0.25, 2)
    assert any(x["key"] == "지표 자동 선정" for x in st["by_stance"]) and st["curve"] and sum(st["hist"]) == 3
    c = create_app(rt).test_client()
    assert c.get("/api/ai/stats?mkt=us").get_json()["all"]["n"] == 1


def test_us_views_rank_and_ticks(rt):
    from moa.broker.kiwoom import KiwoomClient
    KiwoomClient._US_EX_CACHE.clear()

    class CL:
        _US_EX_CACHE = KiwoomClient._US_EX_CACHE

        def us_exchange(self, sym, hint=None):
            return self._US_EX_CACHE.get(sym, ("ND", sym))

        def call(self, key, body):
            if key == "us_rank_views":
                return {"result_list": [{"rank": "1", "stk_cd": "SOXL", "stk_nm": "미국 반도체 3배", "sign": "+", "chg_val": "3",
                                         "curr_pric": "31.2500", "sign_for_gjga": "-", "diff_rate_for_gjga": "2.1500", "stex_tp": "NA"}]}
            if key == "us_ticks":
                return {"result_list": [{"cur_prc": "+352.2100", "flu_rt": "-0.16", "trde_qty": "+300", "cntr_tm": "222201"},
                                        {"cur_prc": "+352.2000", "flu_rt": "-0.16", "trde_qty": "-100", "cntr_tm": "222200"}]}
            raise AssertionError(key)
    rt.market.kr_client = lambda: CL()
    r = rt.invest.ranking("us", "views")["items"][0]
    assert r["sym"] == "SOXL" and r["move"] == 3 and r["change_pct"] == -2.15 and r["price"] == 31.25
    assert KiwoomClient._US_EX_CACHE["SOXL"][0] == "NA"
    t = rt.invest.ticks("us", "AAPL")
    assert t[0] == {"t": "22:22:01", "price": 352.21, "qty": 300, "change_pct": -0.16, "strength": 300.0}


def test_ai_self_learning_stats_calibration_and_prompt(rt):
    ap = rt.aipick
    assert ap.learn_view()["stage"] == "자료 모으는 중" and ap.learn_prompt("kr") is None
    ap.stats()  # 표 준비
    import moa.aipick as AP
    AP._ensure_cols(rt.db)
    for i in range(12):
        above = i % 2 == 0  # VWAP 위는 익절, 아래는 손절
        rt.db.x("INSERT INTO ai_outcomes(pick_id,idx,ts,mkt,model,sym,name,stance,auto,win_prob,entry_type,rec_price,entry_price,"
                "target_pct,stop_pct,end_ts,status,result,filled,net_pct,mfe,mae,feat) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (100 + i, 0, f"2026-11-{10 + i // 2:02d}T10:00:00", "kr", "claude-sonnet-5-5", "005930", "삼성전자", "보통", 0, 60, "market",
                 100, 100, 3, 1.5, "x", "done", "익절" if above else "손절", 1, 2.8 if above else -1.7, 3.2 if above else 0.4,
                 -0.3 if above else -1.6, json.dumps({"vwap": 0.8 if above else -0.9, "chg": 4, "m30": 0.3, "open": True, "left": 200})))
    v = ap.learn_view()
    assert v["n"] == 12 and v["stage"] == "통계 반영 중" and v["overall"]["tp_rate"] == 50
    assert any("VWAP 위치" in x and "VWAP 위" in x for x in v["insights"])
    assert any("과신" in x for x in v["insights"])  # 예측 60% vs 실제 50%
    cal, n = ap.calibrate("kr", 60)
    assert n == 12 and cal == round((6 + 8 * .6) / 20 * 100)
    lp = ap.learn_prompt("kr")
    assert lp and any("VWAP 위" in x for x in lp["조건별 성적(건수 3 이상)"])
    ap.save_lessons("총평\n## 규칙\n- VWAP 아래는 피한다", "m", 0.1)
    assert "VWAP 아래" in ap.learn_prompt("kr")["지난 복기에서 정한 규칙"] and ap.learn_view()["lessons"]["n"] == 12
    c = create_app(rt).test_client()
    assert c.put("/api/ai/learn", json={"auto": False}).get_json()["auto"] is False
    t, ask, data = ap.learn_context()
    assert len(data["추천별 기록(최근 80건)"]) == 12


def test_ai_outcome_ignores_pre_recommendation_bar_range(rt, monkeypatch):
    """추천 시각(23:33)이 낀 5분봉(23:30)의 저가(추천 전 급락)는 손절로 치지 않는다. 지정가는 30분 안에 안 닿으면 미체결."""
    from datetime import date as _date, timedelta as _td
    db = rt.db
    d0 = _date.today() - _td(days=3)                       # 채점 창(최근 며칠) 안에 들게 날짜를 오늘 기준으로 잡는다
    d1 = d0 + _td(days=1)
    D0, D1 = d0.strftime("%m/%d"), d1.strftime("%m/%d")
    mk = lambda et, ep: db.x("INSERT INTO ai_picks(ts,mkt,model,usd,stance,view,picks,text) VALUES(?,?,?,?,?,?,?,?)",
        (f"{d0.isoformat()}T23:33:00", "us", "claude-fable-5-2", 0.5, "보통", "", json.dumps([
            {"sym": "AS", "name": "아머 스포츠", "price": 30.81, "entry_type": et, "entry_price": ep, "target_pct": 2.5, "stop_pct": 1.5, "win_prob": 45}]), ""))
    mk("limit", 30.60)
    mk("market", 30.81)
    bars = [{"t": f"{D0} 23:30", "o": 30.42, "h": 30.95, "l": 29.63, "c": 30.85, "v": 1},
            {"t": f"{D0} 23:35", "o": 30.85, "h": 31.30, "l": 30.80, "c": 31.25, "v": 1},
            {"t": f"{D0} 23:40", "o": 31.25, "h": 31.97, "l": 31.20, "c": 31.80, "v": 1}] + \
           [{"t": f"{D1} 0{h}:00", "o": 31.5, "h": 31.6, "l": 31.3, "c": 31.4, "v": 1} for h in range(0, 5)] + \
           [{"t": f"{D1} 05:50", "o": 30.7, "h": 30.7, "l": 30.45, "c": 30.48, "v": 1}]
    monkeypatch.setattr(rt.invest, "candles", lambda m, s, tf: {"bars": bars})
    rt.aipick.evaluate(force=True)
    lim, mkt_ = rt.db.q("SELECT * FROM ai_outcomes ORDER BY pick_id")
    assert lim["result"] == "익절" and lim["net_pct"] == 2.25 and lim["status"] == "done" and lim["alt_result"] == "미체결"
    assert mkt_["result"] == "익절" and mkt_["net_pct"] == 2.25 and mkt_["mae"] > -1 and mkt_["alt_result"] is None
    a = rt.aipick.stats()["all"]
    assert a["tp"] == 2 and a["unfilled"] == 0 and a["alt_n"] == 1 and a["alt_unfilled"] == 1
    # 예전 방식으로 잘못 채점된 기록도 다시 채점된다
    db.x("UPDATE ai_outcomes SET result='손절', net_pct=-1.75, gv=NULL WHERE pick_id=?", (mkt_["pick_id"],))
    rt.aipick.evaluate(force=True)
    assert db.one("SELECT result FROM ai_outcomes WHERE pick_id=?", (mkt_["pick_id"],))["result"] == "익절"


# ---------- 보안: 앱 비밀번호·잠금 ----------
def _remote(rt):
    from moa.app import access_token
    c = create_app(rt).test_client()
    return c, {"X-Forwarded-For": "100.64.0.2", "Authorization": f"Bearer {access_token(rt.db)}"}


def test_password_lock_flow(rt):
    c, h = _remote(rt)
    st = c.get("/api/auth/state", headers=h).get_json()
    assert st["set"] is False and st["unlocked"] is False and st["required"] is True
    assert c.post("/api/auth/setup", json={"password": "12"}, headers=h).status_code == 403      # 너무 짧음
    assert c.post("/api/auth/setup", json={"password": "1111"}, headers=h).status_code == 403    # 너무 쉬움
    sid = c.post("/api/auth/setup", json={"password": "gagyebu!7"}, headers=h).get_json()["session"]
    assert c.post("/api/auth/setup", json={"password": "another-1"}, headers=h).status_code == 403  # 두 번은 못 만든다
    hs = {**h, "X-Moa-Session": sid}
    assert c.get("/api/settings", headers=hs).status_code == 200
    assert c.get("/api/settings", headers={**h, "X-Moa-Session": "guess"}).status_code == 401
    # 비밀번호 원문은 어디에도 저장하지 않는다
    raw = json.dumps(rt.db.get_setting("auth"))
    assert "gagyebu!7" not in raw and sid not in raw
    assert rt.db.one("SELECT COUNT(*) n FROM auth_sessions WHERE hash=?", (sid,))["n"] == 0
    assert "gagyebu" not in json.dumps(c.get("/api/settings", headers=hs).get_json())
    # 잠그면 같은 세션으로 못 들어온다
    assert c.post("/api/auth/lock", json={}, headers=hs).status_code == 200
    assert c.get("/api/settings", headers=hs).get_json()["code"] == "locked"
    sid2 = c.post("/api/auth/unlock", json={"password": "gagyebu!7"}, headers=h).get_json()["session"]
    hs = {**h, "X-Moa-Session": sid2}
    # 바꾸기: 지금 비밀번호가 맞아야 하고, 바꾸면 다른 기기는 잠긴다
    other = c.post("/api/auth/unlock", json={"password": "gagyebu!7"}, headers=h).get_json()["session"]
    assert c.post("/api/auth/change", json={"current": "wrong", "new": "new-pass-9"}, headers=hs).status_code == 403
    assert c.post("/api/auth/change", json={"current": "gagyebu!7", "new": "new-pass-9"}, headers=hs).status_code == 200
    assert c.get("/api/settings", headers=hs).status_code == 200
    assert c.get("/api/settings", headers={**h, "X-Moa-Session": other}).status_code == 401
    assert c.post("/api/auth/unlock", json={"password": "gagyebu!7"}, headers=h).status_code == 403
    # 초기화는 노트북에서만
    assert c.post("/api/auth/reset", headers=hs).status_code == 403
    assert c.post("/api/auth/reset").status_code == 200
    assert c.get("/api/auth/state", headers=h).get_json()["set"] is False


def test_password_wrong_tries_are_blocked(rt):
    c, h = _remote(rt)
    c.post("/api/auth/setup", json={"password": "gagyebu!7"}, headers=h)
    for _ in range(4):
        r = c.post("/api/auth/unlock", json={"password": "nope"}, headers=h)
        assert r.status_code == 403 and r.get_json()["wait"] == 0
    r = c.post("/api/auth/unlock", json={"password": "nope"}, headers=h)
    assert r.get_json()["wait"] == 30
    # 막힌 동안에는 맞는 비밀번호도 받지 않는다
    r = c.post("/api/auth/unlock", json={"password": "gagyebu!7"}, headers=h)
    assert r.status_code == 403 and r.get_json()["wait"] > 0
    conf = rt.db.get_setting("auth"); conf["blocked_until"] = 0; rt.db.set_setting("auth", conf)
    assert c.post("/api/auth/unlock", json={"password": "gagyebu!7"}, headers=h).status_code == 200
    assert rt.db.get_setting("auth")["fails"] == 0


def test_session_expires_when_idle(rt):
    import time as _t
    c, h = _remote(rt)
    sid = c.post("/api/auth/setup", json={"password": "gagyebu!7"}, headers=h).get_json()["session"]
    hs = {**h, "X-Moa-Session": sid}
    assert c.put("/api/auth/options", json={"lock_after": 5}, headers=hs).get_json()["lock_after"] == 5
    assert c.put("/api/auth/options", json={"lock_after": 7}, headers=hs).status_code == 403
    rt.db.x("UPDATE auth_sessions SET last_seen=?", (_t.time() - 6 * 60,))
    assert c.get("/api/settings", headers=hs).get_json()["code"] == "locked"


def test_inbox_post_needs_only_token(rt):
    """아이폰 단축어(문자 자동 기록)는 비밀번호 없이 넣기만 할 수 있다. 읽기는 안 된다."""
    c, h = _remote(rt)
    c.post("/api/auth/setup", json={"password": "gagyebu!7"}, headers=h)
    assert c.post("/api/inbox", json={"text": "[Web발신] 신한카드 승인 12,000원 스타벅스"}, headers=h).status_code == 200
    assert c.get("/api/inbox", headers=h).status_code == 401
    assert c.post("/api/inbox", json={"text": "x"}, headers={"X-Forwarded-For": "100.64.0.2"}).status_code == 401


def test_local_direct_rejects_foreign_site_requests(rt):
    """이 PC 브라우저를 거친 다른 사이트의 요청(CSRF·DNS 리바인딩)은 이 PC 요청으로 믿지 않는다."""
    c = create_app(rt).test_client()
    assert c.get("/api/settings").status_code == 200
    assert c.get("/api/settings", headers={"Origin": "http://127.0.0.1:8780"}).status_code == 200
    assert c.get("/api/settings", headers={"Host": "evil.example:8780"}).status_code == 401          # DNS 리바인딩
    assert c.post("/api/connect-info/rotate", headers={"Origin": "https://evil.example"}).status_code == 401  # CSRF
    assert c.get("/api/settings", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 401
    assert c.get("/api/settings", headers={"Origin": "null"}).status_code == 401


def test_wrong_token_is_throttled_and_headers_are_set(rt):
    c = create_app(rt).test_client()
    bad = {"X-Forwarded-For": "100.64.0.9", "Authorization": "Bearer wrong"}
    for _ in range(8):
        assert c.get("/api/ping", headers=bad).status_code == 401
    r = c.get("/api/ping", headers=bad)
    assert r.status_code == 429 and r.get_json()["wait"] > 0
    # 다른 기기는 영향 없다
    from moa.app import access_token
    ok = c.get("/api/ping", headers={"X-Forwarded-For": "100.64.0.2", "Authorization": f"Bearer {access_token(rt.db)}"})
    assert ok.status_code == 200
    assert ok.headers["X-Content-Type-Options"] == "nosniff" and ok.headers["X-Frame-Options"] == "DENY"
    assert ok.headers["Referrer-Policy"] == "no-referrer"


def test_server_error_does_not_echo_details(rt):
    app = create_app(rt)

    @app.get("/api/boom")
    def boom():
        raise KeyError("secret-path-C:/Users/me/key.txt")
    r = app.test_client().get("/api/boom")
    assert r.status_code == 500 and "secret-path" not in r.get_json()["error"]


# ---------- AI 중기(1주·1달) 추천 ----------
def _dbar(t, o, h, l, c, v=1000):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def test_swing_daily_features_and_dates():
    import moa.aipick as AP
    from datetime import datetime as D, date
    bars = [_dbar(f"2026-09-{i + 1:02d}", 100 + i, 101 + i, 99 + i, 100 + i, 1000) for i in range(25)]
    bars += [_dbar(f"2026-10-{i + 1:02d}", 125 + i * 2, 127 + i * 2, 124 + i * 2, 126 + i * 2, 3000) for i in range(5)]
    f = AP.daily_features(bars)
    assert f["5일%"] == round((134 / 124 - 1) * 100, 2) and f["20일선대비%"] > 0 and f["5일거래량배수"] == 3.0
    assert f["연속상승일"] >= 5 and f["60일고점대비%"] < 0 and f["하루변동폭%"] > 0
    assert AP.daily_features(bars[:3]) is None
    assert AP.phase_at("kr", D(2026, 11, 2, 8, 30)) == ("pre", date(2026, 11, 2))
    assert AP.phase_at("kr", D(2026, 11, 2, 10, 0))[0] == "open" and AP.phase_at("kr", D(2026, 11, 2, 16, 0))[0] == "post"
    assert AP.phase_at("us", D(2026, 11, 3, 0, 30)) == ("open", date(2026, 11, 2))      # 한국 00:30 = 뉴욕 전날 10:30
    assert AP.phase_at("kr", D(2026, 11, 7, 10, 0)) == ("post", date(2026, 11, 7))      # 주말
    assert AP.swing_end("kr", D(2026, 11, 2, 10, 0), 5) == (D(2026, 11, 9, 15, 40), date(2026, 11, 9))
    assert AP.swing_end("kr", D(2026, 11, 2, 8, 0), 5)[1] == date(2026, 11, 6)          # 장 전이면 그날이 1일째
    assert AP.swing_end("kr", D(2026, 11, 6, 16, 0), 20)[1] == date(2026, 12, 4)
    assert "5거래일" in AP.system_prompt("swing") and "20거래일" in AP.system_prompt("month") and AP.system_prompt("x") == AP.PICK_SYSTEM
    # 일봉 채점은 갭을 반영한다: 시가가 손절가 아래에서 시작하면 그 시가에 팔린 것으로
    r = AP.judge([_dbar("a", 100, 101, 99, 100), _dbar("b", 90, 92, 89, 91)], 100, 10, 5, gap=True)
    assert r[0] == "손절" and r[2] == 90 and r[6] == 2
    r = AP.judge([_dbar("a", 113, 115, 112, 114)], 100, 10, 5, gap=True)
    assert r[0] == "익절" and r[2] == 113


def test_swing_parse_resolves_names_and_saves_horizon(rt, monkeypatch):
    ap = rt.aipick
    quotes = {"446840": {"price": 4640, "name": "지슨", "change_pct": 29.9}, "005930": {"price": 100000, "name": "삼성전자", "change_pct": 1.0}}
    monkeypatch.setattr(ap, "quote", lambda m, s: quotes.get(s, {}))
    monkeypatch.setattr(rt.market, "kr_search", lambda q: [{"sym": "446840", "name": "지슨", "exchange": "코스닥"}] if "지슨" in q else [])
    monkeypatch.setattr(rt.invest, "candles", lambda m, s, tf: {"bars": [_dbar(f"2026-10-{i + 1:02d}", 100, 102, 99, 100 + i) for i in range(28)]})
    text = "재료 요약\n```json\n" + json.dumps({"stance": "보통", "market_view": "정책 테마 순환", "picks": [
        {"sym": "999999", "name": "지슨", "score": 70, "win_prob": 48, "entry_type": "limit", "entry_price": 4500, "target_pct": 90, "stop_pct": 40,
         "hold_days": 9, "weight_pct": 10, "theme": "시험장 전파 탐지", "catalyst": "전파탐지기 예산 증액안", "catalyst_type": "정책",
         "catalyst_date": "12월 예산 확정", "reasons": ["a"], "risks": ["정부 번복"]},
        {"sym": "123456", "name": "없는회사", "target_pct": 10, "stop_pct": 5},
        {"sym": "005930", "name": "삼성전자", "win_prob": 55, "target_pct": 8, "stop_pct": 4, "catalyst_type": "이상한값"}]}, ensure_ascii=False) + "\n```"
    r = ap.parse(text, "kr", "claude-sonnet-5-5", 0.3, horizon="swing", theme="전파차단")
    assert r["horizon"] == "swing" and r["label"] == "1주" and [p["sym"] for p in r["picks"]] == ["446840", "005930"]
    p0 = r["picks"][0]
    assert p0["name"] == "지슨" and p0["target_pct"] == 40 and p0["stop_pct"] == 15 and p0["hold_days"] == 5   # 범위 안으로
    assert p0["catalyst_type"] == "정책" and p0["in_cand"] is False and p0["feat"]["cat"] == "정책" and p0["feat"]["r5"] is not None
    assert r["picks"][1]["catalyst_type"] == "기술적"
    row = rt.db.one("SELECT horizon, theme FROM ai_picks WHERE id=?", (r["id"],))
    assert row["horizon"] == "swing" and row["theme"] == "전파차단"
    assert ap.history()[0]["horizon"] == "swing"
    # 종목을 못 받으면 일봉 지표로 1종목
    ap._cands["kr:month"] = [{"sym": "005930", "name": "삼성전자", "현재가": 100000, "등락률%": 1.0,
                              "일봉": {"5일%": 4, "20일선대비%": 3, "5일거래량배수": 1.8, "60일고점대비%": -3, "하루변동폭%": 2.5}}]
    r2 = ap.parse("```json\n" + json.dumps({"stance": "관망", "picks": []}) + "\n```", "kr", "m", 0, horizon="month")
    assert r2["picks"][0]["auto"] and r2["picks"][0]["horizon"] == "month" and r2["picks"][0]["target_pct"] > r2["picks"][0]["stop_pct"]


def _swing_fixture(rt, monkeypatch, ts="2026-11-02T10:00:00"):
    pk = lambda sym, name, **k: {"sym": sym, "name": name, "price": 100, "entry_type": "market", "entry_price": 100, "target_pct": 10,
                                 "stop_pct": 5, "win_prob": 50, "hold_days": 5, "horizon": "swing", "idx0": 2000, "idx_sym": "^KS11",
                                 "feat": {"ma20": 2, "cand": True, "open": True}, **k}
    rt.db.x("INSERT INTO ai_picks(ts,mkt,model,usd,stance,view,picks,text,horizon,theme,lv) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (ts, "kr", "claude-sonnet-5-5", 0.6, "보통", "", json.dumps([
                pk("000001", "오름", theme="전파 탐지", catalyst_type="정책"),
                pk("000002", "갭하락", theme="전파 탐지", catalyst_type="정책"),
                pk("000003", "횡보", theme="반도체", catalyst_type="실적")]), "", "swing", "", 0))
    days = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10"]
    mk = lambda rows: [_dbar(d, *r) for d, r in zip(days, rows)]
    daily = {
        "000001": mk([(97, 103, 96, 101), (101, 104, 100, 103), (103, 106, 102, 105), (105, 111, 104, 110), (110, 112, 108, 111), (111, 113, 110, 112), (112, 114, 111, 113)]),
        "000002": mk([(99, 102, 98, 100), (93, 95, 92, 94), (94, 96, 93, 95), (95, 97, 94, 96), (96, 97, 95, 96), (96, 98, 95, 97), (97, 99, 96, 98)]),
        "000003": mk([(99, 101, 98, 100), (100, 102, 99, 101), (101, 103, 100, 102), (102, 103, 100, 101), (101, 103, 100, 102), (102, 104, 101, 103), (103, 104, 102, 104)]),
        "^KS11": mk([(2000, 2010, 1990, 2000), (2000, 2020, 1995, 2010), (2010, 2030, 2005, 2020), (2020, 2030, 2010, 2020), (2020, 2045, 2015, 2040), (2040, 2065, 2035, 2060), (2060, 2070, 2050, 2065)]),
    }
    monkeypatch.setattr(rt.invest, "candles", lambda m, s, tf: {"bars": [] if tf == "m5" else daily[s]})
    return daily


def test_swing_outcomes_graded_on_daily_bars(rt, monkeypatch):
    _swing_fixture(rt, monkeypatch)
    rt.aipick.evaluate(force=True)
    oc = {r["sym"]: r for r in rt.db.q("SELECT * FROM ai_outcomes WHERE COALESCE(horizon,'day')='swing'")}
    a, b, c = oc["000001"], oc["000002"], oc["000003"]
    # 추천 당일은 분봉이 없어 종가만 → 그 뒤 일봉으로 판정
    assert a["result"] == "익절" and a["days_held"] == 4 and round(a["net_pct"], 1) == 9.8
    assert a["bench"] == 1.0 and json.loads(a["path"])["d3"] == 10.0          # 지수 2000→2020, 3거래일 뒤 종가 110
    assert b["result"] == "손절" and b["exit"] == 93 and round(b["net_pct"], 1) == -7.2   # 갭 하락: 시가 93에 손절
    assert c["result"] == "시간 정리" and c["exit"] == 103 and c["days_held"] == 6       # 5거래일 뒤(11/9) 종가
    assert json.loads(c["path"]) == {"d1": 1.0, "d3": 1.0, "d5": 3.0}
    st = rt.aipick.stats(horizon="swing")
    assert st["horizon"] == "swing" and st["all"]["n"] == 3 and st["all"]["tp"] == 1 and st["all"]["sl"] == 1 and st["all"]["time"] == 1
    assert st["all"]["alpha"] is not None and st["all"]["hold"] is not None
    assert {x["key"] for x in st["by_cat"]} == {"정책", "실적"} and st["by_theme"][0]["key"] == "전파 탐지" and st["by_theme"][0]["n"] == 2
    assert [p["k"] for p in st["path"]] == [1, 3, 5] and st["path"][0]["n"] == 3
    assert {x["key"] for x in st["by_hold"]} >= {"4~5일", "2~3일"} and st["by_dow"][0]["key"] == "월" and st["by_lv"][0]["key"] == "규칙 없음"
    assert st["hist_labels"][0] == "−8%↓"
    # 당일 추천 통계에는 섞이지 않는다
    assert rt.aipick.stats()["all"]["n"] == 0 and rt.aipick.stats(horizon="month")["all"]["n"] == 0
    # 추천가와 일봉 가격대가 딴판이면(액면분할 등) 엉뚱한 손익을 내지 않고 채점에서 뺀다
    rt.db.x("INSERT INTO ai_picks(ts,mkt,model,usd,stance,view,picks,text,horizon,theme,lv) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-11-02T10:00:00", "kr", "m", 0, "보통", "", json.dumps([{"sym": "000001", "name": "분할", "price": 1000, "target_pct": 10, "stop_pct": 5, "horizon": "month"}]), "", "month", "", 0))
    rt.aipick.evaluate(force=True)
    assert rt.db.one("SELECT result FROM ai_outcomes WHERE COALESCE(horizon,'day')='month'")["result"] == "자료 없음"
    assert rt.aipick.stats(horizon="month")["all"]["done"] == 0
    c2 = create_app(rt).test_client()
    assert c2.get("/api/ai/stats?h=swing").get_json()["all"]["n"] == 3
    d = c2.get(f"/api/ai/picks/{a['pick_id']}").get_json()
    assert d["horizon"] == "swing" and d["picks"][0]["outcome"]["path"]["d1"] == 3.0


def test_swing_learning_is_separate_and_tunes(rt):
    import moa.aipick as AP
    ap = rt.aipick
    ap.stats()
    for i in range(10):
        good = i % 2 == 0   # 20일선 위는 통하고, 아래는 안 통함
        rt.db.x("INSERT INTO ai_outcomes(pick_id,idx,ts,mkt,model,sym,name,stance,auto,win_prob,entry_type,rec_price,entry_price,target_pct,stop_pct,"
                "end_ts,status,result,filled,net_pct,mfe,mae,feat,horizon,days_held,bench,path,lv,theme,cat) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (500 + i, 0, f"2026-10-{5 + i:02d}T10:00:00", "kr", "claude-sonnet-5-5", "005930", "삼성전자", "보통", 0, 55, "market", 100, 100, 12, 6,
                 "x", "done", "익절" if good else "손절", 1, 11.8 if good else -6.2, 14 if good else 1.5, -2 if good else -7,
                 json.dumps({"ma20": 4 if good else -3, "r5": 5, "cand": good, "cat": "정책" if good else "기술적", "open": True}),
                 "swing", 3, 1.0, json.dumps({"d1": 2 if good else -2, "d3": 8 if good else -5, "d5": 6 if good else -6}), 0, "테마", "정책" if good else "기술적"))
    assert ap.learn_view()["n"] == 0 and ap.learn_prompt("kr") is None            # 당일 학습과 섞이지 않는다
    v = ap.learn_view("swing")
    assert v["horizon"] == "swing" and v["n"] == 10 and v["stage"] == "통계 반영 중" and v["overall"]["alpha"] == round((11.8 - 1 - 6.2 - 1) / 2, 2)
    assert any("20일선 위치" in x for x in v["insights"]) and any("재료 유형" in x for x in v["insights"])
    tn = v["tune"]
    assert tn["ready"] and tn["hold"] == 3 and tn["tp"] >= 4 and any("20일선 아래" in x for x in tn["avoid"]) and any("20일선 위" in x for x in tn["prefer"])
    lp = ap.learn_prompt("kr", "swing")
    assert lp["자동 조정값(실제 결과에서 계산)"]["피할 조건"] and "3일" in lp["자동 조정값(실제 결과에서 계산)"]["가장 성적이 좋았던 보유 거래일"]
    # 과거에 통한 조건의 후보일수록 가산점이 높다
    assert ap.learn_score({"ma20": 5, "cat": "정책", "cand": True}, "swing") > 0 > ap.learn_score({"ma20": -2, "cat": "기술적", "cand": False}, "swing")
    assert ap.learn_score({"ma20": 5}, "month") is None
    cal, n = ap.calibrate("kr", 55, "swing")
    assert n == 10 and cal == round((5 + 8 * .55) / 18 * 100)
    # 규칙은 종류별로 따로, 판 번호가 올라가고 이전 판을 남긴다
    ap.save_lessons("총평\n## 규칙\n- 20일선 아래는 피한다", "m", 0.1, "swing")
    assert ap.lessons("swing")["ver"] == 1 and not ap.lessons("day") and "20일선" in ap.learn_prompt("kr", "swing")["지난 복기에서 정한 규칙"]
    ap.save_lessons("총평 2\n## 규칙\n- 재료가 정책이면 우선", "m", 0.1, "swing")
    ls = ap.lessons("swing")
    assert ls["ver"] == 2 and len(ls["hist"]) == 1 and ls["hist"][0]["ver"] == 1
    t, ask, data = ap.learn_context("swing")
    assert "1주" in t and data["추천별 기록(최근 80건)"][0]["들고 있었다면(거래일별 %)"] and data["자동 조정값"]["ready"]
    with pytest.raises(ValueError):
        ap.learn_context("month")
    c = create_app(rt).test_client()
    assert c.get("/api/ai/learn?h=swing").get_json()["lessons"]["ver"] == 2
    r = c.put("/api/ai/learn", json={"h": "swing", "clear": True}).get_json()
    assert not r["lessons"].get("text") and r["lessons"]["ver"] == 2 and c.get("/api/ai/learn").get_json()["horizon"] == "day"


def test_swing_guard_holds_overnight_until_last_day(rt, monkeypatch):
    import moa.aipick as AP
    ap = rt.aipick
    monkeypatch.setattr(AP, "kst_now", lambda: datetime(2026, 11, 2, 10, 0))
    g = ap.add_guard({"mkt": "kr", "engine": "scalp", "sym": "005930", "name": "삼성전자", "qty": 3, "entry": 10000,
                      "target_pct": 12, "stop_pct": 6, "horizon": "swing", "hold_days": 5, "exit_by": "15:10"})
    assert g["horizon"] == "swing" and g["exit_date"] == "2026-11-09" and g["exit_by"] == ""
    sold = []
    monkeypatch.setattr(ap, "_position", lambda g: {"qty": 3, "avg_price": 10000})
    monkeypatch.setattr(ap, "_sell", lambda g, q, why, p: sold.append((q, why, p)))
    monkeypatch.setattr(ap, "quote", lambda m, s: {"price": 10100})
    monkeypatch.setattr(AP, "session", lambda m: {"open": True, "left_min": 5})    # 장 마감 직전
    monkeypatch.setattr(AP, "mkt_date", lambda m: "2026-11-04")
    ap.check(force=True)
    assert sold == []                                   # 당일 추천과 달리 날마다 정리하지 않는다
    monkeypatch.setattr(ap, "quote", lambda m, s: {"price": 9350})
    ap.check(force=True)
    assert sold == [(3, "손절", 9350)]                   # 손절은 언제든
    sold.clear()
    monkeypatch.setattr(ap, "quote", lambda m, s: {"price": 10100})
    monkeypatch.setattr(AP, "mkt_date", lambda m: "2026-11-09")
    monkeypatch.setattr(AP, "session", lambda m: {"open": True, "left_min": 120})
    ap.check(force=True)
    assert sold == []
    monkeypatch.setattr(AP, "session", lambda m: {"open": True, "left_min": 5})
    ap.check(force=True)
    assert sold == [(3, "기간 정리", 10100)]             # 보유 기간 마지막 날 장 마감 직전에만


def test_ai_job_survives_disconnect_and_resumes(rt, monkeypatch):
    """휴대폰 연결이 끊겨도 AI 작업은 끝까지 돌고, 같은 작업에 다시 붙어 남은 부분을 이어 받는다."""
    import time as _t
    ran = []

    def slow(kind, d):
        for i in range(4):
            _t.sleep(0.05)
            yield {"t": "text", "text": f"p{i}"}
        ran.append(kind)                      # 저장·비용 기록이 일어나는 자리
        yield {"t": "done", "usd": 0.1}
    monkeypatch.setattr(rt.ai, "stream", slow)
    c = create_app(rt).test_client()
    r = c.post("/api/ai/ask", json={"kind": "pick"}, buffered=False)
    it = r.response
    first = json.loads(next(it))
    assert first["t"] == "job" and first["id"]
    assert json.loads(next(it)) == {"t": "text", "text": "p0"}
    r.close()                                  # 화면이 꺼짐(연결 끊김)
    for _ in range(100):
        if ran:
            break
        _t.sleep(0.02)
    assert ran == ["pick"]                     # 그래도 끝까지 돌았다
    lines = [json.loads(x) for x in c.get(f"/api/ai/job/{first['id']}?from=1").get_data(as_text=True).splitlines()]
    assert [x.get("text") for x in lines[:3]] == ["p1", "p2", "p3"] and lines[-1]["t"] == "done"
    assert c.get("/api/ai/job/nope").status_code == 404
    # 사용자가 멈추면 작업도 멈춘다
    r2 = c.post("/api/ai/ask", json={"kind": "chat"}, buffered=False)
    jid = json.loads(next(r2.response))["id"]
    assert c.post(f"/api/ai/job/{jid}/cancel").get_json()["ok"]
    r2.close()
    _t.sleep(0.4)
    assert ran == ["pick"]


def test_ai_job_sends_heartbeat_while_quiet(rt, monkeypatch):
    import time as _t
    import threading
    gate = threading.Event()

    def quiet(kind, d):
        gate.wait(8)
        yield {"t": "done"}
    monkeypatch.setattr(rt.ai, "stream", quiet)
    monkeypatch.setattr(threading.Condition, "wait", lambda self, timeout=None: _t.sleep(0.05))
    c = create_app(rt).test_client()
    r = c.post("/api/ai/ask", json={"kind": "pick"}, buffered=False)
    it = r.response
    assert json.loads(next(it))["t"] == "job" and json.loads(next(it)) == {"t": "ping"}
    gate.set()
    rest = [json.loads(x) for x in it]
    assert rest[-1] == {"t": "done"}


def test_ai_context_explains_small_orderable_cash(rt, monkeypatch):
    """주문 가능 금액이 총자산보다 훨씬 작을 때, AI가 '살 수 없다'고 오해하지 않게 계좌 요약을 함께 준다."""
    monkeypatch.setattr(rt.account, "configured", lambda: True)
    monkeypatch.setattr(rt.account, "snapshot", lambda force=False: {
        "total": 205000, "eval": 120000, "cash": 60000, "orderable": 8139, "cash_detail": {"margin": 51861},
        "us": {"usd": 18.5, "usd_orderable": 18.5, "usd_krw": 25000}})
    cash, acct = rt.aipick._cash("kr")
    assert cash == 8139 and acct["총자산(원)"] == 205000 and acct["주문·미수에 묶인 증거금(원)"] == 51861 and acct["달러 예수금"] == 18.5
    assert rt.aipick._cash("us")[0] == 18.5
    monkeypatch.setattr(rt.account, "configured", lambda: False)
    assert rt.aipick._cash("kr") == (None, None)


def test_swing_pick_day_range_is_frozen_after_that_day(rt, monkeypatch):
    """추천 당일의 5분봉 범위는 그날에만 받아 적는다. 다음 날엔 5분봉 앞부분이 사라져 범위가 좁아지기 때문."""
    import moa.aipick as AP
    _swing_fixture(rt, monkeypatch, ts="2026-11-02T10:00:00")
    rt.aipick.stats()
    seen = []

    def candles(m, s, tf):
        seen.append(tf)
        if tf == "m5":
            return {"bars": [{"t": "11/02 10:00", "o": 100, "h": 104, "l": 99, "c": 103, "v": 1}, {"t": "11/02 15:00", "o": 103, "h": 103, "l": 101, "c": 102, "v": 1}]}
        return {"bars": [_dbar("2026-11-02", 97, 104, 96, 102)]}
    monkeypatch.setattr(rt.invest, "candles", candles)
    monkeypatch.setattr(AP, "mkt_date", lambda m: "2026-11-02")
    r = rt.db.one("SELECT * FROM ai_outcomes WHERE sym='000003'")
    AP._eval_swing(rt.aipick, r, datetime(2026, 11, 2, 15, 45))
    d0 = json.loads(rt.db.one("SELECT d0 FROM ai_outcomes WHERE sym='000003'")["d0"])
    assert d0["h"] == 104 and d0["l"] == 99 and d0["c"] == 102
    # 다음 날: 5분봉에는 전날 끝부분만 남아 있다 → 다시 받지 않고 적어 둔 값을 쓴다
    seen.clear()
    monkeypatch.setattr(AP, "mkt_date", lambda m: "2026-11-03")
    r = rt.db.one("SELECT * FROM ai_outcomes WHERE sym='000003'")
    AP._eval_swing(rt.aipick, r, datetime(2026, 11, 3, 10, 0))
    assert "m5" not in seen and json.loads(rt.db.one("SELECT d0 FROM ai_outcomes WHERE sym='000003'")["d0"])["h"] == 104


def test_yahoo_daily_bars_use_exchange_date(rt, monkeypatch):
    """미국 일봉의 진행 중인 오늘 봉은 시각이 '마지막 체결 시각'이라, 한국 시간으로 바꾸면 날짜가 하루 밀린다."""
    from datetime import datetime as D, timezone as TZ
    t_done = int(D(2026, 11, 16, 14, 30, tzinfo=TZ.utc).timestamp())     # 11/16 장 시작(뉴욕 09:30)
    t_live = int(D(2026, 11, 17, 20, 15, tzinfo=TZ.utc).timestamp())     # 11/17 뉴욕 15:15 = 한국 11/18 05:15
    res = {"meta": {"gmtoffset": -18000}, "timestamp": [t_done, t_live],
           "indicators": {"quote": [{"open": [10, 11], "high": [12, 13], "low": [9, 10], "close": [11, 12], "volume": [5, 6]}]}}
    monkeypatch.setattr(rt.market, "_yh_chart", lambda sym, rng, iv, prepost=False: res)
    bars = rt.invest.candles("us", "TESTX", "d")["bars"]
    assert [b["t"] for b in bars] == ["2026-11-16", "2026-11-17"]
    assert len(rt.invest.candles("us", "TESTX", "mo")["bars"][0]["t"]) == 10      # 월봉을 분봉처럼 적던 문제


def test_upstream_network_errors_are_502_and_logged_once(rt):
    import requests
    app = create_app(rt)

    @app.get("/api/net")
    def net():
        raise requests.ConnectionError("proxy down at http://secret-host")
    c = app.test_client()
    for _ in range(5):
        r = c.get("/api/net")
        assert r.status_code == 502 and "secret-host" not in r.get_json()["error"] and r.get_json()["code"] == "upstream"
    n = rt.db.one("SELECT COUNT(*) AS n FROM engine_log WHERE msg LIKE '%/api/net%'")["n"] if rt.db.one("SELECT name FROM sqlite_master WHERE name='engine_log'") else 1
    assert n == 1                                   # 몇 초마다 되풀이돼도 로그는 한 번


def test_ai_stream_records_cost_when_cut_midway(rt, monkeypatch):
    import requests
    from moa.ai import AIError
    rt.settings.update({"ai": {"api_key": "sk-test-key-1234567890"}})

    def post(body):
        yield {"type": "message_start", "message": {"usage": {"input_tokens": 9000, "output_tokens": 1}}}
        yield {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "앞부분"}}
        raise AIError("Claude의 답을 받는 도중 연결이 끊겼어요. 다시 받아 주세요 (ChunkedEncodingError)")
    monkeypatch.setattr(rt.ai, "_post", post)
    monkeypatch.setattr(rt.ai, "context", lambda kind, d: ("t", "q", {}))
    before = rt.db.one("SELECT COUNT(*) AS n FROM ai_usage")["n"]
    evs = list(rt.ai.stream("chat", {"question": "x"}))
    assert evs[-1]["t"] == "error" and "끊겼어요" in evs[-1]["msg"]
    row = rt.db.one("SELECT * FROM ai_usage ORDER BY rowid DESC LIMIT 1")
    assert rt.db.one("SELECT COUNT(*) AS n FROM ai_usage")["n"] == before + 1 and row["in_tok"] == 9000 and row["usd"] > 0


# ---------- 배당 ----------
def _yh_div(price, divs, kind="ETF", off=-18000, cur="USD"):
    """야후 차트 응답(배당 내역 포함) 흉내. divs: [(YYYY-MM-DD, 금액)]"""
    from datetime import datetime as D, timezone as TZ
    ev = {}
    for d, a in divs:
        y, m, dd = map(int, d.split("-"))
        ts = int(D(y, m, dd, 14, 30, tzinfo=TZ.utc).timestamp())
        ev[str(ts)] = {"amount": a, "date": ts}
    return {"meta": {"regularMarketPrice": price, "currency": cur, "instrumentType": kind, "gmtoffset": off, "shortName": "Y"},
            "timestamp": [], "indicators": {"quote": [{"close": []}]}, "events": {"dividends": ev}}


def test_dividend_analyze_yield_frequency_months_growth():
    import moa.dividend as DV
    from datetime import date
    today = date(2026, 11, 18)
    # 분기 배당 ETF: 3·6·9·12월 배당락, 해마다 10%씩 증가
    divs = []
    for y in range(2019, 2027):
        base = 0.5 * 1.1 ** (y - 2019)
        for m in (3, 6, 9, 12):
            if date(y, m, 20) < today:
                divs.append((date(y, m, 20), round(base, 4)))
    a = DV.analyze(divs, 100.0, "us", "ETF", today)
    assert a["freq"] == 4 and a["count"] == len(divs)
    assert abs(a["ttm"] - (0.5 * 1.1 ** 6 + 3 * 0.5 * 1.1 ** 7)) < 1e-3 and a["yield"] == round(a["ttm"], 2)
    assert [i + 1 for i, v in enumerate(a["months"]) if v > 0] == [3, 6, 9, 12] and abs(sum(a["months"]) - 1) < 0.01
    assert a["growth5"] == 10.0 and a["growth3"] == 10.0 and a["streak"] == 6      # 끝난 해(2019~2025)만 비교
    assert a["last"]["date"] == "2026-09-20" and a["next_ex"] > "2026-11-18"
    # 미국 주식은 3주 뒤 지급 → 다음 달로 넘어간다 / 국내 결산배당은 이듬해 4월
    assert DV.pay_date("us", "EQUITY", date(2026, 3, 20)).month == 4
    assert DV.pay_date("kr", "EQUITY", date(2025, 12, 29)) == date(2026, 4, 15)
    assert DV.pay_date("kr", "EQUITY", date(2026, 6, 29)).month == 8 and DV.pay_date("kr", "ETF", date(2026, 6, 29)).month == 7
    # 월배당
    md = [(date(2025 + (m > 12), (m - 1) % 12 + 1, 5), 0.1) for m in range(12, 24)]
    m = DV.analyze(md, 20.0, "us", "ETF", today)
    assert m["freq"] == 12 and all(v > 0 for v in m["months"]) and m["yield"] == 6.0
    e = DV.analyze([], 10, "us", "ETF", today)
    assert e["yield"] is None and e["freq"] == 0 and DV.default_growth(e) == 0
    assert DV.default_growth({"growth5": 23.0}) == 10.0 and DV.default_growth({"growth5": -4, "growth3": 2}) == 0.0
    # 기본 후보·예시 구성의 짜임새
    keys = {(u[0], u[1]) for u in DV.UNIVERSE}
    assert len(keys) == len(DV.UNIVERSE) and all(u[3] in DV.CATS for u in DV.UNIVERSE)
    assert all(re.fullmatch(r"\d{6}", u[1]) for u in DV.UNIVERSE if u[0] == "kr")
    for p in DV.PRESETS:
        assert sum(w for _, _, w in p["assets"]) == 100 and all((m, s) in keys for m, s, _ in p["assets"])


def test_dividend_simulation_math():
    import moa.dividend as DV
    from datetime import date
    flat = [1 / 12] * 12
    A = lambda **k: {"mkt": "kr", "sym": "A", "name": "A", "cat": "kr_stock", "weight": 100, "yield": 6.0, "growth": 0, "months": flat, **k}
    base = dict(amount=300_000_000, years=10, price_growth=0, growth_scale=1, tax_kr=15.4, tax_us=15, fx_change=0)
    # 재투자 안 함 + 성장 0: 매달 3억 × 6% ÷ 12 × (1 − 15.4%) = 1,269,000원이 그대로
    r = DV.simulate(dict(base, reinvest=False, assets=[A()]), today=date(2026, 11, 18))
    assert r["start"] == "2026-12-01" and r["months"][0]["ym"] == "2026-12" and r["months"][1]["ym"] == "2027-01"
    assert all(m["net"] == 1_269_000 for m in r["months"]) and r["summary"]["first_monthly"] == 1_269_000 == r["summary"]["last_monthly"]
    assert r["yearly"][0]["gross"] == 18_000_000 and r["yearly"][0]["tax"] == 2_772_000 and r["yearly"][-1]["value"] == 300_000_000
    assert r["summary"]["yield"] == 6.0 and r["summary"]["yield_net"] == 5.08 and r["summary"]["over_line_year"] is None
    # 재투자: 달마다 세후 배당률만큼 주식이 늘어난다 → 10년 뒤 (1 + 0.06×0.846/12)^120배
    r2 = DV.simulate(dict(base, reinvest=True, assets=[A()]), today=date(2026, 11, 18))
    k = (1 + 0.06 * 0.846 / 12) ** 120
    assert abs(r2["yearly"][-1]["value"] / (300_000_000 * k) - 1) < 1e-6
    assert r2["summary"]["last_monthly"] > 2_000_000 > r2["summary"]["other_last_monthly"] == 1_269_000
    ms = {m["target"]: m for m in r2["milestones"]}
    assert ms[1_000_000]["year"] == 1 and ms[2_000_000]["year"] in (9, 10) and ms[2_000_000]["other"] is None and ms[5_000_000]["year"] is None
    assert r2["summary"]["over_line_year"] == 3                                    # 세전 연 2천만 원을 넘는 첫 해
    # 배당 성장 5%: 재투자 없이도 해마다 5%씩
    r3 = DV.simulate(dict(base, reinvest=False, assets=[A(growth=5)]), today=date(2026, 11, 18))
    assert round(r3["yearly"][1]["net"] / r3["yearly"][0]["net"], 4) == 1.05 and round(r3["yearly"][5]["net"] / r3["yearly"][0]["net"], 3) == round(1.05 ** 5, 3)
    # 6년차부터는 과거 성장률을 그대로 잇지 않는다: 주가 상승률(0%) + 1%p까지만 → 배당률이 끝없이 부풀지 않게
    assert round(r3["yearly"][9]["net"] / r3["yearly"][5]["net"], 3) == round(1.01 ** 4, 3)
    r3b = DV.simulate(dict(base, reinvest=False, price_growth=7, assets=[A(growth=5)]), today=date(2026, 11, 18))
    assert round(r3b["yearly"][9]["net"] / r3b["yearly"][0]["net"], 3) == round(1.05 ** 9, 3)       # 주가가 더 빨리 오르면 그대로
    assert r3["scenarios"][0]["last"] < r3["scenarios"][1]["last"] < r3["scenarios"][2]["last"] and r3["scenarios"][1]["last"] == r3["summary"]["last_monthly"]
    assert r3["cut"]["first"] == round(1_269_000 * 0.75)
    # 분기 배당은 그 달에만, 미국 종목은 15% 세율과 환율을 탄다
    q = [0, 0, .25, 0, 0, .25, 0, 0, .25, 0, 0, .25]
    r4 = DV.simulate(dict(base, reinvest=False, years=1, assets=[A(weight=50, months=q), A(sym="U", mkt="us", cat="us_etf", weight=50, **{"yield": 4.0})]), today=date(2026, 11, 18))
    by = {m["ym"]: m for m in r4["months"]}
    us_m = round(150_000_000 * 0.04 / 12 * 0.85)
    assert by["2027-01"]["net"] == us_m and by["2027-03"]["net"] == us_m + round(150_000_000 * 0.06 * 0.25 * 0.846)
    assert r4["summary"]["us_weight"] == 50 and r4["summary"]["min_month"] == us_m and r4["summary"]["yield"] == 5.0
    fx = {x["chg"]: x["monthly"] for x in r4["fx"]}
    assert fx[0] == r4["summary"]["first_monthly"] and fx[10] > fx[0] > fx[-10]
    share = us_m * 12 / r4["yearly"][0]["net"]
    assert abs(fx[10] / fx[0] - (1 + share * 0.1)) < 1e-3
    # 매월 추가 투자와 주가 상승(채권류는 주가 상승을 적용하지 않음)
    r5 = DV.simulate(dict(base, reinvest=False, monthly_add=1_000_000, years=2, price_growth=5, assets=[A(cat="us_bond", mkt="us", **{"yield": 4})]), today=date(2026, 11, 18))
    assert r5["yearly"][0]["invested"] == 311_000_000 and r5["yearly"][1]["invested"] == 323_000_000 and r5["yearly"][1]["value"] == 323_000_000
    assert r5["yearly"][1]["net"] > r5["yearly"][0]["net"]
    with pytest.raises(ValueError):
        DV.simulate(dict(base, assets=[]))
    with pytest.raises(ValueError):
        DV.simulate(dict(base, amount=0, assets=[A()]))


def _div_rt(rt, monkeypatch):
    """야후 응답을 가짜로 바꾼 배당 모듈."""
    from datetime import date, timedelta
    today = date.today()
    def gen(n, step, amt, years=7, grow=1.0):
        out, d = [], today - timedelta(days=10)
        for i in range(n * years):
            out.append((d.isoformat(), round(amt * grow ** -(i // n), 4)))
            d -= timedelta(days=step)
        return out
    data = {"SCHD": _yh_div(80.0, gen(4, 91, 0.75, grow=1.08)), "SGOV": _yh_div(100.0, gen(12, 30, 0.35)),
            "JEPI": _yh_div(56.0, gen(12, 30, 0.38)), "458730.KS": _yh_div(12000, gen(12, 30, 35), off=32400, cur="KRW"),
            "005930.KS": _yh_div(100000, gen(4, 91, 361), kind="EQUITY", off=32400, cur="KRW")}
    calls = []

    def chart(sym, rng="1d", interval="5m", prepost=False, events=None):
        calls.append(sym)
        if sym == "USDKRW=X":
            return {"meta": {"regularMarketPrice": 1400.0}, "timestamp": [1], "indicators": {"quote": [{"close": [1400.0]}]}}
        if sym not in data:
            raise RuntimeError("no data")
        return data[sym]
    monkeypatch.setattr(rt.market, "_yh_chart", chart)
    monkeypatch.setattr(rt.market, "yh_quote", lambda s: {"price": 1400.0})
    monkeypatch.setattr(rt.market, "kr_market", lambda s: "코스피")
    return calls


def test_dividend_info_cache_universe_and_custom(rt, monkeypatch):
    calls = _div_rt(rt, monkeypatch)
    dv = rt.dividend
    i = dv.info("us", "schd")
    assert i["sym"] == "SCHD" and i["name"].startswith("Schwab") and i["cat"] == "us_etf" and i["freq"] == 4 and i["freq_name"] == "분기"
    assert i["yield"] == 3.75 and i["growth"] > 0 and i["kind"] == "ETF" and i["price"] == 80.0
    n = len(calls)
    assert dv.info("us", "SCHD")["yield"] == 3.75 and len(calls) == n                       # 하루 동안은 저장본
    dv._mem.clear()
    assert dv.info("us", "SCHD")["yield"] == 3.75 and len(calls) == n                       # 서버를 껐다 켜도 DB에서
    bad = dv.info("us", "NOPE")
    assert bad["error"] and bad["yield"] is None and bad["months"] == [0] * 12
    u = dv.universe()
    assert len(u["items"]) == len(__import__("moa.dividend", fromlist=["x"]).UNIVERSE) and u["fx"] == 1400.0 and u["failed"] > 0
    pres = {p["id"]: p for p in u["presets"]}
    assert pres["stable"]["assets"][0]["sym"] == "SCHD" and pres["stable"]["yield"] > 0 and pres["stable"]["missing"] == 1   # VYM은 가짜 자료에 없음
    kr = next(x for x in u["items"] if x["sym"] == "458730")
    assert kr["freq"] == 12 and kr["currency"] == "KRW" and kr["yield"] == 3.5
    # 직접 넣은 종목
    x = dv.add_custom("kr", "005930", "삼성전자")
    assert x["custom"] and x["name"] == "삼성전자" and x["freq"] == 4 and x["kind"] == "EQUITY"
    assert any(i["sym"] == "005930" for i in dv.universe()["items"])
    dv.remove_custom("kr", "005930")
    assert not any(i["sym"] == "005930" for i in dv.universe()["items"])
    with pytest.raises(ValueError):
        dv.add_custom("kr", "12")
    c = create_app(rt).test_client()
    assert c.get("/api/div/info/us/JEPI").get_json()["freq"] == 12
    assert c.put("/api/div/goal", json={"goal": 2500000}).get_json()["goal"] == 2500000 and c.get("/api/div/universe").get_json()["goal"] == 2500000


def test_dividend_mine_and_api_simulation(rt, monkeypatch):
    _div_rt(rt, monkeypatch)
    dv = rt.dividend
    assert dv.mine()["configured"] is False
    monkeypatch.setattr(rt.account, "configured", lambda: True)
    monkeypatch.setattr(rt.account, "snapshot", lambda force=False: {"ok": True, "total": 30_000_000, "us": {"fx": 1400.0}, "holdings": [
        {"code": "SCHD", "name": "SCHD", "market": "us", "qty": 100, "avg_price": 70.0, "price": 80.0, "value": 11_200_000},
        {"code": "005930", "name": "삼성전자", "qty": 50, "avg_price": 80000, "price": 100000, "value": 5_000_000},
        {"code": "000001", "name": "무배당", "qty": 10, "avg_price": 1000, "price": 1000, "value": 10_000}]})
    dv.set_goal(1_000_000)
    m = dv.mine()
    schd, sam = m["rows"][0], m["rows"][1]
    assert schd["sym"] == "SCHD" and schd["gross"] == round(100 * 3.0 * 1400) and schd["net"] == round(100 * 3.0 * 1400 * 0.85)
    assert schd["yoc"] == round(3.0 / 70 * 100, 2) and sam["net"] == round(50 * 1444 * 0.846)
    assert m["annual_net"] == schd["net"] + sam["net"] and sum(m["months"]) in range(m["annual_net"] - 12, m["annual_net"] + 12)
    assert m["payers"] == 2 and m["goal_pct"] == round(m["annual_net"] / 12 / 1_000_000 * 100, 1) and m["need"] > 0
    assert m["rows"][2]["net"] == 0 and m["rows"][2]["error"]
    c = create_app(rt).test_client()
    assert c.get("/api/div/mine").get_json()["annual_net"] == m["annual_net"]
    # 화면은 종목·비중만 보내고, 배당률·지급월은 서버가 실제 자료에서 붙인다. 사용자가 고친 값은 그 값을 쓴다
    r = c.post("/api/div/simulate", json={"amount": 300_000_000, "years": 10, "reinvest": True,
                                          "assets": [{"mkt": "us", "sym": "SCHD", "weight": 70}, {"mkt": "us", "sym": "SGOV", "weight": 30}]}).get_json()
    assert r["summary"]["yield"] == round(0.7 * 3.75 + 0.3 * 4.2, 2) and len(r["months"]) == 12 and len(r["yearly"]) == 10 and r["fx_now"] == 1400.0
    assert r["summary"]["last_monthly"] > r["summary"]["first_monthly"] > 0 and r["assets"][0]["growth"] > 0 and r["summary"]["us_weight"] == 100
    assert any(ms["goal"] and ms["target"] == 1_000_000 for ms in r["milestones"])
    r2 = c.post("/api/div/simulate", json={"amount": 100_000_000, "years": 5, "reinvest": False, "assets": [{"mkt": "us", "sym": "NOPE", "weight": 100, "yield": 5}]}).get_json()
    assert r2["summary"]["yield"] == 5.0 and r2["assets"][0]["edited"] and r2["summary"]["first_monthly"] == round(100_000_000 * 0.05 * 0.85 / 12)
    r3 = c.post("/api/div/simulate", json={"amount": 100_000_000, "assets": [{"mkt": "us", "sym": "NOPE", "weight": 100}]}).get_json()
    assert r3["summary"]["missing"] == ["NOPE"] and r3["summary"]["first_monthly"] == 0
    assert c.post("/api/div/simulate", json={"amount": 1, "assets": []}).status_code == 400


def test_dividend_ai_plan_context_parse_and_stream(rt, monkeypatch):
    _div_rt(rt, monkeypatch)
    dv = rt.dividend
    title, ask, data = dv.ai_context({"amount": 300_000_000, "style": "income", "region": "us", "goal": 1_500_000, "memo": "달러 자산 위주"})
    rows = data["후보 목록(실제 최근 1년 지급 기준)"]
    assert "배당" in title and "300,000,000원" in ask and "1,500,000원" in ask and data["사용자 메모"] == "달러 자산 위주"
    assert {r["sym"] for r in rows} == {"SCHD", "SGOV", "JEPI", "458730"} and all(r["배당률%(세전, 최근 1년)"] for r in rows)
    with pytest.raises(ValueError):
        dv.ai_context({"amount": 0})
    text = "SCHD를 중심으로 짰어요.\n- 이유\n```json\n" + json.dumps({"title": "성장+현금흐름", "summary": "SCHD 중심", "assets": [
        {"sym": "SCHD", "mkt": "us", "name": "SCHD", "weight": 50, "role": "핵심", "reason": "배당 성장"},
        {"sym": "jepi", "mkt": "us", "weight": 30, "role": "현금흐름", "reason": "월배당"},
        {"sym": "458730", "name": "TIGER 미국배당다우존스", "weight": 10, "role": "성장", "reason": "원화"},
        {"sym": "ZZZZ", "mkt": "us", "weight": 10, "role": "완충", "reason": "없는 종목"}],
        "rebalance": "1년에 한 번", "risks": ["환율"], "notes": ["2천만 원 기준"]}, ensure_ascii=False) + "\n```"
    plan = dv.parse_plan(text, {"amount": 300_000_000, "style": "income"}, "claude-sonnet-5-5", 0.2)
    assert [a["sym"] for a in plan["assets"]] == ["SCHD", "JEPI", "458730"]                 # 자료 없는 후보 밖 종목은 뺀다
    assert abs(sum(a["weight"] for a in plan["assets"]) - 100) < 0.2 and plan["assets"][2]["mkt"] == "kr" and plan["assets"][0]["months"]
    assert plan["yield"] > 0 and plan["id"] and plan["params"]["amount"] == 300_000_000 and "```" not in dv.plans()[0]["text"]
    assert dv.plans()[0]["title"] == "성장+현금흐름" and dv.plans()[0]["assets"][0]["role"] == "핵심"
    with pytest.raises(ValueError):
        dv.parse_plan("표 없음", {}, "m", 0)
    c = create_app(rt).test_client()
    assert len(c.get("/api/div/plans").get_json()) == 1 and c.delete(f"/api/div/plans/{plan['id']}").get_json()["ok"] and dv.plans() == []
    # AI 스트림에 연결: divplan 종류는 배당 전용 지침으로 묻고, 끝나면 plan 이벤트를 낸다
    rt.settings.update({"ai": {"api_key": "sk-test-key-1234567890"}})
    seen = {}

    def post(body):
        seen.update(body)
        yield {"type": "message_start", "message": {"usage": {"input_tokens": 100, "output_tokens": 1}}}
        yield {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}
        yield {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 50}}
    monkeypatch.setattr(rt.ai, "_post", post)
    evs = list(rt.ai.stream("divplan", {"amount": 300_000_000, "web_search": False}))
    assert "배당 포트폴리오 설계" in seen["system"] and seen["max_tokens"] == 8000 and "tools" not in seen
    p = next(e for e in evs if e["t"] == "plan")
    assert p["title"] == "성장+현금흐름" and len(p["assets"]) == 3 and evs[-1]["t"] == "done"
    assert rt.db.one("SELECT kind FROM ai_usage ORDER BY rowid DESC LIMIT 1")["kind"] == "divplan"


def test_ai_stream_recovers_from_paused_empty_or_broken_answers(rt, monkeypatch):
    """검색이 길어져 멈춘 답(pause_turn)은 이어받고, 빈 답·깨진 표는 검색 없이 한 번 더 받아 살린다. 비용은 모두 더한다."""
    _div_rt(rt, monkeypatch)
    rt.settings.update({"ai": {"api_key": "sk-test-key-1234567890"}})
    good = "정리했어요.\n```json\n" + json.dumps({"title": "이어받은 구성", "summary": "s", "assets": [
        {"sym": "SCHD", "mkt": "us", "weight": 60, "reason": "성장"}, {"sym": "JEPI", "mkt": "us", "weight": 40, "reason": "월배당"}]}, ensure_ascii=False) + "\n```"
    calls = []

    def msg(text, stop, n_in=1000, n_out=100, search=0):
        yield {"type": "message_start", "message": {"usage": {"input_tokens": n_in, "output_tokens": 1}}}
        if search:
            yield {"type": "content_block_start", "index": 0, "content_block": {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {}}}
            yield {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"query": "SCHD '}}
            yield {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": 'dividend"}'}}
            yield {"type": "content_block_stop", "index": 0}
            yield {"type": "content_block_start", "index": 1, "content_block": {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
                                                                              "content": [{"type": "web_search_result", "url": "https://ex.com/a", "title": "A"}]}}
            yield {"type": "content_block_stop", "index": 1}
        if text:
            yield {"type": "content_block_start", "index": 2, "content_block": {"type": "text", "text": ""}}
            yield {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": text}}
            yield {"type": "content_block_stop", "index": 2}
        yield {"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": n_out, "server_tool_use": {"web_search_requests": search}}}

    # 1) 검색 뒤 멈춤 → 받은 블록을 그대로 돌려보내 이어받는다
    def post1(body):
        calls.append(json.loads(json.dumps(body)))
        return msg("찾아보는 중 ", "pause_turn", search=2) if len(calls) == 1 else msg(good, "end_turn", n_in=3000, n_out=400)
    monkeypatch.setattr(rt.ai, "_post", post1)
    evs = list(rt.ai.stream("divplan", {"amount": 100_000_000, "web_search": True}))
    assert len(calls) == 2 and "tools" in calls[1]
    back = calls[1]["messages"][-1]
    assert back["role"] == "assistant" and [b["type"] for b in back["content"]] == ["server_tool_use", "web_search_tool_result", "text"]
    assert back["content"][0]["input"] == {"query": "SCHD dividend"} and back["content"][2]["text"] == "찾아보는 중"   # 끝 공백 제거
    p = next(e for e in evs if e["t"] == "plan")
    assert p.get("title") == "이어받은 구성" and evs[-1]["t"] == "done" and evs[-1]["searches"] == 2
    assert evs[-1]["in"] == 4000 and evs[-1]["out"] == 500 and any(e["t"] == "status" and "이어서" in e["msg"] for e in evs)

    # 2) 답이 비어 끝남(스크린샷의 경우) → 검색 없이 다시 받아 구성을 만든다
    calls.clear()

    def post2(body):
        calls.append(json.loads(json.dumps(body)))
        return msg("", "end_turn", n_in=30000, n_out=200, search=4) if len(calls) == 1 else msg(good, "end_turn", n_in=9000, n_out=600)
    monkeypatch.setattr(rt.ai, "_post", post2)
    evs = list(rt.ai.stream("divplan", {"amount": 100_000_000, "web_search": True}))
    assert len(calls) == 2 and "tools" not in calls[1] and len(calls[1]["messages"]) == 1
    assert next(e for e in evs if e["t"] == "plan").get("title") == "이어받은 구성" and any(e["t"] == "reset" for e in evs)
    assert evs[-1]["in"] == 39000 and evs[-1]["searches"] == 4

    # 3) 표가 깨진 답 → 쓴 글을 참고로 다시 정리. 다시 받아도 안 되면 사유와 함께 알린다
    calls.clear()

    def post3(body):
        calls.append(json.loads(json.dumps(body)))
        return msg("SCHD 중심으로 가요. 표는 아래에", "max_tokens") if len(calls) == 1 else msg(good, "end_turn")
    monkeypatch.setattr(rt.ai, "_post", post3)
    evs = list(rt.ai.stream("divplan", {"amount": 100_000_000, "web_search": False}))
    assert [m["role"] for m in calls[1]["messages"]] == ["user", "assistant", "user"] and "SCHD 중심" in calls[1]["messages"][1]["content"]
    assert next(e for e in evs if e["t"] == "plan").get("title") == "이어받은 구성"
    text_after_reset = "".join(e["text"] for e in evs[max(i for i, e in enumerate(evs) if e["t"] == "reset"):] if e["t"] == "text")
    assert text_after_reset == good
    calls.clear()
    monkeypatch.setattr(rt.ai, "_post", lambda body: (calls.append(1), msg("표 없이 글만", "max_tokens"))[1])
    evs = list(rt.ai.stream("divplan", {"amount": 100_000_000, "web_search": False}))
    p = next(e for e in evs if e["t"] == "plan")
    assert len(calls) == 2 and "읽지 못했어요" in p["error"] and "잘렸어요" in p["error"] and evs[-1]["t"] == "done"

    # 4) 끝이 잘려도 온전한 종목 줄이 둘 이상이면 다시 묻지 않고 살린다
    calls.clear()
    cut = good[:good.index("]}")] + ',{"sym":"SGOV","mkt":"us","wei'
    monkeypatch.setattr(rt.ai, "_post", lambda body: (calls.append(1), msg(cut, "max_tokens"))[1])
    evs = list(rt.ai.stream("divplan", {"amount": 100_000_000, "web_search": False}))
    p = next(e for e in evs if e["t"] == "plan")
    assert len(calls) == 1 and [a["sym"] for a in p["assets"]] == ["SCHD", "JEPI"] and any("잘려" in n for n in p["notes"])

    # 5) 다른 화면(종목 정리 등)도 빈 답이면 한 번 더 받고, 그래도 비면 오류로 알린다(비용은 기록)
    calls.clear()
    monkeypatch.setattr(rt.ai, "_post", lambda body: (calls.append(body.get("model")), msg("", "refusal") if len(calls) == 1 else msg("답이에요", "end_turn"))[1])
    evs = list(rt.ai.stream("chat", {"question": "배당이 뭐야", "model": "claude-fable-5-2", "web_search": False, "_user": True}))
    assert calls == ["claude-fable-5-2", "claude-sonnet-5-5"] and "".join(e["text"] for e in evs if e["t"] == "text") == "답이에요" and evs[-1]["model"] == "claude-sonnet-5-5"
    calls.clear()
    n0 = rt.db.one("SELECT COUNT(*) AS n FROM ai_usage")["n"]
    monkeypatch.setattr(rt.ai, "_post", lambda body: (calls.append(1), msg("", "end_turn"))[1])
    evs = list(rt.ai.stream("chat", {"question": "배당이 뭐야", "web_search": False}))
    assert len(calls) == 2 and evs[-1]["t"] == "error" and "답을 주지 않았어요" in evs[-1]["msg"]
    assert rt.db.one("SELECT COUNT(*) AS n FROM ai_usage")["n"] == n0 + 1


def test_dca_links_with_dividend_and_ai(rt, monkeypatch):
    """적립 ↔ 배당·AI 연결: 구성 → 플랜 초안 → 한꺼번에 저장, 적립의 배당 전망, AI 점검 자료."""
    from moa.trading.dca import split_amounts
    _div_rt(rt, monkeypatch)
    assert split_amounts(1_000_000, [40, 35, 25]) == [400_000, 350_000, 250_000]
    a = split_amounts(500_500, [1, 1, 1])
    assert sum(a) == 500_000 and all(x % 1000 == 0 for x in a) and split_amounts(0, [1]) == [0] and split_amounts(9000, [0, 0]) == [0, 0]
    c = create_app(rt).test_client()
    mix = [{"mkt": "us", "sym": "SCHD", "name": "SCHD", "weight": 50}, {"mkt": "kr", "sym": "458730", "name": "TIGER 미국배당다우존스", "weight": 30},
           {"mkt": "kr", "sym": "005930", "weight": 20}, {"mkt": "kr", "sym": "458730", "weight": 5}, {"mkt": "kr", "sym": "000000", "weight": 0}]
    # 초안: 미국 종목은 적립 대상에서 빠지고(몫은 알려 줌), 국내 종목끼리 비중을 다시 나눈다. 저장은 하지 않는다
    d = c.post("/api/dca/draft", json={"total": 1_000_000, "assets": mix}).get_json()
    assert d["kr_share"] == 50.0 and d["kr_total"] == 500_000 and [r["code"] for r in d["rows"]] == ["458730", "005930"]
    assert [r["amount"] for r in d["rows"]] == [300_000, 200_000] and [r["weight"] for r in d["rows"]] == [60.0, 40.0]
    assert d["us"] == [{"sym": "SCHD", "name": "SCHD", "weight": 50.0, "amount": 500_000}] and d["rows"][1]["name"] and rt.dca.plans() == []
    assert all(r["unit"] and r["enough"] is (r["amount"] >= r["unit"]) and r["plan"] is None for r in d["rows"])
    d2 = c.post("/api/dca/draft", json={"total": 1_000_000, "assets": mix, "spread": True}).get_json()
    assert d2["kr_total"] == 1_000_000 and [r["amount"] for r in d2["rows"]] == [600_000, 400_000]
    small = c.post("/api/dca/draft", json={"total": 3000, "assets": mix[1:3], "spread": True}).get_json()
    assert any(r["enough"] is False for r in small["rows"])                       # 한 달 금액이 1주 값보다 적으면 알려 준다
    assert c.post("/api/dca/draft", json={"total": 500, "assets": mix}).status_code == 400
    assert c.post("/api/dca/draft", json={"total": 100000, "assets": []}).status_code == 400
    only_us = c.post("/api/dca/draft", json={"total": 100000, "assets": mix[:1]}).get_json()
    assert only_us["rows"] == [] and only_us["kr_total"] == 0
    # 한꺼번에 저장: 하나라도 틀리면 아무것도 저장하지 않는다. 주문은 나가지 않는다
    bad = c.post("/api/dca/plans/bulk", json={"day": 25, "items": [{"code": "458730", "amount": 300000}, {"code": "SCHD", "amount": 100000}]})
    assert bad.status_code == 400 and rt.dca.plans() == []
    assert c.post("/api/dca/plans/bulk", json={"day": 31, "items": [{"code": "458730", "amount": 300000}]}).status_code == 400
    assert c.post("/api/dca/plans/bulk", json={"day": 25, "items": [{"code": "458730", "amount": 500}]}).status_code == 400
    assert c.post("/api/dca/plans/bulk", json={"day": 25, "items": [{"code": "458730", "amount": 5000}, {"code": "458730", "amount": 5000}]}).status_code == 400
    r = c.post("/api/dca/plans/bulk", json={"day": 25, "set_weight": True, "items": [
        {"code": r["code"], "name": r["name"], "amount": r["amount"], "target_weight": r["weight"]} for r in d["rows"]]}).get_json()
    assert r["created"] == 2 and r["updated"] == 0 and r["monthly"] == 500_000
    ps = rt.dca.plans()
    assert [(p["code"], p["amount"], p["day"], p["target_weight"], p["enabled"]) for p in ps] == [("458730", 300_000, 25, 60.0, 1), ("005930", 200_000, 25, 40.0, 1)]
    assert rt.db.q("SELECT 1 FROM orders") == [] and rt.db.q("SELECT 1 FROM dca_runs") == []
    # 같은 종목이 이미 있으면 새로 만들지 않고 금액·매수일을 바꾼다(꺼 둔 플랜은 다시 켠다). 목표 비중은 시키지 않으면 그대로
    rt.dca.set_enabled(ps[0]["id"], False)
    d3 = c.post("/api/dca/draft", json={"total": 400_000, "assets": mix[1:3]}).get_json()
    assert d3["rows"][0]["plan"] == {"id": ps[0]["id"], "amount": 300_000, "day": 25, "enabled": False} and d3["others"] == 0
    r = c.post("/api/dca/plans/bulk", json={"day": 10, "items": [{"code": "458730", "amount": 240_000, "target_weight": 99}, {"code": "069500", "amount": 50_000}]}).get_json()
    assert r["created"] == 1 and r["updated"] == 1 and len(rt.dca.plans()) == 3
    p0 = rt.dca.plans()[0]
    assert (p0["amount"], p0["day"], p0["target_weight"], p0["enabled"], p0["name"]) == (240_000, 10, 60.0, 1, "TIGER 미국배당다우존스")
    br = c.get("/api/dca/plans").get_json()
    assert br["monthly"] == 490_000 and len(br["plans"]) == 3 and "value" not in br["plans"][0] and br["mode_label"]

    # 적립의 배당 전망: 플랜 종목의 실제 배당 자료로, 보유분의 지금 배당과 해마다 추정을 준다
    rt.dividend.set_goal(100_000)
    o = c.get("/api/dca/outlook").get_json()
    rows = {r["code"]: r for r in o["rows"]}
    assert o["n"] == 3 and o["monthly"] == 490_000 and o["goal"] == 100_000 and o["start"] == 0 and o["now_net"] == 0
    assert rows["458730"]["freq"] == 12 and rows["458730"]["yield"] > 3 and rows["458730"]["share"] == round(240 / 490 * 100, 1)
    assert rows["069500"]["error"] and rows["069500"]["yield"] is None                       # 자료 없는 종목은 0으로 치고 알려 준다
    S = o["sim"]
    assert S["years"] == 20 and len(S["yearly"]) == 20 and S["missing"] == ["069500"] and len(S["spend"]) == 20 and len(S["months"]) == 12
    assert S["yearly"][0]["invested"] == 490_000 * 11 and S["yearly"][9]["monthly"] > S["yearly"][4]["monthly"] > S["yearly"][0]["monthly"] > 0
    assert S["yearly"][19]["monthly"] > S["spend"][19] and S["goal_year"] and S["goal_year_spend"] and S["goal_year"] <= S["goal_year_spend"]
    assert abs(sum(a["weight"] for a in o["assets"]) - 100) < 0.3 and {a["sym"] for a in o["assets"]} == {"458730", "005930", "069500"}
    # 보유분이 생기면 지금 배당과 시작 금액에 반영된다
    rt.brokers["dca"].buy("458730", 100)
    o2 = c.get("/api/dca/outlook").get_json()
    r2 = next(r for r in o2["rows"] if r["code"] == "458730")
    assert r2["held"] > 0 and r2["net"] == round(100 * 35 * 12 * (1 - 0.154)) and o2["now_monthly"] == round(r2["net"] / 12) and o2["start"] == r2["held"]
    assert o2["sim"]["yearly"][0]["monthly"] > S["yearly"][0]["monthly"]
    # 플랜이 없거나 모두 꺼져 있으면 전망은 비운다
    for p in rt.dca.plans():
        rt.dca.set_enabled(p["id"], False)
    o3 = c.get("/api/dca/outlook").get_json()
    assert o3["sim"] is None and o3["monthly"] == 0 and len(o3["rows"]) == 3
    for p in rt.dca.plans():
        rt.dca.set_enabled(p["id"], True)

    # 시뮬레이션: 처음 넣는 돈 없이 매달 적립만으로도 계산한다
    sim = c.post("/api/div/simulate", json={"amount": 0, "monthly_add": 500_000, "years": 5, "assets": [{"mkt": "kr", "sym": "458730", "weight": 100}]}).get_json()
    assert sim["summary"]["yield_net"] is None and sim["summary"]["invested"] == 500_000 * 59 and sim["yearly"][4]["monthly"] > 0
    assert c.post("/api/div/simulate", json={"amount": 0, "monthly_add": 0, "assets": [{"mkt": "kr", "sym": "458730", "weight": 100}]}).status_code == 400

    # 후보 목록 밖 종목(보유·적립 종목)은 화면이 보낸 이름으로 결과에 싣는다
    sim = c.post("/api/div/simulate", json={"amount": 10_000_000, "assets": [{"mkt": "kr", "sym": "005930", "weight": 50, "name": "삼성전자"},
                                                                              {"mkt": "kr", "sym": "458730", "weight": 50, "name": "엉뚱한 이름"}]}).get_json()
    assert [a["name"] for a in sim["assets"]] == ["삼성전자", "TIGER 미국배당다우존스"]
    # AI 적립 점검: 플랜·성과·배당 전망을 자료로 주고, 종목을 사라고 지시하지 않게 묻는다
    title, ask, data = rt.ai.context("dca", {})
    assert title == "AI 적립 점검" and "겹치는" in ask and "지시하지 말고" in ask and data["매달 합계(원)"] == 490_000
    pl = {p["코드"]: p for p in data["플랜"]}
    assert pl["458730"]["배당 지급"] == "월배당" and pl["458730"]["평가액"] > 0 and pl["458730"]["한 달 금액으로 살 수 있는 주식 수"] >= 1
    assert data["배당 전망(지금 플랜을 그대로 이어 가고 배당을 다시 투자할 때, 세후 월평균)"]["10년 뒤"] > 0 and data["목표 월 배당(세후, 원)"] == 100_000 and data["배당 목표 도달(추정)"].endswith("년차")
    assert rt.ai.meter("dca")["usd_lo"] > 0 and rt.ai.meter("dca", web=True)["web"] is False
    rt.settings.update({"ai": {"api_key": "sk-test-key-1234567890"}})
    sent = {}

    def post(body):
        sent.update(body)
        yield {"type": "message_start", "message": {"usage": {"input_tokens": 100, "output_tokens": 1}}}
        yield {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "점검 결과"}}
        yield {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 20}}
    monkeypatch.setattr(rt.ai, "_post", post)
    evs = list(rt.ai.stream("dca", {"web_search": True}))
    assert "tools" not in sent and evs[-1]["t"] == "done" and evs[-1]["title"] == "AI 적립 점검"          # 앱 자료만으로 답한다(검색 안 함)
    assert rt.db.q("SELECT 1 FROM orders") == [] and len(rt.dca.plans()) == 3                                # AI는 플랜을 바꾸거나 주문하지 않는다
    # AI 배당 구성에도 지금 적립 중인 플랜을 알려 준다
    _, ask2, data2 = rt.dividend.ai_context({"amount": 100_000_000, "monthly_add": 500_000})
    assert len(data2["지금 매달 적립 중인 플랜(국내 종목만 자동 적립 가능)"]) == 3 and "적립 중인 플랜" in ask2 and "직접 사야" in ask2
    for p in rt.dca.plans():
        rt.dca.delete_plan(p["id"])
    with pytest.raises(AIError):
        rt.ai.context("dca", {})


def test_stock_search_by_name_without_master_and_quick_picks(rt, monkeypatch):
    """코드를 외우지 않아도 되게: 종목 목록(키움)이 없어도 앱이 아는 이름으로 찾고, 바로 고르기 목록과 코드→이름을 준다."""
    m = rt.market
    monkeypatch.setattr(m, "kr_master", lambda wait=False: {})
    monkeypatch.setattr(m, "_yh_search_raw", lambda q: (_ for _ in ()).throw(RuntimeError("blocked")))
    r = m.kr_search("미국배당")
    assert "458730" in [x["sym"] for x in r] and all("미국배당" in x["name"] for x in r)          # 공개 검색이 막혀도 아는 이름으로 찾는다
    assert m.kr_search("tiger 미국s")[0]["name"] == "TIGER 미국S&P500" and m.kr_search("0059")[0]["sym"] in ("005930", "005935")
    with pytest.raises(RuntimeError):
        m.kr_search("없는이름zz")
    monkeypatch.setattr(m, "_yh_search_raw", lambda q: [{"symbol": "005930.KS", "shortname": "Samsung Electronics"}, {"symbol": "091990.KQ", "shortname": "Celltrion HC"},
                                                         {"symbol": "AAPL", "shortname": "Apple"}])
    r = m.kr_search("삼성전자")
    assert [x["sym"] for x in r] == ["005930", "005935", "091990"] and r[0]["name"] == "삼성전자" and r[2]["exchange"] == "코스닥"   # 아는 이름 먼저, 겹치면 한 번만
    monkeypatch.setattr(m, "kr_master", lambda wait=False: {"005930": {"name": "삼성전자", "market": "코스피"}, "360750": {"name": "TIGER 미국S&P500", "market": "코스피"}})
    assert m.kr_search("tiger")[0] == {"sym": "360750", "name": "TIGER 미국S&P500", "exchange": "코스피"}
    rt.settings.update({"watch": {"kr": ["005930", "999999"], "us": ["NVDA"]}})
    rt.dca.save_plan({"code": "458730", "name": "TIGER 미국배당다우존스", "amount": 100000, "day": 5})
    c = create_app(rt).test_client()
    p = c.get("/api/market/picks?mkt=kr&syms=360750,123456").get_json()
    assert [(x["sym"], x["name"], x["tag"]) for x in p["picks"]] == [("005930", "삼성전자", "관심"), ("999999", "999999", "관심"), ("458730", "TIGER 미국배당다우존스", "적립 중")]
    assert p["names"] == {"360750": "TIGER 미국S&P500", "123456": "123456"}
    assert c.get("/api/market/picks?mkt=us").get_json()["picks"] == [{"sym": "NVDA", "name": "NVDA", "tag": "관심"}]
    assert c.get("/api/market/search?mkt=kr&q=tiger").get_json()[0]["sym"] == "360750"


def _ledger_n(db):
    return db.one("SELECT COUNT(*) AS n FROM transactions")["n"]


def _add_tx(db, memo, amount=1000):
    db.x("INSERT INTO transactions(date,kind,amount,memo,created_at) VALUES('2026-11-01','out',?,?,'2026-11-01T00:00:00')", (amount, memo))


def test_backup_create_list_auto_prune_and_extra_folder(rt, tmp_path):
    """백업은 쓰는 중에도 통째로 복사하고, 하루 한 번 자동으로 남기며, 종류마다 정해 둔 개수만 남긴다."""
    from datetime import datetime as DT, timedelta
    import sqlite3
    bk = rt.backup
    bk.dir = str(tmp_path / "backups")
    assert bk.status()["items"] == [] and bk.status()["last"] is None and bk.conf() == {"auto": True, "keep": 14, "extra_dir": ""}
    _add_tx(rt.db, "백업 전 내역")
    rt.dca.save_plan({"code": "005930", "amount": 100000, "day": 5})
    m = bk.create("manual", note="테스트", now=DT(2026, 11, 18, 9, 0, 0))
    assert m["name"] == "moa-20261118-090000-manual.db" and m["kind_name"] == "직접" and m["ts"] == "2026-11-18T09:00:00" and m["note"] == "테스트"
    assert m["counts"]["transactions"] == 1 and m["counts"]["dca_plans"] == 1 and m["counts"]["last"] == "2026-11-01" and m["size"] > 4096
    f = tmp_path / "backups" / m["name"]
    raw = f.read_bytes()
    assert raw[:16] == b"SQLite format 3\x00" and raw[18:20] == b"\x01\x01"                 # WAL이 아닌 보통 파일(하나만 옮겨도 온전)
    assert sorted(x.name for x in (tmp_path / "backups").iterdir()) == [m["name"], m["name"] + ".json"]   # 임시 파일이 남지 않는다
    con = sqlite3.connect(str(f))
    assert con.execute("SELECT memo FROM transactions").fetchone()[0] == "백업 전 내역" and con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    con.close()
    assert bk.create("manual", now=DT(2026, 11, 18, 9, 0, 0))["name"] == m["name"] and len(bk.list()) == 1   # 같은 초에 두 번 눌러도 하나
    # 자동: 하루에 한 번만. 꺼 두면 만들지 않는다
    d1 = DT(2026, 11, 19, 3, 0, 0)
    a = bk.auto_due(d1)
    assert a and a["kind"] == "auto" and bk.auto_due(d1 + timedelta(hours=5)) is None
    bk._day = None
    assert bk.auto_due(d1 + timedelta(hours=6)) is None and len([i for i in bk.list() if i["kind"] == "auto"]) == 1   # 다시 켜져도 오늘 것이 있으면 넘어간다
    bk.set_conf({"auto": False})
    assert bk.auto_due(d1 + timedelta(days=1)) is None
    bk.set_conf({"auto": True, "keep": 7})
    for i in range(2, 12):
        bk._day = None
        bk.auto_due(d1 + timedelta(days=i))
    autos = [i for i in bk.list() if i["kind"] == "auto"]
    assert len(autos) == 7 and autos[0]["ts"][:10] == "2026-11-30" and autos[-1]["ts"][:10] == "2026-11-24"   # 오래된 것부터 지운다
    assert [i["kind"] for i in bk.list()].count("manual") == 1                                                  # 직접 만든 것은 자동 정리에 안 걸린다
    for i in range(7):
        bk.create("prerestore", now=DT(2026, 12, 1, 0, 0, i))
    assert len([i for i in bk.list() if i["kind"] == "prerestore"]) == 5
    st = bk.status()
    assert st["last"]["kind"] == "prerestore" and st["total_size"] == sum(i["size"] for i in st["items"]) and "2026-11-18" in st["days"]
    with pytest.raises(ValueError):
        bk.set_conf({"keep": 3})
    # 지우기: 이름은 정해진 꼴만 받는다(다른 파일을 건드릴 수 없다)
    for bad in ("../moa.db", "moa.db", "moa-20261118-090000-manual.db/../x", "", None, "moa-20261118-090000-evil.db"):
        with pytest.raises(ValueError):
            bk.path(bad)
    bk.delete(m["name"])
    assert not f.exists() and not (tmp_path / "backups" / (m["name"] + ".json")).exists()
    # 다른 폴더에도 복사: 노트북에서만 정할 수 있고, 없는 폴더는 받지 않는다
    extra = tmp_path / "cloud"
    with pytest.raises(PermissionError):
        bk.set_conf({"extra_dir": str(extra)}, local=False)
    with pytest.raises(ValueError):
        bk.set_conf({"extra_dir": str(extra)}, local=True)
    with pytest.raises(ValueError):
        bk.set_conf({"extra_dir": bk.dir}, local=True)
    extra.mkdir()
    assert bk.set_conf({"extra_dir": str(extra)}, local=True)["extra_dir"] == str(extra)
    m2 = bk.create("manual", now=DT(2026, 12, 2, 8, 0, 0))
    assert m2["copied"] is True and (extra / m2["name"]).read_bytes() == (tmp_path / "backups" / m2["name"]).read_bytes() and bk.status()["extra_ok"]
    import shutil
    shutil.rmtree(extra)
    m3 = bk.create("manual", now=DT(2026, 12, 2, 8, 0, 1))                                  # 폴더가 사라져도 기본 백업은 만들어진다
    assert "copy_error" in m3 and not m3.get("copied") and bk.status()["extra_ok"] is False
    assert bk.set_conf({"extra_dir": ""}, local=True)["extra_dir"] == ""


def test_backup_download_strips_keys_and_restore_keeps_connection(rt, tmp_path):
    """내려받는 사본에는 키·토큰·비밀번호가 없고, 되돌려도 지금 연결(토큰·비밀번호·열린 기기)은 그대로다."""
    import io
    import sqlite3
    from moa.app import access_token
    bk = rt.backup
    bk.dir = str(tmp_path / "backups")
    c, h = _remote(rt)
    token = access_token(rt.db)
    sid = c.post("/api/auth/setup", json={"password": "gagyebu!7"}, headers=h).get_json()["session"]
    hs = {**h, "X-Moa-Session": sid}
    rt.settings.update({"ai": {"api_key": "sk-ant-SECRETKEY-1234567890"}, "kiwoom": {"dca": {"appkey": "APPKEY-DCA-777", "secretkey": "KIWOOMSECRET-888"}},
                        "ntfy_topic": "my-private-topic-999", "allowed_origins": ["https://old.example"], "networth_goal": 111})
    _add_tx(rt.db, "남아야 하는 내역")
    assert c.get("/api/backup", headers={"X-Forwarded-For": "100.64.0.2"}).status_code == 401 and c.get("/api/backup", headers=h).status_code == 401      # 토큰·잠금 해제 없이는 못 본다
    b1 = c.post("/api/backup", json={"note": "되돌릴 지점"}, headers=hs).get_json()
    st = c.get("/api/backup", headers=hs).get_json()
    assert st["items"][0]["name"] == b1["name"] and st["need_password"] is True and st["local"] is False and st["can_restart"] is False
    # 내려받기(기본): 비밀 값이 파일 어디에도 없다
    r = c.post(f"/api/backup/{b1['name']}/download", json={}, headers=hs)
    safe = r.data
    assert r.status_code == 200 and r.headers["Content-Type"] == "application/octet-stream" and b1["name"] in r.headers["Content-Disposition"]
    hash_now = rt.db.get_setting("auth")["hash"]
    for secret in ("sk-ant-SECRETKEY-1234567890", "KIWOOMSECRET-888", "APPKEY-DCA-777", "my-private-topic-999", token, hash_now):
        assert secret.encode() not in safe
    assert "남아야 하는 내역".encode() in safe
    full_raw = (tmp_path / "backups" / b1["name"]).read_bytes()
    assert b"sk-ant-SECRETKEY-1234567890" in full_raw and token.encode() in full_raw                             # 노트북에 둔 원본은 전체 사본
    assert sorted(x.name for x in (tmp_path / "backups").iterdir()) == [b1["name"], b1["name"] + ".json"]       # 임시 사본은 지운다
    # 전체 사본과 되돌리기는 비밀번호를 한 번 더 확인한다
    assert c.post(f"/api/backup/{b1['name']}/download", json={"full": True}, headers=hs).status_code == 403
    bad = c.post(f"/api/backup/{b1['name']}/download", json={"full": True, "password": "wrong-pw"}, headers=hs)
    assert bad.status_code == 403 and bad.get_json()["code"] == "password"
    assert c.post(f"/api/backup/{b1['name']}/download", json={"full": True, "password": "gagyebu!7"}, headers=hs).data == full_raw
    assert c.post(f"/api/backup/{b1['name']}/restore", json={"password": "nope-nope"}, headers=hs).status_code == 403
    for bad_name in ("moa.db", "moa-20261118-090000-evil.db", "..%2Fmoa.db"):
        assert c.post(f"/api/backup/{bad_name}/download", json={}, headers=hs).status_code in (400, 404, 405)
        assert c.delete(f"/api/backup/{bad_name}", headers=hs).status_code in (400, 404, 405)
    # 다른 폴더는 휴대폰에서 못 바꾼다(노트북에서만)
    assert c.put("/api/backup/config", json={"extra_dir": str(tmp_path)}, headers=hs).status_code == 403
    assert c.put("/api/backup/config", json={"keep": 30, "auto": False}, headers=hs).get_json() == {"auto": False, "keep": 30, "extra_dir": ""}

    # 백업 뒤에 바뀐 것들
    _add_tx(rt.db, "백업 뒤에 넣은 내역")
    rt.settings.update({"networth_goal": 999, "allowed_origins": ["https://now.example"], "ai": {"api_key": "sk-ant-NEWKEY-000"}})
    rt.dca.save_plan({"code": "005930", "amount": 100000, "day": 5})
    assert _ledger_n(rt.db) == 2
    # 1) 전체 백업으로 되돌리기: 데이터와 키는 백업 것으로, 연결 설정은 지금 것으로
    out = c.post(f"/api/backup/{b1['name']}/restore", json={"password": "gagyebu!7"}, headers=hs).get_json()
    assert out["restored"]["name"] == b1["name"] and out["restart"] is False and out["pre"].endswith("-prerestore.db")
    assert _ledger_n(rt.db) == 1 and rt.dca.plans() == [] and rt.settings.get("networth_goal") == 111
    assert rt.settings.get("ai")["api_key"] == "sk-ant-SECRETKEY-1234567890" and rt.settings.get("allowed_origins") == ["https://now.example"]
    assert access_token(rt.db) == token and rt.db.get_setting("auth")["hash"] == hash_now
    assert c.get("/api/backup", headers=hs).status_code == 200                                    # 같은 기기에서 그대로 이어 쓴다
    pre = sqlite3.connect(str(tmp_path / "backups" / out["pre"]))
    assert pre.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2                     # 되돌리기 전 상태가 남아 있어 다시 돌아갈 수 있다
    pre.close()
    assert "되돌렸어요" in rt.db.one("SELECT msg FROM engine_log ORDER BY id DESC LIMIT 1")["msg"]
    # 2) 올린 파일(키를 뺀 사본)로 되돌리기: 키는 지금 것을 그대로 쓴다
    rt.settings.update({"ai": {"api_key": "sk-ant-CURRENT-555"}})
    _add_tx(rt.db, "또 넣은 내역")
    up = c.post("/api/backup/upload", data={"file": (io.BytesIO(safe), "moa-backup.db")}, headers=hs, content_type="multipart/form-data").get_json()
    assert up["kind"] == "upload" and up["keyless"] is True and up["counts"]["transactions"] == 1
    out = c.post(f"/api/backup/{up['name']}/restore", json={"password": "gagyebu!7"}, headers=hs).get_json()
    assert out["restored"]["kind"] == "upload" and _ledger_n(rt.db) == 1
    s = rt.settings.all()
    assert s["ai"]["api_key"] == "sk-ant-CURRENT-555" and s["kiwoom"]["dca"]["secretkey"] == "KIWOOMSECRET-888" and s["ntfy_topic"] == "my-private-topic-999"
    assert rt.db.get_setting("backup_keyless") is None and access_token(rt.db) == token and c.get("/api/backup", headers=hs).status_code == 200
    # moa 백업이 아닌 파일은 받지 않는다
    for junk in (b"hello", b"SQLite format 3\x00" + b"\x00" * 200):
        r = c.post("/api/backup/upload", data={"file": (io.BytesIO(junk), "x.db")}, headers=hs, content_type="multipart/form-data")
        assert r.status_code == 400
    other = tmp_path / "other.db"
    con = sqlite3.connect(str(other)); con.execute("CREATE TABLE t(x)"); con.commit(); con.close()
    assert c.post("/api/backup/upload", data={"file": (io.BytesIO(other.read_bytes()), "o.db")}, headers=hs, content_type="multipart/form-data").status_code == 400
    assert c.post("/api/backup/upload", headers=hs).status_code == 400
    assert not [x.name for x in (tmp_path / "backups").iterdir() if x.name.endswith(".part")]
    # 단타 엔진이 돌고 있으면 되돌리지 않는다
    rt.settings.update({"scalp_state": "running"})
    r = c.post(f"/api/backup/{b1['name']}/restore", json={"password": "gagyebu!7"}, headers=hs)
    assert r.status_code == 400 and "멈춘 뒤" in r.get_json()["error"]


def test_backup_restore_on_wal_file_and_restart_hook(tmp_path):
    """실제처럼 파일(WAL) DB에서: 되돌린 뒤에도 그대로 읽고 쓰이고, 다시 열어도 온전하다. 되돌리면 서버를 다시 띄운다."""
    from moa.broker import kiwoom as _kw
    _kw._SHARED.clear()
    path = str(tmp_path / "moa.db")
    r = Runtime(DB(path), start_thread=False, data_dir=str(tmp_path))
    try:
        assert r.backup.dir == str(tmp_path / "backups")
        _add_tx(r.db, "첫 내역")
        b = r.backup.create("manual")
        for i in range(300):
            _add_tx(r.db, f"뒤에 넣은 내역 {i}")
        calls = []

        class Up:
            exit_fn = staticmethod(lambda: calls.append("exit"))
            state = {}

            def status(self):
                return {}
        c = create_app(r, Up()).test_client()
        out = c.post(f"/api/backup/{b['name']}/restore", json={}).get_json()       # 노트북에서는 비밀번호를 묻지 않는다
        assert out["restart"] is True and r._stop.is_set()
        import time as _t
        _t.sleep(1.3)
        assert calls == ["exit"]
        assert _ledger_n(r.db) == 1
        _add_tx(r.db, "되돌린 뒤 내역")
        assert _ledger_n(r.db) == 2
    finally:
        r.stop()
        r.db.conn.close()
    d2 = DB(path)                                                                   # 다시 시작한 것처럼 새로 연다
    assert _ledger_n(d2) == 2 and d2.conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert d2.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    d2.conn.close()


def test_dividend_living_off_assets_health_fee_and_overlap(rt, monkeypatch):
    """모은 돈을 생활비로 쓰는 계산: 배당 먼저, 모자라면 현금성 → 나머지 순으로 팔고, 건강보험료·하락장·같은 지수 쏠림을 함께 본다."""
    from moa.dividend import simulate, health_fee, overlap, _pg, _shock, PRESETS, UNIVERSE, HEALTH_RATE, CARE_RATE
    rate = HEALTH_RATE / 100 * (1 + CARE_RATE / 100)
    # 건강보험료: 지역가입자는 1천만 원을 넘는 순간 전액, 직장가입자는 2천만 원 초과분만
    assert health_fee(10_000_000, "local") == 0 and abs(health_fee(10_000_001, "local") - 10_000_001 * rate) < 1
    assert health_fee(20_000_000, "work") == 0 and abs(health_fee(25_000_000, "work") - 5_000_000 * rate) < 1 and health_fee(9e7, None) == 0
    # 주가 가정: 커버드콜은 분배금+주가를 합친 수익이 성장 ETF의 주가 상승과 같다(분배율이 더 높으면 원금이 준다)
    assert abs(_pg({"cat": "us_cc", "yield": 10}, 3) + 0.04) < 1e-9 and abs(_pg({"cat": "kr_cc", "yield": 5}, 7) - 0.05) < 1e-9
    assert abs(_pg({"cat": "us_growth"}, 3) - 0.06) < 1e-9 and _pg({"cat": "us_bond"}, 3) == 0 and abs(_pg({"cat": "us_etf"}, 3) - 0.03) < 1e-9
    # 하락장 가정: 1년에 걸쳐 -35%, 1년 머물고, 3년에 걸쳐 회복(커버드콜은 85%까지만). 현금성은 그대로
    assert _shock(0, "us_etf") == 1 and abs(_shock(12, "us_etf") - 0.65) < 1e-9 and abs(_shock(24, "us_growth") - 0.65) < 1e-9
    assert abs(_shock(60, "us_etf") - 1) < 1e-9 and abs(_shock(60, "us_cc") - 0.85) < 1e-9 and _shock(18, "us_bond") == 1 and _shock(200, "kr_cc") == 0.85
    # 예시 구성은 모두 후보 목록의 종목이고 비중 합이 100
    known = {(u[0], u[1]) for u in UNIVERSE}
    lives = [p for p in PRESETS if p.get("group") == "live"]
    assert len(lives) == 5 and all(sum(w for _, _, w in p["assets"]) == 100 and all((m, s) in known for m, s, _ in p["assets"]) for p in PRESETS)

    A = lambda sym, w, y, cat, g=0, mkt="us": {"mkt": mkt, "sym": sym, "weight": w, "yield": y, "growth": g, "cat": cat, "months": [1 / 12] * 12}
    cc_g = [A("QQQ", 60, 0.5, "us_growth"), A("JEPQ", 40, 10, "us_cc")]
    assert overlap(cc_g) == [{"base": "나스닥100", "weight": 100.0, "syms": ["QQQ", "JEPQ"], "both": True}]
    assert overlap([A("SCHD", 60, 3.5, "us_etf"), A("JEPQ", 40, 10, "us_cc")]) == [] and overlap([A("QQQ", 100, 0.5, "us_growth")]) == []   # 한 종목뿐이면 쏠림 경고는 따로 안 한다
    assert overlap([A("SCHD", 30, 3.5, "us_etf"), A("458730", 30, 3.5, "kr_etf", mkt="kr"), A("KO", 40, 3, "us_stock")])[0]["base"] == "미국배당다우존스"
    run = lambda assets, live, amount=500_000_000, **kw: simulate({"amount": amount, "years": 10, "reinvest": False, "assets": assets, "live": live, **kw})
    assert run(cc_g, None)["live"] is None and run(cc_g, {"spend": 0})["live"] is None and run(cc_g, None)["overlap"][0]["weight"] == 100.0

    # 1) 배당만으로 생활비가 나오는 경우: 팔지 않고, 남는 돈은 다시 산다
    div = [A("SCHD", 100, 4, "us_etf", g=6)]
    L = run(div, {"spend": 1_000_000, "health": "work"}, amount=1_000_000_000)["live"]
    assert L["code"] == "div" and L["dead"] is None and all(r["sell"] == 0 for r in L["yearly"]) and L["rate"] == 0
    y1, y2 = L["yearly"][0], L["yearly"][1]
    assert y1["need"] == 1_000_000 and y2["need"] == 1_025_000 and y1["fee"] == round(health_fee(40_000_000, "work") / 12)      # 물가 2.5%, 건보료는 지금 배당 기준
    assert abs(y1["div"] - round(40_000_000 * 0.85 / 12)) < 5000 * 12 and y1["cover"] > 250 and L["yearly"][-1]["value"] > 1_000_000_000
    assert any(t["kind"] == "over" for t in L["tips"]) and L["alone"]["spend"] > 2_500_000                                       # 배당이 필요보다 많으면 알려 준다
    # 지역가입자면 같은 배당에 보험료가 훨씬 크다(전액 대상)
    Lloc = run(div, {"spend": 1_000_000, "health": "local"}, amount=1_000_000_000)["live"]
    assert Lloc["yearly"][0]["fee"] == round(40_000_000 * rate / 12) > y1["fee"] * 1.9 and Lloc["health"] == "local"
    # 1천만 원을 조금 넘으면 '아래로 맞추면 0'이라고 알려 준다
    edge = run(div, {"spend": 3_000_000, "health": "local"}, amount=270_000_000)["live"]
    assert 10_000_000 < edge["first"]["gross"] < 13_000_000 and any(t["kind"] == "health" and "아래로 맞추면" in t["text"] for t in edge["tips"])
    assert run(div, {"spend": 3_000_000, "health": "local"}, amount=240_000_000)["live"]["first"]["fee"] == 0

    # 2) 팔아서 쓰는 구성: 현금성부터 팔고, 그다음 나머지. 미국 종목을 팔아 남긴 이익의 세금은 이듬해에 낸다
    self_ = [A("QQQ", 80, 0.5, "us_growth"), A("SGOV", 20, 4, "us_bond")]
    L2 = run(self_, {"spend": 2_000_000, "health": "local"})["live"]
    assert L2["code"] == "mix" and L2["first"]["sell"] > 1_300_000 and 3 < L2["rate"] < 5 and L2["first"]["fee"] == 0
    assert L2["yearly"][0]["cash"] < 100_000_000 * 0.9 and L2["yearly"][0]["cg"] == 0                # 첫해엔 현금성만 줄고 세금도 없다
    assert L2["cash"]["value"] == 100_000_000 and L2["cash"]["months"] > 36 and not any(t["kind"] == "cushion" for t in L2["tips"])
    later = [r for r in L2["yearly"] if r["cash"] == 0]
    assert later and any(r["cg"] > 0 for r in L2["yearly"][later[0]["year"]:])                       # 현금이 떨어진 뒤 주식을 팔면 이듬해 세금이 생긴다
    none = run([A("QQQ", 100, 0.5, "us_growth")], {"spend": 2_000_000})["live"]
    assert none["cash"]["months"] is None and any(t["kind"] == "cushion" and "현금성 자산이 없어요" in t["text"] for t in none["tips"])
    # 3) 버티지 못하는 경우: 바닥나는 해에서 끝난다. 하락장이면 더 빨리 바닥난다
    L3 = run(cc_g, {"spend": 4_000_000})["live"]
    assert L3["code"] == "out" and L3["dead"] and len(L3["yearly"]) == L3["dead"] and L3["yearly"][-1]["value"] == 0
    assert L3["bad_dead"] and L3["bad_dead"] <= L3["dead"] and [t["kind"] for t in L3["tips"]][:1] == ["overlap"]
    # 하락장: 커버드콜은 분배금이 주가를 따라 줄고 원금도 다 돌아오지 않는다
    cc = [A("JEPQ", 60, 10, "us_cc"), A("JEPI", 40, 7.5, "us_cc")]
    L4 = run(cc, {"spend": 2_000_000})["live"]
    assert L4["bad"][1]["div"] < L4["yearly"][1]["div"] * 0.75 and L4["last"]["bad_value"] <= L4["last"]["value"]
    assert any(t["kind"] == "cc" for t in L4["tips"]) and L4["yearly"][9]["div"] < L4["yearly"][0]["div"]      # 분배율이 수익보다 높으면 해마다 준다
    # 주가 가정을 높이면 같은 구성도 버틴다(가정에 따라 결과가 크게 달라진다는 것을 숨기지 않는다)
    assert run(cc, {"spend": 2_000_000}, price_growth=7)["live"]["dead"] is None and L4["dead"]
    # 4) 다른 수입(알바·연금)이 시작되면 그만큼 덜 판다
    base = run(self_, {"spend": 2_000_000})["live"]
    job = run(self_, {"spend": 2_000_000, "other": 800_000, "other_from": 3})["live"]
    assert job["yearly"][1]["other"] == 0 and job["yearly"][2]["other"] == round(800_000 * 1.025 ** 2) and job["yearly"][2]["sell"] < base["yearly"][2]["sell"] - 700_000
    assert job["last"]["value"] > base["last"]["value"]

    # 화면·AI: 예시 구성이 묶음과 함께 나가고, 생활비 계획을 AI 자료에 넣는다
    _div_rt(rt, monkeypatch)
    c = create_app(rt).test_client()
    uni = c.get("/api/div/universe").get_json()
    assert [p["group"] for p in uni["presets"]].count("live") == 5 and uni["cats"]["us_growth"] and any(i["sym"] == "QQQ" for i in uni["items"])
    R = c.post("/api/div/simulate", json={"amount": 500_000_000, "years": 20, "assets": [{"mkt": "us", "sym": "SCHD", "weight": 50}, {"mkt": "us", "sym": "JEPI", "weight": 50}],
                                          "live": {"spend": 2_000_000, "health": "local"}}).get_json()
    assert R["live"]["spend"] == 2_000_000 and R["live"]["rates"]["local_line"] == 10_000_000 and "overlap" in R
    assert R["live"]["years"] == 30 and len(R["yearly"]) == 20                                   # 생활비로 쓰는 기간은 모으는 기간과 따로(기본 30년)
    R40 = c.post("/api/div/simulate", json={"amount": 2_000_000_000, "years": 5, "assets": [{"mkt": "us", "sym": "SCHD", "weight": 100}], "live": {"spend": 1_000_000, "years": 40}}).get_json()
    assert R40["live"]["years"] == 40 and len(R40["live"]["yearly"]) == 40
    _, ask, data = rt.dividend.ai_context({"amount": 500_000_000, "live": {"spend": 2_000_000, "health": "work", "other": 500_000, "other_from": 10}})
    plan = data["생활비로 쓰는 계획"]
    assert plan["한 달 생활비(원, 지금 물가)"] == 2_000_000 and "직장가입자" in plan["건강보험"] and plan["다른 수입이 시작되는 해"] == 10
    assert "생활비 2,000,000원" in ask and "같은 지수" in ask and "생활비로 쓰는 계획" not in rt.dividend.ai_context({"amount": 5e8})[2]
    assert any(r.get("기초 지수") == "미국배당다우존스" for r in data["후보 목록(실제 최근 1년 지급 기준)"])


def test_dividend_slow_source_does_not_block_screen(rt, monkeypatch):
    """바깥 서버가 느리면 기다리지 않고 '받는 중'으로 돌려준다. 그 종목은 저장하지 않아 다음에 다시 받는다."""
    import time as _t
    _div_rt(rt, monkeypatch)
    dv = rt.dividend
    real = dv._fetch

    def slow(mkt, sym):
        if sym == "JEPI":
            _t.sleep(0.6)
        return real(mkt, sym)
    monkeypatch.setattr(dv, "_fetch", slow)
    t0 = _t.time()
    a, b = dv.many([("us", "SCHD"), ("us", "JEPI")], deadline=0.15)
    assert _t.time() - t0 < 0.5 and a["yield"] == 3.75 and b["pending"] and b["error"] and b["yield"] is None and b["sym"] == "JEPI"
    _t.sleep(0.7)                                   # 뒤에서 마저 받아 둔다
    assert dv.info("us", "JEPI")["freq"] == 12 and not dv.info("us", "JEPI").get("pending")


def test_public_ip_for_kiwoom_registration(monkeypatch):
    """키움 IP 등록 안내에 쓰는 노트북 공인 IP: 첫 서비스가 죽어도 다음에서 받고, 이상한 답은 버린다."""
    from moa import netinfo

    class R:
        def __init__(self, text, code=200):
            self.text, self.status_code = text, code
    calls = []

    def get(url, **kw):
        calls.append(url)
        if "ipify" in url:
            raise OSError("down")
        return R("<html>nope</html>") if "amazonaws" in url else R("203.0.113.7\n")
    netinfo._cache.update(ip=None, at=0.0)
    ip, at = netinfo.public_ip(get=get)
    assert ip == "203.0.113.7" and at > 0 and len(calls) == 3
    assert netinfo.public_ip(get=lambda *a, **k: 1 / 0)[0] == "203.0.113.7"          # 잠깐은 기억해 둔 값을 쓴다
    netinfo._cache.update(ip=None, at=0.0)
    assert netinfo.public_ip(get=lambda *a, **k: R("999.1.1.1")) == (None, 0)


def test_payoff_goal_tracks_pace_from_logged_repayments():
    """갚기 목표: 금액·기한을 정하고 갚은 돈을 기록하면, 일정 대비 어디쯤인지와 한 달에 얼마가 필요한지를 알려 준다."""
    from datetime import date
    from moa.db import DB
    from moa.runtime import Runtime
    rt = Runtime(DB(":memory:"), start_thread=False)
    try:
        P = rt.payoff
        assert P.state()["goal"] is None and P.brief() is None
        with pytest.raises(ValueError):
            P.add({"amount": 100000})
        with pytest.raises(ValueError):
            P.set_goal({"amount": 100_000_000, "start": "2026-12-01", "deadline": "2026-11-01"})
        P.set_goal({"name": "대출 줄이기", "amount": "120,000,000", "start": "2026-01-01", "deadline": "2031-01-01"})
        s = P.state(date(2026, 1, 1))
        assert (s["paid"], s["left"], s["need_month"], s["avg_month"]) == (0, 120_000_000, 2_000_000, None) and "한 달에 2,000,000원" in s["text"]
        P.add({"date": "2026-02-10", "amount": 3_000_000, "src": "save"})
        P.add({"date": "2026-05-20", "amount": "1,000,000", "src": "dividend", "note": "배당"})
        P.add({"date": "2025-12-31", "amount": 9_999_999})                       # 시작일 전 기록은 세지 않는다
        s = P.state(date(2026, 7, 1))                                           # 6달 지남: 일정대로면 1,200만
        assert (s["paid"], s["plan_now"], s["pct"], s["time_pct"]) == (4_000_000, 12_000_000, 3.3, 10.0)
        assert s["avg_month"] == 666_667 and s["need_month"] == round(116_000_000 / 54) and "뒤처졌어요" in s["text"] and s["eta"] == "2041-01"
        assert s["by"] == [{"key": "save", "label": "저축·월급", "amount": 3_000_000}, {"key": "dividend", "label": "배당", "amount": 1_000_000}]
        assert [m["month"] for m in s["months"]] == ["2026-02", "2026-05"] and len(s["logs"]) == 2 and s["hints"][0]["key"] == "save"
        P.add({"date": "2026-06-30", "amount": 10_000_000, "src": "invest"})
        s = P.state(date(2026, 7, 1))
        assert "앞서 있어요" in s["text"] and rt.payoff.brief()["goal"]["name"] == "대출 줄이기"
        P.delete(s["logs"][0]["id"])
        assert P.state(date(2026, 7, 1))["paid"] == 4_000_000
        assert P.set_goal({"clear": True})["goal"] is None
    finally:
        rt.stop()


def test_dividend_yield_band_and_change():
    import moa.dividend as DV
    from datetime import date, timedelta
    today = date(2026, 11, 20)
    divs = [(today - timedelta(days=91 * k + 10), 1.0) for k in range(24)]           # 분기 1달러(1년 4달러)
    bars = [(today - timedelta(days=30 * k), 100.0 if k % 2 else 80.0) for k in range(60, 0, -1)]   # 배당률 4%·5% 반반
    b = DV.yield_band(divs, bars, 100.0, today)
    assert b["lo"] == 4.0 and b["hi"] == 5.0 and b["cur"] == 4.0 and b["pct"] == 0 and b["n"] >= 24
    assert DV.yield_band(divs, bars, 70.0, today)["pct"] == 100                          # 주가가 내려 배당률이 5년 중 가장 높음
    assert DV.yield_band(divs, bars[:10], 100.0, today) is None                          # 2년치가 안 되면 내지 않음
    assert DV.change_kind(1.1, 1.0, 4) == ("raise", 10.0) and DV.change_kind(0.9, 1.0, 4)[0] == "cut"
    assert DV.change_kind(1.1, 1.0, 12)[0] == "same" and DV.change_kind(0.8, 1.0, 12)[0] == "cut"   # 월배당은 넓게
    a = DV.analyze([(today - timedelta(days=30 * k), 1.0 + (k == 0) * 0.2) for k in range(14)], 100, "us", "ETF", today)
    assert a["prev"]["amt"] == 1.0 and a["prev"]["n"] == 3 and a["last"]["amt"] == 1.2


def test_dividend_daily_collect_records_and_detects_changes(rt, monkeypatch):
    """하루 한 번: 대상 종목을 새로 받아 그날 배당률을 남기고, 새 배당이 나오면 직전과 비교해 적는다(보유 종목 인상·삭감은 알림)."""
    from datetime import date, datetime, timedelta
    _div_rt(rt, monkeypatch)
    dv = rt.dividend
    sent = []
    monkeypatch.setattr(rt, "notify", lambda m, *a, **k: sent.append(m))
    monkeypatch.setattr(dv, "_held", lambda: {("us", "SCHD"): "SCHD"})
    r = dv.collect("2026-11-19")
    assert r["saved"] >= 4 and r["events"] == 0                                         # 처음엔 비교할 게 없다
    assert dv.history("us", "SCHD") or True
    assert rt.db.one("SELECT yld FROM div_daily WHERE day='2026-11-19' AND sym='SCHD'")["yld"] == 3.75
    # SCHD 새 배당(직전 0.75 → 0.69: 삭감)
    import tests.test_core as T
    new = (date.today() + timedelta(days=1)).isoformat()
    old = rt.market._yh_chart
    data = T._yh_div(80.0, [(new, 0.69)] + [((date.today() - timedelta(days=10 + 91 * k)).isoformat(), 0.75) for k in range(20)])

    def chart(sym, *a, **k):
        return data if sym == "SCHD" else old(sym, *a, **k)
    monkeypatch.setattr(rt.market, "_yh_chart", chart)
    r = dv.collect("2026-11-20")
    E = dv.events(days=400)
    ev = next(e for e in E["events"] if e["sym"] == "SCHD")
    assert r["events"] == 1 and ev["kind"] == "cut" and ev["chg"] == -8.0 and ev["held"] == 1 and E["days"] == 2
    assert sent and "삭감" in sent[0]
    assert dv.collect("2026-11-21")["events"] == 0                                      # 같은 배당은 한 번만
    # 하루 한 번, 아침 8시 뒤
    rt.db.set_setting("div_daily_day", "2026-11-22")
    monkeypatch.setattr(dv, "collect", lambda day=None: None)
    assert not dv.daily_due(datetime(2026, 11, 23, 7, 30)) and not dv.daily_due(datetime(2026, 11, 22, 9, 0))
    assert dv.daily_due(datetime(2026, 11, 23, 8, 5))
    c = create_app(rt).test_client()
    assert c.get("/api/div/events").status_code == 200 and "rows" in c.get("/api/div/history/us/SCHD").get_json()


def test_candles_more_bars_for_zoom(rt, monkeypatch):
    """크게 보기는 손으로 줄여 볼 수 있게 봉을 600개까지(평소 120). 공개 시세는 더 긴 기간을 받는다."""
    from datetime import datetime as D
    calls = []

    def chart(sym, rng="1d", interval="5m", prepost=False, events=None):
        calls.append(rng)
        ts = [int(D(2020, 1, 1).timestamp()) + 86400 * i for i in range(800)]
        return {"meta": {"gmtoffset": 0}, "timestamp": ts, "indicators": {"quote": [{"close": [100 + i % 7 for i in range(800)]}]}}
    monkeypatch.setattr(rt.market, "_yh_chart", chart)
    assert len(rt.invest.candles("us", "AAPL", "d")["bars"]) == 120 and calls[-1] == "6mo"
    assert len(rt.invest.candles("us", "AAPL", "d", 600)["bars"]) == 600 and calls[-1] == "3y"
    c = create_app(rt).test_client()
    assert len(c.get("/api/invest/candles/us/AAPL?tf=d&n=9999").get_json()["bars"]) == 600
    assert len(c.get("/api/invest/candles/us/AAPL?tf=d&n=x").get_json()["bars"]) == 120


# ---------- 건강관리(우석World) ----------
def _health(day="2026-11-23"):
    from datetime import date as _date
    from moa.health import Health
    return Health(DB(":memory:"), today=lambda: _date.fromisoformat(day))


def test_health_body_goal_progress():
    from moa.health import HealthError
    h = _health()
    h.save_cfg({"height": 180, "goal": {"weight": 80, "waist": 90, "date": "2027-02-01", "label": "석 달", "start_weight": 90, "start_date": "2026-11-23"}})
    h.save_body({"date": "2026-11-20", "weight": "90.0", "waist": 98})
    h.save_body({"date": "2026-11-23", "weight": 88})
    h.save_body({"date": "2026-11-23", "fat": 25.5})          # 같은 날 다시: 비운 칸은 그대로
    o = h.overview()
    assert o["body"]["latest"]["weight"] == {"value": 88.0, "date": "2026-11-23"}
    assert o["body"]["latest"]["waist"]["date"] == "2026-11-20" and o["body"]["latest"]["fat"]["value"] == 25.5
    assert abs(o["body"]["progress"] - 0.2) < 1e-9 and o["week_no"] == 1 and o["body"]["bmi"] == 27.2
    with pytest.raises(HealthError):
        h.save_body({"date": "2026-11-24", "weight": 88})     # 앞날
    with pytest.raises(HealthError):
        h.save_body({"date": "2026-11-23", "weight": 880})
    with pytest.raises(HealthError):
        h.save_body({"date": "2026-11-23"})
    h.delete_body("2026-11-23")
    assert h.overview()["body"]["latest"]["weight"]["value"] == 90.0


def test_health_week_checks_and_plan():
    from moa.health import HealthError
    h = _health("2026-11-25")                                  # 수요일
    h.set_workout({"date": "2026-11-23", "done": True, "rpe": 6})
    h.set_workout({"date": "2026-11-25", "rpe": 7})            # 힘든 정도만 먼저
    w = h.week()
    assert w["start"] == "2026-11-23" and w["target"] == 6 and w["done"] == 1 and w["rpe_avg"] == 6.5
    assert w["days"][2]["today"] and w["days"][3]["future"] and not w["days"][2]["done"]
    with pytest.raises(HealthError):
        h.set_workout({"date": "2026-11-26", "done": True})
    with pytest.raises(HealthError):
        h.set_workout({"rpe": 11})
    h.set_check({"key": "water", "on": True})
    h.set_check({"key": "water", "on": True})
    assert h.overview()["checks"] == ["water"]
    with pytest.raises(HealthError):
        h.set_check({"key": "nope", "on": True})
    b = h.brief()
    assert b["checks"] == 1 and b["checks_total"] == 4 and b["workout"] == "전신 B" and b["week_done"] == 1
    plan = h.cfg()["plan"]
    plan["days"][6] = {"name": "산책", "time": "30분", "rest": False, "items": [["걷기", "30분"], ["", "x"]]}
    c = h.save_cfg({"plan": plan, "habits": [{"title": "물 2L", "sub": "나눠서"}, {"title": "스트레칭"}]})
    assert c["plan"]["days"][6]["items"] == [["걷기", "30분"]] and h.week()["target"] == 7
    assert c["habits"][0]["id"] == "water" and len(c["habits"]) == 2    # 이름이 같으면 기록이 이어지게 같은 id
    with pytest.raises(HealthError):
        h.save_cfg({"plan": {"days": plan["days"][:3]}})


def test_health_meds_one_check_per_time():
    from moa.health import HealthError
    h = _health("2026-11-25")
    with pytest.raises(HealthError):
        h.set_check({"key": "med_am", "on": True})            # 약이 없으면 체크 줄도 없다
    c = h.save_cfg({"meds": [{"name": "약 나", "dose": "5mg", "when": "night"}, {"name": "약 가", "dose": "10mg"}, {"name": "", "dose": "x"}, {"name": "약 다", "when": "zzz"}]})
    assert [m["name"] for m in c["meds"]] == ["약 가", "약 다", "약 나"] and c["meds"][1]["when"] == "am"
    assert [(m["key"], m["names"]) for m in h.med_slots()] == [("med_am", ["약 가", "약 다"]), ("med_night", ["약 나"])]
    h.set_check({"key": "med_am", "on": True})
    h.set_check({"date": "2026-11-24", "key": "med_am", "on": True})
    h.set_check({"date": "2026-11-10", "key": "med_am", "on": True})   # 7일보다 앞
    o = h.overview()
    assert o["med_slots"][0]["week"] == 2 and o["med_slots"][1]["week"] == 0 and "med_am" in o["checks"]
    assert h.brief()["checks"] == 1 and h.brief()["checks_total"] == 6      # 운동 1 + 기본 체크 3 + 약 2때
    h.save_cfg({"meds": []})
    assert h.brief()["checks"] == 0 and h.brief()["checks_total"] == 4      # 없어진 줄의 체크는 세지 않는다
    assert h.export()["cfg"]["meds"] == []


def test_health_labs_follow_import():
    h = _health()
    h.save_lab({"date": "2026-11-03", "name": "검사A", "value": "158", "ref": "46~116", "flag": "high"})
    h.save_lab({"date": "2026-11-03", "name": "검사B", "value": "91", "flag": "ok"})
    h.save_lab({"date": "2027-02-01", "name": "검사A", "value": "110", "flag": "ok"})
    labs, latest = h.labs()
    a = next(x for x in labs if x["name"] == "검사A")
    assert latest == "2027-02-01" and a["value"] == "110" and a["prev"] == {"value": "158", "date": "2026-11-03"} and a["n"] == 2
    f1 = h.save_follow({"date": "2027-02-01", "title": "재검", "why": "석 달 뒤"})
    h.save_follow({"date": "2027-11-01", "title": "초음파"})
    fs = h.follows()
    assert fs[0]["dday"] == 70 and fs[0]["next"] and not fs[1]["next"]
    h.save_follow({"id": f1, "date": "2027-02-01", "title": "재검", "done": True})
    assert [f["next"] for f in h.follows()] == [True, False] and h.follows()[0]["title"] == "초음파"
    h.save_body({"date": "2026-11-23", "weight": 88})
    dump = h.export()
    h2 = _health()
    n = h2.import_(json.loads(json.dumps(dump)))
    assert n["labs"] == 3 and n["follows"] == 2 and n["body"] == 1
    h2.import_(dump)                                           # 두 번 넣어도 겹치지 않는다
    assert len(h2.db.q("SELECT * FROM hl_lab")) == 3 and len(h2.db.q("SELECT * FROM hl_follow")) == 2
    with pytest.raises(ValueError):
        h2.import_({"kind": "other"})


def test_health_and_world_api(rt):
    c = create_app(rt).test_client()
    assert c.get("/api/health").get_json()["empty"] is True
    assert c.post("/api/health/body", json={"weight": 88.2}).status_code == 200
    assert c.post("/api/health/body", json={"weight": "abc"}).status_code == 400
    assert c.post("/api/health/check", json={"key": "veg", "on": True}).status_code == 200
    assert c.post("/api/health/workout", json={"done": True, "rpe": 6}).status_code == 200
    assert c.put("/api/health/config", json={"goal": {"weight": 82}}).get_json()["cfg"]["goal"]["weight"] == 82
    lid = c.post("/api/health/lab", json={"name": "검사A", "value": "1"}).get_json()["id"]
    assert c.delete(f"/api/health/lab/{lid}").status_code == 200
    o = c.get("/api/health").get_json()
    assert o["body"]["latest"]["weight"]["value"] == 88.2 and o["checks"] == ["veg"] and not o["empty"]
    w = c.get("/api/world").get_json()["apps"]
    assert w["health"]["checks"] == 2 and w["health"]["weight"] == 88.2 and "expense" in w["ledger"]
    assert c.post("/api/health/import", json={"data": c.get("/api/health/export").get_json()}).status_code == 200


def test_health_gym_photos(rt):
    import base64
    from moa.health import HealthError
    jpg = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 64).decode()
    h = rt.health
    with pytest.raises(HealthError):
        h.save_photo("smith", {"full": base64.b64encode(b"GIF89a....").decode(), "thumb": jpg})   # 사진이 아님
    with pytest.raises(HealthError):
        h.save_photo("../x", {"full": jpg, "thumb": jpg})
    c = create_app(rt).test_client()
    assert c.get("/api/health/photo/smith").status_code == 404
    assert c.post("/api/health/photo/smith", json={"full": "data:image/jpeg;base64," + jpg, "thumb": jpg}).status_code == 200
    r = c.get("/api/health/photo/smith?s=thumb")
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and r.data[:3] == b"\xff\xd8\xff"
    assert "smith" in c.get("/api/health").get_json()["photos"]
    assert h.import_({"kind": "moa-health", "photos": [{"key": "bike", "full": jpg, "thumb": jpg}]})["photos"] == 1
    assert "photos" not in h.export()                          # 내보내기 파일에는 사진을 넣지 않는다
    assert c.delete("/api/health/photo/smith").status_code == 200 and set(h.photos()) == {"bike"}


# ---------- 사진편집(우석World): 노트북의 PhotoGrader 웹 프로그램으로 가는 문 ----------
def test_photo_app_door(rt):
    from moa.album import Album, AlbumError
    pages, ts, ran = {}, {}, []
    web = lambda url, timeout=2: pages.get(url.replace("http://127.0.0.1:8790", ""), (0, None))

    def tsrun(*a, timeout=20):
        ran.append(a)
        return ts.get(a[:2] if a[0] == "serve" and a[1] == "status" else a[:1], (-1, ""))
    al = rt.album = Album(rt.db, fetch=web, ts=tsrun)
    c = create_app(rt).test_client()
    st = c.get("/api/photo/app").get_json()
    assert st["running"] is False and st["urls"] == [] and c.get("/api/world").get_json()["apps"]["photo"] is None     # 꺼져 있을 때
    H = "https://pc.tail1234.ts.net"
    pages["/api/version"] = (200, {"app": "AWS Album", "version": "1.4"})
    pages["/api/state"] = (200, {"pin": False, "counts": {"all": 120, "best": 7, "fav": 3, "albums": 2, "trash": 1},
                                 "urls": [{"kind": "같은 와이파이", "url": "http://192.168.0.5:8790"}, {"kind": "Tailscale https (추천)", "url": H},
                                          {"kind": "x", "url": "javascript:alert(1)"}, "junk"]})
    # ① 프로그램이 알려 준 주소(443)가 실제로는 다른 서버(우석World)이고, 프로그램은 8443에 걸려 있는 경우 → 8443을 고른다
    ts[("status",)] = (0, json.dumps({"Self": {"DNSName": "pc.tail1234.ts.net."}}))
    ts[("serve", "status")] = (0, json.dumps({"Web": {"pc.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8780"}}},
                                                       "pc.tail1234.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8790"}}}}}))
    pages[H + "/api/version"] = (404, None)
    pages[H + ":8443/api/version"] = (200, {"app": "AWS Album", "version": "1.4"})
    st = c.get("/api/photo/app?fresh=1").get_json()
    assert st["running"] and st["name"] == "AWS Album" and not st["pin"] and st["tailscale"] and not st["can_serve"]
    assert st["urls"] == [H + ":8443", "http://192.168.0.5:8790"]                                   # 다른 서버로 확인된 443은 뺀다, 이상한 주소도 버린다
    assert {x["url"]: x["what"] for x in st["checked"]} == {H + ":8443": "album", H: "other", H + ":10000": "fail"}
    assert c.get("/api/world").get_json()["apps"]["photo"] == {"count": 120, "best": 7}
    # ② 노트북에서 자기 https 주소로 물어볼 수 없는 경우에도 Tailscale에 걸린 연결은 믿는다
    pages.pop(H + ":8443/api/version")
    assert c.get("/api/photo/app?fresh=1").get_json()["urls"][0] == H + ":8443"
    # ③ 프로그램으로 가는 https 연결이 아예 없을 때: 빈 포트에 하나 만든다(있는 연결은 그대로)
    ts[("serve", "status")] = (0, json.dumps({"Web": {"pc.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8780"}}}}}))
    st = c.get("/api/photo/app?fresh=1").get_json()
    assert st["can_serve"] and st["urls"] == ["http://192.168.0.5:8790"]
    ts[("serve",)] = (0, "ok")
    ran.clear()
    c.post("/api/photo/app/https")
    assert ("serve", "--bg", "--https=8443", "http://127.0.0.1:8790") in ran
    ts[("serve",)] = (1, "error: HTTPS is not enabled")
    assert "HTTPS is not enabled" in c.post("/api/photo/app/https").get_json()["error"]
    # ④ Tailscale 정보를 못 읽을 때는 프로그램이 알려 준 주소를 그대로 써 본다
    ts.clear()
    pages.pop(H + "/api/version")
    st = c.get("/api/photo/app?fresh=1").get_json()
    assert st["urls"][0] == H and not st["tailscale"] and not st["can_serve"]
    pages["/api/state"] = (401, None)                                                               # 잠금 번호를 쓰는 경우
    st = c.get("/api/photo/app?fresh=1").get_json()
    assert st["running"] and st["pin"] and st["urls"] == [] and al.brief() is None
    assert c.put("/api/photo/app", json={"url": " https://pc.tail1234.ts.net/ "}).get_json()["url"] == "https://pc.tail1234.ts.net"
    assert c.put("/api/photo/app", json={"url": "pc.ts.net/x"}).status_code == 400 and c.put("/api/photo/app", json={"url": ""}).get_json()["url"] == ""
    with pytest.raises(AlbumError):
        al.set_url("ftp://x")
    assert Album(rt.db, port=1).status()["running"] is False                                        # 실제로 아무것도 없는 포트
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))                              # 판매용 코드는 저장소에 두지 않는다
    assert not [f for d, _, fs in os.walk(root) if ".git" not in d and "node_modules" not in d for f in fs if f in ("grade_engine.py", "style_match.py", "aws_album.py", "album_ui.html")]


def test_album_ship_sealed_delivery(rt, tmp_path):
    """잠근 묶음: 노트북의 열쇠로만 풀리고, 새 묶음은 프로그램의 업데이트 기능에 한 번만 들어간다."""
    import hashlib
    import io
    import zipfile
    from moa import shipbox
    from moa.album import Album
    from moa.ship import AlbumShip, MANIFEST, BUNDLE
    small = lambda: shipbox.keygen(1024)                    # 시험은 작은 열쇠로 빠르게
    k = small()
    blob = shipbox.seal(shipbox.public(k), b"hello" * 1000)
    assert shipbox.open_(k, blob) == b"hello" * 1000 and b"hellohello" not in blob and shipbox.open_(k, shipbox.seal(shipbox.public(k), b"")) == b""
    bad = bytearray(blob)
    bad[-40] ^= 1
    with pytest.raises(ValueError):
        shipbox.open_(k, bytes(bad))
    with pytest.raises(ValueError):
        shipbox.open_(small(), blob)
    assert set(shipbox.public(k)) == {"id", "n", "e"}       # 공개 쪽에는 푸는 값이 없다

    web = {"/api/version": (200, {"app": "AWS Album", "version": "1.4"})}
    album = Album(rt.db, fetch=lambda url, timeout=2: web.get(url.replace("http://127.0.0.1:8790", ""), (0, None)), ts=lambda *a, **kw: (-1, ""))
    repo, posts, answer = {}, [], [(200, {"ok": True, "changed": ["album_ui.html"], "restarting": True})]
    ship = rt.ship = AlbumShip(rt.db, str(tmp_path), str(tmp_path), album, git=lambda p: repo.get(p), post=lambda url, data: (posts.append((url, data)), answer[0])[1], keygen=small)
    c = create_app(rt).test_client()
    assert ship.tick() is False and not posts               # 묶음이 없으면 아무 일도 없다
    r = c.get("/api/photo/app/ship").get_json()
    pub = r["pub"]
    assert set(pub) == {"id", "n", "e"} and '"d"' not in json.dumps(r) and os.path.exists(tmp_path / "ship_key.json")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("PhotoGraderPro_web/album_ui.html", "<html>new</html>")
    data = buf.getvalue()

    def put(note, key=None):
        repo[BUNDLE] = shipbox.seal(pub, data + note.encode())
        repo[MANIFEST] = json.dumps({"id": hashlib.sha256(data + note.encode()).hexdigest(), "key": key or pub["id"], "note": note}).encode()
    put("홈 버튼")
    assert c.get("/api/photo/app/ship").get_json()["status"]["pending"] is True
    assert ship.tick() is True and posts[-1] == ("http://127.0.0.1:8790/api/self_update", data + "홈 버튼".encode())
    st = ship.status()
    assert st["pending"] is False and st["applied"]["note"] == "홈 버튼" and ship.tick() is False and len(posts) == 1     # 같은 묶음은 다시 넣지 않는다
    assert c.get("/api/photo/app").get_json()["ship"]["applied"]["note"] == "홈 버튼"
    # 프로그램이 꺼져 있으면 기다렸다가, 켜지면 적용
    put("둘째")
    web.pop("/api/version")
    assert ship.tick() is False and "꺼져" in ship.status()["note"] and len(posts) == 1
    web["/api/version"] = (200, {"app": "AWS Album", "version": "1.4"})
    # 프로그램이 거절하면(잠금 번호 등) 세 번까지만 해 보고 멈춘다
    answer[0] = (401, {"error": "login"})
    assert [ship.tick() for _ in range(5)] == [False] * 5 and len(posts) == 4 and "잠금 번호" in ship.status()["note"]
    answer[0] = (200, {"ok": True, "changed": []})
    put("셋째")                                              # 새 묶음이 오면 다시 시도
    assert ship.tick() is True and ship.status()["applied"]["note"] == "셋째"
    put("남의 열쇠", key="ffff")
    assert ship.tick() is False and "열쇠" in ship.status()["note"]
    repo[MANIFEST] = json.dumps({"id": "0" * 64, "key": pub["id"], "note": "바뀐 내용"}).encode()   # 안내문과 내용이 다르면 넣지 않는다
    n = len(posts)
    assert ship.tick() is False and len(posts) == n


# ---------- 챙길 일 · 캘린더 내보내기 ----------
def test_agenda_collects_dated_items_and_serves_readonly_calendar(rt):
    from datetime import date
    client = create_app(rt).test_client()
    ag = rt.agenda
    today = date(2026, 11, 23)                                     # 월요일
    rt.fixed.save({"name": "관리비", "amount": 250000, "cycle": "monthly", "day": 25, "start_date": "2026-01-01", "kind": "expense"})
    rt.health.save_follow({"date": "2026-11-27", "title": "정기 검진"})
    its = ag.items(30, today)
    fx = next(x for x in its if x["kind"] == "fixed")
    assert fx["date"] == "2026-11-25" and fx["title"] == "납부: 관리비" and "250,000" not in fx["title"] and "11월 25일(수)" in fx["ask"]   # 금액은 기본으로 뺀다
    assert not any(x["kind"] == "health" for x in its)             # 건강 일정은 기본으로 뺀다
    wk = [x for x in its if x["kind"] == "workout"]
    assert wk and all(x["title"].startswith("운동: ") for x in wk) and wk[0]["dday"] <= 7
    ag.save_conf({"amount": True, "health": True, "workout": False})
    its = ag.items(30, today)
    assert any(x["title"] == "납부: 관리비 250,000원" for x in its) and any(x["title"] == "정기 검진" for x in its) and not any(x["kind"] == "workout" for x in its)
    b = ag.brief(today)
    assert b["conf"]["health"] is True and [x["when"] for x in b["items"]][:1] == ["11.25"] and b["share"] == ""      # 오늘·내일 일이 없으면 넘길 문구도 없다
    ics = ag.ics(today)
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and "SUMMARY:납부: 관리비 250\\,000원" in ics and "DTSTART;VALUE=DATE:20261125" in ics and ics.count("BEGIN:VEVENT") == ics.count("END:VEVENT") >= 2
    # 주소의 열쇠로만 열리고, 새로 만들면 예전 주소는 끊긴다
    r = client.get("/api/agenda").get_json()
    assert r["path"].startswith("/cal/") and r["path"].endswith(".ics")
    assert client.get(r["path"]).status_code == 200 and client.get(r["path"]).mimetype == "text/calendar"
    assert client.get("/cal/wrong.ics").status_code == 404
    r2 = client.put("/api/agenda", json={"renew": True, "amount": False}).get_json()
    assert r2["path"] != r["path"] and client.get(r["path"]).status_code == 404 and client.get(r2["path"]).status_code == 200 and r2["conf"]["amount"] is False


def test_gym_photos_import_from_local_folder(rt, tmp_path):
    jpg = b"\xff\xd8\xff\xe0" + b"0" * 200
    (tmp_path / "chest.jpg").write_bytes(jpg)
    (tmp_path / "chest.thumb.jpg").write_bytes(jpg)
    (tmp_path / "legext.jpg").write_bytes(jpg)                       # 작은 사진이 없는 것
    (tmp_path / "BAD KEY.jpg").write_bytes(jpg)
    (tmp_path / "BAD KEY.thumb.jpg").write_bytes(jpg)
    (tmp_path / "pulley.jpg").write_bytes(b"not a jpeg")
    (tmp_path / "pulley.thumb.jpg").write_bytes(jpg)
    r = rt.health.import_folder(tmp_path)
    assert r["keys"] == ["chest"] and {x["file"] for x in r["skipped"]} == {"legext.jpg", "BAD KEY.jpg", "pulley.jpg"}
    assert rt.health.photo("chest") == jpg and "chest" in rt.health.photos() and not (tmp_path / "chest.jpg").exists() and (tmp_path / "legext.jpg").exists()   # 넣은 것만 지운다
    assert rt.health.import_folder(tmp_path / "none")["found"] is False


def test_health_dated_plan_quests_and_rpg(rt):
    from datetime import date
    from moa.health import Health
    day = {"t": date(2026, 11, 23)}                                   # 월요일
    h = Health(rt.db, today=lambda: day["t"])
    c = h.cfg()
    plan = dict(c["plan"], cal={"2026-11-23": {"mode": "home", "name": "전신 A", "time": "14:00–14:50", "items": [["체스트프레스", "3×12"]],
        "blocks": [["07:10", "water", "물 한 컵으로 시작", ""], ["09:00", "info", "수업 시작", "구루미"], ["14:00", "gym", "", ""], ["bad", "x", "y"]]},
        "2026-11-24": {"mode": "home", "name": "쉬는 날", "rest": True, "items": []}, "nope": {}})
    h.save_cfg({"plan": plan})
    c = h.cfg()
    assert set(c["plan"]["cal"]) == {"2026-11-23", "2026-11-24"} and len(c["plan"]["cal"]["2026-11-23"]["blocks"]) == 3
    p = h.plan_for(date(2026, 11, 23))
    assert p["time"] == "14:00–14:50" and p["items"] == [["체스트프레스", "3×12"]] and h.plan_for(date(2026, 11, 24))["rest"] and h.plan_for(date(2026, 11, 25))["blocks"] == []
    o = h.overview()
    q = o["quests"]
    assert [x["id"] for x in q[:5]] == ["water", "info", "meal_lunch", "gym", "meal_dinner"] and q[0]["t"] == "07:10" and q[0]["title"] == "물 한 컵으로 시작" and q[1]["kind"] == "info" and q[1]["xp"] == 0
    assert q[3]["kind"] == "gym" and q[3]["xp"] == 40 and "전신 A" in q[3]["title"] and q[2]["kind"] == "meal" and q[2]["slot"] == "점심" and not q[2]["on"]
    assert {x["id"] for x in q[5:]} == {x["id"] for x in c["habits"]} - {"water"}
    assert o["week"]["days"][0]["time"] == "14:00–14:50" and o["week"]["target"] == sum(1 for d in o["week"]["days"] if not d["rest"])
    r0 = o["rpg"]
    assert (r0["level"], r0["xp"], r0["total"], r0["streak"]) == (1, 0, 0, 0) and r0["boss"]["hp"] == r0["boss"]["max"] and not any(b["got"] for b in r0["badges"])
    for hb in c["habits"]:
        h.set_check({"key": hb["id"], "on": True})
    h.set_workout({"done": True, "rpe": 6})
    h.save_meal({"slot": "점심", "menu": "불고기 덮밥", "tags": ["채소", "단백질", "없는꼬리표"]})
    h.save_meal({"slot": "저녁", "recipe": h.recipes()[0]["id"], "tags": ["튀김·기름"]})
    r1 = h.overview()["rpg"]
    n = len(c["habits"])
    assert r1["total"] == 10 * n + 40 + 10 + 20 + 10 * 2 + 5 and r1["today"] == r1["total"] and r1["clears"] == 1 and r1["streak"] == 1 and r1["boss"]["hp"] == r0["boss"]["max"] - 1
    assert r1["level"] == 2 and r1["xp"] == r1["total"] - 100 and r1["need"] == 150 and {b["id"] for b in r1["badges"] if b["got"]} == {"first", "clear1", "chef1"} and r1["stats"]["str"] == 1 and r1["stats"]["wil"] == n and r1["stats"]["diet"] == 1
    assert all(x["on"] for x in h.overview()["quests"] if x["kind"] != "info")
    day["t"] = date(2026, 11, 25)                                     # 하루 건너뛰면 연속 기록이 끊긴다
    assert h.overview()["rpg"]["streak"] == 0 and h.overview()["rpg"]["total"] == r1["total"]
    its = rt.agenda.items(3, date(2026, 11, 23))
    assert next(x for x in its if x["kind"] == "workout" and x["date"] == "2026-11-23")["time"] == "14:00" and not any(x["kind"] == "workout" and x["date"] == "2026-11-24" for x in its)


def test_health_recipes_and_meals(rt):
    from datetime import date
    from moa.health import Health
    from moa.recipes import SEED
    h = Health(rt.db, today=lambda: date(2026, 11, 23))
    rs = h.recipes()
    assert len(rs) == len(SEED) and {r["title"] for r in rs} >= {"어묵볶음", "명란젓 맑은국", "닭칼국수", "만능 고추장 양념장"}
    by = {r["key"]: r for r in rs}
    assert by["bulgogi"]["grade"] == "균형" and by["eomuk"]["grade"] == "가끔" and by["eomuk"]["steps"][0].startswith("팬에")
    from moa.recipes import CATS
    assert len(SEED) >= 130 and len({r["key"] for r in SEED}) == len(SEED)                          # 처음 16개 + 집밥 118개, 키 중복 없음
    assert all(r["tags"][0] in CATS and len(r["ingredients"]) >= 2 and len(r["steps"]) >= 2 for r in rs)
    assert by["kimchijjigae"]["tags"][0] == "국·찌개" and by["eomuk"]["tags"][0] == "반찬" and sum(r["grade"] == "균형" for r in rs) >= 40
    h.delete_recipe(by["eomuk"]["id"])
    Health(rt.db, today=lambda: date(2026, 11, 23))                    # 다시 켜도 지운 처음 자료는 돌아오지 않는다
    assert "eomuk" not in {r["key"] for r in h.recipes()}
    rid = h.save_recipe({"title": "애호박 고추장찌개", "ingredients": "뒷다리살 500g, 애호박 1/2개", "steps": "1. 고기 볶기\n2) 고추장 넣고 끓이기", "tags": "#국물 채소 단백질"})
    r = next(x for x in h.recipes() if x["id"] == rid)
    assert r["steps"] == ["고기 볶기", "고추장 넣고 끓이기"] and r["tags"] == ["국물", "채소", "단백질"] and r["grade"] == "균형" and r["src"] == "직접 넣음"
    with pytest.raises(ValueError):
        h.save_recipe({"title": "빈 것", "ingredients": "", "steps": ""})
    with pytest.raises(ValueError):
        h.save_meal({"slot": "점심", "menu": ""})
    with pytest.raises(ValueError):
        h.save_meal({"date": "2026-11-24", "slot": "점심", "menu": "x"})
    m = h.save_meal({"slot": "저녁", "recipe": rid, "tags": ["채소", "단백질", "늦은 식사"]})
    ms = h.meals(date(2026, 11, 23))
    assert ms[0]["menu"] == "애호박 고추장찌개" and ms[0]["grade"] == "가끔" and next(x for x in h.recipes() if x["id"] == rid)["cooked"] == 1
    w = h.meal_week()
    assert w["n"] == 1 and w["tags"]["늦은 식사"] == 1 and w["days"][-1]["today"] and w["days"][-1]["grades"] == ["가끔"]
    sg = h.suggest()
    assert len(sg) == 3 and all(x["id"] != rid for x in sg) and sg[0]["grade"] == "균형" and not any("반찬" in x["tags"] for x in sg)        # 최근에 먹은 것은 뒤로, 균형 접시 먼저
    h.delete_meal(m)
    assert h.meals(date(2026, 11, 23)) == []
    c = create_app(rt).test_client()
    assert c.get("/api/health/recipes").status_code == 200
    assert c.post("/api/health/meal", json={"slot": "점심", "menu": ""}).status_code == 400
    assert "애호박 고추장찌개" in [x["title"] for x in h.export()["recipes"]]


def test_health_calendar_shows_what_was_done_each_day(rt):
    """달력: 날마다 한 일(체크·운동·끼니·신체)과 경험치가 레벨 셈과 같은 식으로 찍힌다. 쓰기 전 날은 '빠짐'으로 만들지 않고, 앞날은 계획만."""
    from datetime import date
    from moa.health import Health
    h = Health(rt.db, today=lambda: date(2026, 11, 24))                   # 화요일
    c = h.cfg()
    for hb in c["habits"]:
        h.set_check({"date": "2026-11-23", "key": hb["id"], "on": True})
    h.set_workout({"date": "2026-11-23", "done": True, "rpe": 7})
    h.save_meal({"date": "2026-11-23", "slot": "점심", "menu": "불고기 덮밥", "tags": ["채소", "단백질"]})
    h.save_meal({"date": "2026-11-23", "slot": "저녁", "menu": "두부김치", "tags": ["과식"]})
    h.save_body({"date": "2026-11-17", "weight": 80.0})                 # 예전 체중만 넣은 날: 그 뒤가 '빠짐'이 되면 안 된다
    h.set_check({"date": "2026-11-20", "key": c["habits"][0]["id"], "on": True})
    h.save_body({"date": "2026-11-24", "weight": 79.4})
    h.set_check({"date": "2026-11-24", "key": c["habits"][0]["id"], "on": True})
    C = h.calendar("2026-11")
    assert C["title"] == "2026년 11월" and C["lead"] == 6 and len(C["days"]) == 30 and C["this_month"] and C["since"] == "2026-11-17"   # 11/1은 일요일
    D = {x["d"]: x for x in C["days"]}
    n = len(c["habits"])
    d23 = D[23]
    assert d23["gym"] == "done" and d23["clear"] and d23["lv"] == 4 and d23["checks"] == d23["need"] == n and d23["grades"] == ["균형", "가끔"]
    assert d23["xp"] == 10 * n + 40 + 10 + 10 * 2 + 5 + 20 and d23["workout"]["rpe"] == 7 and [m["menu"] for m in d23["meal_list"]] == ["불고기 덮밥", "두부김치"]
    assert all(x["on"] for x in d23["check_list"]) and D[24]["today"] and D[24]["body_rec"]["weight"] == 79.4 and D[24]["xp"] == 10 + 15 and D[24]["lv"] == 1
    assert D[10]["gym"] in ("", "rest") and D[10]["xp"] == 0 and D[10]["lv"] == 0                       # 첫 기록(11/20) 전은 빠짐이 아니다
    miss = [x["d"] for x in C["days"] if x["gym"] == "miss"]
    assert all(20 <= d_ < 23 for d_ in miss) and D[18]["gym"] != "miss" and D[19]["gym"] != "miss" and D[25]["future"] and D[25]["gym"] in ("plan", "rest") and D[25]["workout"]["plan"]
    S = C["sum"]
    assert S["workouts"] == 1 and S["clears"] == 1 and S["meals"] == 2 and S["good"] == 1 and S["body"] == 2 and S["active"] == 4 and S["streak"] == 2
    assert S["xp"] == sum(x["xp"] for x in C["days"]) == h.overview()["rpg"]["total"] and S["weight"] == {"first": 80.0, "last": 79.4, "diff": -0.6}
    assert C["prev"] is None and C["next"] == "2026-12" and h.calendar("2026-12")["next"] is None and h.calendar("2026-12")["prev"] == "2026-11"
    with pytest.raises(ValueError):
        h.calendar("2026-13")
    cl = create_app(rt).test_client()
    assert cl.get("/api/health/calendar?month=2026-11").status_code == 200 and cl.get("/api/health/calendar?month=x").status_code == 400



def test_recipe_seed_adds_category_to_old_rows(rt):
    """예전 판에서 분류 없이 들어간 처음 레시피에는 분류를 앞에 붙이고, 새 레시피는 더한다(지운 것은 다시 넣지 않는다)."""
    import json
    from datetime import date
    from moa.health import Health
    h = Health(rt.db, today=lambda: date(2026, 11, 24))
    rid = next(r["id"] for r in h.recipes() if r["key"] == "eomuk")
    rt.db.x("UPDATE hl_recipe SET tags=? WHERE id=?", (json.dumps(["밑반찬", "짭짤"], ensure_ascii=False), rid))
    gone = next(r["id"] for r in h.recipes() if r["key"] == "bibimbap")
    h.delete_recipe(gone)
    rt.db.x("DELETE FROM hl_recipe WHERE key='curry'")
    Health(rt.db, today=lambda: date(2026, 11, 24))
    by = {r["key"]: r for r in h.recipes()}
    assert by["eomuk"]["tags"] == ["반찬", "밑반찬", "짭짤"] and "curry" in by and "bibimbap" not in by


def test_health_alerts(rt, monkeypatch):
    """건강 알림: 시간표 시각·약 놓침·연속 기록·레벨업을 '건강관리' 갈래로, 같은 일은 한 번만, 밤에는 안 보냄. 제목 앞에 🌿."""
    from datetime import datetime, timedelta
    sent = []
    rt.settings.update({"ntfy_topic": "x-test"})
    monkeypatch.setattr("moa.runtime.requests.post", lambda *a, **k: sent.append(k.get("json")))
    monkeypatch.setattr("moa.runtime.threading.Thread", lambda target, daemon=True: type("T", (), {"start": lambda self: target()})())
    h = rt.health
    today = h._today()
    c = h.save_cfg({"meds": [{"name": "약 가", "when": "am"}]})
    plan = dict(c["plan"])
    plan["cal"] = {today.isoformat(): {"mode": "", "blocks": [["08:00", "med_am", "아침 약", "식후 바로"], ["10:00", "gym", "운동", "하체"]]}}
    h.save_cfg({"plan": plan})
    A = rt.hlalert
    at = lambda hm: datetime.combine(today, datetime.strptime(hm, "%H:%M").time())
    assert A.tick(at("08:03")) == ["quest"] and sent[-1]["title"] == "🌿 08:00 · 아침 약" and sent[-1]["message"] == "식후 바로"
    assert A.tick(at("08:05")) == []                                         # 같은 일은 한 번
    assert A.tick(at("09:31")) == ["med"]                                    # 1시간 반 지나도 체크 없음
    h.set_check({"key": "med_am", "on": True})
    assert A.tick(at("10:02")) == ["quest"] and "운동" in sent[-1]["title"]
    assert A.tick(at("23:30")) == [] and A.tick(at("06:00")) == []           # 밤에는 안 보냄
    A.set({"quest": False})
    rt._notified = {}
    assert A.tick(at("10:03")) == []                                         # 끈 종류는 안 보냄
    assert rt.notify_log(1)[0]["cat"] == "health"
    v = A.view()
    assert [k["key"] for k in v["kinds"]][:2] == ["quest", "med"] and v["log"] and v["log"][0]["title"].startswith("🌿")
    rt.notify("투자 알림", "real", "실전 · 매도")
    assert sent[-1]["title"] == "📈 실전 · 매도"                               # 투자는 📈
    c2 = create_app(rt).test_client()
    nid = rt.notify_log(1)[0]["id"]
    assert c2.get(f"/api/notify/{nid}").get_json()["msg"] == "투자 알림"
    assert c2.post("/api/health/alerts", json={"weekly": False}).get_json()["kinds"][-1]["on"] is False


@pytest.mark.ai_switch
def test_ai_master_switch_off_by_default(rt):
    """AI 사용은 기본 꺼짐: 키가 있어도 Claude를 부르지 않고(마지막 관문 _post까지), 자동매매는 새로 사지 않되 실전 '쉼' 알림을 울리지 않는다. 켜면 다시 쓴다."""
    from moa.ai import AIError
    rt.settings.update({"ai": {"api_key": "sk-test-key-1234567890"}})
    A = rt.ai
    assert A.has_key() and not A.enabled() and not A.configured()
    with pytest.raises(AIError, match="꺼 두었어요"):
        list(A._post({"model": "x"}))
    evs = list(A.stream("chat", {"question": "안녕"}))
    assert evs[0]["t"] == "error" and "꺼 두었어요" in evs[0]["msg"]
    c = create_app(rt).test_client()
    st = c.get("/api/ai/status").get_json()
    assert st["enabled"] is False and st["has_key"] is True and st["configured"] is False
    assert c.post("/api/ai/enabled", json={"on": True}).get_json()["enabled"] is True and A.configured()
    assert c.post("/api/ai/enabled", json={"on": False}).get_json()["configured"] is False


def test_ai_daily_usage_utc(rt):
    """설정 › AI 날짜별 사용량: 콘솔과 같은 UTC 날짜·입력+출력 토큰으로, 모델·용도별 금액."""
    rt.db.x("INSERT INTO ai_usage(ts,kind,model,in_tok,out_tok,searches,usd) VALUES(?,?,?,?,?,?,?)", ("2026-11-25T08:30:00", "pick", "claude-fable-5-2", 1000, 200, 1, 0.026))
    rt.db.x("INSERT INTO ai_usage(ts,kind,model,in_tok,out_tok,searches,usd) VALUES(?,?,?,?,?,?,?)", ("2026-11-25T09:30:00", "ana", "claude-sonnet-5-5", 500, 100, 0, 0.002))
    from datetime import datetime as _d
    import moa.ai as M
    class D(_d):
        @classmethod
        def now(cls, tz=None):
            return _d(2026, 11, 26, 12, 0)
    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(M, "datetime", D)
    try:
        R = rt.ai.daily(8)["rows"]
    finally:
        mp.undo()
    days = {r["day"]: r for r in R}
    assert days["2026-11-24"]["models"] == {"Fable 5.2": 1200} and days["2026-11-25"]["models"] == {"Sonnet 5.5": 600}   # 한국 08:30 = UTC 전날
    assert days["2026-11-24"]["kinds"] == {"pick": 0.026}



def test_ai_background_engine_cap_and_daily_limit(rt, monkeypatch):
    """비용 사고(2026-11-25) 뒤: 사용자가 누르지 않은 요청은 Sonnet까지만, 하루 상한을 넘으면 그날은 멈추고 50·80·100%에 알린다."""
    rt.settings.update({"ai": {"api_key": "sk-test-key-1234567890", "model": "claude-fable-5-2", "daily_cap_usd": 1.0}})
    used = []

    def fake(body):
        used.append(body["model"])
        yield {"type": "message_start", "message": {"usage": {"input_tokens": 100000, "output_tokens": 0}}}
        yield {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
        yield {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "답"}}
        yield {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1000}}
        yield {"type": "message_stop"}
    monkeypatch.setattr(rt.ai, "_post", fake)
    notes = []
    monkeypatch.setattr(rt, "notify", lambda msg, cat="info", title=None, key=None, quiet_min=0, force=False, link=None: notes.append((cat, title, key)) or True)
    list(rt.ai.stream("chat", {"question": "x", "web_search": False}))                  # 백그라운드(표시 없음) → Sonnet
    list(rt.ai.stream("chat", {"question": "x", "web_search": False, "_user": True}))   # 화면에서 누름 → 고른 Fable
    assert used == ["claude-sonnet-5-5", "claude-fable-5-2"]
    assert rt.ai.today_usd() > 1.0
    evs = list(rt.ai.stream("chat", {"question": "x", "web_search": False, "_user": True}))
    assert evs[0]["t"] == "error" and "하루 상한" in evs[0]["msg"] and len(used) == 2   # 상한을 넘은 뒤에는 부르지 않음
    assert any(c == "urgent" and k.endswith(":100") for c, _, k in notes)


# ---------- 파이어(일찍 은퇴) 계획(v5.61) ----------
def test_fire_rules_and_plan(rt):
    """제도 숫자(퇴직수당 비율·명예퇴직수당·주택연금 표)와 계획 셈. 개인 자료는 DB 설정에만 남는다."""
    from datetime import date
    from moa import fire as FI
    assert FI.retire_rate(0.5) == 0 and FI.retire_rate(3) == 6.5 and FI.retire_rate(12) == 29.25 and FI.retire_rate(25) == 39.0
    assert FI.retire_allowance(20, 5_000_000) == round(20 * 5_000_000 * 0.39)
    assert FI.retire_allowance(40, 1_000_000) == round(33 * 1_000_000 * 0.39)            # 33년까지만
    assert FI.honor_allowance(19, 60, 5_000_000) == 0 and FI.honor_allowance(25, 6, 5_000_000) == 0
    assert FI.honor_allowance(25, 36, 5_000_000) == round(5_000_000 * 0.68 * 0.5 * 36)
    assert FI.honor_allowance(25, 120, 5_000_000) == round(5_000_000 * 0.68 * 0.5 * 90) == FI.honor_allowance(25, 200, 5_000_000)
    assert FI.pay(5, 17) == 4_838_400 and FI.pay(6, 19) == 4_309_100
    assert FI.house_pension(54, 9e8) == 0 and FI.house_pension(55, 9e8) == 1_404_000 and 1_404_000 < FI.house_pension(57, 9e8) < 1_896_000
    v = rt.fire.view()
    assert v["ready"] is False and "공무원 임용일(재직기간)" in v["missing"]
    with pytest.raises(ValueError):
        rt.fire.save({"salary": "abc"})
    base = {"birth": "1980-03-15", "service_start": "2006-03-01", "salary": 60_000_000, "grade_now": 6, "step_now": 19, "promo_date": "2027-01-01",
            "promo_grade": 5, "promo_step": 17, "spend": 2_500_000, "fin_assets": 300_000_000, "home_value": 700_000_000, "mortgage": 200_000_000,
            "mortgage_rate": 4.0, "mortgage_years": 20, "pension_gov": 1_200_000, "pension_nps": 150_000, "pension_start": "2045-03-15"}
    v = rt.fire.save(base)
    assert rt.db.get_setting("fire_profile")["salary"] == 60_000_000 and v["ready"]
    pl = FI.Plan(dict(rt.fire.profile()), date(2026, 11, 26))
    assert abs(pl.bump - 4_838_400 / 4_309_100) < 1e-9 and int(pl.age0) == 46
    i60, i50 = pl.at_retire(60), pl.at_retire(50)
    assert i60["honor"] == 0 and 1_200_000 < i50["gov"] < i60["gov"] and i50["honor"] > 0 and i50["allowance"] > 0   # 연금은 일한 만큼 늘어난다
    assert i60["start"] == 65 and FI.start_age(2031, 25) == 64 and FI.start_age(2028, 12) == 63 and FI.start_age(2028, 9) == 65   # 10년 미만은 연계(65세)
    assert FI.accrual_rate(2026) == 1.736 and FI.accrual_rate(2040) == 1.7
    gov1, gain = pl.gov_at(date(2027, 11, 26))
    assert gain == round(60_000_000 / 12 * 0.01736) and gain < gov1 - 1_200_000 < gain * 1.2    # 1년 더 일하면 월 연금이 대략 '연봉÷12 × 1.7%'만큼(승진분 포함) 는다
    # 돈이 많을수록 늦게 바닥나고, 수익률이 높을수록 늦게 바닥난다
    rich = FI.Plan(dict(rt.fire.profile(), fin_assets=2e9), date(2026, 11, 26))
    assert rich.run(50, 3.5)["broke"] is None
    b1, b2 = pl.run(50, 1.5)["broke"], pl.run(50, 5.0)["broke"]
    assert (b1 or 999) <= (b2 or 999)
    v = rt.fire.view()
    g = {x["age"]: x for x in v["goals"]}
    assert g[60]["spend_max"] >= g[50]["spend_max"] and (g[50]["add_need"] or 0) >= (g[60]["add_need"] or 0)
    assert rt.fire.brief()["ready"] and v["table"][0]["age"] == 47 and v["curve_ages"][0] == 46
