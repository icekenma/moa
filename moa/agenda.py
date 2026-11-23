"""챙길 일 모음 + 캘린더 내보내기.

우석World의 앱들이 아는 '날짜가 있는 일'(고정지출 납부일, 적립 매수일, 자동매매 승인 대기·종료일·어제 요약,
운동 계획, 건강 일정)을 한 줄씩 모은다. 쓰임은 둘:
① 첫 화면의 '챙길 일' 카드 — 한 줄씩 다른 앱(피클 같은 AI 비서)에 맡길 문구로 넘긴다(공유).
② 캘린더 구독 주소(/cal/<열쇠>.ics) — 아이폰 캘린더에 구독해 두면, 휴대폰 캘린더를 읽는 앱이 함께 챙긴다.

바깥으로 나가는 자료라 기본은 가볍게: 금액은 넣지 않고(설정에서 켤 수 있음), 건강 일정은 꺼 둔다.
구독 주소의 열쇠는 앱 접속 토큰과 다른 값이고, 이 주소로는 읽기만 된다(새로 만들면 예전 주소는 끊긴다).
"""
import hashlib
import hmac
import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone

KINDS = {"fixed": "고정지출 납부일", "invest": "투자(적립일·자동매매 알림)", "workout": "운동 계획", "health": "건강 일정"}
DEFAULT = {"fixed": True, "invest": True, "workout": True, "health": False, "amount": False}
DOW = "월화수목금토일"


def _esc(s):
    return str(s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


class Agenda:
    def __init__(self, rt):
        self.rt = rt

    # ----- 설정
    def conf(self):
        return dict(DEFAULT, **{k: bool(v) for k, v in (self.rt.db.get_setting("agenda", {}) or {}).items() if k in DEFAULT})

    def save_conf(self, d):
        c = self.conf()
        c.update({k: bool(d[k]) for k in DEFAULT if k in d})
        self.rt.db.set_setting("agenda", c)
        return c

    def token(self, renew=False):
        t = self.rt.db.get_setting("agenda_token", "")
        if renew or not t:
            t = secrets.token_urlsafe(24)
            self.rt.db.set_setting("agenda_token", t)
        return t

    def token_ok(self, got):
        t = self.rt.db.get_setting("agenda_token", "")
        return bool(t) and hmac.compare_digest(str(got or ""), t)

    # ----- 모으기
    def items(self, days=30, today=None):
        rt, c = self.rt, self.conf()
        t0 = today or date.today()
        t1 = t0 + timedelta(days=days)
        out = []

        def add(d, kind, title, note="", ask="", time=""):
            out.append({"date": d.isoformat(), "kind": kind, "title": title, "note": note, "ask": ask, "time": time})
        won = lambda v: f" {int(v):,}원" if c["amount"] and v else ""
        md = lambda d: f"{d.month}월 {d.day}일({DOW[d.weekday()]})"
        if c["fixed"]:
            try:
                for r in rt.fixed.list():
                    if not r.get("active"):
                        continue
                    for d in rt.fixed.dues(r, t0, t1):
                        add(d, "fixed", f"납부: {r['name']}{won(r.get('amount'))}", "우석World 고정지출",
                            f"{md(d)}은 '{r['name']}' 납부일이야. 전날 저녁에 알려주고, 당일 저녁까지 결제 알림이 안 오면 확인해 보라고 말해줘.")
            except Exception:
                pass
        if c["invest"]:
            try:
                for p in rt.dca.plans():
                    if not p.get("enabled"):
                        continue
                    d = t0
                    while d <= t1:
                        if d.day == int(p.get("day") or 0):
                            add(d, "invest", f"적립 매수일: {p.get('name') or p.get('code')}{won(p.get('amount'))}", "우석World 적립식", "")
                        d += timedelta(days=1)
            except Exception:
                pass
            try:
                n = rt.db.one("SELECT COUNT(*) n FROM at_proposals WHERE status='pending'")["n"]
                if n:
                    add(t0, "invest", f"자동매매 개선안 승인 대기 {n}건", "우석World → 투자 → 자동매매에서 승인하거나 반영하지 않기를 골라요", "")
                for r in rt.db.q("SELECT name, mode, end_date FROM at_runs WHERE status IN ('running','paused') AND lab_id IS NULL"):
                    d = date.fromisoformat(r["end_date"])
                    if t0 <= d <= t1:
                        add(d, "invest", f"자동매매 끝나는 날: {r['name'] or ('실전' if r['mode'] == 'real' else '모의')}", "기간이 끝나면 보유 종목을 정리해요", "")
                y = (t0 - timedelta(days=1)).isoformat()
                rows = rt.db.q("SELECT r.mode, SUM(t.entry_day=?) b, SUM(COALESCE(t.exit_day,'')=?) s FROM at_trades t JOIN at_runs r ON r.id=t.run_id "
                               "WHERE (t.entry_day=? OR t.exit_day=?) AND t.status!='cancelled' GROUP BY r.mode", (y, y, y, y))
                parts = [f"{'실전' if r['mode'] == 'real' else '모의'} 매수 {int(r['b'] or 0)}·매도 {int(r['s'] or 0)}" for r in rows if (r["b"] or r["s"])]
                if parts:
                    add(t0, "invest", "어제 자동매매: " + " / ".join(parts), "자세한 내용은 우석World → 투자 → 자동매매", "")
            except Exception:
                pass
        if c["workout"]:
            try:
                hc = rt.health.cfg()
                plan = hc.get("plan") or {}
                for k in range(min(days, 21) + 1):
                    d = t0 + timedelta(days=k)
                    dd = rt.health.plan_for(d, hc)
                    if dd and not dd.get("rest"):
                        # 날짜별 시간표에 운동 시각이 있으면 그 시각으로(없으면 루틴의 시간 글에서 찾는다)
                        tm = next((b[0] for b in dd.get("blocks") or [] if b[1] == "gym"), "") or (re.search(r"(\d{1,2}):(\d{2})", dd.get("time") or "") or [""])[0]
                        add(d, "workout", f"운동: {dd['name']}", " · ".join(x for x in (plan.get("place"), dd.get("time")) if x),
                            f"{md(d)}은 '{dd['name']}' 운동하는 날이야{('(' + dd['time'] + ')') if dd.get('time') else ''}. 시작 30분 전에 알려줘.", tm)
            except Exception:
                pass
        if c["health"]:
            try:
                for r in rt.health.follows():
                    d = date.fromisoformat(r["date"])
                    if not r["done"] and t0 <= d <= t1:
                        add(d, "health", r["title"], "우석World 건강관리", f"{md(d)}에 '{r['title']}' 일정이 있어. 예약이 필요한지 알아보고, 전날 알려줘.")
            except Exception:
                pass
        out.sort(key=lambda x: (x["date"], x["time"] or "99", x["kind"], x["title"]))
        for x in out:
            dd = (date.fromisoformat(x["date"]) - t0).days
            x["dday"], x["when"] = dd, "오늘" if dd == 0 else "내일" if dd == 1 else f"{int(x['date'][5:7])}.{int(x['date'][8:])}"
        return out

    def brief(self, today=None):
        """첫 화면 카드: 앞으로 7일 + 한 번에 넘길 문구."""
        its = self.items(7, today)
        now = [x for x in its if x["dday"] <= 1]
        text = "오늘·내일 챙길 일이야. 시간 맞춰 알려줘.\n" + "\n".join(f"- {x['when']} {x['title']}" for x in now) if now else ""
        return {"items": its[:12], "more": max(0, len(its) - 12), "share": text, "conf": self.conf(), "kinds": KINDS}

    # ----- 캘린더(.ics)
    def ics(self, today=None):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//WooseokWorld//moa//KO", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:우석World", "X-WR-TIMEZONE:Asia/Seoul", "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
        for x in self.items(45, today):
            d = date.fromisoformat(x["date"])
            uid = hashlib.sha1(json.dumps([x["date"], x["kind"], x["title"]], ensure_ascii=False).encode()).hexdigest()[:20]
            L += ["BEGIN:VEVENT", f"UID:{uid}@wooseok.world", f"DTSTAMP:{stamp}"]
            if x["time"]:
                h, m = (int(v) for v in x["time"].split(":"))
                st = datetime(d.year, d.month, d.day, h % 24, m)
                st -= timedelta(hours=9)                 # 한국 시간은 늘 UTC+9(서머타임 없음) → UTC로 적는다
                L += [f"DTSTART:{st:%Y%m%dT%H%M%S}Z", f"DTEND:{st + timedelta(hours=1):%Y%m%dT%H%M%S}Z"]
            else:
                L += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}"]
            L += [f"SUMMARY:{_esc(x['title'])}", f"DESCRIPTION:{_esc(x['note'])}", "TRANSP:TRANSPARENT", "END:VEVENT"]
        L.append("END:VCALENDAR")
        return "\r\n".join(L) + "\r\n"
