"""주문 실행. 엔진(scalp/dca)마다 브로커 하나.

PaperBroker  : 앱 안에서 현재가에 슬리피지를 반영해 즉시 체결. 돈은 DB의 가상 계좌.
KiwoomBroker : 키움으로 주문 전송(모의투자 서버 또는 실전 서버).
"""
from ..trading import costs as C


class OrderError(RuntimeError):
    pass


class PaperBroker:
    def __init__(self, db, engine, feed, costs, start_cash):
        self.db, self.engine, self.feed, self.costs = db, engine, feed, costs
        self.mode = "paper"
        self.label = "앱 내 모의"
        if not db.one("SELECT 1 AS x FROM paper_accounts WHERE engine=?", (engine,)):
            db.x("INSERT INTO paper_accounts(engine,cash) VALUES(?,?)", (engine, int(start_cash)))

    def cash(self):
        return float(self.db.one("SELECT cash FROM paper_accounts WHERE engine=?", (self.engine,))["cash"])

    def positions(self):
        rows = self.db.q("SELECT code,name,qty,avg_price FROM paper_positions WHERE engine=? ORDER BY code",
                         (self.engine,))
        for r in rows:
            try:
                r["price"] = self.feed.quote(r["code"])["price"]
            except Exception:
                r["price"] = r["avg_price"]
        return rows

    def reset(self, cash):
        self.db.x("DELETE FROM paper_positions WHERE engine=?", (self.engine,))
        self.db.x("UPDATE paper_accounts SET cash=? WHERE engine=?", (int(cash), self.engine))

    def buy(self, code, qty, price=None):
        q = self.feed.quote(code)
        if price is not None and price < q["price"]:
            raise OrderError("앱 내 모의는 바로 체결되는 주문만 받아요. 지정가를 현재가 이상으로 넣어 주세요")
        fill = C.buy_fill(q["price"], self.costs)
        amt = fill * qty
        fee = C.buy_fee(amt, self.costs)
        if amt + fee > self.cash():
            raise OrderError("주문 가능 금액이 모자라요")
        with self.db.lock:
            self.db.x("UPDATE paper_accounts SET cash=cash-? WHERE engine=?", (int(round(amt + fee)), self.engine))
            row = self.db.one("SELECT qty,avg_price FROM paper_positions WHERE engine=? AND code=?", (self.engine, code))
            if row:
                nq = row["qty"] + qty
                avg = (row["qty"] * row["avg_price"] + amt) / nq
                self.db.x("UPDATE paper_positions SET qty=?,avg_price=? WHERE engine=? AND code=?",
                          (nq, avg, self.engine, code))
            else:
                self.db.x("INSERT INTO paper_positions(engine,code,name,qty,avg_price) VALUES(?,?,?,?,?)",
                          (self.engine, code, q["name"], qty, fill))
        return {"status": "filled", "fill_price": fill, "broker_no": "", "name": q["name"]}

    def sell(self, code, qty, price=None):
        row = self.db.one("SELECT qty FROM paper_positions WHERE engine=? AND code=?", (self.engine, code))
        if not row or row["qty"] < qty:
            raise OrderError("보유 수량보다 많이 팔 수 없어요")
        q = self.feed.quote(code)
        if price is not None and price > q["price"]:
            raise OrderError("앱 내 모의는 바로 체결되는 주문만 받아요. 지정가를 현재가 이하로 넣어 주세요")
        fill = C.sell_fill(q["price"], self.costs)
        amt = fill * qty
        fee = C.sell_fee(amt, self.costs)
        with self.db.lock:
            self.db.x("UPDATE paper_accounts SET cash=cash+? WHERE engine=?", (int(round(amt - fee)), self.engine))
            if row["qty"] == qty:
                self.db.x("DELETE FROM paper_positions WHERE engine=? AND code=?", (self.engine, code))
            else:
                self.db.x("UPDATE paper_positions SET qty=qty-? WHERE engine=? AND code=?", (qty, self.engine, code))
        return {"status": "filled", "fill_price": fill, "broker_no": "", "name": q["name"]}


class KiwoomBroker:
    def __init__(self, client, feed):
        self.client, self.feed = client, feed
        self.mode = "kiwoom-mock" if client.mock else "kiwoom-real"
        self.label = "키움 모의투자" if client.mock else "키움 실전"

    def cash(self):
        return self.client.cash()

    def positions(self):
        return self.client.holdings()

    def buy(self, code, qty, price=None):
        no = self.client.order("buy", code, qty, price)
        est = price or self.feed.quote(code)["price"]
        return {"status": "accepted", "fill_price": est, "broker_no": no, "name": self.feed.name(code)}

    def sell(self, code, qty, price=None):
        no = self.client.order("sell", code, qty, price)
        est = price or self.feed.quote(code)["price"]
        return {"status": "accepted", "fill_price": est, "broker_no": no, "name": self.feed.name(code)}
