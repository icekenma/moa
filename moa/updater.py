"""GitHub 자동 업데이트.

몇 분마다 GitHub(origin/main)에 새 버전이 있는지 보고, 안전할 때 받아서 다시 시작한다.
- 안전할 때: 단타 엔진이 종목을 들고 있지 않고, 장중에 엔진이 돌고 있지 않을 때
- 다시 시작: 종료 코드 3으로 끝내면 run.bat이 같은 창에서 바로 다시 띄운다
- data/ 폴더(가계부·설정)는 GitHub에 없으므로 업데이트해도 그대로 남는다
"""
import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

RESTART_CODE = 3


def find_git():
    g = shutil.which("git")
    if g:
        return g
    for p in (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe"):
        if os.path.exists(p):
            return p
    return None


def _hash(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError:
        return ""


class Updater:
    def __init__(self, rt, base, interval=300, exit_fn=None):
        self.rt, self.base, self.interval = rt, base, interval
        self.git_path = find_git()
        self.repo = os.path.isdir(os.path.join(base, ".git"))
        self.exit_fn = exit_fn or (lambda: os._exit(RESTART_CODE))
        self.state = {"current": "", "latest": "", "behind": 0, "checked_at": None,
                      "message": "", "updated_at": None}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        if self.available:
            self.state["current"] = self._rev("HEAD")
            self.state["subject"] = self._git("log", "-1", "--format=%s").strip()

    @property
    def available(self):
        return bool(self.git_path and self.repo)

    # ---------- git ----------
    def _git(self, *args, timeout=90):
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        r = subprocess.run([self.git_path, *args], cwd=self.base, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, creationflags=flags)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip()
                               else f"git {args[0]} 실패")
        return r.stdout

    def _rev(self, ref):
        try:
            return self._git("rev-parse", "--short", ref).strip()
        except Exception:
            return ""

    # ---------- 판단 ----------
    def safe_now(self):
        """지금 다시 시작해도 되는지. (가능, 사유)"""
        if self.rt.scalp.open_trades():
            return False, "단타 엔진이 종목을 들고 있어 청산 뒤에 업데이트해요"
        s = self.rt.settings.all()
        if s["scalp_state"] == "running" and self.rt.feed.market_open():
            return False, "장중에 단타 엔진이 돌고 있어 장 마감 뒤에 업데이트해요"
        return True, ""

    def check(self):
        if not self.available:
            self.state["message"] = ("Git이 없어 자동 업데이트를 쓸 수 없어요" if not self.git_path
                                     else "이 폴더가 GitHub 저장소와 연결돼 있지 않아요(upload_github.bat을 한 번 실행)")
            return self.status()
        with self._lock:
            self._git("fetch", "--quiet", "origin", "main")
            self.state["current"] = self._rev("HEAD")
            self.state["latest"] = self._rev("origin/main")
            self.state["behind"] = int(self._git("rev-list", "--count", "HEAD..origin/main").strip() or 0)
            self.state["checked_at"] = datetime.now().isoformat(timespec="seconds")
            if self.state["behind"]:
                self.state["latest_subject"] = self._git("log", "-1", "--format=%s", "origin/main").strip()
                try:
                    nv = self._git("show", "origin/main:VERSION").strip()
                except Exception:
                    nv = ""
                self.state["latest_version"] = nv
                self.state["message"] = f"새 버전 v{nv}이 있어요" if nv else "새 버전이 있어요"
            else:
                self.state["message"] = "최신 버전이에요"
        return self.status()

    def apply(self, force_check=True):
        """새 버전을 받아 다시 시작한다. 받을 게 없거나 지금 안전하지 않으면 사유를 돌려준다."""
        if force_check:
            self.check()
        if not self.available or not self.state["behind"]:
            return False, self.state["message"]
        ok, why = self.safe_now()
        if not ok:
            self.state["message"] = why
            return False, why
        with self._lock:
            req = os.path.join(self.base, "requirements.txt")
            before = _hash(req)
            dirty = self._git("status", "--porcelain", "--untracked-files=no").strip()
            if dirty:
                self._git("stash", "push", "--quiet", "-m", "moa-auto-update")
            self._git("merge", "--ff-only", "--quiet", "origin/main")
            if _hash(req) != before:
                subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", req], cwd=self.base,
                               timeout=600)
            new = self._rev("HEAD")
        nv = self.state.get("latest_version") or new
        msg = f"v{nv}로 업데이트했어요. 다시 시작해요"
        self.rt.db.log("system", "info", msg, datetime.now().isoformat(timespec="seconds"))
        self.state["message"] = msg
        self.rt.stop()
        threading.Timer(1.0, self.exit_fn).start()  # 응답을 보낸 뒤 종료
        return True, msg

    def status(self):
        return {"available": self.available, "auto": self.rt.settings.get("auto_update"),
                "interval_min": self.interval // 60, **self.state}

    # ---------- 반복 ----------
    def start(self):
        if not self.available:
            self.check()
            return
        threading.Thread(target=self._loop, name="moa-updater", daemon=True).start()

    def _loop(self):
        self._stop.wait(20)  # 시작 직후는 잠시 쉼
        while not self._stop.is_set():
            try:
                self.check()
                if self.rt.settings.get("auto_update") and self.state["behind"]:
                    self.apply(force_check=False)
            except Exception as e:
                self.state["message"] = f"업데이트 확인 실패: {e}"
            self._stop.wait(self.interval)
