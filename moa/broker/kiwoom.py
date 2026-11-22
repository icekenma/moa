"""키움 REST API 클라이언트(국내주식).

※ 엔드포인트·api-id·응답 필드명은 키움 REST API 문서 기준으로 작성했다. 키움이 사양을 바꾸면
   아래 API 표만 고치면 된다. 처음 쓸 때 설정 화면의 '연결 확인'으로 꼭 확인할 것.
"""
import re
import threading
import time
from datetime import datetime

import requests

BASE = {True: "https://mockapi.kiwoom.com", False: "https://api.kiwoom.com"}
API = {
    "token": "/oauth2/token",
    "quote": ("/api/dostk/stkinfo", "ka10001"),     # 주식기본정보요청
    "minute": ("/api/dostk/chart", "ka10080"),      # 주식분봉차트조회요청
    "daily": ("/api/dostk/chart", "ka10081"),       # 주식일봉차트조회요청
    "weekly": ("/api/dostk/chart", "ka10082"),      # 주봉
    "monthly": ("/api/dostk/chart", "ka10083"),     # 월봉
    "orderbook": ("/api/dostk/mrkcond", "ka10004"), # 주식호가(10단계)
    "ticks": ("/api/dostk/stkinfo", "ka10003"),     # 체결정보
    "rank_value": ("/api/dostk/rkinfo", "ka10032"), # 거래대금상위
    "rank_volume": ("/api/dostk/rkinfo", "ka10030"),# 당일거래량상위
    "rank_change": ("/api/dostk/rkinfo", "ka10027"),# 전일대비등락률상위
    "us_book": ("/api/us/mrkcond", "usa20101"),     # 미국 10호가
    "us_ticks": ("/api/us/mrkcond", "usa20150"),     # 미국 체결내역
    "us_rank_views": ("/api/us/rkinfo", "usa01980"), # 실시간 종목 조회 순위
    "us_rank_watch": ("/api/us/rkinfo", "usa01990"), # 관심종목 등록 상위
    "us_rank_value": ("/api/us/rkinfo", "usa20540"), # 당일 거래대금 상위
    "us_rank_kiwoom": ("/api/us/rkinfo", "usa20880"),# 키움 고객 매수 상위
    "us_rank_price": ("/api/us/stkinfo", "usa20930"),  # 미국 가격급등락
    "us_rank_volume": ("/api/us/stkinfo", "usa20520"), # 미국 거래량급등
    "acctno": ("/api/dostk/acnt", "ka00001"),      # 계좌번호조회
    "watchinfo": ("/api/dostk/stkinfo", "ka10095"), # 관심종목정보요청(여러 종목 | 로 한 번에)      # 계좌번호조회       # 주식일봉차트조회요청
    "investor": ("/api/dostk/stkinfo", "ka10059"),  # 종목별투자자기관별(날짜별 개인·외국인·기관 순매수)
    "index": ("/api/dostk/sect", "ka20001"),        # 업종현재가요청(코스피 001, 코스닥 101)
    "stocklist": ("/api/dostk/stkinfo", "ka10099"), # 종목정보 리스트(코스피 0, 코스닥 10, ETF 8)
    "buy": ("/api/dostk/ordr", "kt10000"),          # 주식 매수주문
    "sell": ("/api/dostk/ordr", "kt10001"),         # 주식 매도주문
    "cancel": ("/api/dostk/ordr", "kt10003"),       # 주식 취소주문
    "unfilled": ("/api/dostk/acnt", "ka10075"),     # 미체결요청
    "fills": ("/api/dostk/acnt", "kt00007"),        # 계좌별주문체결내역상세(체결만)
    "us_fills": ("/api/us/acnt", "ust21150"),       # 미국주식 일별 주문체결내역
    "balance": ("/api/dostk/acnt", "kt00018"),
    # 미국주식(키움 공식 TR)
    "us_info": ("/api/us/stkinfo", "usa10100"),      # 종목 조회(거래소 구분 확인)
    "us_quote": ("/api/us/mrkcond", "usa20100"),     # 현재가 종목정보
    "us_buy": ("/api/us/ordr", "ust20000"),          # 매수주문
    "us_sell": ("/api/us/ordr", "ust20001"),         # 매도주문
    "us_cancel": ("/api/us/ordr", "ust20003"),       # 취소주문
    "us_unfilled": ("/api/us/acnt", "ust21050"),     # 원장 미체결
    "us_balance": ("/api/us/acnt", "ust21070"),      # 원장 잔고
    "us_deposit": ("/api/us/acnt", "ust21110"),      # 해외주식 예수금      # 계좌평가잔고내역요청
    "deposit": ("/api/dostk/acnt", "kt00001"),      # 예수금상세현황요청
}
MIN_INTERVAL = 0.21  # 같은 앱키의 호출 간 최소 간격(초). 키움 조회 한도(초당 약 5회) 대비.
SLOW_APIS = {"kt00007": 1.0, "ust21150": 1.0, "kt00001": 1.1, "kt00018": 1.1, "ka10075": 1.0, "ust21050": 1.0, "ust21070": 1.0, "ust21110": 1.0}  # 계좌 조회는 같은 TR을 1초에 한 번만
CACHE_APIS = {"kt00007": 5.0, "ust21150": 5.0, "kt00001": 2.5, "kt00018": 2.5, "ka10075": 2.0, "ust21050": 2.0, "ust21070": 2.5, "ust21110": 2.5,
              "usa10100": 86400, "usa20100": 2.0}  # 여러 화면·엔진이 동시에 물어도 키움에는 한 번만
_SHARED = {}  # (앱키, 모의 여부) -> 토큰·호출 간격·응답 저장을 앱키 하나당 하나로 공유
_SHARED_LOCK = threading.Lock()


def _shared(appkey, mock):
    with _SHARED_LOCK:
        return _SHARED.setdefault((appkey, bool(mock)), {
            "lock": threading.Lock(), "last": 0.0, "token": None, "exp": 0.0, "api_last": {}, "cache": {}})


class KiwoomError(RuntimeError):
    pass


def _empty_ok(fn):
    """조회 결과가 없을 때 키움이 오류로 돌려주는 경우('…내역이 없습니다')를 빈 목록으로."""
    def wrap(*a, **k):
        try:
            return fn(*a, **k)
        except KiwoomError as e:
            if re.search(r"(내역|잔고|데이터|조회할 자료)[이가]?\s*없", str(e)) or "571758" in str(e):
                return []
            raise
    wrap.__name__ = fn.__name__
    return wrap


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


US_HINT = {"NMS": "ND", "NGM": "ND", "NCM": "ND", "NASDAQ": "ND", "NASDAQGS": "ND", "NASDAQGM": "ND", "NASDAQCM": "ND", "나스닥": "ND",
           "NYQ": "NY", "NYSE": "NY", "뉴욕": "NY", "ASE": "NA", "AMEX": "NA", "PCX": "NA", "NYSEARCA": "NA", "BATS": "NA", "아멕스": "NA"}


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
        if key in ("buy", "sell", "cancel", "us_buy", "us_sell", "us_cancel"):
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

    # ---- 미국주식 ----
    US_EXCH = {"NASDAQ": "ND", "NYSE": "NY", "AMEX": "NA", "ARCA": "NA", "NYSEARCA": "NA", "BATS": "NA"}

    _US_EX_CACHE = {}  # 종목 → (거래소, 이름), 프로그램이 켜져 있는 동안 기억

    def us_exchange(self, sym, hint=None):
        """종목의 거래소 구분(ND 나스닥, NY 뉴욕, NA 아멕스).
        키움이 빈 거래소 구분을 받지 않아서, 힌트(공개 시세의 거래소)부터 ND→NY→NA 순으로 usa10100에 물어본다."""
        sym = str(sym).upper()
        if sym in self._US_EX_CACHE:
            return self._US_EX_CACHE[sym]
        h = US_HINT.get(str(hint or "").upper().replace(" ", ""), hint if hint in ("ND", "NY", "NA") else None)
        order = [h] + [x for x in ("ND", "NY", "NA") if x != h] if h else ["ND", "NY", "NA"]
        last = None
        for ex in order:
            try:
                d = self.call("us_info", {"stex_tp": ex, "stk_cd": sym})
            except KiwoomError as e:
                last = e
                continue
            got = str(d.get("stex_tp") or "").strip().upper()
            name = d.get("stk_nm") or d.get("stk_enm")
            if not name and not str(d.get("stk_cd") or "").strip():
                continue
            if got not in ("ND", "NY", "NA"):
                mk = str(d.get("mkgb") or "").upper().replace(" ", "")
                got = next((v for k, v in self.US_EXCH.items() if k in mk), ex)
            self._US_EX_CACHE[sym] = (got, name or sym)
            return self._US_EX_CACHE[sym]
        raise KiwoomError(f"{sym} 종목을 키움에서 찾지 못했어요" + (f" ({last})" if last else ""))

    def us_quote(self, sym, exch=None, hint=None):
        exch = exch if exch in ("ND", "NY", "NA") else self.us_exchange(sym, hint or exch)[0]
        d = self.call("us_quote", {"stex_tp": exch, "stk_cd": sym})
        price = num(d.get("cur_prc"))
        if not price:
            raise KiwoomError(f"{sym} 현재가가 없어요")
        chg = snum(d.get("pred_pre"))
        if str(d.get("pred_pre_sig", "")) in ("4", "5") and chg > 0:  # 하락 기호인데 부호가 없을 때
            chg = -chg
        return {"sym": sym, "name": d.get("stk_nm") or d.get("stk_enm") or sym, "price": price, "change": chg,
                "change_pct": snum(d.get("flu_rt")), "prev": num(d.get("base_close_pric")) or None,
                "open": num(d.get("open_pric")) or None, "high": num(d.get("high_pric")) or None,
                "low": num(d.get("low_pric")) or None, "volume": int(num(d.get("acc_trde_qty"))),
                "high52": num(d.get("52wk_hgst_pric")) or None, "low52": num(d.get("52wk_lwst_pric")) or None,
                "fx": num(d.get("base_exrt")) or None, "currency": "USD", "exch": exch,
                "exchange": {"ND": "나스닥", "NY": "뉴욕", "NA": "아멕스"}.get(exch, exch), "live": True}

    def us_order(self, side, sym, qty, price=None, exch=None):
        """미국주식 주문(ust20000 매수, ust20001 매도). price 없으면 시장가(정규장에서만)."""
        if self.read_only:
            raise KiwoomError("조회 전용 연결이라 주문할 수 없어요")
        exch = exch if exch in ("ND", "NY", "NA") else self.us_exchange(sym, exch)[0]
        body = {"stex_tp": exch, "stk_cd": sym, "ord_qty": str(int(qty)),
                "ord_uv": "" if price is None else f"{float(price):.2f}", "trde_tp": "03" if price is None else "00"}
        if side == "sell":
            body["stop_pric"] = ""
        d = self.call("us_buy" if side == "buy" else "us_sell", body)
        return d.get("ord_no", ""), exch

    def us_cancel(self, ord_no, sym, exch=None):
        if self.read_only:
            raise KiwoomError("조회 전용 연결이라 주문을 취소할 수 없어요")
        exch = exch if exch in ("ND", "NY", "NA") else self.us_exchange(sym, exch)[0]
        d = self.call("us_cancel", {"orig_ord_no": str(ord_no), "stex_tp": exch, "stk_cd": sym})
        return d.get("ord_no", "")

    @staticmethod
    def _sum_fills(rows, qty_key, px_key):
        """주문번호별 체결 수량과 평균 체결가(여러 번 나눠 체결돼도 가중 평균)."""
        out = {}
        for r in rows or []:
            no = str(r.get("ord_no", "")).strip().lstrip("0")
            q, px = num(r.get(qty_key)), num(r.get(px_key))
            if not no or q <= 0 or px <= 0:
                continue
            a = out.setdefault(no, [0.0, 0.0])
            a[0] += q
            a[1] += q * px
        return {k: {"qty": int(v[0]), "price": round(v[1] / v[0], 4)} for k, v in out.items()}

    @_empty_ok
    def fills_today(self):
        """오늘 국내 체결 내역(kt00007): {주문번호: {qty, price}}."""
        d = self.call("fills", {"ord_dt": datetime.now().strftime("%Y%m%d"), "qry_tp": "4", "stk_bond_tp": "1",
                                "sell_tp": "0", "stk_cd": "", "fr_ord_no": "", "dmst_stex_tp": "%"})
        return self._sum_fills(d.get("acnt_ord_cntr_prps_dtl"), "cntr_qty", "cntr_uv")

    @_empty_ok
    def us_fills_today(self):
        """오늘 미국 체결 내역(ust21150): {주문번호: {qty, price(달러)}}."""
        d = self.call("us_fills", {"ord_dt": "", "query_tp": "5", "slby_tp": "0", "stex_tp": "", "stk_cd": "",
                                   "oppo_trde_tp": "%", "fr_ord_no": ""})
        return self._sum_fills(d.get("result_list"), "cntr_qty", "cntr_uv")

    @_empty_ok
    def us_unfilled(self):
        d = self.call("us_unfilled", {"ord_dt": "", "slby_tp": "0", "stex_tp": "", "stk_cd": ""})
        out = []
        for r in d.get("result_list") or []:
            left = int(num(r.get("ord_remnq")))
            if left <= 0:
                continue
            nm = str(r.get("stex_nm") or "").upper().replace(" ", "")
            exch = next((v for k, v in self.US_EXCH.items() if k in nm), "")
            side = "sell" if ("매도" in str(r.get("slby_tp_nm", "")) or str(r.get("slby_tp")) == "1") else "buy"
            out.append({"ord_no": str(r.get("ord_no", "")).strip(), "code": str(r.get("stk_cd", "")).strip(),
                        "name": r.get("frgn_stk_nm") or r.get("stk_cd", ""), "side": side, "market": "us", "exch": exch,
                        "qty": int(num(r.get("ord_qty"))), "left": left, "filled": int(num(r.get("cntr_qty"))),
                        "price": num(r.get("ord_uv")), "time": str(r.get("ord_time") or "")})
        return out

    @_empty_ok
    def us_holdings(self):
        d = self.call("us_balance", {"stex_tp": "", "stk_cd": ""})
        out = []
        for r in d.get("result_list") or []:
            q = int(num(r.get("qty")))
            if q <= 0:
                continue
            out.append({"code": str(r.get("stk_cd", "")).strip(), "name": r.get("frgn_stk_nm") or r.get("stk_cd", ""),
                        "market": "us", "qty": q, "sellable": int(num(r.get("sell_alowq"))),
                        "avg_price": num(r.get("frgn_stk_book_uv")), "price": num(r.get("now_pric")),
                        "value_usd": num(r.get("evlt_amt")), "pnl_usd": snum(r.get("pl_amt")),
                        "value": num(r.get("evlt_amt_krw")), "pnl": snum(r.get("pl_amt_krw")),
                        "pnl_pct": snum(r.get("pl_rt")), "fx": num(r.get("exch_rate")) or None})
        return out

    def us_cash(self):
        """미국주식 주문가능 달러와 원화 예수금(ust21110)."""
        d = self.call("us_deposit", {})
        usd = next((r for r in d.get("result_list") or [] if str(r.get("crnc_code", "")).upper() == "USD"), {})
        return {"usd_orderable": num(usd.get("fc_ord_alowa")), "usd": num(usd.get("fc_entra")),
                "krw": num(d.get("krw_entra"))}

    @_empty_ok
    def unfilled(self):
        """미체결 주문(ka10075). 응답 목록 이름이 문서마다 달라 주문번호가 있는 목록을 찾아 쓴다."""
        d = self.call("unfilled", {"all_stk_tp": "0", "trde_tp": "0", "stk_cd": "", "stex_tp": "0"})
        rows = d.get("oso")
        if rows is None:
            rows = next((v for v in d.values() if isinstance(v, list) and v and isinstance(v[0], dict)
                         and "ord_no" in v[0]), [])
        out = []
        for r in rows or []:
            left = int(num(r.get("oso_qty")))
            if left <= 0:
                continue
            kind = f"{r.get('io_tp_nm', '')}{r.get('trde_tp', '')}{r.get('sell_tp', '')}"
            out.append({"ord_no": str(r.get("ord_no", "")).strip(), "code": code6(r.get("stk_cd", "")),
                        "name": r.get("stk_nm", ""), "side": "sell" if "매도" in kind else "buy",
                        "qty": int(num(r.get("ord_qty"))), "left": left, "filled": int(num(r.get("cntr_qty"))),
                        "price": num(r.get("ord_pric")), "time": str(r.get("tm") or r.get("ord_tm") or "")})
        return out

    def cancel(self, ord_no, code, qty=0):
        """주문 취소(kt10003). qty 0이면 남은 수량 전부."""
        if self.read_only:
            raise KiwoomError("조회 전용 연결이라 주문을 취소할 수 없어요")
        d = self.call("cancel", {"dmst_stex_tp": "KRX", "orig_ord_no": str(ord_no), "stk_cd": code,
                                 "cncl_qty": str(int(qty))})
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
            # 주문 가능 금액이 왜 그 값인지 풀어 볼 수 있게(예수금 중 주문·미수에 묶인 돈, 결제 전 금액 등)
            "cash_detail": {"margin": num(dep.get("profa_ch")), "d1": num(dep.get("d1_entra")), "d2": num(dep.get("d2_entra")),
                            "withdrawable": num(dep.get("pymn_alow_amt")), "ord100": num(dep.get("100stk_ord_alow_amt")),
                            "unpaid": num(dep.get("ch_uncla")), "us_krw_hold": num(dep.get("fc_stk_krw_repl_set_amt"))},
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
