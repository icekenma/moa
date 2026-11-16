"""가계부: 내역, 분류·예산, 월 요약."""
import calendar
from datetime import date, datetime


class LedgerError(ValueError):
    pass


def _month_bounds(month):
    y, m = map(int, month.split("-"))
    last = calendar.monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}", last


def validate_tx(d):
    try:
        datetime.strptime(d.get("date", ""), "%Y-%m-%d")
    except ValueError:
        raise LedgerError("날짜는 YYYY-MM-DD 형식으로 넣어 주세요")
    if d.get("kind") not in ("in", "out"):
        raise LedgerError("수입인지 지출인지 골라 주세요")
    try:
        amount = int(str(d.get("amount", "")).replace(",", ""))
    except ValueError:
        raise LedgerError("금액은 숫자로 넣어 주세요")
    if amount <= 0:
        raise LedgerError("금액은 0보다 커야 해요")
    return {
        "date": d["date"], "kind": d["kind"], "amount": amount,
        "category_id": int(d["category_id"]) if d.get("category_id") else None,
        "memo": (d.get("memo") or "").strip()[:100],
        "method": (d.get("method") or "").strip()[:20],
    }


class Ledger:
    def __init__(self, db):
        self.db = db

    def categories(self):
        return self.db.q("SELECT * FROM categories ORDER BY kind DESC, sort, id")

    def save_category(self, d):
        name = (d.get("name") or "").strip()
        if not name:
            raise LedgerError("분류 이름을 넣어 주세요")
        budget = max(0, int(d.get("budget") or 0))
        if d.get("id"):
            self.db.x("UPDATE categories SET name=?, budget=? WHERE id=?", (name, budget, int(d["id"])))
            return int(d["id"])
        kind = d.get("kind") if d.get("kind") in ("in", "out") else "out"
        return self.db.x("INSERT INTO categories(name,kind,budget,sort) VALUES(?,?,?,999)",
                         (name, kind, budget))

    def delete_category(self, cid):
        used = self.db.one("SELECT COUNT(*) AS n FROM transactions WHERE category_id=?", (cid,))
        if used["n"]:
            raise LedgerError(f"내역 {used['n']}건이 이 분류를 쓰고 있어요. 내역의 분류를 먼저 바꿔 주세요")
        self.db.x("DELETE FROM categories WHERE id=?", (cid,))

    def add(self, d):
        t = validate_tx(d)
        return self.db.x(
            "INSERT INTO transactions(date,kind,amount,category_id,memo,method,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (t["date"], t["kind"], t["amount"], t["category_id"], t["memo"], t["method"],
             datetime.now().isoformat(timespec="seconds")))

    def update(self, tid, d):
        t = validate_tx(d)
        self.db.x("UPDATE transactions SET date=?,kind=?,amount=?,category_id=?,memo=?,method=? WHERE id=?",
                  (t["date"], t["kind"], t["amount"], t["category_id"], t["memo"], t["method"], tid))

    def delete(self, tid):
        self.db.x("DELETE FROM transactions WHERE id=?", (tid,))

    def rows(self, month, kind=None, limit=None):
        start, end, _ = _month_bounds(month)
        sql = ("SELECT t.*, c.name AS category FROM transactions t "
               "LEFT JOIN categories c ON c.id=t.category_id WHERE t.date BETWEEN ? AND ?")
        args = [start, end]
        if kind in ("in", "out"):
            sql += " AND t.kind=?"
            args.append(kind)
        sql += " ORDER BY t.date DESC, t.id DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.db.q(sql, args)

    def recent(self, n=5):
        return self.db.q("SELECT t.*, c.name AS category FROM transactions t "
                         "LEFT JOIN categories c ON c.id=t.category_id "
                         "ORDER BY t.date DESC, t.id DESC LIMIT ?", (n,))

    def summary(self, month, today=None):
        today = today or date.today()
        start, end, ndays = _month_bounds(month)
        tot = {r["kind"]: r["s"] for r in self.db.q(
            "SELECT kind, SUM(amount) AS s FROM transactions WHERE date BETWEEN ? AND ? GROUP BY kind",
            (start, end))}
        income, expense = tot.get("in") or 0, tot.get("out") or 0
        cats = self.db.q(
            "SELECT c.id, c.name, c.budget, COALESCE(SUM(t.amount),0) AS used FROM categories c "
            "LEFT JOIN transactions t ON t.category_id=c.id AND t.kind='out' AND t.date BETWEEN ? AND ? "
            "WHERE c.kind='out' GROUP BY c.id ORDER BY c.sort, c.id", (start, end))
        uncategorized = self.db.one(
            "SELECT COALESCE(SUM(amount),0) AS s FROM transactions "
            "WHERE kind='out' AND category_id IS NULL AND date BETWEEN ? AND ?", (start, end))["s"]
        if uncategorized:
            cats.append({"id": None, "name": "분류 없음", "budget": 0, "used": uncategorized})
        budget = sum(c["budget"] for c in cats)
        daily = [0] * ndays
        for r in self.db.q("SELECT date, SUM(amount) AS s FROM transactions "
                           "WHERE kind='out' AND date BETWEEN ? AND ? GROUP BY date", (start, end)):
            daily[int(r["date"][8:]) - 1] = r["s"]
        y, m = map(int, month.split("-"))
        if (today.year, today.month) == (y, m):
            days_left = ndays - today.day + 1
            today_idx = today.day
        elif (today.year, today.month) < (y, m):
            days_left, today_idx = ndays, 0
        else:
            days_left, today_idx = 0, ndays + 1
        remaining = budget - expense
        per_day = remaining // days_left if days_left and remaining > 0 else 0
        return {
            "month": month, "income": income, "expense": expense, "net": income - expense,
            "budget": budget, "remaining": remaining, "days_left": days_left,
            "per_day": per_day, "daily": daily, "today_idx": today_idx,
            "categories": cats,
        }
