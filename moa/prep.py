"""장이 열리기 전 준비: ① 넓게 훑기 ② 장 시작 전 리서치 메모.

① 넓게 훑기(국내): 장이 끝난 뒤 거래대금이 큰 종목 수백 개의 일봉과 수급을 훑어 점수를 매기고, 위쪽 종목을
   다음 날 스윙·장기 추천의 후보에 보탠다. 실시간 순위(그 순간 돈이 몰린 종목)만 보던 후보를 넓히려는 것.
   점수는 규칙으로 매긴 어림값이고, 실제로 통하는지는 추천 성적의 '종목 출처: 넓게 훑기 후보'로 따로 잰다.
② 장 시작 전 리서치: 자동매매가 도는 시장만, 장이 열리기 전에 AI가 밤사이 뉴스·일정·테마를 찾아 짧은 메모로 남긴다.
   그날 정밀 추천이 이 메모를 함께 읽는다. 하루 한 번(시장마다)만 돌고 주문은 내지 않는다.
"""
import json
import threading
import time
from datetime import datetime, timedelta

from . import aipick as AP

SCAN_TOP = 200          # 거래대금 위에서 이만큼만 일봉을 받는다
SCAN_KEEP = 30          # 남기는 종목 수
SCAN_MIN_VALUE = 3e9    # 하루 거래대금이 이보다 작으면 뺀다(30억 원)
SCAN_USE_DAYS = 4       # 이보다 오래된 훑기 결과는 후보에 쓰지 않는다
BRIEF_SYSTEM = """너는 한국 개인 투자자의 앱(moa)에 들어 있는 장 시작 전 리서치 담당이야. 오늘 장이 열리기 전에, 오늘 매매 판단에 쓸 준비 메모를 쓴다.
- 웹에서 확인한 사실만 써. 확인하지 못한 것은 쓰지 말고, 없는 숫자·뉴스를 지어내지 마. 날짜가 오늘·어제가 아닌 오래된 뉴스는 빼.
- 종목을 사라고 권하는 글이 아니야. 판단에 쓸 재료만 적어.
- 한국어로, '- '로 시작하는 줄 12개 이내, 모두 합쳐 900자 이내.
순서: ① 밤사이 해외 시장·환율·금리가 오늘 장에 주는 영향 ② 오늘·이번 주 일정(실적 발표·경제 지표·정책) ③ 오늘 돈이 몰릴 만한 테마 2~3개와 근거
④ '넓게 훑기 상위'나 '보유 종목' 가운데 밤사이 뉴스가 있는 종목(호재·악재) ⑤ 오늘 조심할 것."""


def _pr(vals):
    """값마다 백분위(0~1). 없는 값은 0.5."""
    xs = sorted(v for v in vals if v is not None)
    if not xs:
        return [0.5] * len(vals)
    return [0.5 if v is None else sum(1 for x in xs if x <= v) / len(xs) for v in vals]


def score_rows(rows):
    """일봉 특징으로 점수(0~100)를 매긴다. rows: [{sym, f(daily_features 결과)}].
    오르는 흐름(20일 등락·60일 고점 근처)과 붙는 거래량, 이동평균 위를 좋게 보고, 며칠 급등·지나친 흔들림은 깎는다."""
    f = lambda k: [(r["f"] or {}).get(k) for r in rows]
    p20, phi, pv = _pr(f("20일%")), _pr(f("60일고점대비%")), _pr([None if v is None else min(v, 4) for v in f("5일거래량배수")])
    for i, r in enumerate(rows):
        g = r["f"] or {}
        s = 30 * p20[i] + 20 * phi[i] + 20 * pv[i] + (15 if (g.get("20일선대비%") or 0) > 0 else 0) + (15 if (g.get("60일선대비%") or 0) > 0 else 0)
        if (g.get("5일%") or 0) > 25:
            s -= 25                                    # 며칠 급등: 재료가 이미 반영됐을 가능성
        if (g.get("하루변동폭%") or 0) > 8:
            s -= 15
        r["score"] = round(max(0.0, min(100.0, s)), 1)
    return rows


class Prep:
    def __init__(self, rt):
        self.rt = rt
        db = rt.db
        db.x("""CREATE TABLE IF NOT EXISTS scan_picks(id INTEGER PRIMARY KEY, day TEXT NOT NULL, mkt TEXT NOT NULL, rank INTEGER NOT NULL,
                sym TEXT NOT NULL, name TEXT, score REAL, price REAL, feat TEXT, ts TEXT)""")
        db.x("CREATE INDEX IF NOT EXISTS ix_scan_day ON scan_picks(mkt, day)")
        db.x("""CREATE TABLE IF NOT EXISTS ai_briefs(id INTEGER PRIMARY KEY, day TEXT NOT NULL, mkt TEXT NOT NULL, ts TEXT NOT NULL,
                model TEXT, usd REAL, text TEXT, UNIQUE(day, mkt))""")
        self._busy = {}
        self._note = {}
        self._stop = threading.Event()

    # ------------------------------------------------------------------ 설정
    def conf(self):
        c = self.rt.settings.all().get("prep") or {}
        return {"scan": c.get("scan", True) is not False, "brief": c.get("brief", True) is not False}

    # ------------------------------------------------------------------ 넓게 훑기
    @staticmethod
    def scan_day(now=None):
        """훑기 결과에 붙이는 날짜: 마지막으로 끝난 평일 장의 날짜(오후 3시 50분 전이면 그 전 평일)."""
        n = now or AP.kst_now().replace(tzinfo=None)
        d = n.date()
        if n.weekday() >= 5 or n.hour * 60 + n.minute < 950:
            d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d.isoformat()

    def scan_latest(self, mkt="kr", max_age=SCAN_USE_DAYS):
        r = self.rt.db.one("SELECT day FROM scan_picks WHERE mkt=? ORDER BY day DESC LIMIT 1", (mkt,))
        if not r:
            return None, []
        if max_age and r["day"] < (AP.kst_now().date() - timedelta(days=max_age)).isoformat():
            return r["day"], []
        rows = self.rt.db.q("SELECT rank, sym, name, score, price, feat, ts FROM scan_picks WHERE mkt=? AND day=? ORDER BY rank", (mkt, r["day"]))
        for x in rows:
            x["feat"] = json.loads(x["feat"] or "{}")
        return r["day"], rows

    def _universe(self, cl):
        """거래대금이 큰 보통주. 종목 목록을 50개씩 묶어 한 번에 시세를 받는다(ka10095)."""
        m = self.rt.market
        codes = [c for c, v in (m.kr_master(wait=True) or {}).items()
                 if isinstance(v, dict) and v.get("market") in ("코스피", "코스닥") and not m.junk("kr", c, v.get("name"))]
        out, fail = [], 0
        for i in range(0, len(codes), 50):
            if self._stop.is_set():
                break
            try:
                d = cl.call("watchinfo", {"stk_cd": "|".join(codes[i:i + 50])})
            except Exception:
                fail += 1
                if fail >= 5 and not out:
                    break                              # 이 조회가 안 되는 계좌·환경이면 순위 목록으로 대신한다
                continue
            for r in d.get("atn_stk_infr") or []:
                c = str(r.get("stk_cd", "")).split("_")[0][-6:]
                px, qty = abs(_num(r.get("cur_prc"))), abs(_num(r.get("trde_qty")))
                if c and px >= 1000 and abs(_num(r.get("flu_rt"))) < 29:
                    out.append({"sym": c, "name": r.get("stk_nm") or m.kr_name(c), "price": px, "value": px * qty})
        if len(out) < 100:                              # 대신: 실시간 순위 세 가지(각 40종목)
            seen = {x["sym"] for x in out}
            for k in ("value", "volume", "up"):
                try:
                    for it in self.rt.invest.ranking("kr", k)["items"]:
                        if it["sym"] not in seen and (it.get("price") or 0) >= 1000 and not m.junk("kr", it["sym"], it.get("name")):
                            seen.add(it["sym"])
                            out.append({"sym": it["sym"], "name": it["name"], "price": it["price"], "value": it.get("value") or SCAN_MIN_VALUE})
                except Exception:
                    pass
            return out, "순위 목록"
        out = [x for x in out if x["value"] >= SCAN_MIN_VALUE]
        out.sort(key=lambda x: -x["value"])
        return out[:SCAN_TOP], "전 종목"

    def scan(self, mkt="kr"):
        """넓게 훑는다(1~3분). 결과: {day, n, src, top}. 국내만."""
        if mkt != "kr":
            raise ValueError("넓게 훑기는 국내 시장만 돼요")
        cl = self.rt.market.kr_client()
        if cl is None:
            raise ValueError("키움 계좌를 연결해야 훑을 수 있어요")
        t0, day = time.time(), self.scan_day()
        self._note["scan"] = {"state": "run", "msg": "거래대금이 큰 종목을 고르는 중", "at": datetime.now().isoformat(timespec="seconds")}
        uni, src = self._universe(cl)
        rows = []
        for i, u in enumerate(uni):
            if self._stop.is_set():
                break
            if i % 20 == 0:
                self._note["scan"]["msg"] = f"일봉을 읽는 중 {i}/{len(uni)}"
            try:
                f = AP.daily_features((self.rt.invest.candles("kr", u["sym"], "d") or {}).get("bars"))
            except Exception:
                f = None
            if f and (f.get("일봉_개수") or 0) >= 40:
                rows.append(dict(u, f=f))
        score_rows(rows)
        rows.sort(key=lambda r: -r["score"])
        top = rows[:SCAN_KEEP + 15]
        for r in top:                                  # 위쪽만 수급을 받아 조금 더하고 뺀다
            fl = self.rt.market.kr_flow(r["sym"])
            if fl:
                r["f"]["외국인5일(억)"], r["f"]["기관5일(억)"] = fl["외국인5일"], fl["기관5일"]
                r["score"] = round(max(0.0, min(100.0, r["score"] + (5 if fl["외국인5일"] > 0 else -3 if fl["외국인5일"] < 0 else 0)
                                                    + (5 if fl["기관5일"] > 0 else -3 if fl["기관5일"] < 0 else 0))), 1)
        top.sort(key=lambda r: -r["score"])
        top = top[:SCAN_KEEP]
        if not top:
            self._note["scan"] = {"state": "fail", "msg": "읽을 수 있는 종목이 없었어요", "at": datetime.now().isoformat(timespec="seconds")}
            return {"day": day, "n": 0, "src": src, "top": []}
        now = datetime.now().isoformat(timespec="seconds")
        with self.rt._lock:
            self.rt.db.x("DELETE FROM scan_picks WHERE mkt=? AND (day=? OR day<?)", (mkt, day, (AP.kst_now().date() - timedelta(days=30)).isoformat()))
            for i, r in enumerate(top):
                self.rt.db.x("INSERT INTO scan_picks(day,mkt,rank,sym,name,score,price,feat,ts) VALUES(?,?,?,?,?,?,?,?,?)",
                             (day, mkt, i + 1, r["sym"], r["name"], r["score"], r["price"], json.dumps(r["f"], ensure_ascii=False), now))
        self._note["scan"] = {"state": "ok", "msg": f"{src} {len(uni)}개 가운데 {len(rows)}개를 읽어 {len(top)}개를 남겼어요({time.time() - t0:.0f}초)", "at": now}
        return {"day": day, "n": len(rows), "src": src, "top": [{"sym": r["sym"], "name": r["name"], "score": r["score"]} for r in top[:10]]}

    def scan_due(self):
        n = AP.kst_now().replace(tzinfo=None)
        mins = n.hour * 60 + n.minute
        if n.weekday() < 5 and 525 <= mins < 950:      # 장중에는 오늘 봉이 덜 만들어져 있어 돌리지 않는다
            return False
        last = self.rt.db.one("SELECT MAX(day) d FROM scan_picks WHERE mkt='kr'")
        return (last or {}).get("d") != self.scan_day(n)

    # ------------------------------------------------------------------ 장 시작 전 리서치
    def brief_day(self, mkt):
        return AP.mkt_date(mkt)

    def brief_get(self, mkt, today_only=True):
        r = self.rt.db.one("SELECT day, ts, model, usd, text FROM ai_briefs WHERE mkt=? ORDER BY day DESC LIMIT 1", (mkt,))
        if not r or (today_only and r["day"] != self.brief_day(mkt)):
            return None
        return r

    def brief_context(self, d):
        mkt = "us" if d.get("mkt") == "us" else "kr"
        m = self.rt.market
        try:
            idx = [{k: i.get(k) for k in ("name", "price", "change_pct")} for i in (m.kr_indices() if mkt == "kr" else m.us_indices())]
        except Exception:
            idx = []
        data = {"시장": "국내(KRX)" if mkt == "kr" else "미국", "기준시각(한국)": AP.kst_now().strftime("%Y-%m-%d %H:%M"), "지수(직전 값)": idx}
        if mkt == "kr":
            day, rows = self.scan_latest("kr")
            if rows:
                data["넓게 훑기 상위(" + day + " 장 마감 기준)"] = [f"{r['name']}({r['sym']}) 점수 {r['score']:g} · 20일 {r['feat'].get('20일%')}% · 5일 거래량 {r['feat'].get('5일거래량배수')}배" for r in rows[:12]]
        hold = []
        for t in self.rt.db.q("SELECT DISTINCT t.sym, t.name, t.why FROM at_trades t JOIN at_runs r ON r.id=t.run_id WHERE t.status='open' AND t.mkt=? LIMIT 12", (mkt,)):
            w = json.loads(t["why"] or "{}")
            hold.append(f"{t['name']}({t['sym']})" + (f": {w.get('catalyst')}" if w.get("catalyst") else ""))
        if hold:
            data["보유 종목(자동매매)"] = hold
        where = "국내" if mkt == "kr" else "미국"
        return f"장 시작 전 리서치({where})", f"오늘 {where} 장이 열리기 전에 준비 메모를 써 줘. 밤사이 뉴스와 오늘 일정은 웹에서 확인해.", data

    def brief_due(self, mkt):
        """자동매매가 도는 시장만, 평일 장 시작 30~90분 전에 하루 한 번."""
        if not self.conf()["brief"] or not self.rt.ai.configured() or self.brief_get(mkt):
            return False
        if not self.rt.db.one("SELECT 1 x FROM at_runs WHERE mkt=? AND status='running' LIMIT 1", (mkt,)):
            return False
        n = AP.kst_now() if mkt == "kr" else AP.et_now()
        mins = n.hour * 60 + n.minute
        return n.weekday() < 5 and ((450 <= mins < 535) if mkt == "kr" else (480 <= mins < 565))

    def brief(self, mkt):
        buf, done, err = [], {}, None
        for ev in self.rt.ai.stream("brief", {"mkt": mkt, "web_search": True}):
            if ev.get("t") == "text":
                buf.append(ev.get("text") or "")
            elif ev.get("t") == "error":
                err = ev.get("msg")
            elif ev.get("t") == "done":
                done = ev
        text = "".join(buf).strip()
        if err or not text:
            self._note["brief:" + mkt] = {"state": "fail", "msg": err or "AI 답이 비어 있었어요", "at": datetime.now().isoformat(timespec="seconds")}
            # 실패해도 오늘은 다시 부르지 않게 빈 메모를 남긴다(비용이 되풀이되지 않게)
            text = ""
        with self.rt._lock:
            self.rt.db.x("INSERT OR REPLACE INTO ai_briefs(day,mkt,ts,model,usd,text) VALUES(?,?,?,?,?,?)",
                         (self.brief_day(mkt), mkt, datetime.now().isoformat(timespec="seconds"), done.get("model"), float(done.get("usd") or 0), text[:2400]))
        return {"ok": bool(text), "usd": done.get("usd"), "error": err}

    # ------------------------------------------------------------------ 돌리기
    def _spawn(self, key, fn):
        th = self._busy.get(key)
        if th and th.is_alive():
            return False

        def run():
            try:
                fn()
            except Exception as e:
                self._note[key] = {"state": "fail", "msg": str(e)[:200], "at": datetime.now().isoformat(timespec="seconds")}
        th = threading.Thread(target=run, daemon=True, name=f"prep-{key}")
        self._busy[key] = th
        th.start()
        return True

    def tick(self):
        if self.conf()["scan"] and self.rt.market.kr_client() is not None and self.scan_due():
            self._spawn("scan", lambda: self.scan("kr"))
        for mkt in ("kr", "us"):
            if self.brief_due(mkt):
                self._spawn("brief:" + mkt, lambda mkt=mkt: self.brief(mkt))

    def start(self, every=300):
        def loop():
            time.sleep(90)                              # 서버가 뜬 직후에는 다른 준비가 먼저
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception:
                    pass
                self._stop.wait(every)
        threading.Thread(target=loop, daemon=True, name="prep").start()

    def status(self, mkt="kr"):
        day, rows = self.scan_latest("kr", max_age=0)
        b = self.brief_get(mkt, today_only=False)
        busy = lambda k: bool(self._busy.get(k) and self._busy[k].is_alive())
        return {"conf": self.conf(),
                "scan": {"day": day, "fresh": bool(day and day >= (AP.kst_now().date() - timedelta(days=SCAN_USE_DAYS)).isoformat()), "busy": busy("scan"),
                         "note": self._note.get("scan"), "top": [{"rank": r["rank"], "sym": r["sym"], "name": r["name"], "score": r["score"],
                                                                  "r20": r["feat"].get("20일%"), "f5": r["feat"].get("외국인5일(억)"), "o5": r["feat"].get("기관5일(억)")} for r in rows[:10]]},
                "brief": None if not b else {"day": b["day"], "today": b["day"] == self.brief_day(mkt), "ts": b["ts"], "usd": b["usd"], "model": b["model"], "text": b["text"]},
                "brief_busy": busy("brief:" + mkt), "brief_note": self._note.get("brief:" + mkt)}


def _num(v):
    try:
        return float(str(v or "0").replace(",", "").replace("+", "") or 0)
    except ValueError:
        return 0.0
