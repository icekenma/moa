"""갚기 목표: 정한 날까지 빚을 얼마 갚을지 정하고, 갚은 돈을 기록해 속도를 본다.
금액·기한은 사용자가 넣는다(코드에 개인 정보는 없다). 진행은 '갚은 기록'의 합으로만 계산한다."""
from datetime import date, datetime

SOURCES = {"save": "저축·월급", "invest": "투자 수익", "dividend": "배당", "etc": "기타"}


def _months(a, b):
    """a에서 b까지의 달 수(소수). b가 앞이면 0."""
    return max(0.0, (b.year - a.year) * 12 + (b.month - a.month) + (b.day - a.day) / 30.0)


class Payoff:
    def __init__(self, rt):
        self.rt = rt
        rt.db.x("""CREATE TABLE IF NOT EXISTS payoff_log(id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, amount INTEGER NOT NULL,
            src TEXT NOT NULL DEFAULT 'save', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)""")

    def goal(self):
        g = self.rt.settings.get("payoff") or {}
        return g if g.get("amount") else None

    def set_goal(self, d):
        if d.get("clear"):
            self.rt.settings.update({"payoff": {"name": "", "amount": 0, "start": "", "deadline": ""}})
            return self.state()
        try:
            amount = int(float(str(d.get("amount") or 0).replace(",", "")))
            deadline = date.fromisoformat(str(d.get("deadline") or ""))
            start = date.fromisoformat(str(d.get("start") or date.today().isoformat()))
        except ValueError:
            raise ValueError("금액과 날짜를 확인해 주세요")
        if amount < 10_000:
            raise ValueError("갚을 금액을 넣어 주세요")
        if deadline <= start:
            raise ValueError("끝나는 날은 시작일 뒤여야 해요")
        self.rt.settings.update({"payoff": {"name": str(d.get("name") or "").strip()[:20] or "갚기 목표", "amount": amount,
                                            "start": start.isoformat(), "deadline": deadline.isoformat()}})
        return self.state()

    def add(self, d):
        if not self.goal():
            raise ValueError("먼저 갚기 목표를 정해 주세요")
        try:
            amount = int(float(str(d.get("amount") or 0).replace(",", "")))
            day = date.fromisoformat(str(d.get("date") or date.today().isoformat()))
        except ValueError:
            raise ValueError("금액과 날짜를 확인해 주세요")
        if amount <= 0:
            raise ValueError("갚은 금액을 넣어 주세요")
        src = d.get("src") if d.get("src") in SOURCES else "save"
        self.rt.db.x("INSERT INTO payoff_log(date,amount,src,note,created_at) VALUES(?,?,?,?,?)",
                     (day.isoformat(), amount, src, str(d.get("note") or "").strip()[:40], datetime.now().isoformat(timespec="seconds")))
        return self.state()

    def delete(self, lid):
        self.rt.db.x("DELETE FROM payoff_log WHERE id=?", (lid,))
        return self.state()

    def hints(self, today):
        """이번 달에 보탤 수 있는 돈의 실마리(기록은 사용자가 한다)."""
        out = []
        try:
            s = self.rt.ledger.summary(today.strftime("%Y-%m"), today)
            left = int((s.get("income") or 0) - (s.get("expense") or 0))
            out.append({"key": "save", "label": "이번 달 가계부에 남은 돈", "amount": left, "sub": "수입에서 지출을 뺀 값"})
        except Exception:
            pass
        try:
            net = n = 0
            for r in self.rt.autotrade._runs("WHERE mode='real'"):
                m = self.rt.autotrade._summary(r)["money"]
                fx = self.rt.ai.fx() if r["mkt"] == "us" else 1.0
                net += (m["realized"] - m["ai_cost"]) * fx
                n += 1
            if n:
                out.append({"key": "invest", "label": "실전 자동매매 확정 손익", "amount": int(net), "sub": "지금까지 · AI 비용을 뺀 값"})
        except Exception:
            pass
        return out

    def brief(self):
        """홈 화면용 한 줄 요약. 목표가 없으면 None."""
        try:
            s = self.state()
            return {k: s[k] for k in ("goal", "paid", "left", "pct", "time_pct", "need_month", "text")} if s.get("goal") else None
        except Exception:
            return None

    def state(self, today=None):
        today = today or date.today()
        g = self.goal()
        if not g:
            return {"goal": None, "sources": SOURCES}
        start, end = date.fromisoformat(g["start"]), date.fromisoformat(g["deadline"])
        logs = self.rt.db.q("SELECT id, date, amount, src, note FROM payoff_log WHERE date>=? ORDER BY date DESC, id DESC", (g["start"],))
        paid = sum(r["amount"] for r in logs)
        left = max(0, g["amount"] - paid)
        total_m, gone_m, left_m = _months(start, end), _months(start, min(today, end)), _months(today, end)
        plan_now = round(g["amount"] * (gone_m / total_m)) if total_m else g["amount"]      # 일정대로면 지금까지 갚았어야 할 돈
        avg = paid / gone_m if gone_m >= 1 else None                                         # 한 달 평균(한 달은 지나야 말한다)
        need = round(left / left_m) if left_m > 0 else left
        by = {k: 0 for k in SOURCES}
        for r in logs:
            by[r["src"] if r["src"] in by else "etc"] += r["amount"]
        months = {}
        for r in logs:
            months[r["date"][:7]] = months.get(r["date"][:7], 0) + r["amount"]
        eta = None
        if left == 0:
            eta = "done"
        elif avg and avg > 0:
            m = left / avg
            y, mo = divmod(today.year * 12 + today.month - 1 + int(round(m)), 12)
            eta = f"{y}-{mo + 1:02d}"
        if left == 0:
            text = "목표한 만큼 다 갚았어요."
        elif today > end:
            text = f"기한이 지났어요. {left:,}원이 남았어요."
        elif avg is None:
            text = f"기한에 맞추려면 한 달에 {need:,}원씩 갚아야 해요."
        elif paid >= plan_now:
            text = f"일정보다 {paid - plan_now:,}원 앞서 있어요. 남은 기간은 한 달에 {need:,}원이면 돼요."
        else:
            text = f"일정보다 {plan_now - paid:,}원 뒤처졌어요. 기한에 맞추려면 이제 한 달에 {need:,}원이 필요해요(지금 평균 {round(avg):,}원)."
        return {"goal": g, "sources": SOURCES, "paid": paid, "left": left, "pct": round(paid / g["amount"] * 100, 1), "time_pct": round(gone_m / total_m * 100, 1) if total_m else 100,
                "plan_now": plan_now, "need_month": need, "avg_month": round(avg) if avg is not None else None, "months_left": round(left_m, 1), "eta": eta, "text": text,
                "by": [{"key": k, "label": SOURCES[k], "amount": v} for k, v in by.items() if v], "months": [{"month": k, "amount": v} for k, v in sorted(months.items())][-12:],
                "logs": [dict(r, src_label=SOURCES.get(r["src"], "기타")) for r in logs[:30]], "hints": self.hints(today)}
