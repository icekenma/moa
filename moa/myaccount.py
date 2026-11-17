"""내 계좌 보기(조회 전용). 주문 기능과 완전히 분리되어 있다.

실전 계좌 키를 넣어도 주문은 나가지 않는다(KiwoomClient read_only=True).
키움 호출 제한을 지키려고 결과를 잠깐(기본 4초) 저장해 두고 그 안의 요청에는 저장본을 준다.
"""
import threading
import time
from datetime import datetime

from .broker.kiwoom import KiwoomClient

CACHE_SEC = 4


class AccountView:
    def __init__(self, settings):
        self.settings = settings
        self._client = None
        self._key = None
        self._cache = None
        self._at = 0.0
        self._err = None
        self._lock = threading.Lock()

    def _conf(self):
        return self.settings.get("kiwoom_view")

    def configured(self):
        c = self._conf()
        return bool(c.get("appkey") and c.get("secretkey"))

    def client(self):
        c = self._conf()
        key = (c.get("appkey"), c.get("secretkey"), bool(c.get("mock")))
        if self._client is None or key != self._key:
            self._client = KiwoomClient(c["appkey"], c["secretkey"], c.get("mock", False), read_only=True)
            self._key, self._cache = key, None
        return self._client

    def snapshot(self, force=False):
        if not self.configured():
            return {"configured": False}
        with self._lock:
            if not force and self._cache and time.time() - self._at < CACHE_SEC:
                return self._cache
            try:
                cl = self.client()
                data = cl.account_summary()
                data.update(configured=True, ok=True, server="모의투자" if cl.mock else "실전",
                            updated_at=datetime.now().isoformat(timespec="seconds"))
                self._cache, self._at, self._err = data, time.time(), None
            except Exception as e:
                self._err = str(e)
                if self._cache:  # 마지막 성공값을 보여 주되 오류를 함께 알린다
                    return dict(self._cache, ok=False, error=self._err)
                return {"configured": True, "ok": False, "error": self._err}
            return self._cache

    def total(self):
        s = self.snapshot()
        if not s.get("total") and not s.get("ok"):
            raise RuntimeError(s.get("error") or "키움 계좌를 불러오지 못했어요")
        return s["total"]
