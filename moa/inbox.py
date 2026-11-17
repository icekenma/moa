"""금융 알림 메시지(카드 승인·취소, 은행 입출금) 분석 → 가계부 자동 기록·자산 잔액 갱신.

들어오는 길
- 아이폰 단축어 자동화가 문자를 받는 순간 POST /api/inbox 로 보낸다
- 카카오톡 '대화 내용 내보내기' 파일이나 복사한 문자 묶음을 가져오기로 넣는다

개인정보
- 금융 알림으로 판단되지 않은 메시지는 분석만 하고 저장하지 않는다
- 같은 메시지는 한 번만 처리한다(문자와 카톡 내보내기가 겹쳐도 중복 기록 없음)
"""
import hashlib
import re
from datetime import date, datetime

ISSUERS = ["KB국민", "국민", "신한", "삼성", "현대", "롯데", "하나", "우리", "BC", "비씨", "NH농협", "농협", "씨티",
           "카카오뱅크", "카카오페이", "토스뱅크", "토스", "케이뱅크", "IBK", "기업", "수협", "새마을", "우체국", "SC제일",
           "부산", "대구", "경남", "광주", "전북", "신협"]
CATEGORY_RULES = [
    ("외식", ["스타벅스", "커피", "카페", "투썸", "이디야", "메가", "빽다방", "배달의민족", "배민", "요기요", "쿠팡이츠",
             "맥도날드", "버거", "식당", "치킨", "피자", "분식", "김밥", "레스토랑", "베이커리", "파리바게", "뚜레쥬르"]),
    ("식비", ["이마트", "코스트코", "홈플러스", "롯데마트", "마트", "GS25", "CU", "씨유", "세븐일레븐", "이마트24",
             "편의점", "하나로", "식자재", "정육", "농협하나로", "컬리", "마켓"]),
    ("교통", ["주유", "SK에너지", "GS칼텍스", "S-OIL", "에쓰오일", "현대오일", "택시", "카카오T", "티머니", "코레일",
             "SRT", "고속", "버스", "하이패스", "주차", "충전"]),
    ("주거·통신", ["SKT", "에스케이텔레콤", "KT", "LG유플", "U+", "관리비", "도시가스", "전기", "수도", "알뜰폰"]),
    ("의료", ["약국", "병원", "의원", "치과", "한의원", "안과"]),
    ("생활용품", ["쿠팡", "다이소", "올리브영", "네이버페이", "11번가", "G마켓", "옥션", "무신사"]),
    ("취미", ["넷플릭스", "유튜브", "스포티파이", "멜론", "CGV", "메가박스", "롯데시네마", "교보", "알라딘", "예스24",
             "카메라"]),
]
FIN_HINT = re.compile(r"(승인|결제|취소|입금|출금|이체|잔액|누적|일시불|할부|체크|카드)")
AMOUNT = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+)\s*원")
DATE_MD = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)")
TIME = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
BAL = re.compile(r"잔액\s*:?\s*(\d{1,3}(?:,\d{3})+|\d+)")
ACCT = re.compile(r"(\d{2,6})[-*]+[\d*-]*?(\d{3,6})(?!\d)")


def _n(s):
    return int(s.replace(",", ""))


def guess_category(merchant):
    m = (merchant or "").upper()
    for cat, words in CATEGORY_RULES:
        if any(w.upper() in m for w in words):
            return cat
    return None


def _issuer(text):
    for name in ISSUERS:
        if name in text:
            return {"국민": "KB국민", "비씨": "BC", "농협": "NH농협", "기업": "IBK"}.get(name, name)
    return ""


def parse(text, received=None):
    """메시지 하나 → 금융 알림이면 dict, 아니면 None."""
    raw = (text or "").replace("\r", "").strip()
    if not raw or not FIN_HINT.search(raw):
        return None
    body = raw.replace("[Web발신]", "").replace("(광고)", "").strip()
    received = received or datetime.now()
    is_card = bool(re.search(r"(카드|승인|일시불|할부|체크)", body)) and "입금" not in body
    cancel = "취소" in body
    if is_card and re.search(r"(승인|결제|사용|취소)", body):
        kind, action = "card", ("cancel" if cancel else "spend")
    elif "입금" in body:
        kind, action = "bank", "income"
    elif re.search(r"(출금|이체|자동이체|납부)", body):
        kind, action = "bank", "outflow"
    else:
        return None
    # 금액: '누적'·'잔액' 뒤 숫자는 제외하고 첫 금액
    clean = re.sub(r"(누적|잔액|한도|사용가능)\s*:?\s*[\d,]+\s*원?", " ", body)
    m = AMOUNT.search(clean)
    if not m and kind == "bank":  # 은행 알림은 '원' 없이 숫자만 오기도 한다
        m2 = re.search(r"(입금|출금|이체)\s*:?\s*(\d{1,3}(?:,\d{3})+|\d+)", clean)
        amount = _n(m2.group(2)) if m2 else 0
    else:
        amount = _n(m.group(1)) if m else 0
    if amount <= 0:
        return None
    bal = BAL.search(body)
    # 날짜
    d = received.date()
    dm = DATE_MD.search(body)
    if dm:
        mo, dd = int(dm.group(1)), int(dm.group(2))
        try:
            d = date(received.year, mo, dd)
            if d > received.date():
                d = date(received.year - 1, mo, dd)
        except ValueError:
            pass
    tm = TIME.search(body)
    # 가맹점/보낸 사람: 줄 단위로 의미 없는 줄을 지우고 남은 마지막 줄
    lines = [x.strip() for x in body.split("\n") if x.strip()]
    if len(lines) <= 1:  # 한 줄 형식: '... 11/17 12:30 스타벅스 누적...'
        one = body
        after = re.split(r"\d{1,2}:\d{2}", one, maxsplit=1)
        tail = after[1] if len(after) > 1 else ""
        tail = re.split(r"(누적|잔액)", tail)[0]
        lines = [tail.strip()] if tail.strip() else []
    junk = re.compile(r"(원|승인|취소|누적|잔액|일시불|할부|\d{1,2}/\d{1,2}|\d{1,2}:\d{2}|\*|님$|카드\(?\d*\)?$|^\[|입금$|출금$)")
    cands = [x for x in lines if not junk.search(x) and not re.fullmatch(r"[\d\s,.-]+", x)]
    merchant = cands[-1] if cands else ""
    if not merchant:  # 한 줄에 날짜·계좌·금액·잔액이 섞인 형식(예: 농협) → 군더더기를 지우고 남은 글자
        rest = []
        for x in body.split("\n"):
            y = re.sub(r"-\d+", " ", ACCT.sub(" ", x))
            y = re.sub(r"(잔액|누적|입금|출금|이체|승인|일시불)\s*:?\s*[\d,]*\s*원?", " ", y)
            y = re.sub(r"\d{1,2}/\d{1,2}|\d{1,2}:\d{2}|[\d,]+\s*원|[\d,]{4,}|\[[^\]]*\]|[*]", " ", y)
            for name in ISSUERS:
                y = re.sub(rf"^{name}(은행|카드)?[,\s]*", " ", y.strip())
            y = re.sub(r"\s+", " ", y).strip(" ,.-")
            if y and not re.fullmatch(r"[\d\s,.-]*", y):
                rest.append(y)
        merchant = rest[-1] if rest else ""
    merchant = re.sub(r"\s+", " ", merchant)[:40]
    acct = ACCT.search(body)
    return {
        "kind": kind, "action": action, "amount": amount, "merchant": merchant,
        "issuer": _issuer(body), "date": d.isoformat(),
        "time": f"{int(tm.group(1)):02d}:{tm.group(2)}" if tm else "",
        "balance": _n(bal.group(1)) if bal else None,
        "acct_tail": acct.group(2)[-4:] if acct else "",
        "category": guess_category(merchant) if kind == "card" else None,
        "raw": body[:300],
    }


KAKAO_HEAD = re.compile(
    r"^(\d{4})[.년]\s*(\d{1,2})[.월]\s*(\d{1,2})[.일]?\.?\s*(오전|오후)\s*(\d{1,2}):(\d{2}),\s*(.+?)\s*:\s?(.*)$")


def split_bulk(text):
    """카카오톡 내보내기 파일 또는 빈 줄로 나눈 메시지 묶음 → [(본문, 받은 시각|None)]"""
    text = (text or "").replace("\r", "")
    lines = text.split("\n")
    if sum(1 for x in lines if KAKAO_HEAD.match(x)) >= 1:
        out, cur, when = [], [], None
        for line in lines:
            m = KAKAO_HEAD.match(line)
            if m:
                if cur:
                    out.append(("\n".join(cur), when))
                y, mo, d, ap, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4), int(m.group(5)), int(m.group(6))
                h = h % 12 + (12 if ap == "오후" else 0)
                try:
                    when = datetime(y, mo, d, h, mi)
                except ValueError:
                    when = None
                cur = [m.group(8)]
            elif cur:
                cur.append(line)
        if cur:
            out.append(("\n".join(cur), when))
        return out
    return [(chunk.strip(), None) for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]


class Inbox:
    def __init__(self, db, ledger, settings):
        self.db, self.ledger, self.settings = db, ledger, settings
        db.x("""CREATE TABLE IF NOT EXISTS inbox(
            id INTEGER PRIMARY KEY, hash TEXT UNIQUE, received_at TEXT NOT NULL, source TEXT NOT NULL,
            kind TEXT, action TEXT, amount INTEGER, merchant TEXT, issuer TEXT, date TEXT, time TEXT,
            balance INTEGER, acct_tail TEXT, category TEXT, raw TEXT,
            status TEXT NOT NULL, tx_id INTEGER, asset_id INTEGER, note TEXT NOT NULL DEFAULT '')""")
        cols = {r["name"] for r in db.q("PRAGMA table_info(inbox)")}
        if "prev_balance" not in cols:
            db.x("ALTER TABLE inbox ADD COLUMN prev_balance INTEGER")
        if "transfer" not in cols:
            db.x("ALTER TABLE inbox ADD COLUMN transfer INTEGER NOT NULL DEFAULT 0")

    # ---------- 내 계좌끼리 옮긴 돈(이체) 찾기 ----------
    PAIR_MINUTES = 30

    @staticmethod
    def _norm(x):
        return re.sub(r"[\s()\[\]·*_-]", "", str(x or "")).lower()

    def _own_tokens(self):
        """받는 분·보낸 분 이름이 이 중 하나면 내 계좌로 본다: 설정한 내 이름 + 내 증권 계좌(키움 등) 이름."""
        toks = {self._norm(n) for n in self.settings.get("my_names") or []}
        for a in self.db.q("SELECT name, bank, linked, type FROM accounts WHERE linked IS NOT NULL OR type IN ('stock','pension')"):
            for t in (a["bank"], a["name"]):
                t = self._norm(t)
                if len(t) >= 2:
                    toks.add(t)
                    if t.endswith("증권"):
                        toks.add(t[:-2])
        if self.db.get_setting("kiwoom_asset_auto") or self.db.one("SELECT 1 AS x FROM accounts WHERE linked='kiwoom'"):
            toks.add("키움")
        return {t for t in toks if len(t) >= 2}

    def _name_says_own(self, it, toks=None):
        m = self._norm(it.get("merchant"))
        if not m:
            return False
        if "본인" in m or "내계좌" in m:
            return True
        return any(t in m for t in (toks if toks is not None else self._own_tokens()))

    def _when(self, it):
        try:
            return datetime.strptime(f"{it['date']} {(it.get('time') or '00:00')[:5]}", "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            try:
                return datetime.fromisoformat(it["received_at"])
            except (TypeError, ValueError, KeyError):
                return None

    def _pair_of(self, it):
        """같은 금액이 내 다른 계좌에서 나가고(출금) 들어온(입금) 짝을 30분 안에서 찾는다."""
        if it["kind"] != "bank" or it["action"] not in ("income", "outflow"):
            return None
        other = "outflow" if it["action"] == "income" else "income"
        t0 = self._when(it)
        if not t0:
            return None
        for c in self.db.q("SELECT * FROM inbox WHERE kind='bank' AND action=? AND amount=? AND id!=? "
                           "AND status!='ignored' AND transfer=0 ORDER BY id DESC LIMIT 20",
                           (other, it["amount"], it["id"])):
            t1 = self._when(c)
            if not t1 or abs((t1 - t0).total_seconds()) > self.PAIR_MINUTES * 60:
                continue
            same_acct = (c.get("acct_tail") and c.get("acct_tail") == it.get("acct_tail")
                         and (c.get("issuer") or "") == (it.get("issuer") or ""))
            if not same_acct:
                return c
        return None

    def _mark_transfer(self, it, why):
        """이 알림을 '내 계좌 이체'로: 가계부에 이미 있으면 이체로 바꾸고, 없으면 이체로 넣는다."""
        self.db.x("UPDATE inbox SET transfer=1, note=? WHERE id=?", (why, it["id"]))
        if it.get("tx_id"):
            self.db.x("UPDATE transactions SET transfer=1, category_id=NULL WHERE id=?", (it["tx_id"],))
        elif it["status"] == "pending":
            self.apply(it["id"], transfer=True)

    def detect_transfer(self, iid):
        it = self.get(iid)
        if not it or it["kind"] != "bank" or it.get("transfer") or it["status"] == "ignored":
            return False
        if self._name_says_own(it):
            self._mark_transfer(it, "내 계좌로 옮긴 돈이라 수입·지출에서 뺐어요")
            return True
        pair = self._pair_of(it)
        if pair:
            self._mark_transfer(it, "다른 내 계좌와 짝이 맞아 수입·지출에서 뺐어요")
            self._mark_transfer(self.get(pair["id"]), "다른 내 계좌와 짝이 맞아 수입·지출에서 뺐어요")
            return True
        return False

    def retag(self):
        """지난 입출금 알림을 다시 살펴 내 계좌 이체를 찾는다(이름 설정을 바꿨을 때)."""
        n = 0
        for r in self.db.q("SELECT id FROM inbox WHERE kind='bank' AND transfer=0 AND status!='ignored' ORDER BY id"):
            if self.detect_transfer(r["id"]):
                n += 1
        return n

    def _cat_id(self, name, kind):
        if not name:
            name = "기타지출" if kind == "out" else "기타수입"
        r = self.db.one("SELECT id FROM categories WHERE name=? AND kind=?", (name, kind))
        if not r:
            r = self.db.one("SELECT id FROM categories WHERE kind=? ORDER BY sort LIMIT 1", (kind,))
        return r["id"] if r else None

    def _auto(self, p):
        a = self.settings.get("inbox_auto")
        return {"spend": a.get("card", True), "cancel": a.get("card", True),
                "income": a.get("income", False), "outflow": a.get("outflow", False)}[p["action"]]

    def ingest(self, text, source="sms", received=None):
        """메시지 하나 처리. 반환: 처리 결과 dict 또는 None(금융 알림 아님 → 저장 안 함)"""
        received = received or datetime.now()
        p = parse(text, received)
        if not p:
            return None
        key = re.sub(r"\s+", "", p["raw"])
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()
        if self.db.one("SELECT 1 AS x FROM inbox WHERE hash=?", (h,)):
            return {"status": "duplicate", **p}
        iid = self.db.x(
            "INSERT INTO inbox(hash,received_at,source,kind,action,amount,merchant,issuer,date,time,balance,acct_tail,"
            "category,raw,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (h, received.isoformat(timespec="seconds"), source, p["kind"], p["action"], p["amount"], p["merchant"],
             p["issuer"], p["date"], p["time"], p["balance"], p["acct_tail"], p["category"], p["raw"], "pending"))
        note = []
        if not self.detect_transfer(iid) and self._auto(p):
            self.apply(iid)
        if p["balance"] is not None:
            a = self._update_asset(p)
            if a:
                self.db.x("UPDATE inbox SET asset_id=?, prev_balance=? WHERE id=?", (a["id"], a["balance"], iid))
                note.append(f"{a['name']} 잔액 {p['balance']:,}원으로 갱신")
        if note:
            prev = (self.get(iid) or {}).get("note") or ""
            self.db.x("UPDATE inbox SET note=? WHERE id=?", (" · ".join(([prev] if prev else []) + note), iid))
        return self.get(iid)

    def _update_asset(self, p):
        """잔액이 온 은행 알림 → 계좌번호 끝자리·은행 이름이 맞는 자산 계좌의 잔액을 갱신."""
        tail, issuer = p.get("acct_tail") or "", p.get("issuer") or ""
        best, best_score = None, 0
        accts = self.db.q("SELECT * FROM accounts WHERE linked IS NULL AND type IN ('cash','deposit')")
        for a in accts:
            digits = "".join(ch for ch in (a.get("acct_no") or "") if ch.isdigit())
            score = 0
            if tail and digits and tail in digits:
                score += 2
            elif tail and tail in (a["name"] + " " + a["note"]):
                score += 2
            if issuer and (issuer in (a.get("bank") or "") or (a.get("bank") or "x") in issuer):
                score += 1
            if score > best_score:
                best, best_score = a, score
        # 계좌번호 끝자리가 맞을 때만 잔액을 바꾼다(은행 이름만 맞는 경우는 바꾸지 않음)
        if best and best_score >= 2:
            self.db.x("UPDATE accounts SET balance=? WHERE id=?", (p["balance"], best["id"]))
            return best
        return None

    def bank_status(self):
        """계좌 연결 화면용: 은행 계좌별 마지막 알림."""
        out = []
        for a in self.db.q("SELECT * FROM accounts WHERE bank != '' ORDER BY sort, id"):
            last = self.db.one("SELECT received_at, action, amount FROM inbox WHERE asset_id=? ORDER BY id DESC LIMIT 1",
                               (a["id"],))
            out.append({**a, "last_alert": last})
        return out

    def apply(self, iid, category=None, transfer=None):
        """확인 필요 항목을 가계부에 넣는다(취소는 같은 금액·가맹점 기록을 지운다)."""
        it = self.get(iid)
        if not it or it["status"] == "added":
            return it
        if it["action"] == "cancel":
            tx = self.db.one("SELECT t.id FROM transactions t JOIN inbox i ON i.tx_id=t.id "
                             "WHERE i.action='spend' AND i.amount=? AND i.merchant=? ORDER BY t.id DESC LIMIT 1",
                             (it["amount"], it["merchant"]))
            if tx:
                self.ledger.delete(tx["id"])
                self.db.x("UPDATE inbox SET status='added', note=? WHERE id=?", ("승인 기록을 지웠어요", iid))
            else:
                tid = self.ledger.add({"date": it["date"], "kind": "in", "amount": it["amount"],
                                       "category_id": self._cat_id("기타수입", "in"),
                                       "memo": f"{it['merchant'] or it['issuer']} 결제 취소", "method": "카드"})
                self.db.x("UPDATE inbox SET status='added', tx_id=? WHERE id=?", (tid, iid))
            return self.get(iid)
        kind = "in" if it["action"] == "income" else "out"
        xfer = bool(it.get("transfer")) if transfer is None else bool(transfer)
        cat = None if xfer else (category or it["category"])
        method = ("신용카드" if it["kind"] == "card" else "계좌이체")
        memo = it["merchant"] or (f"{it['issuer']} 입금" if kind == "in" else f"{it['issuer']} 출금")
        tid = self.ledger.add({"date": it["date"], "kind": kind, "amount": it["amount"],
                               "category_id": None if xfer else self._cat_id(cat, kind), "memo": memo,
                               "method": method, "transfer": xfer})
        self.db.x("UPDATE inbox SET status='added', tx_id=?, category=?, transfer=? WHERE id=?",
                  (tid, cat, 1 if xfer else 0, iid))
        return self.get(iid)

    def ignore(self, iid):
        """무시: 가계부에 넣지 않고, 이 알림이 바꾼 계좌 잔액도 되돌린다(그 뒤에 다른 알림이 잔액을 바꾸지 않았을 때)."""
        it = self.get(iid)
        self.db.x("UPDATE inbox SET status='ignored' WHERE id=?", (iid,))
        if it and it.get("asset_id") and it.get("prev_balance") is not None:
            later = self.db.one("SELECT 1 AS x FROM inbox WHERE asset_id=? AND id>? AND status!='ignored'",
                                (it["asset_id"], iid))
            cur = self.db.one("SELECT balance FROM accounts WHERE id=?", (it["asset_id"],))
            if not later and cur and cur["balance"] == it["balance"]:
                self.db.x("UPDATE accounts SET balance=? WHERE id=?", (it["prev_balance"], it["asset_id"]))
                self.db.x("UPDATE inbox SET note=? WHERE id=?", ("무시해서 계좌 잔액을 되돌렸어요", iid))

    def get(self, iid):
        return self.db.one("SELECT * FROM inbox WHERE id=?", (iid,))

    def list(self, limit=60):
        rows = self.db.q("SELECT * FROM inbox ORDER BY id DESC LIMIT ?", (limit,))
        stat = {r["status"]: r["n"] for r in self.db.q("SELECT status, COUNT(*) AS n FROM inbox GROUP BY status")}
        return {"items": rows, "pending": stat.get("pending", 0), "added": stat.get("added", 0),
                "ignored": stat.get("ignored", 0), "auto": self.settings.get("inbox_auto"),
                "my_names": self.settings.get("my_names") or []}

    def import_bulk(self, text, source="import"):
        res = {"total": 0, "financial": 0, "added": 0, "pending": 0, "duplicate": 0}
        for body, when in split_bulk(text):
            res["total"] += 1
            r = self.ingest(body, source, when)
            if not r:
                continue
            res["financial"] += 1
            st = r["status"]
            res[st if st in res else "pending"] += 1
        return res
