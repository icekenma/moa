"""단타 자동매매 엔진(실시간). 판단 규칙은 strategy/risk 모듈을 백테스트와 똑같이 쓴다.

상태
- running : 새로 사고, 보유 종목을 관리한다.
- paused  : 새로 사지 않고, 보유 종목의 손절·익절·장마감 청산만 한다.
- stopped : 아무것도 하지 않는다(멈출 때 보유 종목은 모두 판다).
"""
import time
from datetime import datetime

from . import costs as C
from .risk import check_exit, edge_ok, entry_gate, position_size
from .strategy import STRATEGIES, signal

ENGINE = "scalp"


class ScalpEngine:
    def __init__(self, rt):
        self.rt = rt
        self.last_eval = None
        self.gate_msg = ""
        self.last_reconcile = 0.0
        self.last_error = ""
        self.skip_logged = set()

    # ---------- 공통 ----------
    def _now(self):
        return self.rt.feed.now()

    def log(self, msg, level="info"):
        self.rt.db.log(ENGINE, level, msg, self._now().isoformat(timespec="seconds"))

    def open_trades(self):
        return self.rt.db.q("SELECT * FROM trades WHERE engine=? AND exit_ts IS NULL ORDER BY id", (ENGINE,))

    def closed_today(self, session):
        rows = self.rt.db.q("SELECT pnl, exit_ts FROM trades WHERE engine=? AND session=? AND exit_ts IS NOT NULL "
                            "ORDER BY exit_ts, id", (ENGINE, session))
        return [{"pnl": r["pnl"] or 0, "exit_t": r["exit_ts"][11:16]} for r in rows]

    def _record_order(self, side, code, name, qty, res, msg=""):
        b = self.rt.brokers[ENGINE]
        self.rt.db.x("INSERT INTO orders(ts,engine,mode,code,name,side,qty,price,status,fill_price,broker_no,msg) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (self._now().isoformat(timespec="seconds"), ENGINE, b.mode, code, name, side, qty, None,
                      res["status"], res["fill_price"], res.get("broker_no", ""), msg))

    # ---------- 매매 ----------
    def close(self, tr, reason):
        s = self.rt.settings.all()
        b = self.rt.brokers[ENGINE]
        res = b.sell(tr["code"], tr["qty"])
        fill = res["fill_price"]
        if b.mode != "paper":
            fill = C.sell_fill(fill, s["costs"])  # 키움 주문은 체결가를 바로 모르므로 추정
        pnl, fees = C.trade_pnl(tr["entry_price"], fill, tr["qty"], s["costs"])
        self.rt.db.x("UPDATE trades SET exit_ts=?, exit_price=?, reason=?, pnl=?, fees=? WHERE id=?",
                     (self._now().isoformat(timespec="seconds"), fill, reason, pnl, fees, tr["id"]))
        self._record_order("sell", tr["code"], tr["name"], tr["qty"], res, reason)
        self.log(f"{tr['name']} {tr['qty']}주 매도, {reason}, 손익 {pnl:+,}원")
        self.rt.notify(f"[단타] {tr['name']} {reason} {pnl:+,}원")

    def open(self, code, sig, now, session):
        s = self.rt.settings.all()
        b = self.rt.brokers[ENGINE]
        q = self.rt.feed.quote(code)
        px = q["price"]
        if C.buy_fill(px, s["costs"]) <= sig["stop"]:
            return False
        qty = position_size(C.buy_fill(px, s["costs"]), sig["stop"], b.cash(), s["risk"], s["costs"])
        if qty <= 0:
            self.log(f"{q['name']} 신호가 났지만 살 수량이 0주예요(손절폭이 넓거나 현금 부족)", "warn")
            return False
        res = b.buy(code, qty)
        fill = res["fill_price"]
        if b.mode != "paper":
            fill = C.buy_fill(fill, s["costs"])
        sig = dict(sig, target=fill + sig["r"] * (fill - sig["stop"]))
        t = now.isoformat(timespec="seconds")
        self.rt.db.x("INSERT INTO trades(engine,mode,strategy,code,name,qty,session,entry_ts,entry_price,stop,stop0,"
                     "target,entry_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (ENGINE, b.mode, s["strategy"]["name"], code, res["name"], qty, session, t, fill,
                      sig["stop"], sig["stop"], sig["target"], sig["reason"]))
        self._record_order("buy", code, res["name"], qty, res, sig["reason"])
        self.log(f"{res['name']} {qty}주 매수 {fill:,.0f}원. {sig['reason']}. "
                 f"손절 {sig['stop']:,.0f} / 목표 {sig['target']:,.0f}")
        self.rt.notify(f"[단타] {res['name']} {qty}주 매수 {fill:,.0f}원")
        return True

    def tick(self):
        s = self.rt.settings.all()
        state = s["scalp_state"]
        if state == "stopped" or self.rt.brokers.get(ENGINE) is None:
            return
        feed = self.rt.feed
        if not feed.market_open():
            return
        now = feed.now()
        session, t = now.date().isoformat(), now.strftime("%H:%M")
        opens = self.open_trades()
        # 지난 장에서 남은 포지션은 바로 정리
        for tr in [x for x in opens if x["session"] != session]:
            self._safe(self.close, tr, "이월 청산")
        opens = [x for x in opens if x["session"] == session]
        # 보유 종목 청산 확인
        for tr in opens:
            try:
                px = feed.quote(tr["code"])["price"]
                pos = {"entry": tr["entry_price"], "stop": tr["stop"], "stop0": tr["stop0"],
                       "target": tr["target"], "entry_t": tr["entry_ts"][11:16]}
                hit = check_exit(pos, px, px, px, t, s["exit"], s["risk"])
                if hit:
                    self.close(tr, hit[0])
                elif pos["stop"] != tr["stop"]:
                    self.rt.db.x("UPDATE trades SET stop=? WHERE id=?", (pos["stop"], tr["id"]))
                    self.log(f"{tr['name']} 손절가를 본전 {pos['stop']:,.0f}원으로 올렸어요")
            except Exception as e:
                self._error(f"{tr['name']} 청산 확인 실패: {e}")
        # 새 진입(1분에 한 번, 봉이 완성된 뒤)
        if state == "running" and t != self.last_eval:
            self.last_eval = t
            self._scan(s, now, session, t)
        if self.rt.brokers[ENGINE].mode != "paper" and time.time() - self.last_reconcile > 60:
            self.last_reconcile = time.time()
            self._safe(self._reconcile, now)

    def _scan(self, s, now, session, t):
        closed = self.closed_today(session)
        opens = [x for x in self.open_trades() if x["session"] == session]
        traded = {r["code"] for r in self.rt.db.q("SELECT code FROM trades WHERE engine=? AND session=?",
                                                  (ENGINE, session))}
        self.gate_msg = ""
        for code in s["watchlist"]:
            if code in traded:
                continue
            ok, why = entry_gate(t, closed, len(opens), s["risk"])
            if not ok:
                self.gate_msg = why
                return
            try:
                bars = self.rt.feed.bars(code)
                if not bars:
                    continue
                sig = signal(bars, s["strategy"])
                if not sig:
                    continue
                good, gain, need = edge_ok(sig, s["risk"], s["costs"])
                if not good:
                    if (session, code) not in self.skip_logged:
                        self.skip_logged.add((session, code))
                        self.log(f"{self.rt.feed.name(code)} 신호 무시: 목표 {gain:.2f}%가 비용 기준 {need:.2f}%보다 "
                                 f"작아요(오늘 같은 종목은 다시 알리지 않아요)")
                    continue
                if self.open(code, sig, now, session):
                    opens.append({"code": code})
                    traded.add(code)
            except Exception as e:
                self._error(f"{code} 확인 실패: {e}")

    def _reconcile(self, now):
        """키움 주문은 체결 결과를 잔고로 확인한다. 2분이 지나도 잔고에 없으면 기록을 닫는다."""
        held = {h["code"]: h for h in self.rt.brokers[ENGINE].positions()}
        for tr in self.open_trades():
            age = (now - datetime.fromisoformat(tr["entry_ts"])).total_seconds()
            h = held.get(tr["code"])
            if h is None and age > 120:
                self.rt.db.x("UPDATE trades SET exit_ts=?, reason=?, pnl=0, fees=0 WHERE id=?",
                             (now.isoformat(timespec="seconds"), "잔고에 없음", tr["id"]))
                self.log(f"{tr['name']} 매수 기록이 있지만 잔고에 없어요. 키움 체결 내역을 확인해 주세요", "warn")
            elif h and h["qty"] != tr["qty"]:
                self.rt.db.x("UPDATE trades SET qty=?, entry_price=? WHERE id=?", (h["qty"], h["avg_price"], tr["id"]))
                self.log(f"{tr['name']} 수량을 잔고 기준 {h['qty']}주로 맞췄어요", "warn")

    def _safe(self, fn, *a):
        try:
            fn(*a)
        except Exception as e:
            self._error(str(e))

    def _error(self, msg):
        if msg != self.last_error:
            self.log(msg, "error")
            self.last_error = msg

    # ---------- 조작 ----------
    def control(self, action):
        if action == "start":
            if self.rt.brokers.get(ENGINE) is None:
                raise RuntimeError(self.rt.errors.get(ENGINE) or "주문 계좌가 준비되지 않았어요")
            self.rt.settings.update({"scalp_state": "running"})
            self.log("엔진을 시작했어요")
        elif action == "pause":
            self.rt.settings.update({"scalp_state": "paused"})
            self.log("새 매수를 멈췄어요. 보유 종목은 계속 관리해요")
        elif action == "stop":
            fails = 0
            for tr in self.open_trades():
                try:
                    self.close(tr, "수동 정지")
                except Exception as e:
                    fails += 1
                    self._error(f"{tr['name']} 매도 실패: {e}")
            self.rt.settings.update({"scalp_state": "stopped" if not fails else "paused"})
            self.log("엔진을 멈췄어요" if not fails else "일부 종목을 팔지 못해 '새 매수 멈춤' 상태로 두었어요",
                     "info" if not fails else "warn")
        else:
            raise ValueError("알 수 없는 동작이에요")

    # ---------- 화면용 ----------
    def status(self):
        s = self.rt.settings.all()
        feed = self.rt.feed
        now = feed.now()
        session = now.date().isoformat()
        b = self.rt.brokers.get(ENGINE)
        trades = self.rt.db.q("SELECT * FROM trades WHERE engine=? AND session=? ORDER BY id DESC", (ENGINE, session))
        closed = [x for x in trades if x["exit_ts"]]
        opens = []
        for tr in self.open_trades():
            try:
                px = feed.quote(tr["code"])["price"]
            except Exception:
                px = tr["entry_price"]
            tr["price"] = px
            tr["unrealized"] = int((px - tr["entry_price"]) * tr["qty"])
            tr["unrealized_pct"] = round((px / tr["entry_price"] - 1) * 100, 2)
            opens.append(tr)
        wins = [x for x in closed if (x["pnl"] or 0) > 0]
        hist = self.rt.db.q("SELECT session, SUM(COALESCE(pnl,0)) AS pnl, COUNT(*) AS n FROM trades "
                            "WHERE engine=? AND exit_ts IS NOT NULL GROUP BY session ORDER BY session DESC LIMIT 20",
                            (ENGINE,))
        hist.reverse()
        realized = sum(x["pnl"] or 0 for x in closed)
        return {
            "state": s["scalp_state"], "mode": b.mode if b else None, "mode_label": b.label if b else None,
            "error": self.rt.errors.get(ENGINE), "data_source": feed.kind,
            "now": now.isoformat(timespec="minutes"), "market_open": feed.market_open(),
            "strategy": s["strategy"]["name"], "strategy_label": STRATEGIES[s["strategy"]["name"]]["label"],
            "today": {"realized": realized, "closed": len(closed), "wins": len(wins),
                      "losses": len(closed) - len(wins),
                      "win_rate": round(len(wins) / len(closed) * 100) if closed else None,
                      "loss_left": int(s["risk"]["daily_loss_krw"] + min(0, realized)),
                      "daily_loss_krw": s["risk"]["daily_loss_krw"]},
            "gate": self.gate_msg, "open": opens, "trades": closed[:30], "history": hist,
            "log": self.rt.db.q("SELECT ts, level, msg FROM engine_log WHERE engine=? ORDER BY id DESC LIMIT 40",
                                (ENGINE,)),
        }
