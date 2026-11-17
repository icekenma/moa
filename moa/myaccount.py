"""내 계좌 보기(조회 전용). 주문 기능과 완전히 분리되어 있다.

실전 계좌 키를 넣어도 주문은 나가지 않는다(KiwoomClient read_only=True).
키움 호출 제한을 지키려고 결과를 잠깐(기본 4초) 저장해 두고 그 안의 요청에는 저장본을 준다.
"""
import hashlib
import threading
import time
from datetime import datetime

from .broker.kiwoom import KiwoomClient

CACHE_SEC = 4


class AccountView:
    def __init__(self, settings, fx=None):
        self.settings = settings
        self.fx = fx  # 달러 환율(원) 알려 주는 함수. 미국 주식·달러 예수금을 원화로 합칠 때
        self._client = None
        self._key = None
        self._cache = None
        self._at = 0.0
        self._err = None
        self._lock = threading.Lock()
        self._acct_no = None

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
            self._key, self._cache, self._acct_no = key, None, None
        return self._client

    def snapshot(self, force=False):
        if not self.configured():
            return {"configured": False}
        age = time.time() - self._at
        if not force and self._cache and age < CACHE_SEC:
            return self._cache
        if not force and self._cache and age < 120 and self._cache.get("key_fp") == self._fp():
            # 조금 지난 값은 바로 주고 뒤에서 새로 받는다(화면이 키움 응답을 기다리지 않게)
            if not self._lock.locked():
                threading.Thread(target=self.snapshot, kwargs={"force": True}, daemon=True).start()
            return self._cache
        with self._lock:
            if not force and self._cache and time.time() - self._at < CACHE_SEC:
                return self._cache
            try:
                cl = self.client()
                data = cl.account_summary()
                self._add_us(cl, data)
                data.update(configured=True, ok=True, server="모의투자" if cl.mock else "실전",
                            acct_no=self._account_no(cl), key_fp=self._fp(),
                            updated_at=datetime.now().isoformat(timespec="seconds"))
                self._cache, self._at, self._err = data, time.time(), None
                self._save_last(data)
            except Exception as e:
                self._err = str(e)
                last = (self._cache if self._cache and self._cache.get("key_fp") == self._fp() else None) or self.last()
                if last:  # 마지막 성공값을 보여 주되 오류를 함께 알린다(서버를 다시 켠 뒤에도)
                    return dict(last, ok=False, error=self._err, stale=True)
                return {"configured": True, "ok": False, "error": self._err}
            return self._cache

    def _add_us(self, cl, data):
        """같은 계좌의 미국 주식 잔고와 달러 예수금을 원화로 바꿔 총자산에 합친다. 해외주식을 안 쓰는 계좌면 건너뛴다."""
        try:
            hold = cl.us_holdings()
            cash = cl.us_cash()
        except Exception as e:
            data["us"] = {"error": str(e)}
            return
        fx = next((h["fx"] for h in hold if h.get("fx")), None)
        if not fx and cash["usd"]:
            try:
                fx = float(self.fx()) if self.fx else None
            except Exception:
                fx = None
        usd_krw = round(cash["usd"] * fx) if fx else 0
        ev = sum(h["value"] for h in hold)
        pl = sum(h["pnl"] for h in hold)
        data["us"] = {"eval": ev, "pnl": pl, "usd": cash["usd"], "usd_orderable": cash["usd_orderable"],
                      "usd_krw": usd_krw, "fx": fx, "count": len(hold)}
        if not (hold or usd_krw):
            return
        data["kr_total"] = data["total"]
        data["total"] += ev + usd_krw
        data["eval"] += ev
        data["buy"] += ev - pl
        data["pnl"] += pl
        data["pnl_pct"] = round(data["pnl"] / data["buy"] * 100, 2) if data["buy"] else 0
        data["holdings"] = sorted(data["holdings"] + hold, key=lambda x: -x["value"])

    def _account_no(self, cl):
        """계좌번호(ka00001). 못 받아도 조회에는 지장 없게 빈 값."""
        if self._acct_no is None:
            try:
                d = cl.call("acctno", {})
                self._acct_no = str(d.get("acctNo") or d.get("acct_no") or "").strip()
            except Exception:
                self._acct_no = ""
        return self._acct_no

    def _save_last(self, data):
        """마지막 성공 조회를 DB에 남긴다(10초에 한 번). 키움이 안 될 때·서버 재시작 뒤에도 보여 준다."""
        if time.time() - getattr(self, "_saved_at", 0) < 10:
            return
        self._saved_at = time.time()
        try:
            self.settings.db.set_setting("kiwoom_last", data)
        except Exception:
            pass

    def _fp(self):
        """지금 넣은 앱키의 지문. 다른 계좌 키로 바꾸면 예전 계좌의 마지막 조회값을 쓰지 않으려고."""
        c = self._conf()
        return hashlib.sha256(f"{c.get('appkey', '')}|{bool(c.get('mock'))}".encode()).hexdigest()[:12]

    def last(self):
        try:
            d = self.settings.db.get_setting("kiwoom_last")
        except Exception:
            return None
        return d if d and d.get("key_fp") == self._fp() else None

    def total(self):
        s = self.snapshot()
        if not s.get("total") and not s.get("ok"):
            raise RuntimeError(s.get("error") or "키움 계좌를 불러오지 못했어요")
        return s["total"]
