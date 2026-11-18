"""백업과 되돌리기.

- 백업: SQLite의 온라인 백업으로 data/backups/ 에 통째로 복사한다(앱을 쓰는 중에도 안전).
- 자동: 하루 한 번(노트북이 켜져 있을 때), 업데이트 전, 되돌리기 전. 오래된 것은 정해 둔 개수만 남긴다.
- 내려받기: 기본은 API 키·접속 토큰·비밀번호를 뺀 사본. 전체 사본은 따로 고를 때만 준다.
- 되돌리기: 지금 상태를 먼저 백업한 뒤 덮어쓴다. 접속 토큰·비밀번호·열린 기기·서버 주소는 지금 것을 지켜서
  되돌린 뒤에도 휴대폰 연결이 끊기지 않는다.
"""
import copy
import json
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime

from .db import SCHEMA
from .settings import SECRET_PATHS

KINDS = {"auto": "자동", "manual": "직접", "prerestore": "되돌리기 전", "preupdate": "업데이트 전", "upload": "올린 파일"}
NAME_RE = re.compile(r"^moa-(\d{8})-(\d{6})-(auto|manual|prerestore|preupdate|upload)\.db$")
KEEP_CHOICES = (7, 14, 30)
KEEP_OTHER = {"manual": 30, "upload": 10, "prerestore": 5, "preupdate": 5}
REQUIRED = ("settings", "transactions", "accounts", "categories")
COUNTS = (("transactions", "가계부 내역"), ("accounts", "계좌·자산"), ("dca_plans", "적립 플랜"), ("ai_picks", "AI 추천"), ("orders", "주문 기록"))
# 내려받는 사본에서 지우는 값: 비밀키 + 키움 앱키 + 알림 주제(알면 알림을 엿볼 수 있다)
EXTRA_SECRETS = [("kiwoom", "scalp", "appkey"), ("kiwoom", "dca", "appkey"), ("kiwoom_view", "appkey"), ("ntfy_topic",)]
# 되돌려도 지금 값을 지키는 연결 설정(바뀌면 휴대폰이 접속하지 못한다)
KEEP_NOW = ("allowed_origins", "public_url")
SESSIONS_DDL = "CREATE TABLE IF NOT EXISTS auth_sessions(hash TEXT PRIMARY KEY, created REAL NOT NULL, last_seen REAL NOT NULL, label TEXT)"


def _get(d, path):
    for p in path:
        d = d.get(p) if isinstance(d, dict) else None
    return d


def _set(d, path, v):
    for p in path[:-1]:
        d = d.setdefault(p, {})
    d[path[-1]] = v


def app_version():
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _plain(conn, path):
    """백업 파일은 WAL이 아닌 보통 방식으로 둔다(파일 하나만 옮겨도 온전하고, 읽기 전용으로 열 수 있다)."""
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.commit()


def _tidy(path):
    for x in (path + "-wal", path + "-shm", path + "-journal"):
        try:
            os.remove(x)
        except OSError:
            pass


def _counts(conn):
    out = {}
    for table, _ in COUNTS:
        try:
            out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.Error:
            pass
    try:
        r = conn.execute("SELECT MIN(date), MAX(date) FROM transactions").fetchone()
        out["first"], out["last"] = r[0], r[1]
    except sqlite3.Error:
        pass
    return out


class Backup:
    def __init__(self, rt, data_dir=None, version=app_version):
        self.rt, self.db = rt, rt.db
        # data 폴더를 모르는 경우(테스트 등)에는 임시 폴더를 쓴다
        self.dir = os.path.join(data_dir, "backups") if data_dir else os.path.join(tempfile.gettempdir(), "moa-backups")
        self.version = version
        self._day = None            # 오늘 자동 백업을 이미 확인했는지

    # ---------- 설정 ----------
    def conf(self):
        c = self.rt.settings.get("backup")
        keep = c.get("keep") if c.get("keep") in KEEP_CHOICES else 14
        return {"auto": bool(c.get("auto", True)), "keep": keep, "extra_dir": str(c.get("extra_dir") or "")}

    def set_conf(self, d, local=False):
        patch = {}
        if "auto" in d:
            patch["auto"] = bool(d["auto"])
        if "keep" in d:
            if d["keep"] not in KEEP_CHOICES:
                raise ValueError("보관 개수는 7·14·30 가운데 골라 주세요")
            patch["keep"] = d["keep"]
        if "extra_dir" in d:
            if not local:
                raise PermissionError("다른 폴더는 서버 PC(노트북)에서만 정할 수 있어요")
            p = str(d["extra_dir"] or "").strip().strip('"')
            if p:
                p = os.path.abspath(os.path.expanduser(p))
                if not os.path.isdir(p):
                    raise ValueError("그 폴더를 찾지 못했어요. 폴더를 먼저 만들어 주세요")
                if os.path.abspath(p) == os.path.abspath(self.dir):
                    raise ValueError("기본 백업 폴더와 다른 폴더를 골라 주세요")
                try:
                    fd, t = tempfile.mkstemp(dir=p, prefix="moa-test-")
                    os.close(fd)
                    os.remove(t)
                except OSError:
                    raise ValueError("그 폴더에 쓸 수 없어요. 권한을 확인해 주세요")
            patch["extra_dir"] = p
        self.rt.settings.update({"backup": patch})
        if "keep" in patch:
            self.prune()
        return self.conf()

    # ---------- 목록 ----------
    def path(self, name):
        if not NAME_RE.match(str(name or "")):
            raise ValueError("백업 이름이 올바르지 않아요")
        p = os.path.join(self.dir, name)
        if not os.path.isfile(p):
            raise ValueError("그 백업을 찾지 못했어요")
        return p

    def _meta(self, name):
        m = NAME_RE.match(name)
        p = os.path.join(self.dir, name)
        d, t = m.group(1), m.group(2)
        out = {"name": name, "kind": m.group(3), "kind_name": KINDS[m.group(3)],
               "ts": f"{d[:4]}-{d[4:6]}-{d[6:]}T{t[:2]}:{t[2:4]}:{t[4:]}", "size": os.path.getsize(p), "counts": {}, "version": "", "note": ""}
        try:
            with open(p + ".json", encoding="utf-8") as f:
                side = json.load(f)
            out.update({k: side[k] for k in ("counts", "version", "note", "copied", "copy_error", "keyless") if k in side})
        except (OSError, ValueError):
            pass
        return out

    def list(self):
        try:
            names = [n for n in os.listdir(self.dir) if NAME_RE.match(n)]
        except OSError:
            names = []
        return [self._meta(n) for n in sorted(names, reverse=True)]

    def status(self):
        items = self.list()
        c = self.conf()
        try:
            db_size = os.path.getsize(self.db.path) if self.db.path != ":memory:" else 0
        except OSError:
            db_size = 0
        return {**c, "keep_choices": list(KEEP_CHOICES), "dir": os.path.abspath(self.dir), "items": items, "last": items[0] if items else None,
                "total_size": sum(i["size"] for i in items), "db_size": db_size, "days": sorted({i["ts"][:10] for i in items}),
                "extra_ok": bool(c["extra_dir"]) and os.path.isdir(c["extra_dir"]), "today": datetime.now().date().isoformat(),
                "now": datetime.now().isoformat(timespec="seconds")}

    # ---------- 만들기 ----------
    def create(self, kind="manual", note="", now=None):
        if kind not in KINDS:
            raise ValueError("알 수 없는 백업 종류예요")
        os.makedirs(self.dir, exist_ok=True)
        now = now or datetime.now()
        name = f"moa-{now:%Y%m%d-%H%M%S}-{kind}.db"
        final = os.path.join(self.dir, name)
        if os.path.exists(final):                          # 같은 초에 두 번 누름
            return self._meta(name)
        tmp = final + ".part"
        try:
            with self.db.lock:
                dest = sqlite3.connect(tmp)
                try:
                    self.db.conn.backup(dest)
                    _plain(dest, tmp)
                    counts = _counts(dest)
                finally:
                    dest.close()
            _tidy(tmp)
            os.replace(tmp, final)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        side = {"counts": counts, "version": self.version(), "note": str(note or "")[:60]}
        extra = self.conf()["extra_dir"]
        if extra:
            try:
                shutil.copy2(final, os.path.join(extra, name))
                side["copied"] = True
                self._prune_dir(extra)
            except OSError as e:
                side["copy_error"] = f"다른 폴더에 복사하지 못했어요 ({type(e).__name__})"
        self._side(name, side)
        self.prune()
        return self._meta(name)

    def _side(self, name, side):
        try:
            with open(os.path.join(self.dir, name + ".json"), "w", encoding="utf-8") as f:
                json.dump(side, f, ensure_ascii=False)
        except OSError:
            pass

    def auto_due(self, now=None):
        """하루 한 번 자동 백업(엔진 반복에서 부른다). 오늘 것이 이미 있으면 아무것도 하지 않는다."""
        now = now or datetime.now()
        day = now.date().isoformat()
        if self._day == day:
            return None
        if not self.conf()["auto"]:
            return None
        self._day = day
        if any(i["kind"] == "auto" and i["ts"][:10] == day for i in self.list()):
            return None
        return self.create("auto", now=now)

    # ---------- 지우기 ----------
    def delete(self, name):
        p = self.path(name)
        os.remove(p)
        try:
            os.remove(p + ".json")
        except OSError:
            pass

    def _limits(self):
        return {**KEEP_OTHER, "auto": self.conf()["keep"]}

    def prune(self):
        """종류마다 정해 둔 개수만 남기고 오래된 것부터 지운다."""
        seen, lim = {}, self._limits()
        for i in self.list():                               # 새것부터
            seen[i["kind"]] = seen.get(i["kind"], 0) + 1
            if seen[i["kind"]] > lim[i["kind"]]:
                try:
                    self.delete(i["name"])
                except (OSError, ValueError):
                    pass

    def _prune_dir(self, folder):
        seen, lim = {}, self._limits()
        try:
            names = sorted((n for n in os.listdir(folder) if NAME_RE.match(n)), reverse=True)
        except OSError:
            return
        for n in names:
            k = NAME_RE.match(n).group(3)
            seen[k] = seen.get(k, 0) + 1
            if seen[k] > lim[k]:
                try:
                    os.remove(os.path.join(folder, n))
                except OSError:
                    pass

    # ---------- 내려받기 ----------
    def export(self, name, full=False):
        """내려받을 파일의 내용(bytes). full이 아니면 키·토큰·비밀번호를 지운 사본을 만들어 준다."""
        p = self.path(name)
        if full:
            with open(p, "rb") as f:
                return f.read()
        fd, tmp = tempfile.mkstemp(suffix=".db", dir=self.dir)
        os.close(fd)
        try:
            shutil.copyfile(p, tmp)
            conn = sqlite3.connect(tmp)
            try:
                r = conn.execute("SELECT value FROM settings WHERE key='user'").fetchone()
                user = json.loads(r[0]) if r else {}
                for path in list(SECRET_PATHS) + EXTRA_SECRETS:
                    if _get(user, path):
                        _set(user, path, "")
                conn.execute("INSERT INTO settings(key,value) VALUES('user',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (json.dumps(user, ensure_ascii=False),))
                conn.execute("DELETE FROM settings WHERE key IN ('access_token','auth')")
                conn.execute("INSERT INTO settings(key,value) VALUES('backup_keyless','true') ON CONFLICT(key) DO UPDATE SET value='true'")
                try:
                    conn.execute("DELETE FROM auth_sessions")
                except sqlite3.Error:
                    pass
                conn.commit()
                conn.execute("PRAGMA journal_mode=DELETE")
                conn.execute("VACUUM")                      # 지운 값이 빈 자리에 남지 않게 파일을 다시 쓴다
                conn.commit()
            finally:
                conn.close()
            with open(tmp, "rb") as f:
                return f.read()
        finally:
            for x in (tmp, tmp + "-wal", tmp + "-shm", tmp + "-journal"):
                if os.path.exists(x):
                    os.remove(x)

    # ---------- 올리기·검사 ----------
    @staticmethod
    def check(path):
        """moa 백업 파일이 맞는지 본다. 맞으면 (건수, 키가 빠진 사본인지)."""
        try:
            with open(path, "rb") as f:
                head = f.read(16)
        except OSError:
            raise ValueError("파일을 읽지 못했어요")
        if head != b"SQLite format 3\x00":
            raise ValueError("moa 백업 파일이 아니에요(.db 파일을 골라 주세요)")
        try:
            conn = sqlite3.connect(path)
        except sqlite3.Error:
            raise ValueError("백업 파일을 열지 못했어요")
        try:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("백업 파일이 손상됐어요")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not all(t in tables for t in REQUIRED):
                raise ValueError("moa 백업 파일이 아니에요")
            keyless = bool(conn.execute("SELECT 1 FROM settings WHERE key='backup_keyless'").fetchone())
            return _counts(conn), keyless
        except sqlite3.Error:
            raise ValueError("백업 파일이 손상됐어요")
        finally:
            conn.close()

    def upload(self, stream, now=None):
        """휴대폰·PC에 내려받아 둔 파일을 올린다. 검사만 하고 목록에 넣는다(되돌리기는 따로 누른다)."""
        os.makedirs(self.dir, exist_ok=True)
        now = now or datetime.now()
        name = f"moa-{now:%Y%m%d-%H%M%S}-upload.db"
        final = os.path.join(self.dir, name)
        tmp = final + ".part"
        try:
            with open(tmp, "wb") as f:
                shutil.copyfileobj(stream, f)
            counts, keyless = self.check(tmp)
            conn = sqlite3.connect(tmp)
            try:
                _plain(conn, tmp)
            finally:
                conn.close()
            _tidy(tmp)
            os.replace(tmp, final)
        finally:
            _tidy(tmp)
            if os.path.exists(tmp):
                os.remove(tmp)
        self._side(name, {"counts": counts, "version": "", "note": "올린 파일", "keyless": keyless})
        self.prune()
        return self._meta(name)

    # ---------- 되돌리기 ----------
    def can_restore(self):
        try:
            if self.rt.scalp.open_trades():
                return False, "단타 엔진이 종목을 들고 있어요. 청산한 뒤 되돌려 주세요"
            if self.rt.settings.get("scalp_state") == "running":
                return False, "단타 엔진을 멈춘 뒤 되돌려 주세요"
            if self.rt.autotrade.active():
                return False, "AI 자동매매가 돌고 있어요. 종료한 뒤 되돌려 주세요"
        except Exception:
            pass
        return True, ""

    def restore(self, name):
        """백업 시점으로 되돌린다. 지금 상태는 먼저 '되돌리기 전' 백업으로 남긴다."""
        src_path = self.path(name)
        self.check(src_path)
        ok, why = self.can_restore()
        if not ok:
            raise ValueError(why)
        db = self.db
        pre = self.create("prerestore")
        with self.rt._lock, db.lock:
            now_user = db.get_setting("user", {}) or {}
            now_token = db.get_setting("access_token")
            now_auth = db.get_setting("auth")
            try:
                now_sessions = db.q("SELECT hash, created, last_seen, label FROM auth_sessions")
            except sqlite3.Error:
                now_sessions = []
            db.conn.commit()
            src = sqlite3.connect(src_path)
            try:
                src.backup(db.conn)
            finally:
                src.close()
            # 예전 버전에서 만든 백업이어도 지금 버전이 쓰는 표·열을 갖추게 한다(나머지는 다시 시작할 때 각 기능이 채운다)
            db.conn.executescript(SCHEMA)
            db.conn.execute(SESSIONS_DDL)
            db.conn.commit()
            db._migrate()
            user = copy.deepcopy(db.get_setting("user", {}) or {})
            for path in list(SECRET_PATHS) + EXTRA_SECRETS:        # 백업에 키가 없으면(키를 뺀 사본) 지금 키를 그대로 쓴다
                if not _get(user, path) and _get(now_user, path):
                    _set(user, path, _get(now_user, path))
            for k in KEEP_NOW:
                if k in now_user:
                    user[k] = now_user[k]
                else:
                    user.pop(k, None)
            db.set_setting("user", user)
            db.x("DELETE FROM settings WHERE key='backup_keyless'")
            if now_token:
                db.set_setting("access_token", now_token)
            if (now_auth or {}).get("hash"):                        # 지금 비밀번호와 열린 기기를 지킨다
                db.set_setting("auth", now_auth)
                db.x("DELETE FROM auth_sessions")
                for s in now_sessions:
                    db.x("INSERT INTO auth_sessions(hash,created,last_seen,label) VALUES(?,?,?,?)", (s["hash"], s["created"], s["last_seen"], s["label"]))
            else:                                                   # 지금 비밀번호가 없으면 백업의 비밀번호를 쓰되, 열린 기기는 없앤다
                db.x("DELETE FROM auth_sessions")
            db.log("system", "info", f"백업 {name}으로 되돌렸어요(되돌리기 전 상태: {pre['name']})", datetime.now().isoformat(timespec="seconds"))
        self._day = None
        return {"restored": self._meta(name), "pre": pre["name"]}
