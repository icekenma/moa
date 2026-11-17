"""웹 서버와 API."""
import hmac
import os
import secrets
from datetime import date

from flask import Flask, jsonify, make_response, request, send_from_directory

from .assets import AssetError
from .broker.brokers import OrderError
from .ledger import LedgerError
from .settings import MASK
from .trading import backtest as BT
from .trading.strategy import STRATEGIES

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
FORWARD_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "Forwarded", "Tailscale-User-Login",
                   "CF-Connecting-IP", "X-Real-IP", "Ngrok-Trace-Id")
VERSION = "2026.11.16"


def access_token(db):
    tok = db.get_setting("access_token")
    if not tok:
        tok = secrets.token_urlsafe(24)
        db.set_setting("access_token", tok)
    return tok


def is_local_direct():
    """이 PC 브라우저에서 바로 연 요청인지(터널·프록시를 거치지 않음)."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return False
    return not any(request.headers.get(h) for h in FORWARD_HEADERS)


def create_app(rt, updater=None):
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

    @app.errorhandler(Exception)
    def on_error(e):
        code = getattr(e, "code", None)
        if isinstance(code, int) and 400 <= code < 600 and not isinstance(e, (ValueError, RuntimeError)):
            return jsonify(error=str(getattr(e, "description", e))), code
        known = (LedgerError, AssetError, OrderError, BT.DataError, ValueError, RuntimeError)
        if isinstance(e, known):
            return jsonify(error=str(e)), 400
        app.logger.exception(e)
        return jsonify(error=f"처리하지 못했어요: {e}"), 500

    def body():
        return request.get_json(silent=True) or {}

    token = access_token(rt.db)

    def cors_origin():
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") in [o.rstrip("/") for o in rt.settings.get("allowed_origins")]:
            return origin
        return None

    @app.before_request
    def guard():
        if not request.path.startswith("/api/"):
            return None
        if request.method == "OPTIONS":  # CORS 사전 요청
            resp = make_response("", 204)
            origin = cors_origin()
            if origin:
                resp.headers["Access-Control-Allow-Origin"] = origin
                resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
                resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, ngrok-skip-browser-warning"
                resp.headers["Access-Control-Allow-Private-Network"] = "true"
                resp.headers["Access-Control-Max-Age"] = "600"
                resp.headers["Vary"] = "Origin"
            return resp
        if is_local_direct():
            return None
        got = request.headers.get("Authorization", "")
        got = got[7:] if got.startswith("Bearer ") else ""
        if not got or not hmac.compare_digest(got, token):
            return jsonify(error="접속 토큰이 맞지 않아요. 설정에서 토큰을 다시 넣어 주세요", code="auth"), 401
        return None

    @app.after_request
    def add_cors(resp):
        origin = cors_origin()
        if origin and request.path.startswith("/api/"):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ---------- 화면(GitHub Pages와 같은 파일을 이 서버도 그대로 준다) ----------
    @app.get("/")
    def index():
        return send_from_directory(WEB, "index.html")

    @app.get("/<path:p>")
    def web(p):
        if p.startswith("api/"):
            return jsonify(error="없는 주소예요"), 404
        return send_from_directory(WEB, p)

    # ---------- 연결 ----------
    def version():
        if updater and updater.state.get("current"):
            return updater.state["current"]
        return VERSION

    @app.get("/api/ping")
    def ping():
        return jsonify(ok=True, app="moa", version=version(), local=is_local_direct())

    # ---------- 업데이트 ----------
    @app.get("/api/update")
    def update_status():
        if not updater:
            return jsonify(available=False, message="업데이트 기능이 꺼져 있어요")
        return jsonify(updater.status())

    @app.post("/api/update/check")
    def update_check():
        if not updater:
            raise RuntimeError("업데이트 기능이 꺼져 있어요")
        d = body()
        if d.get("apply"):
            ok, msg = updater.apply()
            return jsonify(updater.status(), applied=ok, msg=msg)
        return jsonify(updater.check())

    @app.put("/api/update/auto")
    def update_auto():
        rt.settings.update({"auto_update": bool(body().get("auto"))})
        return jsonify(ok=True)

    @app.get("/api/connect-info")
    def connect_info():
        if not is_local_direct():
            return jsonify(error="이 정보는 서버 PC에서만 볼 수 있어요"), 403
        return jsonify(token=token, public_url=rt.settings.get("public_url"),
                       pages_url="https://icekenma.github.io/moa/",
                       allowed_origins=rt.settings.get("allowed_origins"))

    @app.post("/api/connect-info/rotate")
    def rotate():
        nonlocal token
        if not is_local_direct():
            return jsonify(error="토큰은 서버 PC에서만 바꿀 수 있어요"), 403
        token = secrets.token_urlsafe(24)
        rt.db.set_setting("access_token", token)
        return jsonify(token=token)

    # ---------- 홈 ----------
    @app.get("/api/home")
    def home():
        month = date.today().strftime("%Y-%m")
        sc = rt.scalp.status()
        dc = rt.dca.status()
        return jsonify(
            ledger=rt.ledger.summary(month), recent=rt.ledger.recent(5),
            assets=rt.assets.overview(),
            scalp={k: sc[k] for k in ("state", "mode_label", "today", "error", "data_source")},
            dca={"schedule": dc["schedule"], "monthly": dc["monthly"], "value": dc["value"],
                 "gain": dc["gain"], "gain_pct": dc["gain_pct"], "plans": len(dc["plans"])},
            feed_error=rt.errors.get("feed"))

    # ---------- 가계부 ----------
    @app.get("/api/ledger")
    def ledger():
        month = request.args.get("month") or date.today().strftime("%Y-%m")
        return jsonify(summary=rt.ledger.summary(month), rows=rt.ledger.rows(month, request.args.get("kind")),
                       categories=rt.ledger.categories())

    @app.post("/api/tx")
    def tx_add():
        return jsonify(id=rt.ledger.add(body()))

    @app.put("/api/tx/<int:tid>")
    def tx_put(tid):
        rt.ledger.update(tid, body())
        return jsonify(ok=True)

    @app.delete("/api/tx/<int:tid>")
    def tx_del(tid):
        rt.ledger.delete(tid)
        return jsonify(ok=True)

    @app.post("/api/categories")
    def cat_save():
        return jsonify(id=rt.ledger.save_category(body()))

    @app.delete("/api/categories/<int:cid>")
    def cat_del(cid):
        rt.ledger.delete_category(cid)
        return jsonify(ok=True)

    # ---------- 자산 ----------
    @app.get("/api/assets")
    def assets():
        o = rt.assets.overview()
        o["goal"] = rt.settings.get("networth_goal")
        return jsonify(o)

    @app.post("/api/accounts")
    def acct_save():
        return jsonify(id=rt.assets.save(body()))

    @app.delete("/api/accounts/<int:aid>")
    def acct_del(aid):
        rt.assets.delete(aid)
        return jsonify(ok=True)

    @app.put("/api/goal")
    def goal():
        rt.settings.update({"networth_goal": max(0, int(body().get("goal") or 0))})
        return jsonify(ok=True)

    # ---------- 주식 ----------
    @app.get("/api/stocks")
    def stocks():
        out = []
        for eng, label in (("scalp", "단타 계좌"), ("dca", "장기투자 계좌")):
            b = rt.brokers.get(eng)
            item = {"engine": eng, "label": label, "error": rt.errors.get(eng),
                    "mode_label": b.label if b else None, "holdings": [], "cash": None}
            if b:
                try:
                    item["cash"] = b.cash()
                    for h in b.positions():
                        h["value"] = int(h["qty"] * h["price"])
                        h["pnl"] = int((h["price"] - h["avg_price"]) * h["qty"])
                        h["pnl_pct"] = round((h["price"] / h["avg_price"] - 1) * 100, 2) if h["avg_price"] else 0
                        item["holdings"].append(h)
                except Exception as e:
                    item["error"] = f"잔고 조회 실패: {e}"
            out.append(item)
        orders = rt.db.q("SELECT * FROM orders ORDER BY id DESC LIMIT 30")
        return jsonify(accounts=out, orders=orders, data_source=rt.feed.kind,
                       market_open=rt.feed.market_open(), now=rt.feed.now().isoformat(timespec="minutes"))

    @app.get("/api/quote/<code>")
    def quote(code):
        return jsonify(rt.feed.quote(code))

    @app.post("/api/order")
    def order():
        d = body()
        eng = d.get("engine")
        b = rt.brokers.get(eng)
        if b is None:
            raise OrderError(rt.errors.get(eng) or "계좌를 골라 주세요")
        code = "".join(ch for ch in str(d.get("code", "")) if ch.isalnum())
        if len(code) != 6:
            raise OrderError("종목코드 6자리를 넣어 주세요")
        try:
            qty = int(d.get("qty") or 0)
            price = int(str(d["price"]).replace(",", "")) if d.get("price") not in (None, "") else None
        except ValueError:
            raise OrderError("수량과 가격은 숫자로 넣어 주세요")
        if qty <= 0:
            raise OrderError("수량은 1주 이상이어야 해요")
        side = d.get("side")
        if side not in ("buy", "sell"):
            raise OrderError("매수인지 매도인지 골라 주세요")
        with rt._lock:
            res = (b.buy if side == "buy" else b.sell)(code, qty, price)
            rt.db.x("INSERT INTO orders(ts,engine,mode,code,name,side,qty,price,status,fill_price,broker_no,msg) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rt.feed.now().isoformat(timespec="seconds"), eng, b.mode, code, res["name"], side, qty, price,
                     res["status"], res["fill_price"], res.get("broker_no", ""), "직접 주문"))
        return jsonify(res)

    # ---------- 단타 ----------
    @app.get("/api/scalp")
    def scalp():
        s = rt.settings.all()
        st = rt.scalp.status()
        st["config"] = {k: s[k] for k in ("strategy", "exit", "risk", "watchlist", "costs")}
        st["strategies"] = {k: v["label"] for k, v in STRATEGIES.items()}
        st["names"] = {c: rt.feed.name(c) for c in s["watchlist"]}
        return jsonify(st)

    @app.post("/api/scalp/control")
    def scalp_control():
        with rt._lock:
            rt.scalp.control(body().get("action"))
        return jsonify(ok=True)

    @app.put("/api/scalp/config")
    def scalp_config():
        d = body()
        patch = {}
        if "strategy" in d:
            if d["strategy"].get("name") not in STRATEGIES:
                raise ValueError("전략을 골라 주세요")
            patch["strategy"] = d["strategy"]
        for k in ("exit", "risk"):
            if k in d:
                patch[k] = d[k]
        if "watchlist" in d:
            wl = []
            for c in d["watchlist"]:
                c = "".join(ch for ch in str(c) if ch.isalnum())
                if len(c) == 6 and c not in wl:
                    wl.append(c)
            if not wl:
                raise ValueError("감시 종목을 하나 이상 넣어 주세요")
            patch["watchlist"] = wl[:30]
        rt.settings.update(patch)
        return jsonify(ok=True)

    @app.post("/api/backtest")
    def backtest():
        s = rt.settings.all()
        cfg = {k: s[k] for k in ("strategy", "exit", "risk", "costs")}
        if request.files.get("file"):
            code = (request.form.get("code") or "CSV").strip()[:12]
            if request.form.get("strategy"):
                import json
                over = json.loads(request.form["strategy"])
                cfg["strategy"] = over
            text = request.files["file"].read().decode("utf-8-sig", errors="replace")
            days = BT.parse_csv(text)
            data = {d: {code: bars} for d, bars in days.items()}
        else:
            d = body()
            if d.get("strategy"):
                cfg["strategy"] = d["strategy"]
            if d.get("exit"):
                cfg["exit"] = d["exit"]
            if d.get("risk"):
                cfg["risk"] = d["risk"]
            n = max(5, min(250, int(d.get("days") or 60)))
            data = BT.demo_dataset(s["watchlist"], n)
        if not data:
            raise BT.DataError("백테스트할 분봉이 없어요")
        res = BT.run(data, cfg)
        res["trades"] = res["trades"][-300:]
        return jsonify(res)

    # ---------- 적립식 ----------
    @app.get("/api/dca")
    def dca():
        return jsonify(rt.dca.status())

    @app.post("/api/dca/plans")
    def dca_save():
        return jsonify(id=rt.dca.save_plan(body()))

    @app.delete("/api/dca/plans/<int:pid>")
    def dca_del(pid):
        rt.dca.delete_plan(pid)
        return jsonify(ok=True)

    @app.put("/api/dca/plans/<int:pid>/enabled")
    def dca_enabled(pid):
        rt.dca.set_enabled(pid, bool(body().get("enabled")))
        return jsonify(ok=True)

    @app.post("/api/dca/plans/<int:pid>/run")
    def dca_run(pid):
        with rt._lock:
            return jsonify(results=rt.dca.run_due(force_plan_id=pid))

    @app.put("/api/dca/config")
    def dca_config():
        d = body()
        patch = {}
        if d.get("mode") in ("fixed", "rebalance"):
            patch["dca_mode"] = d["mode"]
        if "auto" in d:
            patch["dca_auto"] = bool(d["auto"])
        rt.settings.update(patch)
        return jsonify(ok=True)

    # ---------- 설정 ----------
    @app.get("/api/settings")
    def settings_get():
        return jsonify(rt.settings.public())

    @app.put("/api/settings")
    def settings_put():
        d = body()
        allowed = {"data_source", "demo_speed", "trade_mode", "kiwoom", "real_orders_allowed", "paper_start_cash",
                   "costs", "holidays", "ntfy_topic", "dca_window", "public_url", "allowed_origins"}
        patch = {k: v for k, v in d.items() if k in allowed}
        if patch.get("data_source") not in (None, "demo", "kiwoom"):
            raise ValueError("시세 출처를 골라 주세요")
        rt.settings.update(patch)
        with rt._lock:
            rt.rebuild()
        return jsonify(settings=rt.settings.public(), errors=rt.errors)

    @app.post("/api/paper/reset")
    def paper_reset():
        eng = body().get("engine")
        b = rt.brokers.get(eng)
        if not b or b.mode != "paper":
            raise ValueError("앱 내 모의 계좌만 초기화할 수 있어요")
        if eng == "scalp" and rt.scalp.open_trades():
            raise ValueError("단타 엔진이 종목을 들고 있어요. 엔진을 멈춘 뒤 초기화해 주세요")
        b.reset(rt.settings.get("paper_start_cash")[eng])
        return jsonify(ok=True)

    @app.post("/api/kiwoom/test")
    def kiwoom_test():
        eng = body().get("engine")
        if eng not in ("scalp", "dca"):
            raise ValueError("계좌를 골라 주세요")
        c = rt._client(eng)
        c.token()
        cash = c.cash()
        q = c.quote("005930")
        return jsonify(ok=True, server="모의투자" if c.mock else "실전", cash=cash, sample=q)

    return app
