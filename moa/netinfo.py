"""노트북이 인터넷에 나갈 때 쓰는 주소(공인 IP). 키움은 등록된 IP에서만 토큰을 내주므로, 주소가 바뀌면 이 값을 등록해야 한다."""
import re
import threading
import time

import requests

SOURCES = ("https://api.ipify.org", "https://checkip.amazonaws.com", "https://ifconfig.me/ip")
_IP = re.compile(r"^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)$")
_lock = threading.Lock()
_cache = {"ip": None, "at": 0.0}
KEEP = 120   # 초. 주소가 바뀐 직후에도 곧 새 값을 보여 주려고 짧게 둔다


def public_ip(force=False, get=None):
    """(ip, 확인한 시각 epoch). 못 알아내면 (None, 0)."""
    with _lock:
        if not force and _cache["ip"] and time.time() - _cache["at"] < KEEP:
            return _cache["ip"], _cache["at"]
        for url in SOURCES:
            try:
                r = (get or requests.get)(url, timeout=4, headers={"User-Agent": "curl/8"})
                ip = (r.text or "").strip()
                if r.status_code == 200 and _IP.match(ip):
                    _cache.update(ip=ip, at=time.time())
                    return ip, _cache["at"]
            except Exception:
                continue
        return None, 0
