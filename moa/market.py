"""시세: 국내(키움 REST, 실시간) + 미국(공개 시세, Yahoo Finance 차트 데이터).

- 국내 현재가 ka10001, 일봉 ka10081, 분봉 ka10080, 지수 ka20001, 종목 목록 ka10099
- 미국 현재가·차트·검색은 키움 REST 해외 TR의 요청 형식이 공개돼 있지 않아 공개 시세를 쓴다
- 키움 호출 한도를 지키려고 짧게(몇 초) 저장해 두고 그 안의 요청에는 저장본을 준다
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta

import requests

from .broker.kiwoom import KiwoomClient, code6, num, snum

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
YH = "https://query1.finance.yahoo.com"
KR_INDICES = [("코스피", "0", "001", "^KS11"), ("코스닥", "1", "101", "^KQ11")]
US_INDICES = [("S&P 500", "^GSPC"), ("나스닥", "^IXIC"), ("다우", "^DJI"), ("달러 환율", "USDKRW=X")]
RANGES = {"1d": ("1d", "5m"), "1m": ("1mo", "1d"), "3m": ("3mo", "1d"), "1y": ("1y", "1wk")}


class MarketError(RuntimeError):
    pass


class Cache:
    def __init__(self):
        self.d, self.lock = {}, threading.Lock()

    def get(self, key, ttl, fn):
        with self.lock:
            hit = self.d.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        val = fn()
        with self.lock:
            self.d[key] = (time.time(), val)
            if len(self.d) > 800:
                for k in list(self.d)[:200]:
                    self.d.pop(k, None)
        return val


class Market:
    def __init__(self, rt, data_dir):
        self.rt, self.data_dir = rt, data_dir
        self.cache = Cache()
        self.http = requests.Session()
        self.http.headers.update(UA)
        self._master = None
        self._mlock, self._mthread = threading.Lock(), None

    # ---------- 공통 ----------
    def kr_client(self):
        """국내 시세에 쓸 키움 연결: 내 계좌(조회 전용) 키 → 주문 계좌 키 순서."""
        if self.rt.account.configured():
            return self.rt.account.client()
        ks = self.rt.settings.get("kiwoom")
        for eng in ("scalp", "dca"):
            k = ks.get(eng) or {}
            if k.get("appkey") and k.get("secretkey"):
                key = (k["appkey"], k["secretkey"], bool(k.get("mock")))
                if getattr(self, "_ckey", None) != key:
                    self._cl = KiwoomClient(k["appkey"], k["secretkey"], k.get("mock", False), read_only=True)
                    self._ckey = key
                return self._cl
        return None

    def _kr_minutes(self, cl, code):
        """가장 최근 거래일의 1분봉(오래된 순). 장 전·휴일에도 직전 거래일을 보여준다."""
        d = cl.call("minute", {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1"})
        rows = [r for r in (d.get("stk_min_pole_chart_qry") or []) if len(str(r.get("cntr_tm", ""))) >= 12]
        if not rows:
            return []
        day = max(str(r["cntr_tm"])[:8] for r in rows)
        bars = [{"t": f"{str(r['cntr_tm'])[8:10]}:{str(r['cntr_tm'])[10:12]}", "c": num(r.get("cur_prc"))}
                for r in rows if str(r["cntr_tm"])[:8] == day]
        bars.sort(key=lambda b: b["t"])
        return bars

    def watch(self):
        return self.rt.settings.get("watch")

    def set_watch(self, mkt, sym, on):
        w = self.watch()
        lst = [x for x in w[mkt] if x != sym]
        if on:
            lst.append(sym)
        self.rt.settings.update({"watch": {mkt: lst[:40]}})
        return self.watch()

    # ---------- 미국(공개 시세) ----------
    def _yh_chart(self, sym, rng="1d", interval="5m"):
        r = self.http.get(f"{YH}/v8/finance/chart/{requests.utils.quote(sym)}",
                          params={"range": rng, "interval": interval, "includePrePost": "false"}, timeout=8)
        if r.status_code != 200:
            raise MarketError(f"미국 시세 응답 오류({r.status_code})")
        res = ((r.json().get("chart") or {}).get("result") or [None])[0]
        if not res:
            raise MarketError(f"{sym} 시세를 찾지 못했어요")
        return res

    def yh_quote(self, sym):
        def load():
            res = self._yh_chart(sym, "1d", "5m")
            m = res.get("meta") or {}
            closes = (((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
            pts = [c for c in closes if c is not None]
            price = m.get("regularMarketPrice") or (pts[-1] if pts else None)
            prev = m.get("chartPreviousClose") or m.get("previousClose")
            if price is None:
                raise MarketError(f"{sym} 현재가가 없어요")
            chg = price - prev if prev else 0
            return {"sym": sym, "name": m.get("shortName") or m.get("longName") or sym, "price": price,
                    "change": chg, "change_pct": (chg / prev * 100) if prev else 0, "prev": prev,
                    "open": (pts[0] if pts else None), "high": m.get("regularMarketDayHigh"),
                    "low": m.get("regularMarketDayLow"), "volume": m.get("regularMarketVolume"),
                    "high52": m.get("fiftyTwoWeekHigh"), "low52": m.get("fiftyTwoWeekLow"),
                    "currency": m.get("currency") or "USD", "exchange": m.get("exchangeName") or "",
                    "spark": pts[-78:]}
        return self.cache.get(("yq", sym), 10, load)

    def yh_chart(self, sym, rng):
        r, iv = RANGES.get(rng, RANGES["1d"])

        def load():
            res = self._yh_chart(sym, r, iv)
            ts = res.get("timestamp") or []
            closes = (((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
            pts = [{"t": datetime.fromtimestamp(t).strftime("%m-%d %H:%M" if rng == "1d" else "%Y-%m-%d"), "c": c}
                   for t, c in zip(ts, closes) if c is not None]
            m = res.get("meta") or {}
            return {"points": pts, "prev": m.get("chartPreviousClose") or m.get("previousClose")}
        return self.cache.get(("yc", sym, rng), 30 if rng == "1d" else 600, load)

    def _yh_search_raw(self, q):
        r = self.http.get(f"{YH}/v1/finance/search", params={"q": q, "quotesCount": 15, "newsCount": 0}, timeout=8)
        if r.status_code != 200:
            raise MarketError(f"종목 검색 오류({r.status_code})")
        return r.json().get("quotes") or []

    def us_search(self, q):
        out = []
        for x in self._yh_search_raw(q):
            if x.get("quoteType") not in ("EQUITY", "ETF"):
                continue
            if "." in (x.get("symbol") or "") and not x.get("exchange", "").startswith(("NMS", "NYQ", "NGM", "NCM", "PCX", "ASE", "BTS")):
                continue
            out.append({"sym": x["symbol"], "name": x.get("shortname") or x.get("longname") or x["symbol"],
                        "exchange": x.get("exchDisp") or x.get("exchange") or ""})
        return out[:10]

    # ---------- 국내(키움) ----------
    def kr_quote(self, code):
        def load():
            cl = self.kr_client()
            if cl is None:  # 키움 키가 없으면 공개 시세(지연)로
                q = self._kr_yahoo(code)
                q["delayed"] = True
                return q
            d = cl.call("quote", {"stk_cd": code})
            price = num(d.get("cur_prc"))
            return {"sym": code, "name": d.get("stk_nm") or self.kr_name(code), "price": price,
                    "change": snum(d.get("pred_pre")), "change_pct": snum(d.get("flu_rt")),
                    "prev": num(d.get("base_pric")) or (price - snum(d.get("pred_pre"))),
                    "open": num(d.get("open_pric")), "high": num(d.get("high_pric")), "low": num(d.get("low_pric")),
                    "volume": int(num(d.get("trde_qty"))), "high52": num(d.get("250hgst")),
                    "low52": num(d.get("250lwst")), "currency": "KRW", "exchange": self.kr_market(code)}
        return self.cache.get(("kq", code), 4, load)

    def _kr_yahoo(self, code):
        last = None
        for suf in ((".KQ", ".KS") if self.kr_market(code) == "코스닥" else (".KS", ".KQ")):
            try:
                q = dict(self.yh_quote(code + suf))
                q.update(sym=code, name=self.kr_name(code) or q["name"], currency="KRW")
                return q
            except Exception as e:
                last = e
        raise MarketError(f"{code} 시세를 찾지 못했어요: {last}")

    def kr_spark(self, code):
        def load():
            cl = self.kr_client()
            if cl is None:
                return []
            return [b["c"] for b in self._kr_minutes(cl, code)][::5][-78:]
        try:
            return self.cache.get(("ks", code), 60, load)
        except Exception:
            return []

    def kr_chart(self, code, rng):
        def load():
            cl = self.kr_client()
            if cl is None:
                suf = ".KQ" if self.kr_market(code) == "코스닥" else ".KS"
                return self.yh_chart(code + suf, rng)
            if rng == "1d":
                q = self.kr_quote(code)
                return {"points": self._kr_minutes(cl, code), "prev": q.get("prev")}
            d = cl.call("daily", {"stk_cd": code, "base_dt": datetime.now().strftime("%Y%m%d"), "upd_stkpc_tp": "1"})
            rows = d.get("stk_dt_pole_chart_qry") or []
            pts = [{"t": f"{r['dt'][:4]}-{r['dt'][4:6]}-{r['dt'][6:8]}", "c": num(r.get("cur_prc"))}
                   for r in rows if r.get("dt")]
            pts.sort(key=lambda x: x["t"])
            days = {"1m": 31, "3m": 92, "1y": 366}[rng]
            cut = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            return {"points": [p for p in pts if p["t"] >= cut], "prev": None}
        return self.cache.get(("kc", code, rng), 20 if rng == "1d" else 600, load)

    def kr_indices(self):
        def load():
            cl = self.kr_client()
            out = []
            for name, mk, cd, ysym in KR_INDICES:
                try:
                    if cl is None:
                        raise MarketError("키움 연결 없음")
                    d = cl.call("index", {"mrkt_tp": mk, "inds_cd": cd})
                    out.append({"name": name, "price": num(d.get("cur_prc")), "change": snum(d.get("pred_pre")),
                                "change_pct": snum(d.get("flu_rt"))})
                except Exception:
                    try:
                        q = self.yh_quote(ysym)
                        out.append({"name": name, "price": q["price"], "change": q["change"],
                                    "change_pct": q["change_pct"], "delayed": True})
                    except Exception:
                        out.append({"name": name, "price": None, "change": 0, "change_pct": 0})
            return out
        return self.cache.get(("ki",), 10, load)

    def us_indices(self):
        out = []
        for name, sym in US_INDICES:
            try:
                q = self.yh_quote(sym)
                out.append({"name": name, "price": q["price"], "change": q["change"], "change_pct": q["change_pct"]})
            except Exception:
                out.append({"name": name, "price": None, "change": 0, "change_pct": 0})
        return out

    # ---------- 국내 종목 목록(검색용, 하루 한 번 받아 저장) ----------
    def _master_path(self):
        return os.path.join(self.data_dir, "kr_master.json")

    def kr_master(self, wait=False):
        """종목 목록. 처음 받을 땐 시간이 걸려 뒤에서 받고, 그동안은 빈 목록(검색은 wait=True로 기다림)."""
        if self._master is not None:
            return self._master
        with self._mlock:
            if self._mthread is None or not self._mthread.is_alive():
                self._mthread = threading.Thread(target=self._load_master, daemon=True)
                self._mthread.start()
            t = self._mthread
        if wait:
            t.join(60)
        return self._master or {}

    def _load_master(self):
        try:
            self._master = self._fetch_master()
        except Exception:
            self._master = None

    def _fetch_master(self):
        p = self._master_path()
        stale = None
        try:
            with open(p, encoding="utf-8") as f:
                stale = json.load(f)
            if time.time() - os.stat(p).st_mtime < 86400:
                return stale
        except (OSError, ValueError):
            pass
        cl = self.kr_client()
        if cl is None:
            return stale
        master = {}
        for mk, label in (("0", "코스피"), ("10", "코스닥"), ("8", "ETF")):
            key = None
            for _ in range(30):
                try:
                    d = cl.call("stocklist", {"mrkt_tp": mk}, cont_key=key)
                except Exception:
                    if not master:
                        return stale
                    break
                for x in d.get("list") or []:
                    c = code6(x.get("code", ""))
                    if c and c not in master:
                        master[c] = {"name": x.get("name", c), "market": label}
                if cl.last_headers.get("cont-yn") != "Y" or not cl.last_headers.get("next-key"):
                    break
                key = cl.last_headers["next-key"]
        if master:
            try:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(master, f, ensure_ascii=False)
            except OSError:
                pass
        return master or stale

    def kr_name(self, code):
        try:
            return (self.kr_master().get(code) or {}).get("name", "")
        except Exception:
            return ""

    def kr_market(self, code):
        try:
            return (self.kr_master().get(code) or {}).get("market", "")
        except Exception:
            return ""

    def kr_search(self, q):
        q = q.strip()
        m = self.kr_master(wait=True)
        if not m:  # 키움 연결 전: 공개 검색으로 국내 종목 찾기
            out = []
            for x in self._yh_search_raw(q):
                sym = x.get("symbol") or ""
                if sym.endswith((".KS", ".KQ")) and sym[:6].isdigit():
                    out.append({"sym": sym[:6], "name": x.get("shortname") or x.get("longname") or sym[:6],
                                "exchange": "코스닥" if sym.endswith(".KQ") else "코스피"})
            return out[:10]
        if q.isdigit() and len(q) == 6:
            return [{"sym": q, "name": (m.get(q) or {}).get("name") or q, "exchange": (m.get(q) or {}).get("market", "")}]
        ql = q.lower()
        hits = [(c, v) for c, v in m.items() if ql in v["name"].lower() or c.startswith(q)]
        hits.sort(key=lambda cv: (not cv[1]["name"].lower().startswith(ql), len(cv[1]["name"])))
        return [{"sym": c, "name": v["name"], "exchange": v["market"]} for c, v in hits[:15]]

    # ---------- 화면용 ----------
    def overview(self, mkt):
        syms = self.watch()[mkt]
        items, errors = [], []
        for s in syms:
            try:
                q = dict(self.kr_quote(s) if mkt == "kr" else self.yh_quote(s))
                if mkt == "kr" and "spark" not in q:
                    q["spark"] = self.kr_spark(s)
                items.append(q)
            except Exception as e:
                items.append({"sym": s, "name": self.kr_name(s) if mkt == "kr" else s, "price": None, "error": str(e)})
                errors.append(f"{s}: {e}")
        fx = None
        if mkt == "us":
            try:
                fx = self.yh_quote("USDKRW=X")["price"]
            except Exception:
                pass
        return {"mkt": mkt, "indices": self.kr_indices() if mkt == "kr" else self.us_indices(), "items": items,
                "fx": fx, "kiwoom": self.kr_client() is not None, "errors": errors[:5],
                "updated_at": datetime.now().isoformat(timespec="seconds")}

    def holding(self, code):
        if not self.rt.account.configured():
            return None
        snap = self.rt.account.snapshot()
        for h in snap.get("holdings") or []:
            if h["code"] == code:
                return h
        return None

    def diag(self):
        out = {"kiwoom_client": self.kr_client() is not None}
        for name, fn in (("국내 현재가(005930)", lambda: self.kr_quote("005930")),
                         ("국내 지수", self.kr_indices),
                         ("국내 일봉", lambda: len(self.kr_chart("005930", "1m")["points"])),
                         ("종목 목록 수", lambda: len(self.kr_master())),
                         ("미국 현재가(AAPL)", lambda: self.yh_quote("AAPL")),
                         ("미국 검색(apple)", lambda: self.us_search("apple")[:3])):
            try:
                out[name] = fn()
            except Exception as e:
                out[name] = f"오류: {e}"
        return out
