"""사진편집 프로그램 자동 배달(우석World): 저장소에 잠가서 올린 수정분을 노트북이 받아, 프로그램 자체의 업데이트 기능으로 적용한다.

흐름: (대화방) 프로그램 파일을 고침 → tools/ship_album.py로 잠가서 ship/album.bin·ship/album.json을 올림
      → (노트북) 5분마다 저장소를 확인하다가 새 묶음이 보이면 비밀 열쇠로 풀어 프로그램의 '업데이트 → zip 올리기'(/api/self_update)에 넣음
      → 프로그램이 문법 검사·백업 후 스스로 다시 켜짐(안 켜지면 스스로 되돌림).
프로그램 코드는 판매용이라 저장소에는 잠긴 묶음만 있다. 푸는 열쇠는 노트북의 data/ship_key.json에만 있고 어디로도 보내지 않는다.
사진·사진 DB·설정·API 키는 묶음에 들어 있지 않고, 프로그램의 업데이트 기능도 프로그램 파일만 바꾼다.
"""
import hashlib
import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
import uuid
from datetime import datetime

from . import shipbox

MANIFEST, BUNDLE, PUBFILE = "ship/album.json", "ship/album.bin", "ship/album.pub.json"
MAX_TRIES = 3


def _post_zip(url, data, timeout=120):
    """(상태 코드, JSON) — multipart로 zip 한 개를 보낸다."""
    b = "----moa" + uuid.uuid4().hex
    body = (f'--{b}\r\nContent-Disposition: form-data; name="zip"; filename="update.zip"\r\nContent-Type: application/zip\r\n\r\n').encode() + data + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)[:160]}


class AlbumShip:
    def __init__(self, db, data_dir, base, album, git=None, post=None, keygen=None):
        self.db, self.base, self.album = db, base, album
        self.key_path = os.path.join(data_dir, "ship_key.json")
        self.git = git                         # 저장소에서 파일 읽기: f(경로) -> bytes 또는 None
        self._post = post or _post_zip
        self._keygen = keygen or shipbox.keygen
        self._key = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.note = ""

    # ---------- 열쇠 ----------
    def key(self):
        """푸는 열쇠. 없으면 한 번 만든다(노트북 밖으로 나가지 않는다)."""
        with self._lock:
            if self._key is None:
                try:
                    with open(self.key_path, encoding="utf-8") as f:
                        self._key = json.load(f)
                except (OSError, ValueError):
                    self._key = self._keygen()
                    tmp = self.key_path + ".part"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(self._key, f)
                    os.replace(tmp, self.key_path)
            return self._key

    def public(self):
        return shipbox.public(self.key())

    # ---------- 저장소의 묶음 ----------
    def _read(self, path):
        if self.git:
            try:
                data = self.git(path)
                if data is not None:
                    return data
            except Exception:
                pass
        try:                                   # 저장소 최신본을 못 읽으면 받아 둔 폴더의 파일
            with open(os.path.join(self.base, *path.split("/")), "rb") as f:
                return f.read()
        except OSError:
            return None

    def manifest(self):
        raw = self._read(MANIFEST)
        try:
            m = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            m = None
        return m if isinstance(m, dict) and m.get("id") else None

    def _state(self):
        s = self.db.get_setting("album_ship")
        return s if isinstance(s, dict) else {}

    def tick(self):
        """새 묶음이 있으면 프로그램에 적용한다. 적용했으면 True."""
        m = self.manifest()
        st = self._state()
        if not m or m["id"] == st.get("applied"):
            self.note = ""
            return False
        if st.get("failed") == m["id"] and int(st.get("tries") or 0) >= MAX_TRIES:
            return False                        # 같은 묶음이 계속 안 되면 새 묶음이 올 때까지 멈춘다
        key = self.key()
        if m.get("key") != shipbox.key_id(key):
            self.note = "이 노트북의 열쇠로 잠근 묶음이 아니에요"
            return False
        if not self.album.status()["running"]:
            self.note = "프로그램이 꺼져 있어 켜지면 적용해요"
            return False
        now = datetime.now().isoformat(timespec="seconds")
        try:
            blob = self._read(BUNDLE)
            data = shipbox.open_(key, blob or b"")
            if hashlib.sha256(data).hexdigest() != m["id"]:
                raise ValueError("묶음 내용이 안내문과 달라요")
            code, res = self._post(f"http://127.0.0.1:{self.album.port}/api/self_update", data)
            if code == 401:
                raise ValueError("프로그램에 잠금 번호(PIN)가 걸려 있어 적용하지 못했어요")
            if code != 200 or (isinstance(res, dict) and res.get("error")):
                raise ValueError(str((res or {}).get("error") or f"프로그램이 받지 않았어요 ({code})")[:200])
        except Exception as e:
            self.note = str(e)[:200]
            self.db.set_setting("album_ship", dict(st, failed=m["id"], tries=(int(st.get("tries") or 0) + 1) if st.get("failed") == m["id"] else 1, error=self.note, at=now))
            self.db.log("system", "warn", f"사진편집 프로그램 업데이트 실패: {self.note}", now)
            return False
        changed = len(res.get("changed") or []) if isinstance(res, dict) else 0
        self.note = ""
        self.db.set_setting("album_ship", {"applied": m["id"], "note": str(m.get("note") or "")[:200], "at": now, "changed": changed})
        self.db.log("system", "info", f"사진편집 프로그램 업데이트 적용: {m.get('note') or m['id'][:8]} (파일 {changed}개)", now)
        return True

    def status(self):
        m, st = self.manifest(), self._state()
        ready = os.path.exists(self.key_path)
        return {"key": shipbox.key_id(self._key) if self._key else ("" if not ready else shipbox.key_id(self.key())),
                "latest": {"id": m["id"][:12], "note": str(m.get("note") or ""), "at": m.get("at")} if m else None,
                "applied": {"id": str(st.get("applied") or "")[:12], "note": st.get("note") or "", "at": st.get("at")} if st.get("applied") else None,
                "pending": bool(m and m["id"] != st.get("applied")), "note": self.note or (st.get("error") if st.get("failed") and m and st.get("failed") == m["id"] else "") or ""}

    # ---------- 반복 ----------
    def start(self, every=150):
        def loop():
            self._stop.wait(25)
            try:
                self.key()
            except Exception:
                pass
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception as e:
                    self.note = str(e)[:200]
                self._stop.wait(every)
        threading.Thread(target=loop, name="moa-album-ship", daemon=True).start()

    def stop(self):
        self._stop.set()


def git_reader(git_path, base, ref="origin/main"):
    """저장소 최신본(아직 내려받아 합치지 않은 것 포함)에서 파일을 읽는 함수."""
    def read(path):
        r = subprocess.run([git_path, "show", f"{ref}:{path}"], cwd=base, capture_output=True, timeout=60, creationflags=0x08000000 if os.name == "nt" else 0)
        return r.stdout if r.returncode == 0 else None
    return read
