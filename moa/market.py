"""시세: 국내(키움 REST, 실시간) + 미국(공개 시세, Yahoo Finance 차트 데이터).

- 국내 현재가 ka10001, 일봉 ka10081, 분봉 ka10080, 지수 ka20001, 종목 목록 ka10099
- 미국 현재가·차트·검색은 키움 REST 해외 TR의 요청 형식이 공개돼 있지 않아 공개 시세를 쓴다
- 키움 호출 한도를 지키려고 짧게(몇 초) 저장해 두고 그 안의 요청에는 저장본을 준다
"""
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
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
    """짧은 저장 + 오래된 값 먼저 주기(stale-while-revalidate).

    - 신선하면 바로 준다
    - 조금 지났으면(stale 안) 저장본을 바로 주고 뒤에서 새로 받는다(화면이 기다리지 않음)
    - 아예 없거나 너무 오래되면 그때만 기다려 받는다. 같은 키를 동시에 받지 않는다(single-flight)
    """
    def __init__(self):
        self.d, self.lock, self.busy = {}, threading.Lock(), {}

    def peek(self, key):
        hit = self.d.get(key)
        return hit[1] if hit else None

    def fresh(self, key, ttl):
        hit = self.d.get(key)
        return bool(hit) and time.time() - hit[0] < ttl

    def put(self, key, val):
        with self.lock:
            self.d[key] = (time.time(), val)
            if len(self.d) > 1500:
                for k in list(self.d)[:300]:
                    self.d.pop(k, None)

    def _load(self, key, fn):
        with self.lock:
            ev = self.busy.get(key)
            mine = ev is None
            if mine:
                ev = self.busy[key] = threading.Event()
        if not mine:  # 다른 요청이 받는 중이면 그 결과를 기다린다
            ev.wait(15)
            hit = self.d.get(key)
            if hit:
                return hit[1]
            raise MarketError("시세를 받는 중이에요. 잠시 뒤 다시 시도해 주세요")
        try:
            val = fn()
            self.put(key, val)
            return val
        finally:
            with self.lock:
                self.busy.pop(key, None)
            ev.set()

    def get(self, key, ttl, fn, stale=None):
        stale = ttl * 15 if stale is None else stale
        hit = self.d.get(key)
        age = time.time() - hit[0] if hit else None
        if hit and age < ttl:
            return hit[1]
        if hit and age < stale:
            if key not in self.busy:
                threading.Thread(target=self._bg, args=(key, fn), daemon=True).start()
            return hit[1]
        return self._load(key, fn)

    def _bg(self, key, fn):
        try:
            self._load(key, fn)
        except Exception:
            pass


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
    def _yh_chart(self, sym, rng="1d", interval="5m", prepost=False, events=None):
        params = {"range": rng, "interval": interval, "includePrePost": "true" if prepost else "false"}
        if events:  # "div": 배당 내역을 함께 받는다
            params["events"] = events
        r = self.http.get(f"{YH}/v8/finance/chart/{requests.utils.quote(sym)}", params=params, timeout=8)
        if r.status_code != 200:
            raise MarketError(f"미국 시세 응답 오류({r.status_code})")
        res = ((r.json().get("chart") or {}).get("result") or [None])[0]
        if not res:
            raise MarketError(f"{sym} 시세를 찾지 못했어요")
        return res

    def yh_quote(self, sym):
        def load():
            res = self._yh_chart(sym, "1d", "5m", prepost=True)
            m = res.get("meta") or {}
            closes = (((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
            tss = res.get("timestamp") or []
            pts = [c for c in closes if c is not None]
            price = m.get("regularMarketPrice") or (pts[-1] if pts else None)
            prev = m.get("chartPreviousClose") or m.get("previousClose")
            if price is None:
                raise MarketError(f"{sym} 현재가가 없어요")
            # 프리마켓·애프터마켓: 정규장 밖 시간의 마지막 체결가를 현재가로
            session, reg = None, price
            try:
                tp = m.get("currentTradingPeriod") or {}
                now = time.time()
                for name in ("pre", "post"):
                    a, b = tp[name]["start"], tp[name]["end"]
                    if a <= now < b + 900:
                        ext = [c for t, c in zip(tss, closes) if c is not None and a <= t < b]
                        if ext:
                            session, price = name, ext[-1]
                            if name == "pre":
                                prev = reg  # 프리마켓 등락은 직전 정규장 종가 기준
            except Exception:
                pass
            chg = price - prev if prev else 0
            return {"sym": sym, "session": session, "reg_price": reg, "name": m.get("shortName") or m.get("longName") or sym, "price": price,
                    "change": chg, "change_pct": (chg / prev * 100) if prev else 0, "prev": prev,
                    "open": (pts[0] if pts else None), "high": m.get("regularMarketDayHigh"),
                    "low": m.get("regularMarketDayLow"), "volume": m.get("regularMarketVolume"),
                    "high52": m.get("fiftyTwoWeekHigh"), "low52": m.get("fiftyTwoWeekLow"),
                    "currency": m.get("currency") or "USD", "exchange": m.get("exchangeName") or "",
                    "spark": pts[-78:]}
        return self.cache.get(("yq", sym), 8, load, stale=600)

    def us_live(self, sym):
        """종목 상세용: 키움(실시간) → 안 되면 공개 시세(지연)."""
        cl = self.kr_client()
        if cl is not None:
            try:
                return self.cache.get(("uq", sym), 2, lambda: cl.us_quote(sym), stale=60)
            except Exception:
                pass
        return self.yh_quote(sym)

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
        return self.cache.get(("kq", code), 3, load, stale=600)

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

    def kr_board(self, codes):
        """관심종목 시세를 한 번에(ka10095 관심종목정보). 안 되면 종목별(ka10001)로."""
        if not codes:
            return {}
        cl = self.kr_client()
        key = ("kb", tuple(codes))

        def load():
            out = {}
            if cl is not None:
                try:
                    d = cl.call("watchinfo", {"stk_cd": "|".join(codes)})
                    for r in d.get("atn_stk_infr") or []:
                        c = code6(r.get("stk_cd", ""))
                        price = num(r.get("cur_prc"))
                        if not c or not price:
                            continue
                        out[c] = {"sym": c, "name": r.get("stk_nm") or self.kr_name(c), "price": price,
                                  "change": snum(r.get("pred_pre")), "change_pct": snum(r.get("flu_rt")),
                                  "volume": int(num(r.get("trde_qty"))), "currency": "KRW",
                                  "exchange": self.kr_market(c)}
                except Exception:
                    out = {}
            for c in codes:  # 한 번에 못 받은 종목만 하나씩
                if c not in out:
                    try:
                        out[c] = self.kr_quote(c)
                    except Exception as e:
                        out[c] = {"sym": c, "name": self.kr_name(c), "price": None, "error": str(e)}
            return out
        return self.cache.get(key, 3, load, stale=600)

    def kr_spark(self, code):
        def load():
            cl = self.kr_client()
            if cl is None:
                return []
            return [b["c"] for b in self._kr_minutes(cl, code)][::5][-78:]
        k = ("ks", code)
        if self.cache.peek(k) is None:
            if k not in self.cache.busy:
                threading.Thread(target=self.cache._bg, args=(k, load), daemon=True).start()
            return []
        try:
            return self.cache.get(k, 90, load, stale=3600)
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
        return self.cache.get(("ki",), 5, load, stale=600)

    def us_indices(self):
        with ThreadPoolExecutor(max_workers=4) as ex:
            res = list(ex.map(self._safe_yq, [s for _, s in US_INDICES]))
        return [{"name": n, "price": q.get("price"), "change": q.get("change", 0), "change_pct": q.get("change_pct", 0)}
                for (n, _), q in zip(US_INDICES, res)]

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
            if time.time() - os.stat(p).st_mtime < 86400 and "warn" in (next(iter(stale.values()), None) or {}):   # 경고 정보가 없는 예전 목록은 다시 받는다
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
                    if c and c not in master:      # 경고·감리·상태·업종: 자동매매가 위험 종목을 빼고 쏠림을 볼 때 쓴다
                        master[c] = {"name": x.get("name", c), "market": label, "warn": str(x.get("orderWarning") or "").strip(),
                                     "audit": str(x.get("auditInfo") or "").strip(), "state": str(x.get("state") or "").strip(), "sector": str(x.get("upName") or "").strip()}
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

    def kr_info(self, code):
        """종목 목록에 적힌 정보(이름·시장·경고·감리·상태·업종). 모르면 빈 값."""
        try:
            v = self.kr_master().get(code)
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}

    def kr_fund(self, code):
        """재무 지표(키움 주식기본정보): PER·PBR·ROE·시가총액(억원). 못 받으면 None. 종목마다 12시간에 한 번."""
        cl = self.kr_client()
        if cl is None:
            return None

        def f(v):
            try:
                return float(str(v or "").replace(",", "").replace("+", "").strip() or "x")
            except ValueError:
                return None

        def load():
            d = cl.call("quote", {"stk_cd": code})
            out = {"PER": f(d.get("per")), "PBR": f(d.get("pbr")), "ROE": f(d.get("roe")), "시총(억)": f(d.get("mac"))}
            return out if any(v is not None for v in out.values()) else None
        try:
            return self.cache.get(("fund", code), 12 * 3600, load, stale=72 * 3600)
        except Exception:
            return None

    def kr_flow(self, code):
        """수급: 외국인·기관의 최근 5·20거래일 순매수(억원)와 연속 순매수 일수(순매도면 음수). 못 받으면 None.
        키움 ka10059(금액 기준, 백만원)를 종목마다 3시간에 한 번만 받는다."""
        cl = self.kr_client()
        if cl is None:
            return None

        def n(v):
            t = str(v or "").replace(",", "").replace("+", "").strip()
            neg = t.startswith("-")
            try:
                return (-1 if neg else 1) * float(t.lstrip("-") or 0)
            except ValueError:
                return 0.0

        def load():
            d = cl.call("investor", {"dt": datetime.now().strftime("%Y%m%d"), "stk_cd": code, "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1000"})
            rows = sorted((r for r in (d.get("stk_invsr_orgn") or []) if r.get("dt")), key=lambda r: str(r["dt"]), reverse=True)[:20]
            if not rows:
                return None
            f, o = [n(r.get("frgnr_invsr")) for r in rows], [n(r.get("orgn")) for r in rows]

            def run(xs):
                k = 0
                for v in xs:
                    if v == 0 or (k and (v > 0) != (k > 0)):
                        break
                    k += 1 if v > 0 else -1
                return k
            eok = lambda xs: round(sum(xs) / 100, 1)          # 백만원 → 억원
            return {"외국인5일": eok(f[:5]), "기관5일": eok(o[:5]), "외국인20일": eok(f), "기관20일": eok(o),
                    "외국인연속일": run(f), "기관연속일": run(o), "기준일": str(rows[0]["dt"]), "일수": len(rows)}
        try:
            return self.cache.get(("flow", code), 3 * 3600, load, stale=24 * 3600)
        except Exception:
            return None

    def junk(self, mkt, sym, name=""):
        """자동매매에서 뺄 종목이면 그 이유, 아니면 빈 글."""
        from .trading.universe import junk_reason
        return junk_reason(mkt, sym, name, self.kr_info(str(sym)) if mkt == "kr" else None)

    def kr_market(self, code):
        try:
            return (self.kr_master().get(code) or {}).get("market", "")
        except Exception:
            return ""

    def kr_local(self):
        """종목 목록(키움)을 못 받았을 때도 이름으로 찾을 수 있게, 앱이 이미 아는 국내 종목 이름을 모은다."""
        out = {}
        try:
            from .broker.demo import NAMES
            out.update({c: v[0] for c, v in NAMES.items()})
        except Exception:
            pass
        try:
            from .dividend import UNIVERSE
            out.update({u[1]: u[2] for u in UNIVERSE if u[0] == "kr"})
            out.update({c["sym"]: c["name"] for c in self.rt.dividend.custom() if c["mkt"] == "kr" and c.get("name")})
        except Exception:
            pass
        try:
            out.update({p["code"]: p["name"] for p in self.rt.dca.plans() if p.get("name") and p["name"] != p["code"]})
        except Exception:
            pass
        return out

    def kr_label(self, code):
        """화면에 보여 줄 종목 이름(없으면 빈 글자)."""
        return self.kr_name(code) or self.kr_local().get(code, "")

    def kr_search(self, q):
        q = q.strip()
        m = self.kr_master(wait=True)
        if not m:  # 키움 연결 전: 앱이 아는 이름 + 공개 검색으로 국내 종목 찾기
            ql, out, seen = q.lower(), [], set()
            for c, n in self.kr_local().items():
                if ql in n.lower() or c.startswith(q):
                    out.append({"sym": c, "name": n, "exchange": ""})
                    seen.add(c)
            out.sort(key=lambda x: (not x["name"].lower().startswith(ql), len(x["name"])))
            try:
                raw = self._yh_search_raw(q)
            except Exception:
                if out:
                    return out[:15]
                raise
            for x in raw:
                sym = x.get("symbol") or ""
                if sym.endswith((".KS", ".KQ")) and sym[:6].isdigit() and sym[:6] not in seen:
                    seen.add(sym[:6])
                    out.append({"sym": sym[:6], "name": x.get("shortname") or x.get("longname") or sym[:6],
                                "exchange": "코스닥" if sym.endswith(".KQ") else "코스피"})
            return out[:15]
        if q.isdigit() and len(q) == 6:
            return [{"sym": q, "name": (m.get(q) or {}).get("name") or q, "exchange": (m.get(q) or {}).get("market", "")}]
        ql = q.lower()
        hits = [(c, v) for c, v in m.items() if ql in v["name"].lower() or c.startswith(q)]
        hits.sort(key=lambda cv: (not cv[1]["name"].lower().startswith(ql), len(cv[1]["name"])))
        return [{"sym": c, "name": v["name"], "exchange": v["market"]} for c, v in hits[:15]]

    def picks(self, mkt, syms=()):
        """종목 고르기 칸의 바로 고르기 목록(관심종목 + 적립 중인 종목)과, 코드만 아는 종목들의 이름."""
        us = mkt == "us"
        label = (lambda c: c) if us else (lambda c: self.kr_label(c) or c)
        out, seen = [], set()

        def add(sym, tag):
            if sym and sym not in seen:
                seen.add(sym)
                out.append({"sym": sym, "name": label(sym), "tag": tag})
        for sym in (self.watch().get(mkt) or []):
            add(sym, "관심")
        if not us:
            try:
                for p in self.rt.dca.plans():
                    add(p["code"], "적립 중")
            except Exception:
                pass
        return {"picks": out[:12], "names": {c: label(c) for c in list(syms)[:40]}}

    # ---------- 화면용 ----------
    def overview(self, mkt):
        syms = list(self.watch()[mkt])
        errors, fx = [], None
        if mkt == "kr":
            board = self.kr_board(syms)
            items = []
            for c in syms:
                q = dict(board.get(c) or {"sym": c, "name": self.kr_name(c), "price": None})
                q["spark"] = self.kr_spark(c)
                items.append(q)
            indices = self.kr_indices()
        else:  # 미국: 여러 종목을 동시에 받는다
            want = syms + [s for _, s in US_INDICES]
            with ThreadPoolExecutor(max_workers=8) as ex:
                res = dict(zip(want, ex.map(self._safe_yq, want)))
            items = [dict(res[s]) if "error" not in res[s] else dict(res[s], name=s) for s in syms]
            cl = self.kr_client()
            if cl is not None:  # 키움이 연결돼 있으면 실시간(프리·애프터·주간거래 포함) 값으로 덮어쓴다
                for q in items:
                    try:
                        k = self.cache.get(("uq", q["sym"]), 3, lambda sy=q["sym"], h=q.get("exchange"): cl.us_quote(sy, hint=h), stale=120)
                        for f in ("price", "change", "change_pct", "prev", "high", "low", "volume", "exch"):
                            if k.get(f) is not None:
                                q[f] = k[f]
                        q["live"] = True
                        q.pop("error", None)
                    except Exception:
                        pass
            indices = [{"name": n, "price": res[s].get("price"), "change": res[s].get("change", 0),
                        "change_pct": res[s].get("change_pct", 0)} for n, s in US_INDICES]
            fx = res["USDKRW=X"].get("price")
        errors = [f"{q['sym']}: {q['error']}" for q in items if q.get("error")]
        return {"mkt": mkt, "indices": indices, "items": items, "fx": fx, "kiwoom": self.kr_client() is not None,
                "errors": errors[:5], "updated_at": datetime.now().isoformat(timespec="seconds")}

    def _safe_yq(self, sym):
        try:
            return self.yh_quote(sym)
        except Exception as e:
            return {"sym": sym, "price": None, "error": str(e)}

    def warm(self):
        """서버를 켜자마자 관심종목·지수를 미리 받아 두어 첫 화면이 기다리지 않게."""
        def run():
            for m in ("kr", "us"):
                try:
                    self.overview(m)
                except Exception:
                    pass
            try:
                self.kr_master()
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    def holding(self, code):
        if not self.rt.account.configured():
            return None
        snap = self.rt.account.snapshot()
        for h in snap.get("holdings") or []:
            if h["code"] == code:
                return h
        return None

    def us_holding(self, sym):
        cl = self.kr_client()
        if cl is None:
            return None
        try:
            return next((h for h in cl.us_holdings() if h["code"].upper() == sym.upper()), None)
        except Exception:
            return None

    def _master_check(self):
        """자동매매의 '경고·특수 종목 제외'가 쓰는 정보가 실제로 들어오는지: 값의 종류와 걸러질 종목 예."""
        from .trading.universe import junk_reason
        m = self.kr_master()
        vals = lambda k: sorted({str(v.get(k) or "") for v in m.values()})[:12]
        out = [(c, v.get("name"), junk_reason("kr", c, v.get("name"), v)) for c, v in m.items()]
        hit = [x for x in out if x[2]]
        kinds = {}
        for _, _, why in hit:
            kinds[why] = kinds.get(why, 0) + 1
        return {"경고 값": vals("warn"), "감리 값": vals("audit"), "업종 있는 종목": sum(1 for v in m.values() if v.get("sector")),
                "삼성전자": m.get("005930"), "걸러지는 종목 수": len(hit), "이유별": kinds, "예": hit[:8]}

    def diag(self):
        out = {"kiwoom_client": self.kr_client() is not None}
        for name, fn in (("국내 현재가(005930)", lambda: self.kr_quote("005930")),
                         ("국내 지수", self.kr_indices),
                         ("국내 일봉", lambda: len(self.kr_chart("005930", "1m")["points"])),
                         ("종목 목록 수", lambda: len(self.kr_master())),
                         ("종목 목록 경고·업종 정보", self._master_check),
                         ("재무 지표(삼성전자)", lambda: self.kr_fund("005930") or "받지 못함"),
                         ("전자공시(DART)", lambda: self.rt.dart.status() if getattr(self.rt, "dart", None) else "없음"),
                         ("수급(외국인·기관 순매수, 삼성전자)", lambda: self.kr_flow("005930") or "받지 못함"),
                         ("넓게 훑기 · 장 시작 전 리서치", lambda: self.rt.prep.status("kr") if getattr(self.rt, "prep", None) else "없음"),
                         ("미국 현재가(AAPL)", lambda: self.yh_quote("AAPL")),
                         ("미국 현재가 키움(AAPL)", lambda: self.kr_client().us_quote("AAPL")),
                         ("미국 잔고 키움", lambda: self.kr_client().us_holdings()),
                         ("미국 예수금 키움", lambda: self.kr_client().us_cash()),
                         ("미국 검색(apple)", lambda: self.us_search("apple")[:3])):
            try:
                out[name] = fn()
            except Exception as e:
                out[name] = f"오류: {e}"
        return out
