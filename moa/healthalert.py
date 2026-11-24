"""건강관리 알림: 오늘 시간표·약·연속 기록·주간 보스·검진 일정·레벨업처럼 놓치면 아쉬운 것만 휴대폰으로.

모두 rt.notify(갈래 "health")로 보낸다 → 앱 알림(웹 푸시)·ntfy 둘 다. 같은 일은 하루(또는 그 일) 한 번만(key).
밤 11시 ~ 아침 7시에는 보내지 않는다. 종류마다 켬/끔은 설정 health_alerts(건강관리 › 알림에서 바꿈).
건강 자료는 서버 DB에서만 읽고, 알림 글에는 몸무게 같은 수치를 넣지 않는다(잠금 화면에 보일 수 있어서).
"""
import time
from datetime import date, datetime, timedelta

KINDS = {
    "quest": ("시간표 알림", "오늘 시간표의 할 일 시각이 되면(아직 안 했을 때만)"),
    "med": ("약 챙기기", "약 먹을 시각에서 1시간 반이 지나도 체크가 없으면 한 번 더"),
    "streak": ("연속 기록 지키기", "밤 9시까지 오늘 기록이 하나도 없으면(연속 2일 이상일 때)"),
    "boss": ("주간 보스", "토요일 저녁, 이번 주 운동이 목표보다 모자라면"),
    "follow": ("검진·병원 일정", "사흘 전과 당일 아침"),
    "weigh": ("몸 기록", "신체 기록이 일주일 넘게 없으면 월요일 아침"),
    "levelup": ("레벨업·업적", "레벨이 오르거나 새 업적을 얻으면"),
    "weekly": ("주간 정리", "일요일 저녁, 이번 주 운동·체크·식단 한 줄"),
}
QUIET = ("23:00", "07:00")
LINK = "#health/today"


class HealthAlert:
    def __init__(self, rt):
        self.rt = rt
        self._at = 0.0

    def conf(self):
        s = self.rt.db.get_setting("health_alerts") or {}
        return {k: bool(s.get(k, True)) for k in KINDS}

    def set(self, d):
        c = self.conf()
        for k in KINDS:
            if k in (d or {}):
                c[k] = bool(d[k])
        self.rt.db.set_setting("health_alerts", c)
        return self.view()

    def view(self):
        c = self.conf()
        log = [r for r in self.rt.notify_log(80) if r["cat"] == "health"][:20]
        return {"kinds": [{"key": k, "label": v[0], "desc": v[1], "on": c[k]} for k, v in KINDS.items()], "cat_on": self.rt.notify_on("health"),
                "log": log, "devices": self.rt.push.count() if getattr(self.rt, "push", None) else 0, "ntfy": bool(self.rt.settings.get("ntfy_topic"))}

    def _send(self, kind, title, msg, key, link=LINK):
        if not self.conf().get(kind, True):
            return False
        return self.rt.notify(msg, "health", title, key=f"hl:{key}", link=link)

    # ------------------------------------------------------------------ 1분마다
    def tick(self, now=None, force=False):
        if not force and now is None and time.time() - self._at < 55:
            return []
        self._at = time.time()
        now = now or datetime.now()
        hm = now.strftime("%H:%M")
        if hm >= QUIET[0] or hm < QUIET[1]:
            return []
        H = self.rt.health
        t = now.date()
        c = H.cfg()
        slots = H.med_slots(c)
        sent = []

        def go(*a, **k):
            if self._send(*a, **k):
                sent.append(a[0])
        # ① 시간표·② 약
        qs = H.quests(c, t, slots)
        med = {m["key"] for m in slots}
        for q in qs:
            tm = q.get("t") or ""
            if len(tm) != 5 or q["on"] or q["kind"] == "info":
                continue
            if tm <= hm < _add(tm, 10):
                go("quest", f"{tm} · {q['title']}", (q.get("sub") or "지금 할 시간이에요")[:120], f"q:{t}:{q['id']}")
            if q["id"] in med and _add(tm, 90) <= hm < _add(tm, 100):
                go("med", f"{q['title']} 아직이에요", "체크가 없어요. 먹었으면 앱에서 체크해 주세요", f"m:{t}:{q['id']}")
        # ③ 연속 기록
        if "21:00" <= hm < "21:10":
            r = H.rpg(c, t, slots)
            if not r["today"] and r["streak"] >= 2:
                go("streak", f"연속 {r['streak']}일이 끊기기 직전", "오늘 기록이 아직 없어요. 체크 하나만 해도 이어져요", f"s:{t}")
        # ④ 주간 보스(토요일 저녁)
        if t.weekday() == 5 and "18:00" <= hm < "18:10":
            wk = H.week()
            if wk["done"] < wk["target"]:
                go("boss", f"이번 주 보스 체력 {wk['target'] - wk['done']} 남음", f"운동 {wk['done']}/{wk['target']}번 · 주말에 {wk['target'] - wk['done']}번 더 하면 처치해요", f"b:{t}")
        # ⑤ 검진·병원
        if "09:00" <= hm < "09:10":
            for f in H.follows():
                if f["done"]:
                    continue
                if f["dday"] in (3, 0):
                    go("follow", f"{'오늘' if f['dday'] == 0 else 'D-3'} · {f.get('title') or '건강 일정'}", f"{f['date']}{(' · ' + f['why']) if f.get('why') else ''}"[:120],
                       f"f:{f.get('id')}:{f['dday']}", link="#health/checkup")
        # ⑥ 몸 기록(월요일 아침)
        if t.weekday() == 0 and "08:30" <= hm < "08:40":
            last = self.rt.db.one("SELECT MAX(date) d FROM hl_body")
            if last and last.get("d") and (t - date.fromisoformat(last["d"])).days >= 7:
                go("weigh", "몸 기록할 때예요", f"마지막 기록이 {(t - date.fromisoformat(last['d'])).days}일 전이에요. 아침에 재면 가장 정확해요", f"w:{t}", link="#health/body")
        # ⑦ 레벨업·업적(바뀌었을 때)
        r = H.rpg(c, t, slots)
        st = self.rt.db.get_setting("health_alert_state") or {}
        got = sorted(b["id"] for b in r["badges"] if b["got"])
        if st:
            if r["level"] > int(st.get("level") or 0):
                go("levelup", f"레벨 {r['level']} 달성!", f"'{r['title']}'이 됐어요. 다음 레벨까지 {r['need'] - r['xp']} 경험치", f"lv:{r['level']}")
            new = [b for b in r["badges"] if b["got"] and b["id"] not in set(st.get("badges") or [])]
            for b in new[:2]:
                go("levelup", f"업적 · {b['title']}", b["desc"], f"bd:{b['id']}")
        if st.get("level") != r["level"] or st.get("badges") != got:
            self.rt.db.set_setting("health_alert_state", {"level": r["level"], "badges": got})
        # ⑧ 주간 정리(일요일 저녁)
        if t.weekday() == 6 and "20:00" <= hm < "20:10":
            wk = H.week()
            mon = t - timedelta(days=6)
            n_chk = self.rt.db.one("SELECT COUNT(*) n FROM hl_check WHERE date BETWEEN ? AND ?", (mon.isoformat(), t.isoformat()))["n"]
            ml = self.rt.db.q("SELECT grade FROM hl_meal WHERE date BETWEEN ? AND ?", (mon.isoformat(), t.isoformat()))
            good = sum(1 for m in ml if m["grade"] == "균형")
            go("weekly", "이번 주 정리", f"운동 {wk['done']}/{wk['target']}번 · 체크 {n_chk}개 · 끼니 기록 {len(ml)}번(균형 {good}) · 연속 {r['streak']}일", f"wk:{t}")
        return sent


def _add(hm, minutes):
    h, m = map(int, hm.split(":"))
    x = h * 60 + m + minutes
    return f"{min(23, x // 60):02d}:{x % 60 if x // 60 < 24 else 59:02d}"
