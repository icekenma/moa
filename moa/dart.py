"""금융감독원 전자공시(OpenDART) 최근 공시 제목.

인증키는 사용자가 설정 화면에서 직접 넣는다(설정의 dart.api_key, 가려서 보관). 키가 없으면 아무것도 하지 않는다.
공시검색(list.json)을 회사 지정 없이 최근 며칠만 받아(주요사항보고 B · 거래소공시 I), 종목코드별로 제목을 묶어 둔다.
AI 추천 후보와 보유 종목 재료 확인에 '최근 공시'로 붙인다. 제목만 쓰고 본문은 받지 않는다.
"""
import threading
import time
from datetime import datetime, timedelta

import requests

URL = "https://opendart.fss.or.kr/api/list.json"
TYPES = ("B", "I")          # 주요사항보고 · 거래소공시(공급계약, 실적, 증자, 거래정지 같은 주가에 바로 닿는 것)
DAYS = 4                    # 주말을 끼어도 마지막 거래일이 들어오게
MAX_PAGES = 12              # 유형마다(한 장 100건)
TTL = 1800                  # 30분에 한 번만 다시 받는다


class Dart:
    def __init__(self, rt, get=None):
        self.rt = rt
        self._get = get or (lambda params: requests.get(URL, params=params, timeout=8).json())
        self._lock = threading.Lock()
        self._at, self._map, self.note = 0.0, {}, None

    def key(self):
        return str((self.rt.settings.all().get("dart") or {}).get("api_key") or "").strip()

    def configured(self):
        return bool(self.key())

    def _load(self):
        key, now = self.key(), datetime.now()
        out, n = {}, 0
        for ty in TYPES:
            for page in range(1, MAX_PAGES + 1):
                d = self._get({"crtfc_key": key, "bgn_de": (now - timedelta(days=DAYS)).strftime("%Y%m%d"), "end_de": now.strftime("%Y%m%d"),
                               "pblntf_ty": ty, "page_no": page, "page_count": 100})
                st = str(d.get("status") or "")
                if st == "013":                         # 조회된 자료 없음
                    break
                if st != "000":
                    raise RuntimeError(f"DART 응답 {st}: {d.get('message') or ''}"[:120])
                for r in d.get("list") or []:
                    code = str(r.get("stock_code") or "").strip()
                    if len(code) == 6:
                        dt = str(r.get("rcept_dt") or "")
                        out.setdefault(code, []).append((dt, f"{dt[4:6]}-{dt[6:8]} {str(r.get('report_nm') or '').strip()}"[:70]))
                        n += 1
                if page >= int(d.get("total_page") or 1):
                    break
        for code in out:
            out[code] = [t for _, t in sorted(set(out[code]), reverse=True)]
        self.note = {"ok": True, "n": n, "codes": len(out), "at": now.isoformat(timespec="seconds")}
        return out

    def recent(self):
        """종목코드 → 최근 공시 제목들(새것부터). 키가 없거나 못 받으면 빈 사전(직전에 받은 것이 있으면 그것)."""
        if not self.configured():
            return {}
        with self._lock:
            if time.time() - self._at >= TTL:
                self._at = time.time()                  # 실패해도 30분 안에는 다시 부르지 않는다
                try:
                    self._map = self._load()
                except Exception as e:
                    self.note = {"ok": False, "error": str(e)[:160], "at": datetime.now().isoformat(timespec="seconds")}
            return self._map

    def titles(self, code, n=4):
        return (self.recent().get(str(code)) or [])[:n]

    def status(self, fresh=False):
        if fresh:
            self._at = 0.0
        m = self.recent()
        return {"configured": self.configured(), "note": self.note, "sample": {k: v[:2] for k, v in list(m.items())[:3]}}
