"""사진편집(우석World) = 노트북에서 따로 도는 PhotoGrader 웹 프로그램(AWS Album)으로 가는 문.

그 프로그램은 판매용 코드라 이 저장소에 없다. 같은 노트북에서 자기 서버(기본 8790)로 돌고 있고, 화면·기능·업데이트는 전부 그쪽 것이다.
우석World는 그 서버가 켜져 있는지와 '휴대폰에서 열 수 있는 https 주소'만 알아내서, 사진편집을 누르면 그 프로그램의 첫 화면을 그대로 띄운다.

주소 찾기가 까다로운 이유: 프로그램은 자기 https 주소를 '노트북 이름.ts.net'(443)이라고만 알려 주는데, 그 자리를 다른 서버(우석World 등)가
쓰고 있을 수 있다. 그래서 ① Tailscale에 실제로 걸린 연결(serve)을 읽고 ② 후보 주소마다 직접 물어봐서 그 프로그램이 맞는지 확인한다.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

URL_OK = re.compile(r"https?://[A-Za-z0-9.\-]+(:\d{1,5})?/?")
HTTPS_PORTS = (443, 8443, 10000)             # Tailscale serve가 https로 열 수 있는 포트


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


def _ts_exe():
    for p in (shutil.which("tailscale"), r"C:\Program Files\Tailscale\tailscale.exe",
              "/Applications/Tailscale.app/Contents/MacOS/Tailscale", "/usr/bin/tailscale"):
        if p and os.path.exists(p):
            return p
    return None


def _ts_run(*args, timeout=20):
    """(종료 코드, 출력). Tailscale이 없으면 (-1, '')."""
    exe = _ts_exe()
    if not exe:
        return -1, ""
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, creationflags=0x08000000 if os.name == "nt" else 0)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return -2, str(e)[:200]


def _url(host, port):
    return f"https://{host}" + ("" if int(port) == 443 else f":{int(port)}")


class Album:
    def __init__(self, db, fetch=None, port=None, ts=None):
        self.db = db
        self.port = int(port or os.environ.get("ALBUM_PORT") or 8790)
        self._fetch = fetch or _fetch
        self._ts = ts or _ts_run
        self._seen = (0.0, None)             # 주소 확인은 몇 초 걸릴 수 있어 잠깐 기억해 둔다

    def _get(self, path):
        return self._fetch(f"http://127.0.0.1:{self.port}{path}")

    # ---------- Tailscale에 걸린 연결 ----------
    def serve_map(self):
        """[(https 주소, 이어진 곳)] — 'tailscale serve'에 걸려 있는 것 전부. host: 이 노트북의 ts.net 이름."""
        out, host = [], ""
        code, txt = self._ts("status", "--json")
        if code == 0:
            try:
                host = str(((json.loads(txt) or {}).get("Self") or {}).get("DNSName") or "").rstrip(".")
            except ValueError:
                pass
        code, txt = self._ts("serve", "status", "--json")
        if code == 0:
            try:
                web = (json.loads(txt or "{}") or {}).get("Web") or {}
            except ValueError:
                web = {}
            for hp, v in web.items():
                h, _, p = str(hp).partition(":")
                for path, hd in ((v or {}).get("Handlers") or {}).items():
                    if path == "/" and isinstance(hd, dict) and hd.get("Proxy") and re.fullmatch(r"[A-Za-z0-9.\-]+", h):
                        out.append((_url(h, p or 443), str(hd["Proxy"])))
                        host = host or h
        return out, host

    def _find(self, reported):
        """후보 주소마다 직접 물어봐서 그 프로그램인지 가린다 → [{url, what}] (what: album·other·fail·serve)"""
        served, host = self.serve_map()
        mine = [u for u, to in served if re.search(rf"(127\.0\.0\.1|localhost):{self.port}\b", to)]
        hosts = {host} if host else set()
        hosts |= {re.sub(r"^https://|:\d+$|/$", "", u) for u in reported if u.startswith("https://")}
        cands = list(dict.fromkeys(mine + [u for u in reported if u.startswith("https://")] + [_url(h, p) for h in sorted(hosts) if h for p in HTTPS_PORTS]))
        res = {}

        def ask(u):
            code, v = self._fetch(u + "/api/version", 4.0)
            res[u] = "album" if code == 200 and isinstance(v, dict) and str(v.get("app") or "").startswith("AWS Album") else ("fail" if code == 0 else "other")
        th = [threading.Thread(target=ask, args=(u,), daemon=True) for u in cands[:8]]
        [t.start() for t in th]
        [t.join(6) for t in th]
        out = [{"url": u, "what": "serve" if (u in mine and res.get(u) != "album") else res.get(u, "fail")} for u in cands[:8]]
        rank = {"album": 0, "serve": 1, "fail": 2, "other": 3}
        return sorted(out, key=lambda x: rank[x["what"]]), bool(host), bool(mine)

    def status(self, fresh=False):
        """running: 프로그램 서버가 켜져 있는가. urls: 휴대폰에서 써 볼 주소(확실한 것부터). checked: 주소마다 확인한 결과."""
        out = {"running": False, "name": "", "version": "", "port": self.port, "pin": False, "urls": [], "checked": [], "counts": None,
               "tailscale": False, "can_serve": False, "url": str(self.db.get_setting("album_url") or "")}
        code, v = self._get("/api/version")
        if code != 200 or not isinstance(v, dict):
            return out
        out.update(running=True, name=str(v.get("app") or ""), version=str(v.get("version") or ""))
        code, s = self._get("/api/state")
        reported = []
        if code == 401:
            out["pin"] = True
        elif code == 200 and isinstance(s, dict):
            reported = [u for u in (str(x.get("url") or "").rstrip("/") for x in (s.get("urls") or []) if isinstance(x, dict)) if URL_OK.fullmatch(u)]
            out["pin"] = bool(s.get("pin"))
            c = s.get("counts") if isinstance(s.get("counts"), dict) else {}
            out["counts"] = {k: int(c.get(k) or 0) for k in ("all", "best", "fav", "albums")}
        at, seen = self._seen
        if fresh or not seen or time.time() - at > 45:
            seen = self._find(reported)
            self._seen = (time.time(), seen)
        checked, has_ts, mapped = seen
        out["checked"] = checked
        # 그 프로그램이 맞다고 확인된 주소 → Tailscale에 걸려 있는 주소 → (확인은 못 했지만) 프로그램이 알려 준 주소. 다른 서버로 확인된 주소는 뺀다
        good = [x["url"] for x in checked if x["what"] in ("album", "serve")]
        maybe = [x["url"] for x in checked if x["what"] == "fail" and x["url"] in reported]
        out["urls"] = good + maybe + sorted(u for u in reported if u.startswith("http://"))
        out["tailscale"] = has_ts
        out["can_serve"] = has_ts and not good
        return out

    def make_https(self):
        """이 프로그램으로 가는 https 주소가 Tailscale에 없을 때 하나 만든다(비어 있는 포트에, 이미 있는 연결은 건드리지 않는다)."""
        st = self.status(fresh=True)
        if not st["running"]:
            raise AlbumError("프로그램이 꺼져 있어요. 먼저 노트북에서 프로그램을 켜 주세요")
        if any(x["what"] in ("album", "serve") for x in st["checked"]):
            return st
        served, host = self.serve_map()
        if not host:
            raise AlbumError("노트북의 Tailscale이 꺼져 있거나 설치되지 않았어요")
        used = {u for u, _ in served}
        port = next((p for p in HTTPS_PORTS if _url(host, p) not in used), None)
        if port is None:
            raise AlbumError("Tailscale https 포트(443·8443·10000)가 모두 쓰이고 있어요")
        code, txt = self._ts("serve", "--bg", f"--https={port}", f"http://127.0.0.1:{self.port}", timeout=40)
        if code != 0:
            raise AlbumError("https 주소를 만들지 못했어요: " + (txt.strip().splitlines() or ["알 수 없는 오류"])[-1][:160])
        return self.status(fresh=True)

    def set_url(self, url):
        """접속 주소를 직접 정한다(자동으로 못 찾을 때). 빈 값이면 자동으로 되돌린다."""
        url = str(url or "").strip().rstrip("/")
        if url and not URL_OK.fullmatch(url):
            raise AlbumError("주소는 https://… 처럼 적어 주세요 (경로 없이)")
        self.db.set_setting("album_url", url)
        return self.status()

    def brief(self):
        code, s = self._get("/api/state")
        c = s.get("counts") if code == 200 and isinstance(s, dict) and isinstance(s.get("counts"), dict) else None
        return {"count": int(c.get("all") or 0), "best": int(c.get("best") or 0)} if c else None
