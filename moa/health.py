"""건강관리: 신체 기록(체중·허리·체지방), 하루 체크, 주간 운동, 검진 수치·추적 일정.

개인 건강 수치는 코드에 넣지 않는다. 전부 이 서버의 DB에만 저장하고,
처음 자료는 앱에서 직접 넣거나 '자료 가져오기'(JSON 파일)로 넣는다.
"""
import base64
import json
import re
import zlib
from datetime import date, datetime, timedelta

# 퀘스트 경험치: 체크 하나 10, 운동 40(+힘든 정도 기록 10), 신체 기록 15, 하루 전부 완료 보너스 20
XP = {"check": 10, "gym": 40, "rpe": 10, "body": 15, "clear": 20, "info": 0, "meal": 10, "plate": 5}
MEAL_SLOTS = ["아침", "점심", "저녁", "간식"]
MEAL_TAGS = ["채소", "단백질", "잡곡", "국물", "튀김·기름", "과식", "늦은 식사", "외식·배달"]
MEAL_Q = {"meal_lunch": "점심", "meal_dinner": "저녁"}
MEAL_T = {"meal_lunch": "12:30", "meal_dinner": "18:40"}
LV_BASE, LV_STEP = 100, 50       # 다음 레벨까지: 100, 150, 200 … (하루를 다 채우면 100 남짓)
TITLES = [(1, "견습 모험가"), (3, "초보 전사"), (5, "단련된 전사"), (8, "숙련 전사"), (12, "베테랑"), (16, "용사"), (20, "전설")]
BOSSES = ["게으름 슬라임", "야식 고블린", "소파 골렘", "작심삼일 오크", "핑계 마법사", "뱃살 드래곤"]
DOW = ["월", "화", "수", "목", "금", "토", "일"]
FLAGS = {"high": "높음", "low": "낮음", "watch": "관리", "check": "확인", "ok": "정상"}
WHEN = {"am": "아침", "noon": "점심", "pm": "저녁", "night": "자기 전"}   # 약 먹는 때

# 처음 쓰는 사람을 위한 기본 루틴·체크 항목(누구에게나 맞는 일반적인 것). 앱에서 고칠 수 있다.
_A = [["체스트프레스", "3×12"], ["랫풀다운", "3×12"], ["레그 익스텐션", "3×12"], ["레그 컬", "3×12"], ["계단 오르기", "10분"], ["플랭크", "30초×3"]]
_B = [["스쿼트", "3×12"], ["케이블 로우", "3×12"], ["숄더프레스", "3×12"], ["백익스텐션", "2×12"], ["일립티컬", "15분"], ["플랭크", "30초×3"]]
_C = [["일립티컬", "20분"], ["실내 자전거", "20분"], ["플랭크", "30초×3"]]
DEFAULT_PLAN = {"place": "헬스장", "days": [
    {"name": "전신 A", "time": "50분", "rest": False, "items": _A},
    {"name": "유산소", "time": "45분", "rest": False, "items": _C},
    {"name": "전신 B", "time": "50분", "rest": False, "items": _B},
    {"name": "유산소", "time": "45분", "rest": False, "items": _C},
    {"name": "전신 A", "time": "50분", "rest": False, "items": _A},
    {"name": "가볍게", "time": "40분", "rest": False, "items": [["실내 자전거(가볍게)", "30분"], ["스트레칭", "10분"]]},
    {"name": "휴식", "time": "기록하는 날", "rest": True, "items": [["공복 체중·허리 재기", "기록"], ["다음 주 계획 보기", ""]]},
]}
DEFAULT_HABITS = [
    {"id": "water", "title": "물 2L", "sub": "하루 동안 나눠 마시기"},
    {"id": "veg", "title": "매끼 채소 한 접시", "sub": "점심·저녁에 채소 먼저"},
    {"id": "alcohol", "title": "금주", "sub": "오늘은 마시지 않기"},
]
DEFAULT_CFG = {"height": None, "goal": {"weight": None, "waist": None, "fat": None, "date": "", "label": "", "start_weight": None, "start_date": ""},
               "habits": DEFAULT_HABITS, "plan": DEFAULT_PLAN, "note": "", "meds": []}


class HealthError(ValueError):
    pass


def _d(s, what="날짜"):
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        raise HealthError(f"{what}를 2026-11-23처럼 넣어 주세요")


def _num(v, lo, hi, what):
    if v is None or str(v).strip() == "":
        return None
    try:
        f = round(float(str(v).replace(",", "").strip()), 1)
    except ValueError:
        raise HealthError(f"{what}는 숫자로 넣어 주세요")
    if not lo <= f <= hi:
        raise HealthError(f"{what}는 {lo:g}~{hi:g} 사이로 넣어 주세요")
    return f


def _txt(v, n):
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


class Health:
    def __init__(self, db, today=None):
        self.db = db
        self._today = today or date.today
        db.x("""CREATE TABLE IF NOT EXISTS hl_body(
            date TEXT PRIMARY KEY, weight REAL, waist REAL, fat REAL, note TEXT NOT NULL DEFAULT '')""")
        db.x("CREATE TABLE IF NOT EXISTS hl_check(date TEXT NOT NULL, key TEXT NOT NULL, PRIMARY KEY(date, key))")
        db.x("""CREATE TABLE IF NOT EXISTS hl_workout(
            date TEXT PRIMARY KEY, done INTEGER NOT NULL DEFAULT 0, rpe INTEGER, name TEXT NOT NULL DEFAULT '')""")
        db.x("""CREATE TABLE IF NOT EXISTS hl_lab(
            id INTEGER PRIMARY KEY, date TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL,
            ref TEXT NOT NULL DEFAULT '', flag TEXT NOT NULL DEFAULT 'ok', sort INTEGER NOT NULL DEFAULT 0)""")
        db.x("""CREATE TABLE IF NOT EXISTS hl_follow(
            id INTEGER PRIMARY KEY, date TEXT NOT NULL, title TEXT NOT NULL, why TEXT NOT NULL DEFAULT '',
            done INTEGER NOT NULL DEFAULT 0)""")
        # 헬스장 기구 사진(내 휴대폰으로 찍은 것). 저장소가 아니라 이 서버에만 둔다
        db.x("CREATE TABLE IF NOT EXISTS hl_photo(key TEXT PRIMARY KEY, full BLOB NOT NULL, thumb BLOB NOT NULL, updated TEXT NOT NULL)")
        # 식단: 끼니 기록(무엇을, 어떤 접시였는지 꼬리표)과 요리책(지난 대화의 레시피가 처음 자료)
        db.x("""CREATE TABLE IF NOT EXISTS hl_meal(id INTEGER PRIMARY KEY, date TEXT NOT NULL, slot TEXT NOT NULL, menu TEXT NOT NULL DEFAULT '',
                recipe INTEGER, tags TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '', ts TEXT NOT NULL)""")
        db.x("CREATE INDEX IF NOT EXISTS ix_hl_meal ON hl_meal(date)")
        db.x("""CREATE TABLE IF NOT EXISTS hl_recipe(id INTEGER PRIMARY KEY, key TEXT, title TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]',
                ingredients TEXT NOT NULL DEFAULT '[]', steps TEXT NOT NULL DEFAULT '[]', memo TEXT NOT NULL DEFAULT '', time TEXT NOT NULL DEFAULT '',
                serves TEXT NOT NULL DEFAULT '', src TEXT NOT NULL DEFAULT '', fav INTEGER NOT NULL DEFAULT 0, cooked INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL)""")
        self._seed_recipes()

    # ---------- 설정(목표·루틴·체크 항목) ----------
    def cfg(self):
        c = json.loads(json.dumps(DEFAULT_CFG))
        saved = self.db.get_setting("health") or {}
        for k in ("height", "note"):
            if saved.get(k) is not None:
                c[k] = saved[k]
        c["goal"].update(saved.get("goal") or {})
        if saved.get("habits"):
            c["habits"] = saved["habits"]
        if saved.get("plan"):
            c["plan"] = saved["plan"]
        c["meds"] = saved.get("meds") or []
        return c

    def save_cfg(self, d):
        c = self.cfg()
        if "height" in d:
            c["height"] = _num(d["height"], 100, 230, "키")
        if "note" in d:
            c["note"] = _txt(d["note"], 300)
        if isinstance(d.get("goal"), dict):
            g, n = c["goal"], d["goal"]
            for k, lo, hi, what in (("weight", 30, 250, "목표 체중"), ("waist", 40, 200, "목표 허리"), ("fat", 3, 60, "목표 체지방"),
                                    ("start_weight", 30, 250, "시작 체중")):
                if k in n:
                    g[k] = _num(n[k], lo, hi, what)
            for k, what in (("date", "목표 날짜"), ("start_date", "시작 날짜")):
                if k in n:
                    g[k] = _d(n[k], what).isoformat() if str(n[k] or "").strip() else ""
            if "label" in n:
                g["label"] = _txt(n["label"], 30)
        if "habits" in d:
            c["habits"] = self._habits(d["habits"], c["habits"])
        if "plan" in d:
            c["plan"] = self._plan(d["plan"])
        if "meds" in d:
            c["meds"] = self._meds(d["meds"])
        self.db.set_setting("health", c)
        return c

    @staticmethod
    def _habits(rows, old):
        if not isinstance(rows, list) or not rows:
            raise HealthError("체크 항목을 하나 이상 넣어 주세요")
        by_title = {h["title"]: h["id"] for h in old}
        used, out = set(), []
        for i, r in enumerate(rows[:8]):
            title = _txt((r or {}).get("title"), 24)
            if not title:
                continue
            hid = _txt(r.get("id"), 20) or by_title.get(title) or f"h{i}{zlib.crc32(title.encode()) % 100000}"
            if not re.fullmatch(r"[A-Za-z0-9_]{1,20}", hid) or hid in used or hid == "gym":
                hid = f"h{i}{zlib.crc32(title.encode()) % 100000}"
            used.add(hid)
            out.append({"id": hid, "title": title, "sub": _txt(r.get("sub"), 50)})
        if not out:
            raise HealthError("체크 항목을 하나 이상 넣어 주세요")
        return out

    @staticmethod
    def _meds(rows):
        """먹는 약 목록. 이름·용량·먹는 때만 적는다(먹었는지는 하루 체크에 때마다 한 번)."""
        out = []
        for r in (rows if isinstance(rows, list) else [])[:20]:
            name = _txt((r or {}).get("name"), 30)
            if name:
                out.append({"name": name, "dose": _txt(r.get("dose"), 24), "note": _txt(r.get("note"), 40),
                            "when": r.get("when") if r.get("when") in WHEN else "am"})
        out.sort(key=lambda m: list(WHEN).index(m["when"]))
        return out

    def med_slots(self, c=None):
        """약을 먹는 때마다 한 줄: [{key, when, label, names}]."""
        meds = (c or self.cfg())["meds"]
        return [{"key": "med_" + w, "when": w, "label": WHEN[w] + " 약", "names": [m["name"] for m in meds if m["when"] == w]}
                for w in WHEN if any(m["when"] == w for m in meds)]

    @staticmethod
    def _plan(p):
        days = (p or {}).get("days")
        if not isinstance(days, list) or len(days) != 7:
            raise HealthError("요일 7개의 루틴이 모두 있어야 해요")
        out = []
        for dd in days:
            name = _txt((dd or {}).get("name"), 16)
            if not name:
                raise HealthError("요일마다 루틴 이름을 넣어 주세요")
            items = []
            for it in (dd.get("items") or [])[:12]:
                n = _txt(it[0] if isinstance(it, (list, tuple)) and it else "", 30)
                if n:
                    items.append([n, _txt(it[1] if len(it) > 1 else "", 14)])
            out.append({"name": name, "time": _txt(dd.get("time"), 12), "rest": bool(dd.get("rest")), "items": items})
        # 날짜별 계획(cal): 그날만의 루틴 이름·시간·할 일 시간표. 없는 날은 요일 루틴 그대로
        cal = {}
        old = (date.today() - timedelta(days=60)).isoformat()
        for k, v in sorted(((p or {}).get("cal") or {}).items())[-150:]:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(k)) or k < old or not isinstance(v, dict):
                continue
            blocks = []
            for b in (v.get("blocks") or [])[:16]:
                if isinstance(b, (list, tuple)) and len(b) >= 3 and re.fullmatch(r"\d{2}:\d{2}", str(b[0])):
                    blocks.append([str(b[0]), _txt(b[1], 20), _txt(b[2], 40), _txt(b[3] if len(b) > 3 else "", 70)])
            e = {"mode": _txt(v.get("mode"), 10), "blocks": blocks}
            if _txt(v.get("name"), 16):
                its = []
                for it in (v.get("items") or [])[:12]:
                    n = _txt(it[0] if isinstance(it, (list, tuple)) and it else "", 30)
                    if n:
                        its.append([n, _txt(it[1] if len(it) > 1 else "", 14)])
                e.update(name=_txt(v.get("name"), 16), time=_txt(v.get("time"), 12), rest=bool(v.get("rest")), items=its)
            cal[k] = e
        return {"place": _txt(p.get("place"), 20), "days": out, "cal": cal}

    def plan_for(self, day, c=None):
        """그날의 계획: 요일 루틴에 날짜별 계획(cal)을 덮어쓴 것."""
        plan = (c or self.cfg())["plan"]
        p = dict(plan["days"][day.weekday()])
        o = (plan.get("cal") or {}).get(day.isoformat())
        p.update(mode="", blocks=[])
        if o:
            p.update({k: o[k] for k in ("name", "time", "rest", "items") if k in o})
            p.update(mode=o.get("mode") or "", blocks=o.get("blocks") or [])
        return p

    # ---------- 신체 기록 ----------
    def save_body(self, d):
        day = _d(d.get("date") or self._today().isoformat())
        if day > self._today():
            raise HealthError("앞날의 기록은 넣을 수 없어요")
        w, wa, f = _num(d.get("weight"), 30, 250, "체중"), _num(d.get("waist"), 40, 200, "허리"), _num(d.get("fat"), 3, 60, "체지방")
        if w is None and wa is None and f is None:
            raise HealthError("체중·허리·체지방 중 하나는 넣어 주세요")
        old = self.db.one("SELECT * FROM hl_body WHERE date=?", (day.isoformat(),)) or {}
        if not d.get("replace"):   # 같은 날 다시 넣으면 비운 칸은 예전 값을 둔다
            w, wa, f = (w if w is not None else old.get("weight")), (wa if wa is not None else old.get("waist")), (f if f is not None else old.get("fat"))
        self.db.x("INSERT INTO hl_body(date,weight,waist,fat,note) VALUES(?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET "
                  "weight=excluded.weight, waist=excluded.waist, fat=excluded.fat, note=excluded.note",
                  (day.isoformat(), w, wa, f, _txt(d.get("note") if "note" in d else old.get("note"), 30)))
        return day.isoformat()

    def delete_body(self, day):
        self.db.x("DELETE FROM hl_body WHERE date=?", (_d(day).isoformat(),))

    def body(self):
        rows = self.db.q("SELECT * FROM hl_body ORDER BY date")
        latest = {}
        for k in ("weight", "waist", "fat"):
            r = next((x for x in reversed(rows) if x[k] is not None), None)
            latest[k] = {"value": r[k], "date": r["date"]} if r else None
        return rows[-400:], latest

    # ---------- 하루 체크·운동 ----------
    def set_check(self, d):
        day = _d(d.get("date") or self._today().isoformat()).isoformat()
        key = _txt(d.get("key"), 20)
        c = self.cfg()
        if key not in {h["id"] for h in c["habits"]} | {m["key"] for m in self.med_slots(c)}:
            raise HealthError("없는 체크 항목이에요")
        if d.get("on"):
            self.db.x("INSERT OR IGNORE INTO hl_check(date,key) VALUES(?,?)", (day, key))
        else:
            self.db.x("DELETE FROM hl_check WHERE date=? AND key=?", (day, key))

    def set_workout(self, d):
        day = _d(d.get("date") or self._today().isoformat())
        if day > self._today():
            raise HealthError("앞날의 운동은 미리 완료로 둘 수 없어요")
        old = self.db.one("SELECT * FROM hl_workout WHERE date=?", (day.isoformat(),)) or {"done": 0, "rpe": None}
        done = int(bool(d["done"])) if "done" in d else old["done"]
        rpe = old["rpe"]
        if "rpe" in d:
            rpe = None if d["rpe"] in (None, "", 0) else int(d["rpe"])
            if rpe is not None and not 1 <= rpe <= 10:
                raise HealthError("힘든 정도는 1~10으로 넣어 주세요")
        name = self.plan_for(day)["name"]
        self.db.x("INSERT INTO hl_workout(date,done,rpe,name) VALUES(?,?,?,?) ON CONFLICT(date) DO UPDATE SET "
                  "done=excluded.done, rpe=excluded.rpe, name=excluded.name", (day.isoformat(), done, rpe, name))

    def week(self, start=None):
        t = self._today()
        mon = _d(start) if start else t
        mon -= timedelta(days=mon.weekday())
        days = [mon + timedelta(days=i) for i in range(7)]
        got = {r["date"]: r for r in self.db.q("SELECT * FROM hl_workout WHERE date BETWEEN ? AND ?", (days[0].isoformat(), days[6].isoformat()))}
        c = self.cfg()
        plan = [self.plan_for(dd, c) for dd in days]
        out = []
        for i, dd in enumerate(days):
            r = got.get(dd.isoformat()) or {}
            out.append({"date": dd.isoformat(), "dow": DOW[i], "done": bool(r.get("done")), "rpe": r.get("rpe"),
                        "today": dd == t, "future": dd > t, "name": plan[i]["name"], "time": plan[i]["time"], "rest": plan[i]["rest"],
                        "items": plan[i]["items"], "mode": plan[i]["mode"], "blocks": plan[i]["blocks"]})
        target = sum(1 for p in plan if not p["rest"])
        done = sum(1 for i, o in enumerate(out) if o["done"] and not plan[i]["rest"])
        rpes = [o["rpe"] for o in out if o["rpe"]]
        return {"start": days[0].isoformat(), "end": days[6].isoformat(), "days": out, "target": target, "done": done,
                "rpe_avg": round(sum(rpes) / len(rpes), 1) if rpes else None, "this_week": days[0] <= t <= days[6]}

    # ---------- 검진 수치·추적 일정 ----------
    def save_lab(self, d):
        name, value = _txt(d.get("name"), 30), _txt(d.get("value"), 16)
        if not name or not value:
            raise HealthError("항목 이름과 수치를 넣어 주세요")
        flag = d.get("flag") if d.get("flag") in FLAGS else "ok"
        args = (_d(d.get("date") or self._today().isoformat(), "검사 날짜").isoformat(), name, value, _txt(d.get("ref"), 50), flag)
        if d.get("id"):
            self.db.x("UPDATE hl_lab SET date=?, name=?, value=?, ref=?, flag=? WHERE id=?", args + (int(d["id"]),))
            return int(d["id"])
        return self.db.x("INSERT INTO hl_lab(date,name,value,ref,flag,sort) VALUES(?,?,?,?,?,?)", args + (int(d.get("sort") or 0),))

    def delete_lab(self, lid):
        self.db.x("DELETE FROM hl_lab WHERE id=?", (int(lid),))

    def labs(self):
        """항목마다 가장 최근 수치와 그 앞 수치(변화를 보려고)."""
        rows = self.db.q("SELECT * FROM hl_lab ORDER BY date DESC, sort, id")
        seen, out = {}, []
        for r in rows:
            if r["name"] in seen:
                cur = seen[r["name"]]
                if cur.get("prev") is None:
                    cur["prev"] = {"value": r["value"], "date": r["date"]}
                cur["n"] += 1
                continue
            r["flag_label"], r["prev"], r["n"] = FLAGS.get(r["flag"], "정상"), None, 1
            seen[r["name"]] = r
            out.append(r)
        order = {"high": 0, "low": 1, "watch": 2, "check": 3, "ok": 4}
        out.sort(key=lambda r: (order.get(r["flag"], 4), r["sort"], r["id"]))
        return out, (rows[0]["date"] if rows else "")

    def save_follow(self, d):
        title = _txt(d.get("title"), 40)
        if not title:
            raise HealthError("일정 이름을 넣어 주세요")
        args = (_d(d.get("date"), "검사 날짜").isoformat(), title, _txt(d.get("why"), 40), int(bool(d.get("done"))))
        if d.get("id"):
            self.db.x("UPDATE hl_follow SET date=?, title=?, why=?, done=? WHERE id=?", args + (int(d["id"]),))
            return int(d["id"])
        return self.db.x("INSERT INTO hl_follow(date,title,why,done) VALUES(?,?,?,?)", args)

    def delete_follow(self, fid):
        self.db.x("DELETE FROM hl_follow WHERE id=?", (int(fid),))

    def follows(self):
        t = self._today()
        rows = self.db.q("SELECT * FROM hl_follow ORDER BY done, date, id")
        nxt = False
        for r in rows:
            r["dday"] = (date.fromisoformat(r["date"]) - t).days
            r["done"] = bool(r["done"])
            r["next"] = not r["done"] and not nxt
            nxt = nxt or r["next"]
        return rows

    # ---------- 기구 사진 ----------
    @staticmethod
    def _jpeg(v, limit, what):
        s = str(v or "")
        if s.startswith("data:"):
            s = s.split(",", 1)[-1]
        try:
            raw = base64.b64decode(s, validate=True)
        except Exception:
            raise HealthError(f"{what}을 읽지 못했어요")
        if raw[:3] != b"\xff\xd8\xff":
            raise HealthError("사진(JPEG)만 넣을 수 있어요")
        if len(raw) > limit:
            raise HealthError("사진이 너무 커요")
        return raw

    def save_photo(self, key, d):
        if not re.fullmatch(r"[a-z0-9_]{1,30}", str(key)):
            raise HealthError("없는 기구예요")
        full, thumb = self._jpeg(d.get("full"), 2_000_000, "사진"), self._jpeg(d.get("thumb"), 300_000, "작은 사진")
        now = datetime.now().isoformat(timespec="seconds")
        self.db.x("INSERT INTO hl_photo(key,full,thumb,updated) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                  "full=excluded.full, thumb=excluded.thumb, updated=excluded.updated", (key, full, thumb, now))
        return now

    def import_folder(self, folder=None):
        """이 컴퓨터의 폴더(기본: 사진\\moa_gym_photos)에 놓인 기구 사진을 한꺼번에 넣는다. 파일 이름은 <기구>.jpg 와 <기구>.thumb.jpg 한 쌍.
        사진을 저장소에 올리지 않고 노트북으로 바로 건네기 위한 길이다. 넣은 파일은 폴더에서 지운다."""
        from pathlib import Path
        d = Path(folder) if folder else Path.home() / "Pictures" / "moa_gym_photos"
        done, skipped = [], []
        if not d.is_dir():
            return {"n": 0, "keys": [], "skipped": [], "folder": str(d), "found": False}
        for f in sorted(d.glob("*.jpg")):
            if f.name.endswith(".thumb.jpg"):
                continue
            key, th = f.stem, f.with_name(f.stem + ".thumb.jpg")
            try:
                if not th.exists():
                    raise HealthError("작은 사진이 없어요")
                self.save_photo(key, {"full": base64.b64encode(f.read_bytes()).decode(), "thumb": base64.b64encode(th.read_bytes()).decode()})
                done.append(key)
                f.unlink()
                th.unlink()
            except (HealthError, OSError) as e:
                skipped.append({"file": f.name, "why": str(e)})
        try:
            d.rmdir()                                    # 다 넣어서 비었으면 폴더도 치운다
        except OSError:
            pass
        return {"n": len(done), "keys": done, "skipped": skipped, "folder": str(d), "found": True}

    def photo(self, key, thumb=False):
        r = self.db.one(f"SELECT {'thumb' if thumb else 'full'} AS b FROM hl_photo WHERE key=?", (str(key),))
        return bytes(r["b"]) if r else None

    def delete_photo(self, key):
        self.db.x("DELETE FROM hl_photo WHERE key=?", (str(key),))

    def photos(self):
        return {r["key"]: r["updated"] for r in self.db.q("SELECT key, updated FROM hl_photo")}

    # ---------- 화면 한 번에 ----------
    def overview(self, week=None):
        t = self._today()
        c = self.cfg()
        rows, latest = self.body()
        g = c["goal"]
        start_w = g.get("start_weight") or (next((r["weight"] for r in rows if r["weight"] is not None), None))
        prog = None
        if latest["weight"] and g.get("weight") and start_w and start_w != g["weight"]:
            prog = max(0.0, min(1.0, (start_w - latest["weight"]["value"]) / (start_w - g["weight"])))
        week_no = None
        if g.get("start_date"):
            s = date.fromisoformat(g["start_date"])
            s -= timedelta(days=s.weekday())
            week_no = (t - s).days // 7 + 1 if t >= s else None
        labs, lab_date = self.labs()
        bmi = None
        if c.get("height") and latest["weight"]:
            bmi = round(latest["weight"]["value"] / (c["height"] / 100) ** 2, 1)
        slots = self.med_slots(c)
        since = (t - timedelta(days=6)).isoformat()
        for m in slots:   # 최근 7일 중 먹은 날
            m["week"] = (self.db.one("SELECT COUNT(*) AS n FROM hl_check WHERE key=? AND date BETWEEN ? AND ?", (m["key"], since, t.isoformat())) or {"n": 0})["n"]
        return {"today": t.isoformat(), "dow": DOW[t.weekday()], "week_no": week_no, "cfg": c, "med_slots": slots, "when": [[k, v] for k, v in WHEN.items()], "flags": [[k, v] for k, v in FLAGS.items()],
                "body": {"rows": rows, "latest": latest, "start_weight": start_w, "progress": prog, "bmi": bmi},
                "checks": [r["key"] for r in self.db.q("SELECT key FROM hl_check WHERE date=?", (t.isoformat(),))],
                "week": self.week(week), "now_week": self.week() if week else None,
                "labs": labs, "lab_date": lab_date, "follows": self.follows(), "empty": not rows and not labs, "photos": self.photos(),
                "quests": self.quests(c, t, slots), "rpg": self.rpg(c, t, slots),
                "meals": self.meals(t), "meal_week": self.meal_week(t), "suggest": self.suggest(t), "meal_tags": MEAL_TAGS, "meal_slots": MEAL_SLOTS,
                "recipe_n": (self.db.one("SELECT COUNT(*) n FROM hl_recipe") or {"n": 0})["n"]}

    # ---------- 식단: 요리책 ----------
    def _seed_recipes(self):
        """지난 대화에서 만든 레시피를 처음 한 번 넣는다. 사용자가 지운 처음 자료는 다시 넣지 않는다."""
        from .recipes import SEED
        have = {r["key"] for r in self.db.q("SELECT key FROM hl_recipe WHERE key IS NOT NULL")}
        gone = set(self.db.get_setting("health_recipe_gone") or [])
        now = datetime.now().isoformat(timespec="seconds")
        for r in SEED:
            if r["key"] not in have and r["key"] not in gone:
                self.db.x("INSERT INTO hl_recipe(key,title,tags,ingredients,steps,memo,time,serves,src,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (r["key"], r["title"], json.dumps(r["tags"], ensure_ascii=False), json.dumps(r["ingredients"], ensure_ascii=False),
                           json.dumps(r["steps"], ensure_ascii=False), r["memo"], r["time"], r["serves"], r["from"], now))

    @staticmethod
    def _rrow(r):
        out = dict(r)
        for k in ("tags", "ingredients", "steps"):
            out[k] = json.loads(out[k] or "[]")
        t = set(out["tags"])
        out["grade"] = "균형" if {"채소", "단백질"} <= t else "가끔" if t & {"짭짤", "기름"} else ""
        return out

    def recipes(self):
        return [self._rrow(r) for r in self.db.q("SELECT * FROM hl_recipe ORDER BY fav DESC, cooked DESC, id")]

    def save_recipe(self, d):
        title = _txt(d.get("title"), 40)
        if not title:
            raise HealthError("요리 이름을 넣어 주세요")
        lines = lambda v, n, m: [_txt(x, m) for x in (v if isinstance(v, list) else re.split(r"[\n,，]" if n == "i" else r"\n", str(v or ""))) if _txt(x, m)][:30]
        ing, steps = lines(d.get("ingredients"), "i", 60), [re.sub(r"^\d+[.)]\s*", "", x) for x in lines(d.get("steps"), "s", 200)]
        if not ing or not steps:
            raise HealthError("재료와 만드는 법을 한 가지 이상 넣어 주세요")
        tags = [_txt(x, 10).lstrip("#") for x in (d.get("tags") if isinstance(d.get("tags"), list) else re.split(r"[,，\s]+", str(d.get("tags") or ""))) if _txt(x, 10).lstrip("#")][:8]
        vals = (title, json.dumps(tags, ensure_ascii=False), json.dumps(ing, ensure_ascii=False), json.dumps(steps, ensure_ascii=False),
                _txt(d.get("memo"), 400), _txt(d.get("time"), 16), _txt(d.get("serves"), 20), datetime.now().isoformat(timespec="seconds"))
        rid = int(d.get("id") or 0)
        if rid and self.db.one("SELECT 1 x FROM hl_recipe WHERE id=?", (rid,)):
            self.db.x("UPDATE hl_recipe SET title=?,tags=?,ingredients=?,steps=?,memo=?,time=?,serves=?,updated=? WHERE id=?", vals + (rid,))
        else:
            rid = self.db.x("INSERT INTO hl_recipe(title,tags,ingredients,steps,memo,time,serves,updated,src) VALUES(?,?,?,?,?,?,?,?,'직접 넣음')", vals)
        return rid

    def fav_recipe(self, rid, on):
        self.db.x("UPDATE hl_recipe SET fav=? WHERE id=?", (1 if on else 0, int(rid)))

    def delete_recipe(self, rid):
        r = self.db.one("SELECT key FROM hl_recipe WHERE id=?", (int(rid),))
        if r and r["key"]:
            gone = set(self.db.get_setting("health_recipe_gone") or []) | {r["key"]}
            self.db.set_setting("health_recipe_gone", sorted(gone))
        self.db.x("DELETE FROM hl_recipe WHERE id=?", (int(rid),))

    def suggest(self, t=None, n=3):
        """오늘 뭐 먹지: 균형 잡힌 요리(채소+단백질)를 먼저, 최근 3일에 먹은 것은 뒤로. 날마다 순서가 바뀐다."""
        t = t or self._today()
        recent = {r["recipe"] for r in self.db.q("SELECT recipe FROM hl_meal WHERE date>=? AND recipe IS NOT NULL", ((t - timedelta(days=3)).isoformat(),))}
        rs = [r for r in self.recipes() if "양념장" not in r["tags"] and "밑반찬" not in r["tags"]]
        seed = t.toordinal()
        rs.sort(key=lambda r: (r["id"] in recent, {"균형": 0, "": 1, "가끔": 2}[r["grade"]], (r["id"] * 7919 + seed) % 97))
        return [{"id": r["id"], "title": r["title"], "grade": r["grade"], "time": r["time"], "tags": r["tags"]} for r in rs[:n]]

    # ---------- 식단: 끼니 기록 ----------
    def save_meal(self, d):
        day = _d(d.get("date") or self._today().isoformat())
        if day > self._today():
            raise HealthError("앞날의 끼니는 미리 기록할 수 없어요")
        slot = d.get("slot") if d.get("slot") in MEAL_SLOTS else "간식"
        menu = _txt(d.get("menu"), 60)
        rid = int(d.get("recipe") or 0) or None
        if rid:
            r = self.db.one("SELECT title FROM hl_recipe WHERE id=?", (rid,))
            if not r:
                rid = None
            elif not menu:
                menu = r["title"]
        if not menu:
            raise HealthError("무엇을 먹었는지 적어 주세요")
        tags = [x for x in (d.get("tags") or []) if x in MEAL_TAGS]
        now = datetime.now().isoformat(timespec="seconds")
        mid = int(d.get("id") or 0)
        if mid and self.db.one("SELECT 1 x FROM hl_meal WHERE id=?", (mid,)):
            self.db.x("UPDATE hl_meal SET date=?,slot=?,menu=?,recipe=?,tags=?,note=? WHERE id=?",
                      (day.isoformat(), slot, menu, rid, json.dumps(tags, ensure_ascii=False), _txt(d.get("note"), 120), mid))
        else:
            mid = self.db.x("INSERT INTO hl_meal(date,slot,menu,recipe,tags,note,ts) VALUES(?,?,?,?,?,?,?)",
                            (day.isoformat(), slot, menu, rid, json.dumps(tags, ensure_ascii=False), _txt(d.get("note"), 120), now))
            if rid:
                self.db.x("UPDATE hl_recipe SET cooked=cooked+1 WHERE id=?", (rid,))
        return mid

    def delete_meal(self, mid):
        self.db.x("DELETE FROM hl_meal WHERE id=?", (int(mid),))

    @staticmethod
    def meal_grade(tags):
        t = set(tags)
        if t & {"튀김·기름", "과식", "늦은 식사"}:
            return "가끔"
        return "균형" if {"채소", "단백질"} <= t else "보통"

    def meals(self, day):
        out = []
        for r in self.db.q("SELECT * FROM hl_meal WHERE date=? ORDER BY id", (day.isoformat() if hasattr(day, "isoformat") else day,)):
            r = dict(r)
            r["tags"] = json.loads(r["tags"] or "[]")
            r["grade"] = self.meal_grade(r["tags"])
            out.append(r)
        out.sort(key=lambda m: MEAL_SLOTS.index(m["slot"]))
        return out

    def meal_week(self, t=None):
        t = t or self._today()
        days = [t - timedelta(days=i) for i in range(6, -1, -1)]
        rows = self.db.q("SELECT date, tags FROM hl_meal WHERE date BETWEEN ? AND ?", (days[0].isoformat(), t.isoformat()))
        by = {}
        cnt = {k: 0 for k in MEAL_TAGS}
        good = 0
        for r in rows:
            tg = json.loads(r["tags"] or "[]")
            g = self.meal_grade(tg)
            by.setdefault(r["date"], []).append(g)
            good += g == "균형"
            for k in tg:
                cnt[k] = cnt.get(k, 0) + 1
        return {"n": len(rows), "good": good, "tags": cnt,
                "days": [{"date": dd.isoformat(), "dow": DOW[dd.weekday()], "grades": by.get(dd.isoformat(), []), "today": dd == t} for dd in days]}

    # ---------- 퀘스트 · 레벨(기록에서 그때그때 셈한다. 따로 저장하지 않는다) ----------
    def quests(self, c, t, slots):
        """오늘의 퀘스트: 날짜별 시간표(blocks)가 있으면 그 순서·시간대로, 없으면 약 → 운동 → 습관."""
        p = self.plan_for(t, c)
        iso = t.isoformat()
        checks = {r["key"] for r in self.db.q("SELECT key FROM hl_check WHERE date=?", (iso,))}
        w = self.db.one("SELECT done FROM hl_workout WHERE date=?", (iso,)) or {}
        body = bool(self.db.one("SELECT 1 x FROM hl_body WHERE date=?", (iso,)))
        base = {m["key"]: ("check", f"{m['label']} {len(m['names'])}가지", ", ".join(m["names"])) for m in slots}
        base["gym"] = ("gym", ("오늘 할 일 · " if p["rest"] else "운동 · ") + p["name"], " · ".join(x for x in (p["time"], ", ".join(i[0] for i in p["items"][:4])) if x))
        base.update({h["id"]: ("check", h["title"], h.get("sub") or "") for h in c["habits"]})
        logged = {m["slot"]: m for m in self.meals(t)}
        for sl, qid in (("점심", "meal_lunch"), ("저녁", "meal_dinner")):
            m = logged.get(sl)
            base[qid] = ("meal", f"{sl} 기록", f"{m['menu']} · {m['grade']}" if m else "무엇을 먹었는지 남기면 식단 점수가 쌓여요")
        out, seen = [], set()

        def add(qid, tm, title, sub):
            kind = base[qid][0] if qid in base else "body" if qid == "body" else "info"
            if qid in seen and kind != "info":
                return
            seen.add(qid)
            on = (qid in checks) if kind == "check" else bool(w.get("done")) if kind == "gym" else body if kind == "body" else \
                (MEAL_Q[qid] in logged) if kind == "meal" else False
            if kind == "meal":                             # 끼니는 기록한 내용이 보이게(시간표 제목보다 우선)
                title, sub = base[qid][1], base[qid][2]
            out.append({"id": qid, "kind": kind, "t": tm, "title": title or (base[qid][1] if qid in base else ""), "sub": sub or (base[qid][2] if qid in base else ""),
                        "xp": XP.get(kind, 0), "on": on, **({"slot": MEAL_Q[qid]} if kind == "meal" else {})})
        for b in p["blocks"]:
            add(b[1], b[0], b[2], b[3])
        for qid in base:                               # 시간표에 없는 항목은 뒤에(시간 없이). 끼니는 기본 시각을 붙인다
            if qid not in seen:
                add(qid, MEAL_T.get(qid, ""), "", "")
        out.sort(key=lambda q: q["t"] or "99")
        return out

    def _daymap(self, a=None, b=None):
        """날짜별 기록 묶음 {날짜: {c: 체크한 것, w: 운동, b: 신체 기록 여부, m: [(끼니, 등급)]}}. a~b를 주면 그 기간만."""
        w, arg = (" WHERE date BETWEEN ? AND ?", (a, b)) if a else ("", ())
        days = {}
        new = lambda: {"c": set(), "w": None, "b": False, "m": []}
        for r in self.db.q("SELECT date, key FROM hl_check" + w, arg):
            days.setdefault(r["date"], new())["c"].add(r["key"])
        for r in self.db.q("SELECT date, done, rpe, name FROM hl_workout" + w, arg):
            days.setdefault(r["date"], new())["w"] = r
        for r in self.db.q("SELECT date FROM hl_body" + w, arg):
            days.setdefault(r["date"], new())["b"] = True
        for r in self.db.q("SELECT date, slot, tags FROM hl_meal" + w + " ORDER BY id", arg):
            days.setdefault(r["date"], new())["m"].append((r["slot"], self.meal_grade(json.loads(r["tags"] or "[]"))))
        return days

    @staticmethod
    def _day_xp(v, need_ids):
        """하루 경험치와 올 클리어 여부. 레벨 셈과 달력이 같은 식을 쓴다."""
        done = bool(v["w"] and v["w"]["done"])
        main = {s_ for s_, _ in v["m"] if s_ in ("점심", "저녁")}
        x = XP["check"] * len(v["c"]) + (XP["gym"] + (XP["rpe"] if v["w"]["rpe"] else 0) if done else 0) + (XP["body"] if v["b"] else 0) \
            + XP["meal"] * len(main) + XP["plate"] * sum(1 for _, g in v["m"] if g == "균형")
        clear = bool(done and need_ids and need_ids <= v["c"] and len(main) == 2)
        return x + (XP["clear"] if clear else 0), clear

    # ---------- 달력: 날마다 무엇을 했는지 ----------
    def calendar(self, month=None):
        """한 달 달력. 칸마다 찍을 표시(운동·체크·끼니·신체·경험치)와, 날짜를 눌렀을 때 보일 그날 내역을 함께 준다.
        앞날은 계획만 보여 준다. '운동 빠짐'은 첫 기록이 생긴 날부터만 센다(쓰기 전 날들을 빠진 날로 만들지 않는다)."""
        t = self._today()
        try:
            y, m = (int(x) for x in str(month or t.isoformat()[:7]).split("-")[:2])
            first = date(y, m, 1)
        except ValueError:
            raise HealthError("달을 2026-11처럼 넣어 주세요")
        nxt = (first + timedelta(days=32)).replace(day=1)
        last, prev = nxt - timedelta(days=1), (first - timedelta(days=1)).replace(day=1)
        c = self.cfg()
        slots = self.med_slots(c)
        items = [{"id": m_["key"], "title": m_["label"], "sub": ", ".join(m_["names"]), "kind": "med"} for m_ in slots] + \
                [{"id": h["id"], "title": h["title"], "sub": h.get("sub") or "", "kind": "habit"} for h in c["habits"]]
        need_ids = {i["id"] for i in items}
        a, b = first.isoformat(), last.isoformat()
        rec = self._daymap(a, b)
        since = min((r["d"] for r in self.db.q("""SELECT MIN(date) d FROM hl_check UNION ALL SELECT MIN(date) FROM hl_workout
                     UNION ALL SELECT MIN(date) FROM hl_body UNION ALL SELECT MIN(date) FROM hl_meal""") if r["d"]), default=None)
        body = {r["date"]: r for r in self.db.q("SELECT * FROM hl_body WHERE date BETWEEN ? AND ?", (a, b))}
        meals = {}
        for r in self.db.q("SELECT * FROM hl_meal WHERE date BETWEEN ? AND ? ORDER BY id", (a, b)):
            tags = json.loads(r["tags"] or "[]")
            meals.setdefault(r["date"], []).append({"id": r["id"], "slot": r["slot"], "menu": r["menu"], "tags": tags, "note": r["note"], "grade": self.meal_grade(tags)})
        events = {}
        for r in self.db.q("SELECT date, title, done FROM hl_follow WHERE date BETWEEN ? AND ? ORDER BY id", (a, b)):
            events.setdefault(r["date"], []).append({"kind": "follow", "title": r["title"], "done": bool(r["done"])})
        for r in self.db.q("SELECT date, COUNT(*) n FROM hl_lab WHERE date BETWEEN ? AND ? GROUP BY date", (a, b)):
            events.setdefault(r["date"], []).append({"kind": "lab", "title": f"검진 수치 {r['n']}개 기록", "done": True})
        days, S = [], {"active": 0, "workouts": 0, "target": 0, "clears": 0, "xp": 0, "meals": 0, "good": 0, "body": 0, "checks": 0, "streak": 0}
        run = 0
        for i in range(last.day):
            dd = first + timedelta(days=i)
            k = dd.isoformat()
            v = rec.get(k) or {"c": set(), "w": None, "b": False, "m": []}
            p = self.plan_for(dd, c)
            done = bool(v["w"] and v["w"]["done"])
            future = dd > t
            xp, clear = self._day_xp(v, need_ids)
            started = bool(since) and k >= since
            gym = "done" if done else "rest" if p["rest"] else "plan" if dd >= t else "miss" if started else ""
            ms = sorted(meals.get(k, []), key=lambda x: MEAL_SLOTS.index(x["slot"]))
            on = [i_ for i_ in items if i_["id"] in v["c"]]
            days.append({"date": k, "d": dd.day, "wd": dd.weekday(), "dow": DOW[dd.weekday()], "today": dd == t, "future": future,
                         "xp": xp, "clear": clear, "lv": 4 if clear else 0 if not xp else 1 if xp < 40 else 2 if xp < 80 else 3,
                         "gym": gym, "checks": len(on), "need": len(items), "grades": [x["grade"] for x in ms if x["slot"] != "간식"][:3],
                         "body": bool(v["b"]), "ev": len(events.get(k, [])),
                         "workout": {"name": (v["w"] or {}).get("name") or p["name"], "plan": p["name"], "time": p["time"], "rest": p["rest"], "done": done,
                                     "rpe": (v["w"] or {}).get("rpe"), "items": [x[0] for x in p["items"]][:8]},
                         "check_list": [dict(i_, on=i_["id"] in v["c"]) for i_ in items], "meal_list": ms,
                         "body_rec": ({x: body[k][x] for x in ("weight", "waist", "fat", "note")} if k in body else None),
                         "events": events.get(k, [])})
            if not future:
                S["active"] += xp > 0; S["workouts"] += done; S["clears"] += clear; S["xp"] += xp; S["body"] += bool(v["b"]); S["checks"] += len(on)
                S["meals"] += len(ms); S["good"] += sum(1 for x in ms if x["grade"] == "균형")
                S["target"] += (not p["rest"]) and (started or done)
                run = run + 1 if xp > 0 else 0
                S["streak"] = max(S["streak"], run)
        ws = [body[k]["weight"] for k in sorted(body) if body[k]["weight"] is not None]
        S["weight"] = {"first": ws[0], "last": ws[-1], "diff": round(ws[-1] - ws[0], 1)} if ws else None
        cur = t.replace(day=1)
        return {"month": first.isoformat()[:7], "title": f"{first.year}년 {first.month}월", "lead": first.weekday(), "today": t.isoformat(), "since": since,
                "prev": prev.isoformat()[:7] if since and prev.isoformat()[:7] >= since[:7] else None,
                "next": nxt.isoformat()[:7] if nxt <= (cur + timedelta(days=32)).replace(day=1) else None,
                "this_month": first == cur, "days": days, "sum": S, "xp_rule": XP}

    def rpg(self, c, t, slots):
        """레벨·경험치·연속 기록·이번 주 보스·능력치·업적. 체크·운동·신체 기록에서 셈한다."""
        need_ids = {m["key"] for m in slots} | {h["id"] for h in c["habits"]}
        med_ids = {m["key"] for m in slots}
        days = self._daymap()
        xp_of, clears, st = {}, 0, {"str": 0, "end": 0, "wil": 0, "care": 0, "diet": 0}
        n_work = 0
        for d_, v in days.items():
            done = bool(v["w"] and v["w"]["done"])
            st["diet"] += sum(1 for _, g in v["m"] if g == "균형")
            xp_of[d_], clear = self._day_xp(v, need_ids)
            clears += clear
            if done:
                n_work += 1
                nm = v["w"]["name"] or ""
                st["end" if any(k in nm for k in ("유산소", "가볍게", "걷기")) else "str"] += 1
            st["wil"] += len(v["c"] - med_ids)
            st["care"] += len(v["c"] & med_ids) + (1 if v["b"] else 0)
        total = sum(xp_of.values())
        lv, left = 1, total
        while left >= LV_BASE + LV_STEP * (lv - 1):
            left -= LV_BASE + LV_STEP * (lv - 1)
            lv += 1
        d0 = t if xp_of.get(t.isoformat()) else t - timedelta(days=1)
        streak = 0
        while xp_of.get(d0.isoformat()):
            streak += 1
            d0 -= timedelta(days=1)
        wk = self.week()
        # 지난 주들의 보스: 그 주의 운동 횟수가 지금 목표(주간 운동 횟수) 이상이면 처치
        per_week = {}
        for d_, v in days.items():
            if v["w"] and v["w"]["done"]:
                dd = date.fromisoformat(d_)
                per_week[(dd - timedelta(days=dd.weekday())).isoformat()] = per_week.get((dd - timedelta(days=dd.weekday())).isoformat(), 0) + 1
        target = max(1, wk["target"])
        slain = sum(1 for k, n in per_week.items() if k != wk["start"] and n >= target) + (1 if wk["done"] >= target else 0)
        rows, latest = self.body()
        g = c["goal"]
        start_w = g.get("start_weight") or next((r["weight"] for r in rows if r["weight"] is not None), None)
        lost = round(start_w - latest["weight"]["value"], 1) if start_w and latest["weight"] else 0
        B = lambda i, title, desc, got: {"id": i, "title": title, "desc": desc, "got": bool(got)}
        badges = [B("first", "첫걸음", "첫 운동 완료", n_work >= 1), B("s3", "작심삼일 돌파", "3일 연속 기록", streak >= 3), B("s7", "일주일 전사", "7일 연속 기록", streak >= 7),
                  B("s21", "습관의 주인", "21일 연속 기록", streak >= 21), B("clear1", "올 클리어", "하루 퀘스트 전부 완료", clears >= 1), B("clear7", "완벽주의자", "올 클리어 7번", clears >= 7),
                  B("boss1", "보스 사냥꾼", "주간 보스 처치", slain >= 1), B("boss4", "토벌대장", "주간 보스 4마리 처치", slain >= 4),
                  B("w10", "단골", "운동 10회", n_work >= 10), B("w30", "철인", "운동 30회", n_work >= 30),
                  B("chef1", "첫 요리", "요리책 레시피로 한 끼", bool(self.db.one("SELECT 1 x FROM hl_meal WHERE recipe IS NOT NULL"))),
                  B("plate10", "균형 접시", "채소+단백질 접시 10번", st["diet"] >= 10),
                  B("k1", "첫 1kg", "시작보다 1kg 감량", lost >= 1), B("k3", "가벼워진 몸", "시작보다 3kg 감량", lost >= 3), B("k5", "변신", "시작보다 5kg 감량", lost >= 5)]
        iso = t.isocalendar()
        today_q = self.quests(c, t, slots)
        return {"level": lv, "xp": left, "need": LV_BASE + LV_STEP * (lv - 1), "total": total, "title": next(n for l, n in reversed(TITLES) if lv >= l),
                "streak": streak, "today": xp_of.get(t.isoformat(), 0), "today_max": sum(q["xp"] for q in today_q) + XP["rpe"] + XP["clear"],
                "clears": clears, "workouts": n_work, "stats": st,
                "boss": {"name": BOSSES[(iso[0] * 53 + iso[1]) % len(BOSSES)], "max": target, "hp": max(0, target - wk["done"]), "slain": slain, "i": (iso[0] * 53 + iso[1]) % len(BOSSES)},
                "badges": badges, "xp_rule": XP}

    def brief(self):
        """첫 화면(우석World) 타일에 보일 한 줄."""
        t = self._today()
        c = self.cfg()
        wk = self.week()
        today = wk["days"][t.weekday()]
        slots = self.med_slots(c)
        valid = {h["id"] for h in c["habits"]} | {m["key"] for m in slots}
        n = sum(1 for r in self.db.q("SELECT key FROM hl_check WHERE date=?", (t.isoformat(),)) if r["key"] in valid) + (1 if today["done"] else 0)
        nxt = next((f for f in self.follows() if f["next"]), None)
        _, latest = self.body()
        return {"checks": n, "checks_total": len(c["habits"]) + len(slots) + 1, "workout": c["plan"]["days"][t.weekday()]["name"],
                "workout_done": today["done"], "week_done": wk["done"], "week_target": wk["target"],
                "weight": latest["weight"]["value"] if latest["weight"] else None,
                "next": {"title": nxt["title"], "dday": nxt["dday"]} if nxt else None}

    # ---------- 가져오기·내보내기 ----------
    def export(self):
        return {"kind": "moa-health", "v": 1, "exported": datetime.now().isoformat(timespec="seconds"), "cfg": self.cfg(),
                "body": self.db.q("SELECT * FROM hl_body ORDER BY date"),
                "checks": self.db.q("SELECT * FROM hl_check ORDER BY date, key"),
                "workouts": self.db.q("SELECT * FROM hl_workout ORDER BY date"),
                "labs": self.db.q("SELECT date,name,value,ref,flag,sort FROM hl_lab ORDER BY date, sort, id"),
                "follows": self.db.q("SELECT date,title,why,done FROM hl_follow ORDER BY date, id"),
                "meals": self.db.q("SELECT date,slot,menu,tags,note FROM hl_meal ORDER BY date, id"),
                "recipes": [{k: r[k] for k in ("title", "tags", "ingredients", "steps", "memo", "time", "serves")} for r in self.recipes() if not r.get("key")]}

    def import_(self, d):
        """내보낸 파일(또는 처음 자료 파일)을 넣는다. 같은 날짜·같은 항목은 파일 값으로 바꾸고, 나머지는 그대로 둔다."""
        if not isinstance(d, dict) or d.get("kind") != "moa-health":
            raise HealthError("건강관리 자료 파일이 아니에요")
        n = {"body": 0, "labs": 0, "follows": 0, "workouts": 0, "checks": 0, "photos": 0}
        if isinstance(d.get("cfg"), dict):
            self.save_cfg({k: d["cfg"][k] for k in ("height", "note", "goal", "habits", "plan", "meds") if k in d["cfg"]})
        for r in d.get("body") or []:
            self.save_body({**r, "replace": True, "date": r.get("date")})
            n["body"] += 1
        for r in d.get("labs") or []:
            day = _d(r.get("date"), "검사 날짜").isoformat()
            old = self.db.one("SELECT id FROM hl_lab WHERE date=? AND name=?", (day, _txt(r.get("name"), 30)))
            self.save_lab({**r, "id": old["id"] if old else None})
            n["labs"] += 1
        for r in d.get("follows") or []:
            day = _d(r.get("date"), "검사 날짜").isoformat()
            old = self.db.one("SELECT id FROM hl_follow WHERE date=? AND title=?", (day, _txt(r.get("title"), 40)))
            self.save_follow({**r, "id": old["id"] if old else None})
            n["follows"] += 1
        for r in d.get("workouts") or []:
            day = _d(r.get("date")).isoformat()
            self.db.x("INSERT INTO hl_workout(date,done,rpe,name) VALUES(?,?,?,?) ON CONFLICT(date) DO UPDATE SET done=excluded.done, rpe=excluded.rpe, name=excluded.name",
                      (day, int(bool(r.get("done"))), r.get("rpe") if isinstance(r.get("rpe"), int) and 1 <= r["rpe"] <= 10 else None, _txt(r.get("name"), 16)))
            n["workouts"] += 1
        for r in d.get("checks") or []:
            self.db.x("INSERT OR IGNORE INTO hl_check(date,key) VALUES(?,?)", (_d(r.get("date")).isoformat(), _txt(r.get("key"), 20)))
            n["checks"] += 1
        for r in d.get("photos") or []:   # 기구 사진 묶음(내보내기에는 넣지 않는다: 백업에 들어 있다)
            self.save_photo(r.get("key"), r)
            n["photos"] += 1
        return n
