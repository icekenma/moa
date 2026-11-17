"""앱 비밀번호와 잠금.

접속 토큰(기기 열쇠)만으로는 데이터를 볼 수 없게, 서버가 비밀번호를 확인한 '열림 상태(세션)'를 따로 둔다.
- 비밀번호는 PBKDF2-SHA256(소금 + 24만 번)으로만 저장한다. 원문은 어디에도 남기지 않는다.
- 세션 값은 한 번만 내려주고, 서버에는 SHA-256 해시만 둔다. 일정 시간 안 쓰면 다시 잠긴다.
- 틀리면 점점 길게 막는다(5번부터 30초, 그 뒤 2배씩, 최대 1시간).
"""
import hashlib
import hmac
import secrets
import time
from datetime import datetime

ITER = 240_000
MIN_LEN = 4
LOCK_CHOICES = (5, 30, 240, 1440, 10080)   # 분: 5분, 30분, 4시간, 하루, 일주일
DEFAULT_LOCK = 30
MAX_AGE = 30 * 86400                       # 세션 최대 수명(초)
FREE_TRIES = 5


def _hash(password, salt, n=ITER):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), n).hex()


class AuthError(Exception):
    def __init__(self, msg, wait=0):
        super().__init__(msg)
        self.wait = wait


class Auth:
    def __init__(self, db, notify=None):
        self.db = db
        self.notify = notify or (lambda m: None)
        db.x("""CREATE TABLE IF NOT EXISTS auth_sessions(
            hash TEXT PRIMARY KEY, created REAL NOT NULL, last_seen REAL NOT NULL, label TEXT)""")
        self._touch = {}

    # ---------- 상태 ----------
    def conf(self):
        return self.db.get_setting("auth") or {}

    def is_set(self):
        return bool(self.conf().get("hash"))

    def lock_after(self):
        v = self.conf().get("lock_after")
        return v if v in LOCK_CHOICES else DEFAULT_LOCK

    def wait_left(self):
        c = self.conf()
        return max(0, int((c.get("blocked_until") or 0) - time.time()))

    def state(self, session=None):
        return {"set": self.is_set(), "unlocked": self.valid(session), "lock_after": self.lock_after(),
                "choices": list(LOCK_CHOICES), "wait": self.wait_left(), "min_len": MIN_LEN,
                "sessions": self.db.one("SELECT COUNT(*) n FROM auth_sessions")["n"], "fails": self.conf().get("fails", 0),
                "last_fail": self.conf().get("last_fail")}

    # ---------- 비밀번호 ----------
    def _check_new(self, pw):
        pw = str(pw or "")
        if len(pw) < MIN_LEN:
            raise AuthError(f"비밀번호는 {MIN_LEN}자 이상으로 정해 주세요")
        if len(pw) > 128:
            raise AuthError("비밀번호가 너무 길어요")
        if len(set(pw)) == 1 or pw in ("1234", "12345", "123456", "0000", "1111", "password", "qwerty"):
            raise AuthError("너무 쉬운 비밀번호예요. 다른 것으로 정해 주세요")
        return pw

    def _store(self, pw, keep=None):
        salt = secrets.token_hex(16)
        c = dict(keep or {})
        c.update(salt=salt, hash=_hash(pw, salt), iter=ITER, fails=0, blocked_until=0,
                 set_at=datetime.now().isoformat(timespec="seconds"))
        self.db.set_setting("auth", c)

    def setup(self, pw, label=""):
        if self.is_set():
            raise AuthError("이미 비밀번호가 있어요. 바꾸려면 설정 → 보안에서 바꿔 주세요")
        self._store(self._check_new(pw), {"lock_after": self.lock_after()})
        return self._new_session(label)

    def verify(self, pw):
        """맞으면 True. 틀리면 횟수를 올리고 AuthError(막힌 시간 포함)."""
        c = self.conf()
        if not c.get("hash"):
            raise AuthError("비밀번호가 아직 없어요")
        left = self.wait_left()
        if left > 0:
            raise AuthError(f"여러 번 틀려서 잠시 막았어요. {left}초 뒤에 다시 해 주세요", left)
        ok = hmac.compare_digest(_hash(str(pw or ""), c["salt"], c.get("iter", ITER)), c["hash"])
        if ok:
            if c.get("fails"):
                c.update(fails=0, blocked_until=0)
                self.db.set_setting("auth", c)
            return True
        fails = int(c.get("fails") or 0) + 1
        wait = 0
        if fails >= FREE_TRIES:
            wait = min(3600, 30 * 2 ** (fails - FREE_TRIES))
        c.update(fails=fails, blocked_until=time.time() + wait, last_fail=datetime.now().isoformat(timespec="seconds"))
        self.db.set_setting("auth", c)
        if fails == FREE_TRIES or (fails > FREE_TRIES and fails % 3 == 0):
            self.notify(f"[moa 보안] 앱 비밀번호를 {fails}번 틀렸어요. 본인이 아니면 노트북에서 접속 토큰을 새로 만드세요")
        raise AuthError("비밀번호가 맞지 않아요" + (f". {wait}초 동안 막을게요" if wait else f" ({FREE_TRIES - fails}번 더 틀리면 잠시 막아요)"), wait)

    def unlock(self, pw, label=""):
        self.verify(pw)
        return self._new_session(label)

    def change(self, current, new, session=None):
        self.verify(current)
        self._store(self._check_new(new), {"lock_after": self.lock_after()})
        # 다른 기기에 열려 있던 것은 모두 닫고, 지금 것만 남긴다
        keep = hashlib.sha256((session or "").encode()).hexdigest()
        self.db.x("DELETE FROM auth_sessions WHERE hash<>?", (keep,))

    def set_lock_after(self, minutes):
        if minutes not in LOCK_CHOICES:
            raise AuthError("고를 수 없는 시간이에요")
        c = self.conf()
        c["lock_after"] = minutes
        self.db.set_setting("auth", c)

    def reset(self):
        """비밀번호와 열린 상태를 모두 지운다(서버 PC에서만 부른다)."""
        self.db.set_setting("auth", {"lock_after": self.lock_after()})
        self.db.x("DELETE FROM auth_sessions")

    # ---------- 세션 ----------
    def _new_session(self, label=""):
        tok = secrets.token_urlsafe(32)
        now = time.time()
        self.db.x("DELETE FROM auth_sessions WHERE created < ?", (now - MAX_AGE,))
        self.db.x("INSERT INTO auth_sessions(hash,created,last_seen,label) VALUES(?,?,?,?)",
                  (hashlib.sha256(tok.encode()).hexdigest(), now, now, str(label or "")[:80]))
        return tok

    def valid(self, session):
        if not session or not self.is_set():
            return False
        h = hashlib.sha256(str(session).encode()).hexdigest()
        r = self.db.one("SELECT created, last_seen FROM auth_sessions WHERE hash=?", (h,))
        now = time.time()
        if not r:
            return False
        if now - r["created"] > MAX_AGE or now - r["last_seen"] > self.lock_after() * 60:
            self.db.x("DELETE FROM auth_sessions WHERE hash=?", (h,))
            return False
        if now - self._touch.get(h, 0) > 20:  # 매 요청마다 쓰지 않게 20초에 한 번만 갱신
            self._touch[h] = now
            self.db.x("UPDATE auth_sessions SET last_seen=? WHERE hash=?", (now, h))
        return True

    def lock(self, session=None, everywhere=False):
        if everywhere:
            self.db.x("DELETE FROM auth_sessions")
        elif session:
            self.db.x("DELETE FROM auth_sessions WHERE hash=?", (hashlib.sha256(str(session).encode()).hexdigest(),))


class Throttle:
    """틀린 접속 토큰을 계속 넣는 곳을 잠시 막는다(주소별)."""

    def __init__(self, limit=8, window=300, block=600):
        self.limit, self.window, self.block = limit, window, block
        self.fails, self.blocked = {}, {}

    def wait(self, who):
        t = self.blocked.get(who, 0) - time.time()
        return int(t) + 1 if t > 0 else 0

    def fail(self, who):
        now = time.time()
        xs = [t for t in self.fails.get(who, []) if now - t < self.window] + [now]
        self.fails[who] = xs
        if len(self.fails) > 500:  # 메모리 보호
            self.fails.clear()
        if len(xs) >= self.limit:
            self.blocked[who] = now + self.block
            self.fails[who] = []
            return True
        return False

    def ok(self, who):
        self.fails.pop(who, None)
