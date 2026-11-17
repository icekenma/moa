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
    "daily": ("/api/dostk/chart", "ka10081"),
    "acctno": ("/api/dostk/acnt", "ka00001"),      # 계좌번호조회
    "watchinfo": ("/api/dostk/stkinfo", "ka10095"), # 관심종목정보요청(여러 종목 | 로 한 번에)      # 계좌번호조회       # 주식일봉차트조회요청
    "index": ("/api/dostk/sect", "ka20001"),        # 업종현재가요청(코스피 001, 코스닥 101)
    "stocklist": ("/api/dostk/stkinfo", "ka10099"), # 종목정보 리스트(코스피 0, 코스닥 10, ETF 8)
    "buy": ("/api/dostk/ordr", "kt10000"),          # 주식 매수주문
    "sell": ("/api/dostk/ordr", "kt10001"),         # 주식 매도주문
    "balance": ("/api/dostk/acnt", "kt00018"),      # 계좌평가잔고내역요청
    "deposit": ("/api/dostk/acnt", "kt00001"),      # 예수금상세현황요청
}
MIN_INTERVAL = 0.21  # 같은 앱키의 호출 간 최소 간격(초). 키움 조회 한도(초당 약 5회) 대비.
SLOW_APIS = {"kt00001": 1.1, "kt00018": 1.1}  # 계좌 조회는 같은 TR을 1초에 한 번만
CACHE_APIS = {"kt00001": 2.5, "kt00018": 2.5}  # 여러 화면·엔진이 동시에 물어도 키움에는 한 번만
_SHARED = {}  # (앱키, 모의 여부) -> 토큰·호출 간격·응답 저장을 앱키 하나당 하나로 공유
_SHARED_LOCK = threading.Lock()


def _shared(appkey, mock):
    with _SHARED_LOCK:
        return _SHARED.setdefault((appkey, bool(mock)), {
            "lock": threading.Lock(), "last": 0.0, "token": None, "exp": 0.0, "api_last": {}, "cache": {}})


class KiwoomError(RuntimeError):
    pass


def snum(v):
    """부호를 살린 숫자. '-1,234' -> -1234.0"""
    if v is None:
        return 0.0
    t = str(v).replace(",", "").strip()
    if not t:
        return 0.0
    try:
        return float(t)
    except ValueError:
        return 0.0


def code6(v):
    """'A005930' 같은 접두어를 떼고 6자리 코드만."""
    d = "".join(ch for ch in str(v) if ch.isalnum())
    return d[-6:] if len(d) > 6 else d


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
    def __init__(self, appkey, secretkey, mock=True, timeout=8, read_only=False):
        if not appkey or not secretkey:
            raise KiwoomError("키움 앱키와 시크릿키를 설정에서 넣어 주세요")
        self.appkey, self.secretkey, self.mock = appkey, secretkey, bool(mock)
        self.base = BASE[self.mock]
        self.read_only = read_only  # True면 주문 함수가 아예 동작하지 않는다(조회 전용)
        self.last_headers = {}
        self.timeout = timeout
        self._sh = _shared(appkey, self.mock)
        self.session = requests.Session()

    def token(self):
        sh = self._sh
        if sh["token"] and time.time() < sh["exp"] - 60:
            return sh["token"]
        r = self.session.post(self.base + API["token"], timeout=self.timeout, json={
            "grant_type": "client_credentials", "appkey": self.appkey, "secretkey": self.secretkey})
        data = r.json() if r.content else {}
        if r.status_code != 200 or not data.get("token"):
            raise KiwoomError(f"토큰 발급 실패: {data.get('return_msg') or r.status_code}")
        sh["token"] = data["token"]
        exp = data.get("expires_dt")
        try:
            sh["exp"] = datetime.strptime(exp, "%Y%m%d%H%M%S").timestamp()
        except (TypeError, ValueError):
            sh["exp"] = time.time() + 3600
        return sh["token"]

    def call(self, key, body, cont_key=None):
        path, api_id = API[key]
        sh = self._sh
        ckey = (api_id, repr(sorted(body.items())), cont_key)
        ttl = CACHE_APIS.get(api_id)
        if ttl:
            hit = sh["cache"].get(ckey)
            if hit and time.time() - hit[0] < ttl:
                self.last_headers = hit[2]
                return hit[1]
        for attempt in range(2):
            with sh["lock"]:
                now = time.time()
                wait = max(MIN_INTERVAL - (now - sh["last"]),
                           SLOW_APIS.get(api_id, 0) - (now - sh["api_last"].get(api_id, 0)))
                if wait > 0:
                    time.sleep(wait)
                headers = {"Content-Type": "application/json;charset=UTF-8", "api-id": api_id,
                           "authorization": f"Bearer {self.token()}"}
                if cont_key:
                    headers.update({"cont-yn": "Y", "next-key": cont_key})
                r = self.session.post(self.base + path, json=body, headers=headers, timeout=self.timeout)
                sh["last"] = sh["api_last"][api_id] = time.time()
                self.last_headers = {k.lower(): v for k, v in getattr(r, "headers", {}).items()}
            data = r.json() if r.content else {}
            code = str(data.get("return_code", "0"))
            msg = str(data.get("return_msg") or "")
            if attempt == 0 and ("1700" in msg or code == "5"):  # 요청 한도 초과: 잠깐 쉬고 한 번 더
                time.sleep(1.2)
                continue
            break
        if r.status_code != 200 or code not in ("0", ""):
            raise KiwoomError(f"{api_id} 실패: {msg or r.status_code}")
        if ttl:
            sh["cache"][ckey] = (time.time(), data, self.last_headers)
        if key in ("buy", "sell"):
            sh["cache"].clear()  # 주문 뒤에는 잔고·예수금을 새로 받는다
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
        if self.read_only:
            raise KiwoomError("조회 전용 연결이라 주문할 수 없어요")
        body = {"dmst_stex_tp": "KRX", "stk_cd": code, "ord_qty": str(int(qty)),
                "ord_uv": "" if price is None else str(int(price)),
                "trde_tp": "3" if price is None else "0", "cond_uv": ""}
        d = self.call(side, body)
        return d.get("ord_no", "")

    def balance_pages(self, max_pages=10):
        """kt00018 전체(연속조회 포함). 첫 페이지의 계좌 요약과 모든 종목 행을 돌려준다."""
        first, rows, key = None, [], None
        for _ in range(max_pages):
            d = self.call("balance", {"qry_tp": "1", "dmst_stex_tp": "KRX"}, cont_key=key)
            first = first or d
            rows += d.get("acnt_evlt_remn_indv_tot") or []
            if self.last_headers.get("cont-yn") != "Y" or not self.last_headers.get("next-key"):
                break
            key = self.last_headers["next-key"]
        return first or {}, rows

    def holdings(self):
        _, rows = self.balance_pages()
        out = []
        for r in rows:
            qty = int(num(r.get("rmnd_qty")))
            if qty <= 0:
                continue
            out.append({"code": code6(r.get("stk_cd", "")), "name": r.get("stk_nm", ""),
                        "qty": qty, "avg_price": num(r.get("pur_pric")), "price": num(r.get("cur_prc"))})
        return out

    def account_summary(self):
        """계좌 전체 요약 + 종목별 상세(조회만)."""
        top, rows = self.balance_pages()
        dep = self.call("deposit", {"qry_tp": "3"})
        items = []
        for r in rows:
            qty = int(num(r.get("rmnd_qty")))
            if qty <= 0:
                continue
            items.append({
                "code": code6(r.get("stk_cd", "")), "name": r.get("stk_nm", ""), "qty": qty,
                "sellable": int(num(r.get("trde_able_qty"))),
                "avg_price": num(r.get("pur_pric")), "price": num(r.get("cur_prc")),
                "buy_amt": num(r.get("pur_amt")), "value": num(r.get("evlt_amt")),
                "pnl": snum(r.get("evltv_prft")), "pnl_pct": snum(r.get("prft_rt")),
            })
        items.sort(key=lambda x: -x["value"])
        cash = num(dep.get("entr"))
        return {
            "total": num(top.get("prsm_dpst_aset_amt")) or (num(top.get("tot_evlt_amt")) + cash),
            "eval": num(top.get("tot_evlt_amt")), "buy": num(top.get("tot_pur_amt")),
            "pnl": snum(top.get("tot_evlt_pl")), "pnl_pct": snum(top.get("tot_prft_rt")),
            "cash": cash, "cash_d2": num(dep.get("d2_entra")), "orderable": num(dep.get("ord_alow_amt")),
            "holdings": items,
        }

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
