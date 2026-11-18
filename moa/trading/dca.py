"""적립식 장기투자 엔진.

- 플랜마다 매달 정한 날(1~28일)에 정한 금액만큼 산다. 휴장일이면 다음 영업일에 산다.
- PC가 꺼져 있어서 놓친 매수는 7일 안이면 다음에 켜졌을 때 산다.
- 비중 맞추기(rebalance) 방식은 그날 살 돈 전체를 목표 비중에서 모자란 종목부터 나눠 산다.
"""
import math
from datetime import date, datetime, timedelta

from . import costs as C

ENGINE = "dca"
CATCH_UP_DAYS = 7


def is_business_day(d, holidays):
    return d.weekday() < 5 and d.isoformat() not in holidays


def effective_date(y, m, day, holidays):
    d = date(y, m, day)
    while not is_business_day(d, holidays):
        d += timedelta(days=1)
    return d


def _months_back(today, n):
    y, m = today.year, today.month
    for _ in range(n):
        yield y, m
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)


def due_list(plans, today, done, holidays):
    """오늘 사야 하는 (플랜, 예정일) 목록. done: {(plan_id, 'YYYY-MM-DD')}"""
    out = []
    for p in plans:
        if not p["enabled"]:
            continue
        created = datetime.fromisoformat(p["created_at"]).date()
        for y, m in _months_back(today, 2):
            eff = effective_date(y, m, p["day"], holidays)
            if eff > today or eff < created or (today - eff).days > CATCH_UP_DAYS:
                continue
            if (p["id"], eff.isoformat()) not in done:
                out.append((p, eff))
    return out


def next_dates(plan, today, holidays, n=3):
    out, y, m = [], today.year, today.month
    while len(out) < n:
        eff = effective_date(y, m, plan["day"], holidays)
        if eff >= today:
            out.append(eff)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def allocate(plans, values, mode):
    """플랜별 이번에 쓸 금액. values: {종목: 현재 평가금}"""
    total = sum(p["amount"] for p in plans)
    ws = [max(0.0, float(p["target_weight"] or 0)) for p in plans]
    if mode != "rebalance" or sum(ws) <= 0:
        return {p["id"]: p["amount"] for p in plans}
    wsum = sum(ws)
    ws = [w / wsum for w in ws]
    cur = [values.get(p["code"], 0) for p in plans]
    after = sum(cur) + total
    need = [max(0.0, w * after - c) for w, c in zip(ws, cur)]
    base = need if sum(need) > 0 else ws
    s = sum(base)
    alloc = [int(total * x / s) for x in base]
    alloc[max(range(len(alloc)), key=lambda i: base[i])] += total - sum(alloc)
    return {p["id"]: a for p, a in zip(plans, alloc)}


def split_amounts(total, weights, unit=1000):
    """총액을 비중대로 나눈다. unit(원) 단위로 내림하고 남은 돈은 비중이 가장 큰 쪽에 붙여 합이 총액과 같게 한다."""
    total = int(total // unit * unit)
    ws = [max(0.0, float(w or 0)) for w in weights]
    if total <= 0 or sum(ws) <= 0:
        return [0] * len(ws)
    out = [int(total * w / sum(ws) // unit * unit) for w in ws]
    out[max(range(len(ws)), key=lambda i: ws[i])] += total - sum(out)
    return out


def clean_code(v):
    return "".join(ch for ch in str(v or "") if ch.isalnum()).upper()


class DcaEngine:
    def __init__(self, rt):
        self.rt = rt

    # ---------- 다른 화면(배당·AI·종목)과 잇기 ----------
    def brief(self):
        """다른 화면에서 '이미 적립 중인지'만 가볍게 볼 때 쓴다(잔고·시세를 묻지 않는다)."""
        plans = [{k: p[k] for k in ("id", "code", "name", "amount", "day", "target_weight", "enabled")} for p in self.plans()]
        b = self.rt.brokers.get(ENGINE)
        return {"plans": plans, "monthly": sum(p["amount"] for p in plans if p["enabled"]), "auto": self.rt.settings.get("dca_auto"),
                "mode_label": b.label if b else None}

    def _unit(self, code, costs):
        """1주를 사는 데 드는 돈(호가 한 칸 불리하게 + 수수료). 시세를 못 받으면 None."""
        try:
            price = float(self.rt.feed.quote(code)["price"])
        except Exception:
            return None, None
        return price, C.buy_fill(price, costs) * (1 + costs["commission"])

    def draft(self, d):
        """구성(종목·비중)과 매달 넣을 돈을 받아 적립 플랜 초안을 만든다. 저장하지 않는다.
        국내 종목만 적립할 수 있어서 미국 종목은 따로 알려 주고, spread가 참이면 그 몫까지 국내 종목에 나눈다."""
        s = self.rt.settings.all()
        try:
            total = int(float(str(d.get("total") or 0).replace(",", "")))
        except ValueError:
            raise ValueError("매달 넣을 금액은 숫자로 넣어 주세요")
        if total < 1000:
            raise ValueError("매달 넣을 금액을 1,000원 이상으로 넣어 주세요")
        kr, us, seen = [], [], set()
        for a in (d.get("assets") or [])[:30]:
            try:
                w = float(a.get("weight") or 0)
            except (TypeError, ValueError):
                w = 0
            if w <= 0 or not a.get("sym"):
                continue
            code = clean_code(a["sym"])
            if a.get("mkt") == "kr" and len(code) == 6:
                if code in seen:
                    continue
                seen.add(code)
                kr.append({"code": code, "name": str(a.get("name") or "")[:40], "w": w})
            else:
                us.append({"sym": str(a["sym"])[:12], "name": str(a.get("name") or a["sym"])[:40], "w": w})
        tw = sum(x["w"] for x in kr) + sum(x["w"] for x in us)
        if not tw:
            raise ValueError("구성에 종목이 없어요")
        kr_share = sum(x["w"] for x in kr) / tw
        spread = bool(d.get("spread"))
        kr_total = total if spread else int(total * kr_share // 1000 * 1000)
        amounts = split_amounts(kr_total, [x["w"] for x in kr]) if kr else []
        mine = {}
        for p in self.plans():
            mine.setdefault(p["code"], p)
        rows = []
        for x, amt in zip(kr, amounts):
            price, unit = self._unit(x["code"], s["costs"])
            name = x["name"]
            if not name:
                try:
                    name = self.rt.feed.name(x["code"])
                except Exception:
                    name = x["code"]
            p = mine.get(x["code"])
            rows.append({"code": x["code"], "name": name, "weight": round(x["w"] / (kr_share * tw) * 100, 1), "amount": amt,
                         "price": price, "unit": None if unit is None else int(math.ceil(unit)),
                         "enough": None if unit is None else amt >= unit,
                         "plan": {"id": p["id"], "amount": p["amount"], "day": p["day"], "enabled": bool(p["enabled"])} if p else None})
        codes = {r["code"] for r in rows}
        others = [p for p in self.plans() if p["code"] not in codes]
        b = self.rt.brokers.get(ENGINE)
        return {"total": total, "kr_total": kr_total, "kr_share": round(kr_share * 100, 1), "spread": spread, "rows": rows,
                "us": [{"sym": x["sym"], "name": x["name"], "weight": round(x["w"] / tw * 100, 1),
                        "amount": int(total * x["w"] / tw // 1000 * 1000)} for x in us],
                "others": len(others), "others_monthly": sum(p["amount"] for p in others if p["enabled"]),
                "auto": s["dca_auto"], "mode": b.mode if b else None, "mode_label": b.label if b else None,
                "cash": self._cash(b), "error": self.rt.errors.get(ENGINE)}

    @staticmethod
    def _cash(b):
        try:
            return None if b is None else b.cash()
        except Exception:
            return None

    def save_many(self, d):
        """초안을 플랜으로 저장한다. 같은 종목 플랜이 이미 있으면 새로 만들지 않고 금액·매수일을 바꾼다.
        하나라도 값이 잘못되면 아무것도 저장하지 않는다. 주문은 하지 않는다(매수일에 자동 매수가 산다)."""
        items = [x for x in (d.get("items") or []) if x][:30]
        if not items:
            raise ValueError("저장할 플랜이 없어요")
        try:
            day = int(d.get("day") or 0)
        except (TypeError, ValueError):
            day = 0
        if not 1 <= day <= 28:
            raise ValueError("매수일은 1일부터 28일 사이로 골라 주세요")
        ready, seen = [], set()
        for x in items:
            code = clean_code(x.get("code"))
            if len(code) != 6:
                raise ValueError(f"{x.get('name') or x.get('code') or '종목'}: 국내 종목코드 6자리만 적립할 수 있어요")
            if code in seen:
                raise ValueError(f"{x.get('name') or code}: 같은 종목이 두 번 들어 있어요")
            seen.add(code)
            try:
                amount = int(float(str(x.get("amount") or 0).replace(",", "")))
                weight = max(0.0, float(x.get("target_weight") or 0))
            except (TypeError, ValueError):
                raise ValueError(f"{x.get('name') or code}: 금액은 숫자로 넣어 주세요")
            if amount < 1000:
                raise ValueError(f"{x.get('name') or code}: 매달 금액은 1,000원 이상이어야 해요")
            ready.append((code, str(x.get("name") or "").strip()[:40], amount, weight))
        mine = {}
        for p in self.plans():
            mine.setdefault(p["code"], p)
        set_w = bool(d.get("set_weight"))
        made, changed, ids = 0, 0, []
        for code, name, amount, weight in ready:
            p = mine.get(code)
            row = {"id": p["id"] if p else None, "code": code, "name": name or (p["name"] if p else ""), "amount": amount, "day": day,
                   "target_weight": weight if set_w else (p["target_weight"] if p else 0), "enabled": True}
            ids.append(self.save_plan(row))
            made, changed = made + (0 if p else 1), changed + (1 if p else 0)
        self.log(f"다른 화면의 구성으로 플랜 {made}개 만들고 {changed}개 바꿈(매월 {day}일)")
        return {"created": made, "updated": changed, "ids": ids, "monthly": sum(p["amount"] for p in self.plans() if p["enabled"])}

    def log(self, msg, level="info"):
        self.rt.db.log(ENGINE, level, msg, datetime.now().isoformat(timespec="seconds"))

    def plans(self):
        return self.rt.db.q("SELECT * FROM dca_plans ORDER BY id")

    def save_plan(self, d):
        code = clean_code(d.get("code"))
        if len(code) != 6:
            raise ValueError("종목코드 6자리를 넣어 주세요")
        try:
            amount = int(str(d.get("amount", "")).replace(",", ""))
            day = int(d.get("day", 0))
            weight = float(d.get("target_weight") or 0)
        except ValueError:
            raise ValueError("금액·매수일·비중은 숫자로 넣어 주세요")
        if amount < 1000:
            raise ValueError("매달 금액은 1,000원 이상이어야 해요")
        if not 1 <= day <= 28:
            raise ValueError("매수일은 1일부터 28일 사이로 골라 주세요")
        name = (d.get("name") or "").strip()
        if not name:
            try:
                name = self.rt.feed.name(code)
            except Exception:
                name = code
        enabled = 1 if d.get("enabled", True) else 0
        if d.get("id"):
            self.rt.db.x("UPDATE dca_plans SET code=?,name=?,amount=?,day=?,target_weight=?,enabled=? WHERE id=?",
                         (code, name, amount, day, weight, enabled, int(d["id"])))
            return int(d["id"])
        return self.rt.db.x("INSERT INTO dca_plans(code,name,amount,day,target_weight,enabled,created_at) "
                            "VALUES(?,?,?,?,?,?,?)", (code, name, amount, day, weight, enabled,
                                                     datetime.now().isoformat(timespec="seconds")))

    def delete_plan(self, pid):
        self.rt.db.x("DELETE FROM dca_plans WHERE id=?", (pid,))

    def set_enabled(self, pid, on):
        self.rt.db.x("UPDATE dca_plans SET enabled=? WHERE id=?", (1 if on else 0, pid))

    def _done(self):
        return {(r["plan_id"], r["due_date"]) for r in self.rt.db.q("SELECT plan_id, due_date FROM dca_runs")}

    def run_due(self, now=None, force_plan_id=None):
        s = self.rt.settings.all()
        b = self.rt.brokers.get(ENGINE)
        now = now or datetime.now()
        today = now.date()
        if force_plan_id is not None:
            p = self.rt.db.one("SELECT * FROM dca_plans WHERE id=?", (force_plan_id,))
            if not p:
                raise ValueError("플랜을 찾지 못했어요")
            due = [(p, None)]
        else:
            if not s["dca_auto"] or b is None:
                return []
            if not is_business_day(today, s["holidays"]):
                return []
            hhmm = now.strftime("%H:%M")
            if not s["dca_window"]["start"] <= hhmm <= s["dca_window"]["end"]:
                return []
            due = due_list(self.plans(), today, self._done(), s["holidays"])
            if not due:
                return []
        if b is None:
            raise RuntimeError(self.rt.errors.get(ENGINE) or "주문 계좌가 준비되지 않았어요")
        values = {}
        try:
            for h in b.positions():
                values[h["code"]] = values.get(h["code"], 0) + h["qty"] * h["price"]
        except Exception as e:
            self.log(f"잔고 조회 실패: {e}", "warn")
        alloc = allocate([p for p, _ in due], values, s["dca_mode"])
        results = []
        for p, eff in due:
            key = eff.isoformat() if eff else f"{today.isoformat()}m{now.strftime('%H%M%S')}"
            budget = alloc[p["id"]]
            status, qty, price, amount, msg = "failed", 0, None, 0, ""
            try:
                price = self.rt.feed.quote(p["code"])["price"]
                unit = C.buy_fill(price, s["costs"]) * (1 + s["costs"]["commission"])
                qty = math.floor(budget / unit) if unit else 0
                if qty <= 0:
                    status, msg = "skipped", f"{budget:,}원으로는 1주({price:,.0f}원)를 살 수 없어요"
                else:
                    res = b.buy(p["code"], qty)
                    price = res["fill_price"]
                    amount = int(round(price * qty))
                    status = "done"
                    msg = "늦게 매수(예정일 " + eff.isoformat() + ")" if eff and eff < today else ""
                    self.rt.db.x("INSERT INTO orders(ts,engine,mode,code,name,side,qty,price,status,fill_price,"
                                 "broker_no,msg) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (now.isoformat(timespec="seconds"), ENGINE, b.mode, p["code"], p["name"], "buy",
                                  qty, None, res["status"], price, res.get("broker_no", ""), "적립 매수"))
            except Exception as e:
                msg = str(e)
            self.rt.db.x("INSERT OR IGNORE INTO dca_runs(plan_id,due_date,run_ts,qty,price,amount,status,msg) "
                         "VALUES(?,?,?,?,?,?,?,?)", (p["id"], key, now.isoformat(timespec="seconds"),
                                                     qty, price, amount, status, msg))
            text = {"done": f"{p['name']} {qty}주 매수 {amount:,}원",
                    "skipped": f"{p['name']} 매수 건너뜀: {msg}",
                    "failed": f"{p['name']} 매수 실패: {msg}"}[status]
            self.log(text, "info" if status == "done" else "warn")
            self.rt.notify(f"[적립] {text}")
            results.append({"plan_id": p["id"], "status": status, "qty": qty, "amount": amount, "msg": msg})
        return results

    def snapshot(self, today=None):
        """하루 한 번 평가금 기록(추이 그래프용)."""
        today = (today or date.today()).isoformat()
        hist = self.rt.db.get_setting("dca_history", [])
        if hist and hist[-1]["date"] == today:
            return
        b = self.rt.brokers.get(ENGINE)
        if b is None:
            return
        pos = b.positions()
        if not pos:
            return
        cost = int(sum(h["qty"] * h["avg_price"] for h in pos))
        value = int(sum(h["qty"] * h["price"] for h in pos))
        hist.append({"date": today, "cost": cost, "value": value})
        self.rt.db.set_setting("dca_history", hist[-400:])

    def status(self):
        s = self.rt.settings.all()
        b = self.rt.brokers.get(ENGINE)
        today = date.today()
        plans = self.plans()
        holdings, cash = [], None
        if b is not None:
            try:
                holdings = b.positions()
                cash = b.cash()
            except Exception as e:
                self.rt.errors[ENGINE] = f"잔고 조회 실패: {e}"
        by_code = {}
        for h in holdings:
            by_code[h["code"]] = h
        cost = sum(h["qty"] * h["avg_price"] for h in holdings)
        value = sum(h["qty"] * h["price"] for h in holdings)
        wsum = sum(max(0, p["target_weight"]) for p in plans if p["enabled"])
        for p in plans:
            h = by_code.get(p["code"])
            p["value"] = int(h["qty"] * h["price"]) if h else 0
            p["cost"] = int(h["qty"] * h["avg_price"]) if h else 0
            p["gain_pct"] = round((p["value"] / p["cost"] - 1) * 100, 1) if p["cost"] else None
            p["weight_now"] = round(p["value"] / value * 100, 1) if value else 0
            p["weight_goal"] = round(p["target_weight"] / wsum * 100, 1) if wsum and p["enabled"] else 0
            p["next"] = [d.isoformat() for d in next_dates(p, today, s["holidays"], 1)]
        upcoming = {}
        for p in plans:
            if not p["enabled"]:
                continue
            for d in next_dates(p, today, s["holidays"], 3):
                upcoming.setdefault(d.isoformat(), 0)
                upcoming[d.isoformat()] += p["amount"]
        sched = [{"date": d, "amount": a} for d, a in sorted(upcoming.items())][:3]
        runs = self.rt.db.q("SELECT r.*, p.name FROM dca_runs r LEFT JOIN dca_plans p ON p.id=r.plan_id "
                            "ORDER BY r.id DESC LIMIT 20")
        return {
            "mode": b.mode if b else None, "mode_label": b.label if b else None,
            "error": self.rt.errors.get(ENGINE), "auto": s["dca_auto"], "rebalance": s["dca_mode"],
            "plans": plans, "monthly": sum(p["amount"] for p in plans if p["enabled"]),
            "cost": int(cost), "value": int(value), "gain": int(value - cost),
            "gain_pct": round((value / cost - 1) * 100, 2) if cost else None,
            "cash": cash, "schedule": sched, "runs": runs,
            "history": self.rt.db.get_setting("dca_history", [])[-365:],
            "holdings": holdings,
        }
