"""웹 서버와 API."""
import hmac
import json
import os
import secrets
from datetime import date

from flask import Flask, jsonify, make_response, request, send_from_directory

from .assets import AssetError
from .broker.brokers import OrderError
from .ledger import LedgerError
from .market import Cache
from .settings import MASK
from .trading import backtest as BT
from .trading.strategy import STRATEGIES

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
FORWARD_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "Forwarded", "Tailscale-User-Login",
                   "CF-Connecting-IP", "X-Real-IP", "Ngrok-Trace-Id")
VERSION = "0.1"


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
        try:
            with open(os.path.join(os.path.dirname(WEB), "VERSION"), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return VERSION

    def build():
        return (updater.state.get("current") if updater else "") or ""

    @app.get("/api/ping")
    def ping():
        return jsonify(ok=True, app="moa", version=version(), build=build(), local=is_local_direct())

    # ---------- 업데이트 ----------
    @app.get("/api/update")
    def update_status():
        if not updater:
            return jsonify(available=False, message="업데이트 기능이 꺼져 있어요", version=version())
        return jsonify({**updater.status(), "version": version()})

    @app.post("/api/update/check")
    def update_check():
        if not updater:
            raise RuntimeError("업데이트 기능이 꺼져 있어요")
        d = body()
        if d.get("apply"):
            ok, msg = updater.apply()
            return jsonify({**updater.status(), "applied": ok, "msg": msg, "version": version()})
        return jsonify({**updater.check(), "version": version()})

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
        acct = rt.account.snapshot() if rt.account.configured() else {"configured": False}
        return jsonify(
            account={k: acct.get(k) for k in ("configured", "ok", "error", "total", "eval", "pnl", "pnl_pct", "cash", "updated_at", "server")},
            ledger=rt.ledger.summary(month), recent=rt.ledger.recent(5),
            assets=rt.assets.overview(),
            scalp={k: sc[k] for k in ("state", "mode_label", "today", "error", "data_source")},
            ai=rt.aipick.today(),
            dca={"schedule": dc["schedule"], "monthly": dc["monthly"], "value": dc["value"],
                 "gain": dc["gain"], "gain_pct": dc["gain_pct"], "plans": len(dc["plans"])},
            feed_error=rt.errors.get("feed"))

    # ---------- 가계부 ----------
    @app.get("/api/ledger")
    def ledger():
        month = request.args.get("month") or date.today().strftime("%Y-%m")
        try:
            soon = [x for x in rt.fixed.summary()["upcoming"] if 0 <= x["d"] <= 5 and x["status"] == "due" and x["kind"] == "out"]
        except Exception:
            soon = []
        return jsonify(fixed_soon=soon[:5], summary=rt.ledger.summary(month), rows=rt.ledger.rows(month, request.args.get("kind")),
                       categories=rt.ledger.categories())

    @app.get("/api/ledger/trend")
    def ledger_trend():
        return jsonify(rt.ledger.trend(max(1, min(24, int(request.args.get("months") or 6)))))

    @app.get("/api/timeline")
    def timeline():
        """홈 > 최근 활동: 가계부·주문·적립·엔진·업데이트 기록을 시간순으로."""
        items = []
        for t in rt.db.q("SELECT t.*, c.name AS category FROM transactions t LEFT JOIN categories c "
                         "ON c.id=t.category_id ORDER BY t.id DESC LIMIT 20"):
            items.append({"ts": t["created_at"], "kind": "ledger", "title": t["memo"] or t["category"] or "내역",
                          "sub": f"{t['category'] or '분류 없음'} · {t['date'][5:].replace('-', '.')}",
                          "amount": None if t.get("transfer") else (t["amount"] if t["kind"] == "in" else -t["amount"])})
        eng_name = {"scalp": "단타", "dca": "적립"}
        for o in rt.db.q("SELECT * FROM orders ORDER BY id DESC LIMIT 20"):
            items.append({"ts": o["ts"], "kind": "order", "title": f"{o['name'] or o['code']} {'매수' if o['side'] == 'buy' else '매도'}",
                          "sub": f"{eng_name.get(o['engine'], o['engine'])} · {o['qty']}주 · {o['msg']}",
                          "side": o["side"], "price": o["fill_price"]})
        for r in rt.db.q("SELECT r.*, p.name FROM dca_runs r LEFT JOIN dca_plans p ON p.id=r.plan_id "
                         "WHERE r.status != 'done' ORDER BY r.id DESC LIMIT 10"):
            items.append({"ts": r["run_ts"], "kind": "warn", "title": f"{r['name'] or '적립'} 매수 {'건너뜀' if r['status'] == 'skipped' else '실패'}",
                          "sub": r["msg"]})
        for l in rt.db.q("SELECT * FROM engine_log WHERE level != 'info' OR engine='system' ORDER BY id DESC LIMIT 20"):
            items.append({"ts": l["ts"], "kind": "update" if l["engine"] == "system" and l["level"] == "info" else "warn",
                          "title": {"system": "시스템", "scalp": "단타 엔진", "dca": "적립"}.get(l["engine"], l["engine"]),
                          "sub": l["msg"]})
        items.sort(key=lambda x: x["ts"] or "", reverse=True)
        return jsonify(items[:60])

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
    stocks_cache = Cache()

    @app.get("/api/stocks")
    def stocks():
        # 키움 계좌면 잔고 조회가 1~2초 걸려서, 직전 결과를 바로 주고 뒤에서 새로 받는다
        d = dict(stocks_cache.get("s", 2, stocks_payload, stale=60))
        d["orders"] = rt.db.q("SELECT * FROM orders ORDER BY id DESC LIMIT 30")
        return jsonify(d)

    def stocks_payload():
        out = []
        st = rt.settings.all()
        same = (st["trade_mode"]["scalp"] == st["trade_mode"]["dca"] == "kiwoom"
                and st["kiwoom"]["scalp"].get("appkey") == st["kiwoom"]["dca"].get("appkey")
                and bool(st["kiwoom"]["scalp"].get("mock")) == bool(st["kiwoom"]["dca"].get("mock")))
        for eng, label in (("scalp", "기본 계좌"), ("dca", "장기투자 계좌")):
            b = rt.brokers.get(eng)
            item = {"engine": eng, "label": label, "error": rt.errors.get(eng),
                    "mode_label": b.label if b else None, "holdings": [], "cash": None}
            if eng == "dca" and same and b:  # 두 엔진이 같은 키움 계좌 키를 쓰면 한 번만 조회해 겹쳐 보이지 않게
                item.update(same_as="scalp", cash=out[0]["cash"], error=out[0]["error"])
                out.append(item)
                continue
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
                cl = getattr(b, "client", None)
                if cl is not None:  # 키움 계좌면 미국주식 잔고·달러 예수금도
                    try:
                        item["us_holdings"] = cl.us_holdings()
                        item["us_cash"] = cl.us_cash()
                    except Exception as e:
                        item["us_error"] = f"미국 잔고 조회 실패: {e}"
            out.append(item)
        return dict(accounts=out, data_source=rt.feed.kind,
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
        if d.get("market") == "us":
            return us_order(eng, b, d)
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
        stocks_cache.d.clear()
        return jsonify(res)

    # ---------- 미국주식 주문(키움 계좌만) ----------
    def us_client(b):
        cl = getattr(b, "client", None)
        if cl is None:
            raise OrderError("미국 주식은 키움 계좌로만 주문할 수 있어요. 설정 → 계좌 연결 → 자동매매 주문 계좌에서 주문 방식을 '키움으로 주문'으로 바꿔 주세요")
        return cl

    def us_sym(x):
        sym = (x or "").strip().upper()
        if not sym or len(sym) > 10 or not all(ch.isalnum() or ch in ".-" for ch in sym):
            raise OrderError("미국 종목 기호(예: AAPL)를 넣어 주세요")
        return sym

    def us_hint(msg):
        """키움 미국 주문 거절 사유에 바로 할 일을 붙인다(계좌 쪽 설정이라 앱에서 대신 못 하는 것들)."""
        tips = [("571771", "키움에서 '해외증권 위험고지' 확인이 한 번 필요해요. 영웅문S# → 메뉴 → 해외주식 → 해외주식 위험고지(또는 키움 홈페이지 → 해외주식 → 위험고지 확인)에서 동의한 뒤 다시 주문하세요."),
                ("위험고지", "키움에서 '해외증권 위험고지' 확인이 한 번 필요해요. 영웅문S# 해외주식 메뉴에서 동의한 뒤 다시 주문하세요."),
                ("주문가능", "달러가 부족해요. 영웅문S#에서 환전하거나 '원화주문(통합증거금)' 서비스를 신청하세요."),
                ("증거금", "달러가 부족해요. 영웅문S#에서 환전하거나 '원화주문(통합증거금)' 서비스를 신청하세요."),
                ("약정", "해외주식 거래 약정(신청)이 필요해요. 영웅문S# → 해외주식 → 해외주식 거래 신청을 먼저 해 주세요.")]
        for k, t in tips:
            if k in msg:
                return f"{t}\n(키움 응답: {msg})"
        return msg

    def us_order(eng, b, d):
        cl = us_client(b)
        sym = us_sym(d.get("code"))
        try:
            qty = int(d.get("qty") or 0)
            price = float(str(d["price"]).replace(",", "")) if d.get("price") not in (None, "") else None
        except ValueError:
            raise OrderError("수량과 가격은 숫자로 넣어 주세요")
        if qty <= 0:
            raise OrderError("수량은 1주 이상이어야 해요")
        if price is not None and price <= 0:
            raise OrderError("가격을 확인해 주세요")
        side = d.get("side")
        if side not in ("buy", "sell"):
            raise OrderError("매수인지 매도인지 골라 주세요")
        exch = d.get("exch") or d.get("exchange") or None  # ND/NY/NA 또는 공개 시세의 거래소 이름(NMS 등)을 힌트로
        with rt._lock:
            try:
                no, exch = cl.us_order(side, sym, qty, price, exch)
            except Exception as e:
                raise OrderError(us_hint(str(e)))
            est = price
            if est is None:
                try:
                    est = cl.us_quote(sym, exch)["price"]
                except Exception:
                    est = None
            name = d.get("name") or sym
            rt.db.x("INSERT INTO orders(ts,engine,mode,code,name,side,qty,price,status,fill_price,broker_no,msg) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rt.feed.now().isoformat(timespec="seconds"), eng, b.mode, sym, name, side, qty, price,
                     "accepted", est, f"{no}|{exch}", "미국 직접 주문(달러)"))
        stocks_cache.d.clear()
        return jsonify(status="accepted", fill_price=est, broker_no=no, name=name, market="us", exch=exch)

    @app.get("/api/us/quote/<sym>")
    def us_quote_api(sym):
        """주문 창용 미국 현재가: 키움 실시간 → 안 되면 공개 시세(지연)."""
        sym = us_sym(sym)
        eng = request.args.get("engine")
        cl = getattr(rt.brokers.get(eng), "client", None) or rt.market.kr_client()
        if cl is not None:
            try:
                return jsonify(cl.us_quote(sym))
            except Exception as e:
                err = str(e)
        else:
            err = "키움 연결 없음"
        q = dict(rt.market.yh_quote(sym))
        q.update(delayed=True, live=False, note=err)
        return jsonify(q)

    # ---------- 미체결 주문·취소 ----------
    def same_account():
        st = rt.settings.all()
        return (st["trade_mode"]["scalp"] == st["trade_mode"]["dca"] == "kiwoom"
                and st["kiwoom"]["scalp"].get("appkey") == st["kiwoom"]["dca"].get("appkey")
                and bool(st["kiwoom"]["scalp"].get("mock")) == bool(st["kiwoom"]["dca"].get("mock")))

    def settle_orders(eng, ok_kr, ok_us, items, cl=None):
        """오늘 '접수' 주문을 키움 체결 내역과 맞춰 실제 체결가·체결 수량을 반영한다.
        체결 내역 조회가 안 되면 미체결 목록에 없는 주문을 체결로만 표시한다(가격은 예상가 유지)."""
        today = rt.feed.now().date().isoformat()
        open_nos = {str(o["ord_no"]).lstrip("0") for o in items}
        engines = ("scalp", "dca") if eng == "scalp" and same_account() else (eng,)
        rows = rt.db.q("SELECT id, code, qty, broker_no, msg FROM orders WHERE status IN ('accepted','partial') AND ts >= ? "
                       "AND engine IN (%s)" % ",".join("?" * len(engines)), (today,) + engines)
        if not rows:
            return
        fills = {"kr": None, "us": None}
        if cl is not None:
            try:
                fills["kr"] = cl.fills_today()
            except Exception:
                pass
            if any(not str(o["code"]).isdigit() for o in rows):
                try:
                    fills["us"] = cl.us_fills_today()
                except Exception:
                    pass
        for o in rows:
            no = str(o["broker_no"] or "").split("|")[0].lstrip("0")
            us = not str(o["code"]).isdigit()
            if not no:
                continue
            f = (fills["us"] if us else fills["kr"])
            if f is not None and no in f:
                st = "filled" if f[no]["qty"] >= o["qty"] else "partial"
                unit = "달러" if us else "원"
                px = f[no]["price"]
                msg = (o["msg"] or "").split(" · 체결")[0] + f" · 체결 {f[no]['qty']}주 {px:,.2f}{unit}" if us else \
                      (o["msg"] or "").split(" · 체결")[0] + f" · 체결 {f[no]['qty']}주 {px:,.0f}{unit}"
                rt.db.x("UPDATE orders SET status=?, fill_price=?, msg=? WHERE id=?", (st, px, msg, o["id"]))
            elif ((us and ok_us) or (not us and ok_kr)) and no not in open_nos:  # 체결 내역엔 아직 없지만 미체결도 아님
                rt.db.x("UPDATE orders SET status='filled' WHERE id=?", (o["id"],))

    @app.get("/api/orders/open")
    def orders_open():
        out, errors = [], {}
        for eng in ("scalp", "dca"):
            if eng == "dca" and same_account():
                continue
            b = rt.brokers.get(eng)
            if b is None:
                continue
            mine, ok_kr, ok_us = [], False, False
            try:
                mine += [dict(o, engine=eng, market="kr") for o in b.unfilled()]
                ok_kr = True
            except Exception as e:
                errors[eng] = f"미체결 조회 실패: {e}"
            cl = getattr(b, "client", None)
            if cl is not None:
                try:
                    mine += [dict(o, engine=eng) for o in cl.us_unfilled()]
                    ok_us = True
                except Exception as e:
                    errors.setdefault(eng, f"미국 미체결 조회 실패: {e}")
                try:
                    settle_orders(eng, ok_kr, ok_us, mine, cl)
                except Exception:
                    pass
            out += mine
        return jsonify(items=out, errors=errors)

    @app.post("/api/orders/cancel")
    def orders_cancel():
        d = body()
        eng = d.get("engine")
        b = rt.brokers.get(eng)
        if b is None:
            raise OrderError(rt.errors.get(eng) or "계좌를 골라 주세요")
        ord_no = "".join(ch for ch in str(d.get("ord_no", "")) if ch.isalnum())
        us = d.get("market") == "us"
        code = us_sym(d.get("code")) if us else "".join(ch for ch in str(d.get("code", "")) if ch.isalnum())
        if not ord_no or (not us and len(code) != 6):
            raise OrderError("취소할 주문을 다시 골라 주세요")
        with rt._lock:
            if us:
                try:
                    no = us_client(b).us_cancel(ord_no, code, d.get("exch") if d.get("exch") in ("ND", "NY", "NA") else None)
                except OrderError:
                    raise
                except Exception as e:
                    raise OrderError(str(e))
                rt.db.x("UPDATE orders SET status='cancelled' WHERE broker_no LIKE ? AND engine=?", (ord_no + "|%", eng))
            else:
                no = b.cancel(ord_no, code)
            rt.db.x("UPDATE orders SET status='cancelled' WHERE broker_no=? AND engine=?", (ord_no, eng))
            rt.db.x("INSERT INTO orders(ts,engine,mode,code,name,side,qty,price,status,fill_price,broker_no,msg) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rt.feed.now().isoformat(timespec="seconds"), eng, b.mode, code, d.get("name") or code,
                     d.get("side") if d.get("side") in ("buy", "sell") else "buy", int(d.get("left") or 0), None,
                     "cancel", None, no, f"주문 취소(원주문 {ord_no})"))
        stocks_cache.d.clear()
        return jsonify(ok=True, ord_no=no)

    # ---------- 문자·알림톡 자동 기록 ----------
    @app.post("/api/inbox")
    def inbox_in():
        """아이폰 단축어가 문자를 받는 순간 보내는 곳. 금융 알림이 아니면 저장하지 않는다."""
        d = request.get_json(silent=True)
        if d is None:  # 단축어가 본문을 그냥 텍스트로 보낸 경우
            d = {"text": request.get_data(as_text=True)}
        text = str(d.get("text") or d.get("message") or "")[:2000]
        if not text.strip():
            raise ValueError("메시지 내용이 비어 있어요")
        r = rt.inbox.ingest(text, (d.get("source") or "sms")[:20])
        if not r:
            return jsonify(ok=True, financial=False)
        if r.get("status") == "added":
            sign = "+" if r["action"] in ("income", "cancel") else "-"
            rt.notify(f"[가계부] {r['merchant'] or r['issuer']} {sign}{r['amount']:,}원 자동 기록")
        return jsonify(ok=True, financial=True, item=r)

    @app.get("/api/inbox")
    def inbox_list():
        return jsonify(rt.inbox.list())

    @app.post("/api/inbox/<int:iid>/apply")
    def inbox_apply(iid):
        d = body()
        return jsonify(rt.inbox.apply(iid, d.get("category"), transfer=bool(d.get("transfer"))))

    @app.post("/api/inbox/<int:iid>/ignore")
    def inbox_ignore(iid):
        rt.inbox.ignore(iid)
        return jsonify(ok=True)

    @app.post("/api/inbox/import")
    def inbox_import():
        if request.files.get("file"):
            text = request.files["file"].read().decode("utf-8-sig", errors="replace")
        else:
            text = str(body().get("text") or "")
        if not text.strip():
            raise ValueError("가져올 내용이 없어요")
        return jsonify(rt.inbox.import_bulk(text[:5_000_000]))

    @app.put("/api/inbox/names")
    def inbox_names():
        names = [n.strip()[:20] for n in (body().get("names") or []) if isinstance(n, str) and len(n.strip()) >= 2][:5]
        rt.settings.update({"my_names": names})
        return jsonify(names=names, retagged=rt.inbox.retag())

    @app.put("/api/inbox/auto")
    def inbox_auto():
        d = body()
        rt.settings.update({"inbox_auto": {k: bool(d[k]) for k in ("card", "income", "outflow") if k in d}})
        return jsonify(ok=True)

    # ---------- 계좌 연결(한곳에서 보기) ----------
    @app.get("/api/connections")
    def connections():
        acct = rt.account.snapshot() if rt.account.configured() else {"configured": False}
        last = rt.db.one("SELECT received_at FROM inbox ORDER BY id DESC LIMIT 1")
        s = rt.settings.all()
        return jsonify(
            kiwoom=acct,
            banks=rt.inbox.bank_status(),
            inbox={"last_at": last["received_at"] if last else None, **{k: v for k, v in rt.inbox.list(1).items() if k != "items"}},
            trading={e: {"mode": s["trade_mode"][e], "label": (rt.brokers[e].label if rt.brokers.get(e) else None),
                         "error": rt.errors.get(e)} for e in ("scalp", "dca")})

    # ---------- 시세(국내·미국) ----------
    def mkt_arg(m):
        if m not in ("kr", "us"):
            raise ValueError("시장은 kr 또는 us예요")
        return m

    def sym_arg(m, s):
        s = (s or "").strip().upper()
        if m == "kr":
            s = "".join(ch for ch in s if ch.isalnum())
            if len(s) != 6:
                raise ValueError("국내 종목코드는 6자리예요")
        elif not s or len(s) > 12 or not all(ch.isalnum() or ch in ".-^=" for ch in s):
            raise ValueError("미국 종목 기호를 확인해 주세요")
        return s

    @app.get("/api/market/overview")
    def market_overview():
        return jsonify(rt.market.overview(mkt_arg(request.args.get("mkt", "kr"))))

    @app.get("/api/market/quote/<m>/<sym>")
    def market_quote(m, sym):
        m = mkt_arg(m); sym = sym_arg(m, sym)
        q = dict(rt.market.kr_quote(sym) if m == "kr" else rt.market.us_live(sym))
        q["watched"] = sym in rt.market.watch()[m]
        q["holding"] = rt.market.holding(sym) if m == "kr" else rt.market.us_holding(sym)
        if m == "us" and not q.get("fx"):
            try:
                q["fx"] = rt.market.yh_quote("USDKRW=X")["price"]
            except Exception:
                q["fx"] = None
        return jsonify(q)

    @app.get("/api/market/chart/<m>/<sym>")
    def market_chart(m, sym):
        m = mkt_arg(m); sym = sym_arg(m, sym)
        rng = request.args.get("range", "1d")
        if rng not in ("1d", "1m", "3m", "1y"):
            raise ValueError("기간을 골라 주세요")
        return jsonify(rt.market.kr_chart(sym, rng) if m == "kr" else rt.market.yh_chart(sym, rng))

    @app.get("/api/market/search")
    def market_search():
        m = mkt_arg(request.args.get("mkt", "kr"))
        q = (request.args.get("q") or "").strip()[:30]
        if not q:
            return jsonify([])
        return jsonify(rt.market.kr_search(q) if m == "kr" else rt.market.us_search(q))

    @app.post("/api/market/watch")
    def market_watch():
        d = body()
        m = mkt_arg(d.get("mkt")); sym = sym_arg(m, d.get("sym"))
        return jsonify(rt.market.set_watch(m, sym, bool(d.get("on"))))

    @app.get("/api/market/diag")
    def market_diag():
        return app.response_class(json.dumps(rt.market.diag(), ensure_ascii=False, default=str, indent=1),
                                  mimetype="application/json")

    # ---------- 내 계좌(조회 전용) ----------
    @app.get("/api/myaccount")
    def myaccount():
        return jsonify(rt.account.snapshot(force=request.args.get("force") == "1"))

    @app.post("/api/myaccount/test")
    def myaccount_test():
        if not rt.account.configured():
            raise ValueError("설정에서 내 계좌 앱키와 시크릿키를 먼저 넣어 주세요")
        s = rt.account.snapshot(force=True)
        if not s.get("ok"):
            raise RuntimeError(s.get("error") or "연결하지 못했어요")
        return jsonify(s)

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

    # ---------- 투자 화면(홈·호가·캔들·체결·정보·랭킹·알림) ----------
    @app.get("/api/invest/home")
    def invest_home():
        return jsonify(rt.invest.home())

    @app.get("/api/invest/book/<m>/<sym>")
    def invest_book(m, sym):
        m = mkt_arg(m)
        return jsonify(rt.invest.orderbook(m, sym_arg(m, sym)))

    @app.get("/api/invest/ticks/<m>/<sym>")
    def invest_ticks(m, sym):
        m = mkt_arg(m)
        return jsonify(rt.invest.ticks(m, sym_arg(m, sym)))

    @app.get("/api/invest/candles/<m>/<sym>")
    def invest_candles(m, sym):
        m = mkt_arg(m)
        return jsonify(rt.invest.candles(m, sym_arg(m, sym), request.args.get("tf", "d")))

    @app.get("/api/invest/info/<m>/<sym>")
    def invest_info(m, sym):
        m = mkt_arg(m)
        return jsonify(rt.invest.info(m, sym_arg(m, sym)))

    @app.get("/api/invest/rank")
    def invest_rank():
        return jsonify(rt.invest.ranking(mkt_arg(request.args.get("mkt", "kr")), request.args.get("kind", "value")))

    @app.get("/api/invest/alerts")
    def invest_alerts():
        return jsonify(rt.invest.alerts())

    @app.post("/api/invest/alerts")
    def invest_alert_add():
        return jsonify(id=rt.invest.add_alert(body()))

    @app.delete("/api/invest/alerts/<int:aid>")
    def invest_alert_del(aid):
        rt.invest.del_alert(aid)
        return jsonify(ok=True)

    # ---------- 고정 지출·구독 ----------
    from .fixed import FixedError

    def fixed_call(fn, *a):
        try:
            return fn(*a)
        except FixedError as e:
            raise ValueError(str(e))

    @app.get("/api/fixed")
    def fixed_get():
        rt.fixed.run()
        return jsonify(summary=rt.fixed.summary(), items=rt.fixed.list(), suggest=rt.fixed.suggestions(),
                       cards=[{"id": c["id"], "name": c["name"]} for c in rt.fixed.cards() if c["active"]])

    @app.post("/api/fixed")
    def fixed_save():
        return jsonify(id=fixed_call(rt.fixed.save, body()))

    @app.delete("/api/fixed/<int:rid>")
    def fixed_del(rid):
        rt.fixed.delete(rid)
        return jsonify(ok=True)

    @app.post("/api/fixed/dismiss")
    def fixed_dismiss():
        rt.fixed.dismiss(body().get("key"))
        return jsonify(ok=True)

    @app.post("/api/fixed/<int:rid>/mark")
    def fixed_mark(rid):
        d = body()
        st = d.get("status") if d.get("status") in ("skipped", "paid") else "skipped"
        from datetime import date as _d
        due = _d.fromisoformat(d.get("due"))
        r = rt.db.one("SELECT * FROM recurring WHERE id=?", (rid,))
        if not r:
            raise ValueError("고정 지출을 찾지 못했어요")
        rt.fixed._hit(rid, due, None, st, r["amount"] if st == "paid" else 0)
        return jsonify(ok=True)

    # ---------- 카드 실적·청구 ----------
    @app.get("/api/cards")
    def cards_get():
        return jsonify(rt.fixed.card_overview())

    @app.get("/api/cards/simple")
    def cards_simple():
        return jsonify([{"id": c["id"], "name": c["name"], "kind": c["kind"]} for c in rt.fixed.cards() if c["active"]])

    @app.post("/api/cards")
    def cards_save():
        return jsonify(id=fixed_call(rt.fixed.save_card, body()))

    @app.delete("/api/cards/<int:cid>")
    def cards_del(cid):
        rt.fixed.delete_card(cid)
        return jsonify(ok=True)

    @app.get("/api/cards/<int:cid>/txs")
    def cards_txs(cid):
        return jsonify(rt.fixed.card_txs(cid, request.args.get("start", "0000"), request.args.get("end", "9999")))

    # ---------- Claude AI 도우미 ----------
    @app.get("/api/ai/status")
    def ai_status():
        return jsonify(rt.ai.status())

    @app.get("/api/ai/meter")
    def ai_meter():
        w = request.args.get("web")
        return jsonify(rt.ai.meter(request.args.get("kind") or "chat", request.args.get("model"),
                                   None if w is None else w == "1"))

    @app.get("/api/ai/credit")
    def ai_credit():
        return jsonify(rt.ai.credit())

    @app.post("/api/ai/credit")
    def ai_credit_set():
        from .ai import AIError
        try:
            return jsonify(rt.ai.set_credit(body().get("usd")))
        except AIError as e:
            raise ValueError(str(e))

    @app.post("/api/ai/test")
    def ai_test():
        from .ai import AIError
        try:
            return jsonify(rt.ai.test())
        except AIError as e:
            raise ValueError(str(e))

    @app.get("/api/ai/pick/estimate")
    def ai_pick_estimate():
        return jsonify(rt.aipick.estimate("us" if request.args.get("mkt") == "us" else "kr"))

    @app.get("/api/ai/stats")
    def ai_stats():
        if request.args.get("refresh"):
            rt.aipick.evaluate(force=True)
        return jsonify(rt.aipick.stats(int(request.args.get("days") or 0), request.args.get("mkt") or "",
                                       request.args.get("model") or ""))

    @app.get("/api/ai/picks")
    def ai_picks():
        return jsonify(rt.aipick.history())

    @app.get("/api/ai/guards")
    def ai_guards():
        return jsonify(rt.aipick.guards())

    @app.post("/api/ai/guards")
    def ai_guard_add():
        return jsonify(rt.aipick.add_guard(body()))

    @app.delete("/api/ai/guards/<int:gid>")
    def ai_guard_del(gid):
        return jsonify(rt.aipick.cancel_guard(gid))

    @app.post("/api/ai/ask")
    def ai_ask():
        d = body()
        kind = d.get("kind")

        def gen():
            try:
                for ev in rt.ai.stream(kind, d):
                    yield json.dumps(ev, ensure_ascii=False) + "\n"
            except Exception as e:  # 중간에 끊겨도 화면이 이유를 알 수 있게
                yield json.dumps({"t": "error", "msg": f"AI 처리 중 오류: {e}"}, ensure_ascii=False) + "\n"
        return app.response_class(gen(), mimetype="application/x-ndjson",
                                  headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---------- 단타 조건 대량 훑기(vectorbt) ----------
    @app.get("/api/sweep")
    def sweep_status():
        return jsonify(rt.sweep.status())

    @app.post("/api/sweep")
    def sweep_start():
        from .trading.sweep import SweepError
        try:
            return jsonify(rt.sweep.start(body()))
        except SweepError as e:
            raise ValueError(str(e))

    @app.post("/api/sweep/stop")
    def sweep_stop():
        rt.sweep.stop()
        return jsonify(ok=True)

    @app.post("/api/sweep/apply")
    def sweep_apply():
        st = rt.sweep.status()
        rid = body().get("id")
        r = next((x for x in st.get("top") or [] if x["id"] == rid), None)
        if not r:
            raise ValueError("적용할 조건을 다시 골라 주세요")
        name = st["strategy"]
        p = dict(r["params"])
        hold = p.pop("max_hold_min", None)
        patch = {"strategy": {"name": name, name: p}}
        if hold:
            patch["exit"] = {"max_hold_min": hold}
        rt.settings.update(patch)
        return jsonify(ok=True, strategy=name, params=p, max_hold_min=hold)

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
        stocks_cache.d.clear()
        d = body()
        allowed = {"data_source", "demo_speed", "trade_mode", "kiwoom", "kiwoom_view", "real_orders_allowed", "paper_start_cash",
                   "costs", "holidays", "ntfy_topic", "dca_window", "public_url", "allowed_origins", "ai"}
        patch = {k: v for k, v in d.items() if k in allowed}
        if patch.get("data_source") not in (None, "demo", "kiwoom"):
            raise ValueError("시세 출처를 골라 주세요")
        rt.settings.update(patch)
        with rt._lock:
            rt.rebuild()
        return jsonify(settings=rt.settings.public(), errors=rt.errors)

    @app.post("/api/paper/reset")
    def paper_reset():
        stocks_cache.d.clear()
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
