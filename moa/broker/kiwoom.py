"""키움 REST API 클라이언트(국내주식).

※ 엔드포인트·api-id·응답 필드명은 키움 REST API 문서 기준으로 작성했다. 키움이 사양을 바꾸면
   아래 API 표만 고치면 된다. 처음 쓸 때 설정 화면의 '연결 확인'으로 꼭 확인할 것.
"""
import threading
import time
from datetime import datetime

import requests

BASE = {True: "https://mockapi.kiwoom.com", False: "https://api.kiwoom.com"}
API = {
    "token": "/oauth2/token",
    "quote": ("/api/dostk/stkinfo", "ka10001"),     # 주식기본정보요청
    "minute": ("/api/dostk/chart", "ka10080"),      # 주식분봉차트조회요청
    "buy": ("/api/dostk/ordr", "kt10000"),          # 주식 매수주문
    "sell": ("/api/dostk/ordr", "kt10001"),         # 주식 매도주문
    "balance": ("/api/dostk/acnt", "kt00018"),      # 계좌평가잔고내역요청
    "deposit": ("/api/dostk/acnt", "kt00001"),      # 예수금상세현황요청
}
MIN_INTERVAL = 0.25  # 호출 간 최소 간격(초). 유량 제한 대비.


class KiwoomError(RuntimeError):
    pass


def num(v):
    """'+70100', '-1,234', '' 같은 값을 숫자로."""
    if v is None:
        return 0.0
    s = str(v).replace(",", "").replace("+", "").strip()
    if not s:
        return 0.0
    try:
        return abs(float(s))
    except ValueError:
        return 0.0


class KiwoomClient:
    def __init__(self, appkey, secretkey, mock=True, timeout=8):
        if not appkey or not secretkey:
            raise KiwoomError("키움 앱키와 시크릿키를 설정에서 넣어 주세요")
        self.appkey, self.secretkey, self.mock = appkey, secretkey, bool(mock)
        self.base = BASE[self.mock]
        self.timeout = timeout
        self._token, self._exp = None, 0.0
        self._lock = threading.Lock()
        self._last = 0.0
        self.session = requests.Session()

    def token(self):
        if self._token and time.time() < self._exp - 60:
            return self._token
        r = self.session.post(self.base + API["token"], timeout=self.timeout, json={
            "grant_type": "client_credentials", "appkey": self.appkey, "secretkey": self.secretkey})
        data = r.json() if r.content else {}
        if r.status_code != 200 or not data.get("token"):
            raise KiwoomError(f"토큰 발급 실패: {data.get('return_msg') or r.status_code}")
        self._token = data["token"]
        exp = data.get("expires_dt")
        try:
            self._exp = datetime.strptime(exp, "%Y%m%d%H%M%S").timestamp()
        except (TypeError, ValueError):
            self._exp = time.time() + 3600
        return self._token

    def call(self, key, body, cont_key=None):
        path, api_id = API[key]
        with self._lock:
            wait = MIN_INTERVAL - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            headers = {"Content-Type": "application/json;charset=UTF-8", "api-id": api_id,
                       "authorization": f"Bearer {self.token()}"}
            if cont_key:
                headers.update({"cont-yn": "Y", "next-key": cont_key})
            r = self.session.post(self.base + path, json=body, headers=headers, timeout=self.timeout)
            self._last = time.time()
        data = r.json() if r.content else {}
        if r.status_code != 200 or str(data.get("return_code", "0")) not in ("0", ""):
            raise KiwoomError(f"{api_id} 실패: {data.get('return_msg') or r.status_code}")
        return data

    # ---- 시세 ----
    def quote(self, code):
        d = self.call("quote", {"stk_cd": code})
        return {"code": code, "name": d.get("stk_nm", code), "price": num(d.get("cur_prc")),
                "open": num(d.get("open_pric")), "high": num(d.get("high_pric")),
                "low": num(d.get("low_pric")), "volume": int(num(d.get("trde_qty")))}

    def minute_bars(self, code, day):
        """당일(YYYYMMDD) 완성 분봉, 오래된 순."""
        d = self.call("minute", {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1"})
        rows = d.get("stk_min_pole_chart_qry") or []
        bars = []
        for r in rows:
            ts = str(r.get("cntr_tm", ""))
            if ts[:8] != day:
                continue
            bars.append({"t": f"{ts[8:10]}:{ts[10:12]}", "o": num(r.get("open_pric")),
                         "h": num(r.get("high_pric")), "l": num(r.get("low_pric")),
                         "c": num(r.get("cur_prc")), "v": int(num(r.get("trde_qty")))})
        bars.sort(key=lambda b: b["t"])
        now_t = datetime.now().strftime("%H:%M")
        return [b for b in bars if "09:00" <= b["t"] < now_t]  # 진행 중인 봉 제외

    # ---- 계좌 ----
    def order(self, side, code, qty, price=None):
        body = {"dmst_stex_tp": "KRX", "stk_cd": code, "ord_qty": str(int(qty)),
                "ord_uv": "" if price is None else str(int(price)),
                "trde_tp": "3" if price is None else "0", "cond_uv": ""}
        d = self.call(side, body)
        return d.get("ord_no", "")

    def holdings(self):
        d = self.call("balance", {"qry_tp": "1", "dmst_stex_tp": "KRX"})
        out = []
        for r in d.get("acnt_evlt_remn_indv_tot") or []:
            qty = int(num(r.get("rmnd_qty")))
            if qty <= 0:
                continue
            out.append({"code": str(r.get("stk_cd", "")).lstrip("A"), "name": r.get("stk_nm", ""),
                        "qty": qty, "avg_price": num(r.get("pur_pric")), "price": num(r.get("cur_prc"))})
        return out

    def cash(self):
        d = self.call("deposit", {"qry_tp": "3"})
        for k in ("ord_alow_amt", "d2_entra", "entr"):
            if d.get(k) not in (None, ""):
                return num(d.get(k))
        return 0.0


class KiwoomFeed:
    """키움 시세를 엔진이 쓰는 형식으로. 분봉은 1분에 한 번만 다시 받는다."""
    kind = "kiwoom"

    def __init__(self, client):
        self.client = client
        self._bars = {}
        self._names = {}

    def now(self):
        return datetime.now()

    def market_open(self):
        n = datetime.now()
        return n.weekday() < 5 and "09:00" <= n.strftime("%H:%M") < "15:30"

    def name(self, code):
        if code not in self._names:
            try:
                self._names[code] = self.client.quote(code)["name"]
            except KiwoomError:
                return code
        return self._names[code]

    def quote(self, code):
        q = self.client.quote(code)
        self._names[code] = q["name"]
        return q

    def bars(self, code):
        n = datetime.now()
        key = n.strftime("%Y%m%d%H%M")
        hit = self._bars.get(code)
        if hit and hit[0] == key:
            return hit[1]
        bars = self.client.minute_bars(code, n.strftime("%Y%m%d"))
        self._bars[code] = (key, bars)
        return bars
