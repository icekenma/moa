"""고정 지출·구독(정기 결제)과 카드 실적·청구 관리.

고정 지출
- 매월/매년/매주 정해진 날에 나가는 돈을 등록하면, 그날 들어온 카드·은행 문자와 짝을 맞추고(중복 기록 방지),
  문자가 없으면(현금·자동이체 등) 자동으로 가계부에 기록한다.
- 지난 내역에서 매달 비슷한 날·비슷한 금액으로 반복되는 결제를 찾아 등록을 제안한다.
- 결제 며칠 전 휴대폰 알림(ntfy).

카드
- 카드 문자(카드사·끝 4자리)로 내역을 카드에 연결한다.
- 전월 실적(달력 기준) 진행률, 다음 결제일·청구 예정액(이용기간 기준)을 계산한다.
- 은행 문자의 '카드 대금' 출금은 이미 카드 사용 때 기록했으므로 지출에서 뺀다(이체 처리).
"""
import calendar
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta


class FixedError(ValueError):
    pass


SUB_HINTS = ["넷플릭스", "netflix", "유튜브", "youtube", "디즈니", "disney", "티빙", "tving", "웨이브", "왓챠", "쿠팡와우",
             "와우멤버십", "멜론", "지니", "스포티파이", "spotify", "애플", "apple", "icloud", "구글", "google", "네이버플러스",
             "claude", "anthropic", "openai", "chatgpt", "github", "microsoft", "어도비", "adobe", "skt", "sk텔레콤", "kt",
             "lg유플러스", "u+", "알뜰폰", "보험", "관리비", "월세", "통신", "인터넷", "렌탈", "정수기", "헬스", "학원", "적금"]
CARD_ISSUERS = ["KB국민", "신한", "삼성", "현대", "롯데", "하나", "우리", "BC", "NH농협", "씨티", "IBK", "카카오뱅크", "토스"]
CYCLES = {"monthly": "매월", "yearly": "매년", "weekly": "매주"}
WD = "월화수목금토일"


def _norm(s):
    return re.sub(r"[\s()\[\]·*_\-.,/㈜]|주식회사|\(주\)", "", str(s or "")).lower()


def _key(memo):
    """가맹점 이름을 묶기 위한 키: 숫자·지점명 꼬리를 덜어낸 앞부분."""
    k = re.sub(r"\d+", "", _norm(memo))
    k = re.sub(r"(지점|점|본점|역점)$", "", k)
    return k[:12]


def _day_in(y, m, day):
    return date(y, m, min(int(day), calendar.monthrange(y, m)[1]))


def _add_month(y, m, k):
    m2 = m - 1 + k
    return y + m2 // 12, m2 % 12 + 1


class Fixed:
    def __init__(self, rt):
        self.rt, self.db = rt, rt.db
        db = self.db
        db.x("""CREATE TABLE IF NOT EXISTS recurring(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, amount INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'out',
            category_id INTEGER, method TEXT NOT NULL DEFAULT '', card_id INTEGER, cycle TEXT NOT NULL DEFAULT 'monthly',
            day INTEGER NOT NULL DEFAULT 1, month INTEGER, weekday INTEGER, start_date TEXT NOT NULL, end_date TEXT,
            auto_record INTEGER NOT NULL DEFAULT 1, notify_days INTEGER NOT NULL DEFAULT 1, match TEXT NOT NULL DEFAULT '',
            variable INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)""")
        db.x("""CREATE TABLE IF NOT EXISTS recurring_hits(
            id INTEGER PRIMARY KEY, rec_id INTEGER NOT NULL, due_date TEXT NOT NULL, tx_id INTEGER,
            status TEXT NOT NULL, amount INTEGER, at TEXT NOT NULL, UNIQUE(rec_id, due_date))""")
        db.x("CREATE TABLE IF NOT EXISTS recurring_notified(rec_id INTEGER, due_date TEXT, PRIMARY KEY(rec_id, due_date))")
        db.x("CREATE TABLE IF NOT EXISTS recurring_dismissed(key TEXT PRIMARY KEY)")
        db.x("""CREATE TABLE IF NOT EXISTS cards(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, issuer TEXT NOT NULL DEFAULT '', last4 TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT 'credit', perf_target INTEGER NOT NULL DEFAULT 0, cycle_start INTEGER NOT NULL DEFAULT 1,
            pay_day INTEGER NOT NULL DEFAULT 14, exclude_kw TEXT NOT NULL DEFAULT '', annual_fee INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)""")
        cols = {r["name"] for r in db.q("PRAGMA table_info(transactions)")}
        for c in ("card_id", "recurring_id"):
            if c not in cols:
                db.x(f"ALTER TABLE transactions ADD COLUMN {c} INTEGER")
        icols = {r["name"] for r in db.q("PRAGMA table_info(inbox)")}
        if "card_tail" not in icols:
            db.x("ALTER TABLE inbox ADD COLUMN card_tail TEXT NOT NULL DEFAULT ''")
        self._last_run = None

    # =================================================================
    # 고정 지출
    # =================================================================
    def list(self):
        rows = self.db.q("SELECT r.*, c.name AS category, k.name AS card FROM recurring r "
                         "LEFT JOIN categories c ON c.id=r.category_id LEFT JOIN cards k ON k.id=r.card_id "
                         "ORDER BY r.active DESC, r.cycle, r.day, r.id")
        for r in rows:
            r["when"] = self.when_label(r)
            nxt = self.next_due(r, date.today())
            r["next_due"] = nxt.isoformat() if nxt else None
        return rows

    @staticmethod
    def when_label(r):
        d = "말일" if int(r["day"]) >= 31 else f"{r['day']}일"
        if r["cycle"] == "yearly":
            return f"매년 {r['month']}월 {d}"
        if r["cycle"] == "weekly":
            return f"매주 {WD[int(r['weekday'] or 0)]}요일"
        return f"매월 {d}"

    def save(self, d):
        name = (d.get("name") or "").strip()[:40]
        if not name:
            raise FixedError("이름을 넣어 주세요")
        try:
            amount = int(str(d.get("amount") or 0).replace(",", ""))
        except ValueError:
            raise FixedError("금액은 숫자로 넣어 주세요")
        if amount <= 0:
            raise FixedError("금액을 넣어 주세요")
        cycle = d.get("cycle") if d.get("cycle") in CYCLES else "monthly"
        day = max(1, min(31, int(d.get("day") or 1)))
        month = max(1, min(12, int(d.get("month") or date.today().month))) if cycle == "yearly" else None
        weekday = max(0, min(6, int(d.get("weekday") or 0))) if cycle == "weekly" else None
        start = d.get("start_date") or date.today().isoformat()
        try:
            date.fromisoformat(start)
            end = d.get("end_date") or None
            if end:
                date.fromisoformat(end)
        except ValueError:
            raise FixedError("날짜 형식을 확인해 주세요")
        vals = (name, amount, "in" if d.get("kind") == "in" else "out", d.get("category_id") or None,
                (d.get("method") or "")[:20], d.get("card_id") or None, cycle, day, month, weekday, start, end,
                1 if d.get("auto_record", True) else 0, max(-1, min(7, int(d.get("notify_days", 1)))),
                (d.get("match") or name).strip()[:40], 1 if d.get("variable") else 0, 1 if d.get("active", True) else 0)
        if d.get("id"):
            self.db.x("UPDATE recurring SET name=?,amount=?,kind=?,category_id=?,method=?,card_id=?,cycle=?,day=?,month=?,"
                      "weekday=?,start_date=?,end_date=?,auto_record=?,notify_days=?,match=?,variable=?,active=? WHERE id=?",
                      vals + (int(d["id"]),))
            rid = int(d["id"])
        else:
            rid = self.db.x("INSERT INTO recurring(name,amount,kind,category_id,method,card_id,cycle,day,month,weekday,"
                            "start_date,end_date,auto_record,notify_days,match,variable,active,created_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals + (datetime.now().isoformat(timespec="seconds"),))
        self.run(force=True)
        return rid

    def delete(self, rid):
        self.db.x("DELETE FROM recurring WHERE id=?", (rid,))
        self.db.x("DELETE FROM recurring_hits WHERE rec_id=?", (rid,))
        self.db.x("UPDATE transactions SET recurring_id=NULL WHERE recurring_id=?", (rid,))

    def dues(self, r, start, end):
        """start~end(포함) 사이 결제 예정일."""
        out = []
        s0 = max(start, date.fromisoformat(r["start_date"]))
        e0 = min(end, date.fromisoformat(r["end_date"])) if r.get("end_date") else end
        if s0 > e0:
            return out
        if r["cycle"] == "weekly":
            d = s0 + timedelta(days=(int(r["weekday"] or 0) - s0.weekday()) % 7)
            while d <= e0:
                out.append(d)
                d += timedelta(days=7)
        elif r["cycle"] == "yearly":
            for y in range(s0.year, e0.year + 1):
                d = _day_in(y, int(r["month"] or 1), r["day"])
                if s0 <= d <= e0:
                    out.append(d)
        else:
            y, m = s0.year, s0.month
            while True:
                d = _day_in(y, m, r["day"])
                if d > e0:
                    break
                if d >= s0:
                    out.append(d)
                y, m = _add_month(y, m, 1)
        return out

    def next_due(self, r, today):
        if not r["active"]:
            return None
        ds = self.dues(r, today, today + timedelta(days=400))
        return ds[0] if ds else None

    def _match_tx(self, r, due, window=4):
        lo, hi = (due - timedelta(days=window)).isoformat(), (due + timedelta(days=window)).isoformat()
        tol = 0.3 if r["variable"] else 0.02
        cands = self.db.q("SELECT * FROM transactions WHERE kind=? AND transfer=0 AND date BETWEEN ? AND ? "
                          "AND (recurring_id IS NULL OR recurring_id=?) ORDER BY ABS(julianday(date)-julianday(?))",
                          (r["kind"], lo, hi, r["id"], due.isoformat()))
        key = _norm(r["match"] or r["name"])
        best = None
        for t in cands:
            if abs(t["amount"] - r["amount"]) > max(r["amount"] * tol, 10):
                continue
            name_ok = key and (key in _norm(t["memo"]) or _norm(t["memo"])[:6] in key and len(_norm(t["memo"])) >= 3)
            card_ok = r.get("card_id") and t.get("card_id") == r["card_id"] and t["amount"] == r["amount"]
            if name_ok or card_ok:
                best = t
                break
        return best

    def run(self, today=None, force=False):
        """결제일이 지난 고정 지출을 문자 기록과 짝짓거나, 없으면 자동 기록. 결제 전 알림."""
        today = today or date.today()
        if not force and self._last_run and (datetime.now() - self._last_run).total_seconds() < 300:
            return
        self._last_run = datetime.now()
        for r in self.db.q("SELECT * FROM recurring WHERE active=1"):
            since = date.fromisoformat(r["start_date"])
            since = max(since, today - timedelta(days=62))
            for due in self.dues(r, since, today + timedelta(days=3)):
                hit = self.db.one("SELECT * FROM recurring_hits WHERE rec_id=? AND due_date=?", (r["id"], due.isoformat()))
                if hit and hit["status"] in ("paid", "posted", "skipped"):
                    continue
                t = self._match_tx(r, due)
                if t:
                    self.db.x("UPDATE transactions SET recurring_id=? WHERE id=?", (r["id"], t["id"]))
                    self._hit(r["id"], due, t["id"], "paid", t["amount"])
                    continue
                # 카드로 내는 건 문자가 늦게 올 수 있어 3일 기다린 뒤 기록
                grace = 3 if r.get("card_id") else 0
                if r["auto_record"] and today >= due + timedelta(days=grace):
                    tid = self.rt.ledger.add({"date": due.isoformat(), "kind": r["kind"], "amount": r["amount"],
                                              "category_id": r["category_id"], "memo": r["name"],
                                              "method": r["method"] or ("신용카드" if r.get("card_id") else "자동이체")})
                    self.db.x("UPDATE transactions SET recurring_id=?, card_id=? WHERE id=?", (r["id"], r.get("card_id"), tid))
                    self._hit(r["id"], due, tid, "posted", r["amount"])
        self._notify(today)

    def _hit(self, rid, due, tid, status, amount):
        self.db.x("INSERT INTO recurring_hits(rec_id,due_date,tx_id,status,amount,at) VALUES(?,?,?,?,?,?) "
                  "ON CONFLICT(rec_id,due_date) DO UPDATE SET tx_id=excluded.tx_id, status=excluded.status, amount=excluded.amount",
                  (rid, due.isoformat(), tid, status, amount, datetime.now().isoformat(timespec="seconds")))

    def _notify(self, today):
        if datetime.now().hour < 8:
            return
        for r in self.db.q("SELECT * FROM recurring WHERE active=1 AND notify_days>=0"):
            for due in self.dues(r, today, today + timedelta(days=int(r["notify_days"]))):
                if self.db.one("SELECT 1 AS x FROM recurring_notified WHERE rec_id=? AND due_date=?", (r["id"], due.isoformat())):
                    continue
                left = (due - today).days
                when = "오늘" if left == 0 else "내일" if left == 1 else f"{left}일 뒤"
                self.rt.notify(f"[moa] {when}({due.month}/{due.day}) {r['name']} {r['amount']:,}원 {'입금' if r['kind'] == 'in' else '결제'} 예정")
                self.db.x("INSERT OR IGNORE INTO recurring_notified VALUES(?,?)", (r["id"], due.isoformat()))

    def absorb_sms(self, it):
        """카드·출금 문자가 들어왔을 때: 이미 자동 기록한 고정 지출이면 새로 넣지 말고 그 기록을 쓴다."""
        if it["action"] not in ("spend", "outflow"):
            return None
        d = date.fromisoformat(it["date"])
        for r in self.db.q("SELECT * FROM recurring WHERE active=1 AND kind='out'"):
            tol = 0.3 if r["variable"] else 0.02
            if abs(it["amount"] - r["amount"]) > max(r["amount"] * tol, 10):
                continue
            key = _norm(r["match"] or r["name"])
            if not key or key not in _norm(it.get("merchant")):
                continue
            hit = self.db.one("SELECT * FROM recurring_hits WHERE rec_id=? AND status='posted' AND "
                              "ABS(julianday(due_date)-julianday(?)) <= 5", (r["id"], d.isoformat()))
            if hit and hit["tx_id"]:
                card = self.card_for(it)
                self.db.x("UPDATE transactions SET amount=?, card_id=COALESCE(?, card_id), method=? WHERE id=?",
                          (it["amount"], card["id"] if card else None, "신용카드" if it["kind"] == "card" else "자동이체",
                           hit["tx_id"]))
                self.db.x("UPDATE recurring_hits SET status='paid', amount=? WHERE id=?", (it["amount"], hit["id"]))
                return hit["tx_id"]
        return None

    def summary(self, today=None):
        today = today or date.today()
        first = today.replace(day=1)
        last = _day_in(today.year, today.month, 31)
        items, total, paid = [], 0, 0
        for r in self.db.q("SELECT r.*, c.name AS category FROM recurring r LEFT JOIN categories c ON c.id=r.category_id "
                           "WHERE r.active=1 AND r.kind='out'"):
            for due in self.dues(r, first, last):
                hit = self.db.one("SELECT * FROM recurring_hits WHERE rec_id=? AND due_date=?", (r["id"], due.isoformat()))
                amt = hit["amount"] if hit and hit["amount"] else r["amount"]
                total += amt
                done = bool(hit and hit["status"] in ("paid", "posted"))
                paid += amt if done else 0
        upcoming = []
        for r in self.db.q("SELECT r.*, k.name AS card FROM recurring r LEFT JOIN cards k ON k.id=r.card_id WHERE r.active=1"):
            for due in self.dues(r, today - timedelta(days=7), today + timedelta(days=31)):
                hit = self.db.one("SELECT * FROM recurring_hits WHERE rec_id=? AND due_date=?", (r["id"], due.isoformat()))
                st = hit["status"] if hit else ("due" if due >= today else "late")
                if st in ("paid", "posted") and due < today - timedelta(days=3):
                    continue
                upcoming.append({"id": r["id"], "name": r["name"], "amount": hit["amount"] if hit and hit["amount"] else r["amount"],
                                 "kind": r["kind"], "due": due.isoformat(), "d": (due - today).days, "status": st,
                                 "card": r.get("card"), "auto": r["auto_record"]})
        upcoming.sort(key=lambda x: x["due"])
        yearly = 0
        for r in self.db.q("SELECT * FROM recurring WHERE active=1 AND kind='out'"):
            yearly += r["amount"] * {"monthly": 12, "yearly": 1, "weekly": 52}[r["cycle"]]
        return {"month_total": total, "month_paid": paid, "month_left": total - paid, "yearly": yearly,
                "upcoming": upcoming[:20], "count": self.db.one("SELECT COUNT(*) AS n FROM recurring WHERE active=1")["n"]}

    def suggestions(self, today=None):
        """지난 4개월 내역에서 매달 비슷한 날·금액으로 반복된 지출을 찾는다."""
        today = today or date.today()
        since = (today - timedelta(days=125)).isoformat()
        rows = self.db.q("SELECT * FROM transactions WHERE kind='out' AND transfer=0 AND recurring_id IS NULL "
                         "AND date >= ? AND memo != ''", (since,))
        groups = defaultdict(list)
        for t in rows:
            k = _key(t["memo"])
            if len(k) >= 2:
                groups[k].append(t)
        have = [_norm(r["match"] or r["name"]) for r in self.db.q("SELECT name, match FROM recurring")]
        dismissed = {r["key"] for r in self.db.q("SELECT key FROM recurring_dismissed")}
        out = []
        for k, ts in groups.items():
            if k in dismissed or any(h and (h in k or k in h) for h in have):
                continue
            months = sorted({t["date"][:7] for t in ts})
            amts = sorted(t["amount"] for t in ts)
            med = amts[len(amts) // 2]
            similar = [t for t in ts if abs(t["amount"] - med) <= max(med * 0.15, 100)]
            days = sorted(int(t["date"][8:]) for t in similar)
            hint = any(h in k for h in (_norm(x) for x in SUB_HINTS))
            sim_months = sorted({t["date"][:7] for t in similar})
            spread = (days[-1] - days[0]) if days else 99
            if len(sim_months) >= 2 and spread <= 5:
                conf = "high" if len(sim_months) >= 3 else "mid"
            elif hint and similar:
                conf = "low"
            else:
                continue
            last = max(similar, key=lambda t: t["date"])
            card_ids = Counter(t.get("card_id") for t in similar if t.get("card_id"))
            out.append({"key": k, "name": last["memo"][:30], "amount": med, "day": days[len(days) // 2],
                        "months": len(sim_months), "variable": int(amts[-1] != amts[0]),
                        "category_id": last["category_id"], "method": last["method"],
                        "card_id": card_ids.most_common(1)[0][0] if card_ids else None,
                        "last": last["date"], "confidence": conf, "subscription": hint})
        order = {"high": 0, "mid": 1, "low": 2}
        out.sort(key=lambda x: (order[x["confidence"]], -x["months"], -x["amount"]))
        return out[:12]

    def dismiss(self, key):
        self.db.x("INSERT OR IGNORE INTO recurring_dismissed(key) VALUES(?)", (str(key)[:40],))

    # =================================================================
    # 카드
    # =================================================================
    def cards(self):
        return self.db.q("SELECT * FROM cards ORDER BY active DESC, id")

    def save_card(self, d):
        name = (d.get("name") or "").strip()[:30]
        if not name:
            raise FixedError("카드 이름을 넣어 주세요")
        issuer = (d.get("issuer") or "").strip()[:10]
        last4 = "".join(ch for ch in str(d.get("last4") or "") if ch.isdigit())[-4:]
        vals = (name, issuer, last4, "check" if d.get("kind") == "check" else "credit",
                max(0, int(str(d.get("perf_target") or 0).replace(",", ""))),
                max(1, min(28, int(d.get("cycle_start") or 1))), max(1, min(28, int(d.get("pay_day") or 14))),
                (d.get("exclude_kw") or "").strip()[:200], max(0, int(str(d.get("annual_fee") or 0).replace(",", ""))),
                (d.get("note") or "").strip()[:100], 1 if d.get("active", True) else 0)
        if d.get("id"):
            self.db.x("UPDATE cards SET name=?,issuer=?,last4=?,kind=?,perf_target=?,cycle_start=?,pay_day=?,exclude_kw=?,"
                      "annual_fee=?,note=?,active=? WHERE id=?", vals + (int(d["id"]),))
            cid = int(d["id"])
        else:
            cid = self.db.x("INSERT INTO cards(name,issuer,last4,kind,perf_target,cycle_start,pay_day,exclude_kw,annual_fee,"
                            "note,active,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            vals + (datetime.now().isoformat(timespec="seconds"),))
        self.relink()
        return cid

    def delete_card(self, cid):
        self.db.x("DELETE FROM cards WHERE id=?", (cid,))
        self.db.x("UPDATE transactions SET card_id=NULL WHERE card_id=?", (cid,))
        self.db.x("UPDATE recurring SET card_id=NULL WHERE card_id=?", (cid,))

    def card_for(self, it):
        """문자의 카드사·끝 4자리로 내 카드를 찾는다. 끝 4자리가 맞으면 우선, 아니면 그 카드사 카드가 하나뿐일 때."""
        if it.get("kind") != "card":
            return None
        iss, tail = it.get("issuer") or "", it.get("card_tail") or ""
        cards = [c for c in self.db.q("SELECT * FROM cards WHERE active=1") if c["issuer"] and iss and
                 (c["issuer"] in iss or iss in c["issuer"])]
        if tail:
            for c in cards:
                if c["last4"] and c["last4"] == tail:
                    return c
        no_tail = [c for c in cards if not c["last4"]] or (cards if not tail else [])
        return no_tail[0] if len(no_tail) == 1 else None

    def relink(self):
        """카드를 등록·수정하면 지난 카드 문자 기록도 카드에 연결한다."""
        n = 0
        for it in self.db.q("SELECT * FROM inbox WHERE kind='card' AND tx_id IS NOT NULL"):
            c = self.card_for(it)
            if c:
                self.db.x("UPDATE transactions SET card_id=? WHERE id=? AND (card_id IS NULL OR card_id!=?)", (c["id"], it["tx_id"], c["id"]))
                n += 1
        return n

    @staticmethod
    def billing_window(card, pay_date):
        """결제일 pay_date에 청구되는 이용기간 [시작, 끝]. 이용기간이 끝나고 7일 넘게 지난 첫 결제일이 그 기간의 결제일."""
        s = int(card["cycle_start"])
        # 후보: 결제일 기준 1~2달 전 시작
        for back in (1, 2, 3):
            y, m = _add_month(pay_date.year, pay_date.month, -back)
            start = _day_in(y, m, s)
            y2, m2 = _add_month(y, m, 1)
            end = _day_in(y2, m2, s) - timedelta(days=1)
            if Fixed._pay_after(card, end) == pay_date:
                return start, end
        return None, None

    @staticmethod
    def _pay_after(card, end):
        d = end + timedelta(days=8)
        p = _day_in(d.year, d.month, card["pay_day"])
        if p < d:
            y, m = _add_month(d.year, d.month, 1)
            p = _day_in(y, m, card["pay_day"])
        return p

    def _spend(self, card, start, end):
        """기간 안 카드 사용액(취소 반영). 실적 제외 키워드는 따로 돌려준다."""
        rows = self.db.q("SELECT * FROM transactions WHERE card_id=? AND transfer=0 AND date BETWEEN ? AND ?",
                         (card["id"], start.isoformat(), end.isoformat()))
        ex = [_norm(x) for x in re.split(r"[,\s]+", card["exclude_kw"] or "") if x.strip()]
        total = excl = 0
        for t in rows:
            v = t["amount"] if t["kind"] == "out" else -t["amount"]
            total += v
            if ex and any(k in _norm(t["memo"]) for k in ex):
                excl += v
        return total, total - excl, len(rows)

    def card_status(self, card, today=None):
        today = today or date.today()
        first = today.replace(day=1)
        last = _day_in(today.year, today.month, 31)
        py, pm = _add_month(today.year, today.month, -1)
        pfirst, plast = date(py, pm, 1), _day_in(py, pm, 31)
        _, perf, n = self._spend(card, first, today)
        _, prev_perf, _ = self._spend(card, pfirst, plast)
        target = card["perf_target"]
        days_left = (last - today).days + 1
        need = max(0, target - perf) if target else 0
        # 다음 결제: 오늘 이후 첫 결제일과 그 이용기간
        p = _day_in(today.year, today.month, card["pay_day"])
        if p < today:
            y, m = _add_month(today.year, today.month, 1)
            p = _day_in(y, m, card["pay_day"])
        bills = []
        for k in range(2):
            y, m = _add_month(p.year, p.month, k)
            pd = _day_in(y, m, card["pay_day"])
            s, e = self.billing_window(card, pd)
            if s is None:
                continue
            amt, _, cnt = self._spend(card, s, min(e, today))
            bills.append({"pay_date": pd.isoformat(), "d": (pd - today).days, "start": s.isoformat(), "end": e.isoformat(),
                          "amount": amt, "count": cnt, "closed": e < today})
        return {**card, "perf": perf, "perf_pct": round(perf / target * 100, 1) if target else None,
                "perf_need": need, "perf_daily": int(need / days_left) if need and days_left else 0, "days_left": days_left,
                "prev_perf": prev_perf, "prev_met": (prev_perf >= target) if target else None, "month_count": n,
                "bills": bills, "kind_label": "체크카드" if card["kind"] == "check" else "신용카드"}

    def card_overview(self, today=None):
        cards = [self.card_status(c, today) for c in self.db.q("SELECT * FROM cards WHERE active=1 ORDER BY id")]
        due = sum(c["bills"][0]["amount"] for c in cards if c["kind"] == "credit" and c["bills"])
        return {"cards": cards, "next_due_total": due, "suggest": self.card_suggestions(),
                "issuers": CARD_ISSUERS}

    def card_suggestions(self):
        rows = self.db.q("SELECT issuer, card_tail, COUNT(*) AS n, MAX(date) AS last FROM inbox WHERE kind='card' "
                         "AND issuer != '' GROUP BY issuer, card_tail ORDER BY n DESC")
        out = []
        for r in rows:
            fake = {"kind": "card", "issuer": r["issuer"], "card_tail": r["card_tail"]}
            if self.card_for(fake):
                continue
            out.append({"issuer": r["issuer"], "last4": r["card_tail"], "count": r["n"], "last": r["last"]})
        return out[:6]

    def card_txs(self, cid, start, end):
        return self.db.q("SELECT t.*, c.name AS category FROM transactions t LEFT JOIN categories c ON c.id=t.category_id "
                         "WHERE t.card_id=? AND t.date BETWEEN ? AND ? ORDER BY t.date DESC, t.id DESC", (cid, start, end))

    def is_card_payment(self, it):
        """은행 출금 문자가 카드 대금이면 True(카드 사용 때 이미 기록했으니 지출에서 뺀다)."""
        if it.get("action") != "outflow" or it.get("kind") != "bank":
            return False
        text = (it.get("merchant") or "") + " " + (it.get("raw") or "")
        m = re.search(r"(KB국민|국민|신한|삼성|현대|롯데|하나|우리|BC|비씨|NH농협|농협|씨티|IBK|카카오뱅크|토스)\s*카드\s*(대금|결제)?", text)
        if not m and "카드대금" not in text.replace(" ", ""):
            return False
        iss = m.group(1) if m else ""
        key = {"국민": "국민", "KB국민": "국민", "비씨": "BC", "농협": "농협", "NH농협": "농협"}.get(iss, iss)
        # 그 카드사 사용 문자가 최근 70일 안에 있을 때만(카드 사용을 따로 기록하고 있다는 뜻)
        since = (date.today() - timedelta(days=70)).isoformat()
        q = "SELECT 1 AS x FROM inbox WHERE kind='card' AND date >= ?" + (" AND issuer LIKE ?" if key else "")
        return bool(self.db.one(q, (since, f"%{key}%") if key else (since,)))
