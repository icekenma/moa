"""사진편집(우석World) = 노트북에서 따로 도는 PhotoGrader 웹 프로그램(AWS Album)으로 가는 문.

그 프로그램은 판매용 코드라 이 저장소에 없다. 같은 노트북에서 자기 서버(기본 8790)로 돌고 있고, 화면·기능·업데이트는 전부 그쪽 것이다.
우석World는 그 서버가 켜져 있는지와 접속 주소만 알아내서, 사진편집을 누르면 그 프로그램의 첫 화면을 그대로 띄운다.
"""
import json
import os
import re
import urllib.error
import urllib.request

URL_OK = re.compile(r"https?://[A-Za-z0-9.\-]+(:\d{1,5})?/?")


class AlbumError(ValueError):
    pass


def _fetch(url, timeout=2.0):
    """(상태 코드, JSON) — 연결이 안 되면 (0, None)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None


class Album:
    def __init__(self, db, fetch=None, port=None):
        self.db = db
        self.port = int(port or os.environ.get("ALBUM_PORT") or 8790)
        self._fetch = fetch or _fetch

    def _get(self, path):
        return self._fetch(f"http://127.0.0.1:{self.port}{path}")

    def status(self):
        """running: 프로그램 서버가 켜져 있는가. urls: 그 프로그램이 알려 주는 접속 주소(https 먼저). pin: 잠금 번호를 쓰는가."""
        out = {"running": False, "name": "", "version": "", "port": self.port, "pin": False, "urls": [], "counts": None,
               "url": str(self.db.get_setting("album_url") or "")}
        code, v = self._get("/api/version")
        if code != 200 or not isinstance(v, dict):
            return out
        out.update(running=True, name=str(v.get("app") or ""), version=str(v.get("version") or ""))
        code, s = self._get("/api/state")
        if code == 401:
            out["pin"] = True
        elif code == 200 and isinstance(s, dict):
            urls = [str(u.get("url") or "") for u in (s.get("urls") or []) if isinstance(u, dict)]
            out["urls"] = sorted({u for u in urls if URL_OK.fullmatch(u)}, key=lambda u: (not u.startswith("https://"), u))
            out["pin"] = bool(s.get("pin"))
            c = s.get("counts") if isinstance(s.get("counts"), dict) else {}
            out["counts"] = {k: int(c.get(k) or 0) for k in ("all", "best", "fav", "albums")}
        return out

    def set_url(self, url):
        """접속 주소를 직접 정한다(자동으로 못 찾을 때). 빈 값이면 자동으로 되돌린다."""
        url = str(url or "").strip().rstrip("/")
        if url and not URL_OK.fullmatch(url):
            raise AlbumError("주소는 https://… 처럼 적어 주세요 (경로 없이)")
        self.db.set_setting("album_url", url)
        return self.status()

    def brief(self):
        c = self.status()["counts"]
        return {"count": c["all"], "best": c["best"]} if c else None
