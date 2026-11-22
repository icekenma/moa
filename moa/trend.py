"""검색량 추이(네이버 데이터랩 통합검색어 트렌드).

사용자가 설정에서 네이버 개발자센터의 Client ID·Secret을 직접 넣었을 때만 돈다(없으면 아무것도 하지 않는다).
종목 이름의 검색량이 최근 7일에 그 전 3주보다 얼마나 늘었는지(배수)만 쓴다. 한 번에 5개씩, 6시간에 한 번만 받는다.
검색량이 주가를 맞히는지는 모른다. 추천 성적의 '검색량' 기준으로 따로 잰다.
"""
import threading
import time
from datetime import datetime, timedelta

import requests

URL = "https://openapi.naver.com/v1/datalab/search"
TTL = 6 * 3600


class Trend:
    def __init__(self, rt, post=None):
        self.rt = rt
        self._post = post or (lambda body, cid, sec: requests.post(URL, json=body, timeout=8, headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec}).json())
        self._lock = threading.Lock()
        self._cache, self.note = {}, None

    def keys(self):
        c = self.rt.settings.all().get("naver") or {}
        return str(c.get("client_id") or "").strip(), str(c.get("client_secret") or "").strip()

    def configured(self):
        return all(self.keys())

    def ratios(self, names):
        """이름 → 검색량 배수(최근 7일 평균 ÷ 그 전 21일 평균). 못 구한 이름은 빠진다."""
        if not self.configured():
            return {}
        cid, sec = self.keys()
        now, out = time.time(), {}
        with self._lock:
            need = [n for n in dict.fromkeys(names) if n and (n not in self._cache or now - self._cache[n][0] >= TTL)]
            end = datetime.now().date() - timedelta(days=1)
            for i in range(0, len(need), 5):
                grp = need[i:i + 5]
                for n in grp:
                    self._cache[n] = (now, None)            # 실패해도 6시간 안에는 다시 부르지 않는다
                try:
                    d = self._post({"startDate": (end - timedelta(days=27)).isoformat(), "endDate": end.isoformat(), "timeUnit": "date",
                                    "keywordGroups": [{"groupName": n, "keywords": [n]} for n in grp]}, cid, sec)
                    if "results" not in d:
                        raise RuntimeError(str(d.get("errorMessage") or d)[:120])
                    for r in d["results"]:
                        by = {x["period"]: float(x["ratio"]) for x in r.get("data") or []}
                        days = [(end - timedelta(days=k)).isoformat() for k in range(28)]
                        new, old = [by.get(x, 0.0) for x in days[:7]], [by.get(x, 0.0) for x in days[7:]]
                        base = sum(old) / len(old)
                        if base > 0:
                            self._cache[r["title"]] = (now, round(sum(new) / len(new) / base, 2))
                    self.note = {"ok": True, "at": datetime.now().isoformat(timespec="seconds")}
                except Exception as e:
                    self.note = {"ok": False, "error": str(e)[:160], "at": datetime.now().isoformat(timespec="seconds")}
                    break
            for n in names:
                v = (self._cache.get(n) or (0, None))[1]
                if v is not None:
                    out[n] = v
        return out

    def status(self):
        return {"configured": self.configured(), "note": self.note}
