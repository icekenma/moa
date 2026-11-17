import json
import os
import sys
from datetime import date, datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    assert c.get("/api/connect-info", headers={**fwd, "Authorization": f"Bearer {tok}"}).status_code == 403
    pre = c.options("/api/home", headers={"Origin": "https://icekenma.github.io",
                                          "Access-Control-Request-Method": "GET",
                                          "Access-Control-Request-Headers": "authorization"})
    assert pre.status_code == 204 and "Authorization" in pre.headers["Access-Control-Allow-Headers"]
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
    rt.notify = lambda m: sent.append(m)
    F._notify(date(2026, 12, 9))
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
    rt.notify = lambda m: sent.append(m)
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
    assert AP.judge([b(100, 104, 98, 101)], 100, 3, 1.5)[0] == "손절"  # 한 봉에 둘 다 → 보수적으로 손절
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
