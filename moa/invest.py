"""투자 화면 데이터: 호가·캔들·체결·랭킹·종목 정보·투자 홈·가격 알림.

국내는 키움 REST(실시간), 미국은 키움(연결 시) → 안 되면 공개 시세(Yahoo, 지연)로 받는다.
키움 호출 한도를 지키려고 market.Cache(짧은 저장 + 지난 값 먼저 주기)를 같이 쓴다.
"""
from datetime import datetime, timezone

from .broker.kiwoom import num, snum
from .market import YH, MarketError

US_EX = {"ND": "나스닥", "NY": "뉴욕", "NA": "아멕스"}


def _sgn(v):
    """키움 가격은 등락 부호가 붙어 온다. 가격은 절댓값."""
    return num(v)


class Invest:
    def __init__(self, rt):
        self.rt, self.m = rt, rt.market
        rt.db.x("""CREATE TABLE IF NOT EXISTS price_alerts(
            id INTEGER PRIMARY KEY, mkt TEXT NOT NULL, sym TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
            cond TEXT NOT NULL, price REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, fired_at TEXT, fired_price REAL)""")
        self._alert_at = 0.0

    @property
    def cache(self):
        return self.m.cache

    def kr(self):
        return self.m.kr_client()

    # ---------- 호가 ----------
    def orderbook(self, mkt, sym):
        if mkt == "kr":
            def load():
                cl = self.kr()
                if cl is None:
                    raise MarketError("호가는 키움을 연결해야 볼 수 있어요")
                d = cl.call("orderbook", {"stk_cd": sym})
                asks, bids = [], []
                for n in range(1, 11):
                    if n == 1:
                        ap, aq, bp, bq = d.get("sel_fpr_bid"), d.get("sel_fpr_req"), d.get("buy_fpr_bid"), d.get("buy_fpr_req")
                    else:
                        ap, aq = d.get(f"sel_{n}th_pre_bid"), d.get(f"sel_{n}th_pre_req")
                        bp, bq = d.get(f"buy_{n}th_pre_bid"), d.get(f"buy_{n}th_pre_req")
                    if _sgn(ap):
                        asks.append({"price": _sgn(ap), "qty": int(num(aq))})
                    if _sgn(bp):
                        bids.append({"price": _sgn(bp), "qty": int(num(bq))})
                return {"asks": asks, "bids": bids, "tot_ask": int(num(d.get("tot_sel_req"))),
                        "tot_bid": int(num(d.get("tot_buy_req"))), "time": d.get("bid_req_base_tm", "")}
            return self.cache.get(("ob", sym), 1.5, load, stale=30)

        def load_us():
            cl = self.kr()
            if cl is None:
                raise MarketError("미국 호가는 키움을 연결해야 볼 수 있어요")
            ex = cl.us_exchange(sym)[0]
            d = cl.call("us_book", {"stex_tp": ex, "stk_cd": sym})
            asks = [{"price": num(d.get(f"sel_{n}bid")), "qty": int(num(d.get(f"sel_{n}bid_req")))} for n in range(1, 11)]
            bids = [{"price": num(d.get(f"buy_{n}bid")), "qty": int(num(d.get(f"buy_{n}bid_req")))} for n in range(1, 11)]
            asks = [x for x in asks if x["price"]]
            bids = [x for x in bids if x["price"]]
            return {"asks": asks, "bids": bids, "tot_ask": sum(x["qty"] for x in asks),
                    "tot_bid": sum(x["qty"] for x in bids), "time": d.get("bid_tm", "")}
        return self.cache.get(("obu", sym), 2, load_us, stale=30)

    # ---------- 체결(틱) ----------
    def ticks(self, mkt, sym):
        if mkt != "kr":
            def load_us():
                cl = self.kr()
                if cl is None:
                    raise MarketError("체결은 키움을 연결해야 볼 수 있어요")
                d = cl.call("us_ticks", {"stex_tp": cl.us_exchange(sym)[0], "stk_cd": sym})
                rows = (d.get("result_list") or [])[:40]
                out = []
                for r in rows:
                    tm = str(r.get("cntr_tm", ""))
                    out.append({"t": f"{tm[:2]}:{tm[2:4]}:{tm[4:6]}" if len(tm) >= 6 else tm, "price": abs(snum(r.get("cur_prc"))),
                                "qty": int(snum(r.get("trde_qty"))), "change_pct": snum(r.get("flu_rt")), "strength": 0})
                buy = sum(x["qty"] for x in out if x["qty"] > 0)
                sell = -sum(x["qty"] for x in out if x["qty"] < 0)
                st = round(min(999.0, buy / sell * 100), 1) if sell else (999.0 if buy else 0)
                for x in out:  # 미국은 체결강도를 따로 주지 않아 최근 체결의 매수/매도 수량 비로 계산
                    x["strength"] = st
                return out
            return self.cache.get(("tku", sym), 2, load_us, stale=30)

        def load():
            cl = self.kr()
            if cl is None:
                raise MarketError("체결은 키움을 연결해야 볼 수 있어요")
            d = cl.call("ticks", {"stk_cd": sym})
            out = []
            for r in (d.get("cntr_infr") or [])[:40]:
                tm = str(r.get("tm", ""))
                out.append({"t": f"{tm[:2]}:{tm[2:4]}:{tm[4:6]}" if len(tm) >= 6 else tm, "price": num(r.get("cur_prc")),
                            "qty": int(snum(r.get("cntr_trde_qty"))), "change_pct": snum(r.get("pre_rt")),
                            "strength": snum(r.get("cntr_str"))})
            return out
        return self.cache.get(("tk", sym), 2, load, stale=30)

    # ---------- 캔들 ----------
    TF = {"m1": ("1분", "minute", "1"), "m5": ("5분", "minute", "5"), "d": ("일", "daily", None),
          "w": ("주", "weekly", None), "mo": ("월", "monthly", None)}

    def candles(self, mkt, sym, tf, n=120):
        """봉 자료. n: 돌려줄 봉 수(평소 120, 크게 보기에서 손으로 줄여 볼 때는 600까지)."""
        if tf not in self.TF:
            tf = "d"
        n = max(120, min(600, int(n or 120)))
        long = n > 120
        if mkt == "kr" and self.kr() is not None:
            def load():
                cl = self.kr()
                _, key, tic = self.TF[tf]
                if key == "minute":
                    d = cl.call("minute", {"stk_cd": sym, "tic_scope": tic, "upd_stkpc_tp": "1"})
                    rows = d.get("stk_min_pole_chart_qry") or []
                    out = [{"t": f"{str(r['cntr_tm'])[4:6]}/{str(r['cntr_tm'])[6:8]} {str(r['cntr_tm'])[8:10]}:{str(r['cntr_tm'])[10:12]}",
                            "o": num(r.get("open_pric")), "h": num(r.get("high_pric")), "l": num(r.get("low_pric")),
                            "c": num(r.get("cur_prc")), "v": int(num(r.get("trde_qty")))}
                           for r in rows if len(str(r.get("cntr_tm", ""))) >= 12]
                else:
                    d = cl.call(key, {"stk_cd": sym, "base_dt": datetime.now().strftime("%Y%m%d"), "upd_stkpc_tp": "1"})
                    lk = {"daily": "stk_dt_pole_chart_qry", "weekly": "stk_stk_pole_chart_qry",
                          "monthly": "stk_mth_pole_chart_qry"}[key]
                    out = [{"t": f"{r['dt'][:4]}-{r['dt'][4:6]}-{r['dt'][6:8]}", "o": num(r.get("open_pric")),
                            "h": num(r.get("high_pric")), "l": num(r.get("low_pric")), "c": num(r.get("cur_prc")),
                            "v": int(num(r.get("trde_qty")))} for r in (d.get(lk) or []) if r.get("dt")]
                out = [x for x in out if x["c"]]
                out.sort(key=lambda x: x["t"])
                return {"tf": tf, "bars": out[-600:], "source": "키움"}
            res = self.cache.get(("cd", sym, tf), 3 if self.TF[tf][1] == "minute" else 60, load, stale=600)
            return dict(res, bars=(res.get("bars") or [])[-n:])
        ysym = sym if mkt == "us" else sym + (".KQ" if self.m.kr_market(sym) == "코스닥" else ".KS")
        rng, iv = ({"m1": ("1d", "1m"), "m5": ("1mo", "5m"), "d": ("3y", "1d"), "w": ("10y", "1wk"), "mo": ("max", "1mo")} if long else
                   {"m1": ("1d", "1m"), "m5": ("5d", "5m"), "d": ("6mo", "1d"), "w": ("2y", "1wk"), "mo": ("10y", "1mo")})[tf]

        def load_y():
            res = self.m._yh_chart(ysym, rng, iv)
            q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
            minute = self.TF[tf][1] == "minute"
            off = (res.get("meta") or {}).get("gmtoffset")
            out = []
            for i, t in enumerate(res.get("timestamp") or []):
                c = (q.get("close") or [None])[i] if i < len(q.get("close") or []) else None
                if c is None:
                    continue
                g = lambda k: (q.get(k) or [None] * (i + 1))[i]
                if minute:  # 분봉: 이 PC 시각(한국 시간)으로
                    label = datetime.fromtimestamp(t).strftime("%m/%d %H:%M")
                elif off is not None:
                    # 일·주·월봉: 그 거래소의 날짜로. 진행 중인 오늘 봉은 시각이 '마지막 체결 시각'이라
                    # 한국 시간으로 바꾸면 미국 봉이 다음 날짜로 밀린다(채점에서 추천 전 움직임이 섞이는 원인)
                    label = datetime.fromtimestamp(t + off, timezone.utc).strftime("%Y-%m-%d")
                else:
                    label = datetime.fromtimestamp(t).strftime("%Y-%m-%d")
                out.append({"t": label, "o": g("open") or c, "h": g("high") or c,
                            "l": g("low") or c, "c": c, "v": int(g("volume") or 0)})
            return {"tf": tf, "bars": out[-n:], "source": "공개 시세(지연)"}
        return self.cache.get(("cdy", ysym, tf, long), 20 if self.TF[tf][1] == "minute" else 600, load_y, stale=3600)

    # ---------- 종목 정보(기본) ----------
    def info(self, mkt, sym):
        if mkt != "kr" or self.kr() is None:
            q = self.m.us_live(sym) if mkt == "us" else self.m.kr_quote(sym)
            return {"items": [["52주 최고", q.get("high52")], ["52주 최저", q.get("low52")]], "currency": q.get("currency")}

        def load():
            d = self.kr().call("quote", {"stk_cd": sym})
            mac = num(d.get("mac"))  # 억 원
            items = [["시가총액", f"{mac / 10000:,.1f}조" if mac >= 10000 else f"{mac:,.0f}억"],
                     ["PER", d.get("per") or "-"], ["PBR", d.get("pbr") or "-"], ["EPS", f"{snum(d.get('eps')):,.0f}"],
                     ["ROE", f"{d.get('roe') or '-'}%"], ["BPS", f"{snum(d.get('bps')):,.0f}"],
                     ["외국인 소진율", f"{d.get('for_exh_rt') or '-'}%"], ["유통 비율", f"{d.get('dstr_rt') or '-'}%"],
                     ["매출액", f"{snum(d.get('sale_amt')):,.0f}억"], ["영업이익", f"{snum(d.get('bus_pro')):,.0f}억"],
                     ["당기순이익", f"{snum(d.get('cup_nga')):,.0f}억"], ["결산월", f"{d.get('setl_mm') or '-'}월"],
                     ["상한가", f"{num(d.get('upl_pric')):,.0f}"], ["하한가", f"{num(d.get('lst_pric')):,.0f}"]]
            return {"items": items, "currency": "KRW"}
        return self.cache.get(("inf", sym), 60, load, stale=3600)

    # ---------- 랭킹(발견) ----------
    KR_RANK = {"value": "거래대금", "up": "급상승", "down": "급하락", "volume": "거래량"}

    def ranking(self, mkt, kind):
        if kind not in list(self.KR_RANK) + (["views", "watch", "buy"] if mkt == "us" else []):
            kind = "value"
        cl = self.kr()
        if cl is None:
            raise MarketError("순위는 키움을 연결해야 볼 수 있어요")

        def load():
            out = []
            if mkt == "kr":
                if kind == "value":
                    d = cl.call("rank_value", {"mrkt_tp": "000", "mang_stk_incls": "0", "stex_tp": "3"})
                    rows, vkey = d.get("trde_prica_upper") or [], "trde_prica"
                elif kind == "volume":
                    d = cl.call("rank_volume", {"mrkt_tp": "000", "sort_tp": "1", "mang_stk_incls": "1", "crd_tp": "0",
                                                "trde_qty_tp": "0", "pric_tp": "0", "trde_prica_tp": "0",
                                                "mrkt_open_tp": "0", "stex_tp": "3"})
                    rows, vkey = d.get("tdy_trde_qty_upper") or [], "trde_amt"
                else:
                    d = cl.call("rank_change", {"mrkt_tp": "000", "sort_tp": "1" if kind == "up" else "3",
                                                "trde_qty_cnd": "0010", "stk_cnd": "1", "crd_cnd": "0",
                                                "updown_incls": "1", "pric_cnd": "0", "trde_prica_cnd": "0", "stex_tp": "3"})
                    rows, vkey = d.get("pred_pre_flu_rt_upper") or [], None
                for i, r in enumerate(rows[:40]):
                    code = str(r.get("stk_cd", "")).split("_")[0][-6:]
                    out.append({"rank": i + 1, "sym": code, "name": r.get("stk_nm", code), "price": num(r.get("cur_prc")),
                                "change": snum(r.get("pred_pre")), "change_pct": snum(r.get("flu_rt")),
                                "value": num(r.get(vkey)) if vkey else None,
                                "volume": int(num(r.get("trde_qty") or r.get("now_trde_qty")))})
            elif kind in ("views", "watch", "buy", "value"):
                sg = lambda v, sign: -abs(snum(v)) if str(sign).strip() in ("-", "4", "5") else snum(v)
                if kind == "views":
                    d = cl.call("us_rank_views", {"svc_type": "B282"})
                elif kind == "watch":
                    d = cl.call("us_rank_watch", {"dt_unit_tp": "D", "stk_tp": "A"})
                elif kind == "buy":
                    d = cl.call("us_rank_kiwoom", {"qry_tp": "1", "dt_unit_tp": "1"})
                else:
                    d = cl.call("us_rank_value", {"stex_tp": "0", "inds_cd": "000", "stk_tp": "0", "trde_qty_tp": "0",
                                                  "stk_cnd": "0", "pric_cnd": "0", "trde_prica_cnd": "0"})
                for i, r in enumerate((d.get("result_list") or [])[:40]):
                    sym = str(r.get("stk_cd", "")).strip()
                    if not sym:
                        continue
                    ex = str(r.get("stex_tp") or "").strip()
                    if ex in ("ND", "NY", "NA"):  # 순위에 거래소가 같이 오니 기억해 둔다(주문·호가가 빨라짐)
                        cl._US_EX_CACHE.setdefault(sym, (ex, r.get("stk_nm") or sym))
                    if kind == "views":
                        mv = sg(r.get("chg_val"), r.get("sign"))
                        cp = sg(r.get("diff_rate_for_gjga"), r.get("sign_for_gjga"))
                        price = abs(snum(r.get("curr_pric")))
                    else:
                        mv = snum(r.get("rank_flu") or r.get("kw_high_rank_hl")) if kind != "value" else None
                        if kind == "buy" and str(r.get("kw_high_rank_sig")) == "2" and mv:
                            mv = -abs(mv)
                        cp, price = snum(r.get("flu_rt")), abs(snum(r.get("cur_prc")))
                    out.append({"rank": i + 1, "sym": sym, "name": r.get("stk_nm") or r.get("stk_enm") or sym, "price": price,
                                "change": snum(r.get("pred_pre")) if r.get("pred_pre") else None, "change_pct": cp,
                                "value": num(r.get("trde_prica")) * 1000 if r.get("trde_prica") else None,
                                "volume": int(num(r.get("acc_trde_qty"))) if r.get("acc_trde_qty") else 0,
                                "move": None if mv is None else int(mv),
                                "new": str(r.get("rank_flu_sig")) == "N" or str(r.get("kw_high_rank_sig")) == "0"})
            else:
                if kind in ("up", "down"):
                    d = cl.call("us_rank_price", {"stex_tp": "0", "stk_tp": "1", "inds_cd": "000", "stk_cnd": "0",
                                                  "flu_tp": "1" if kind == "up" else "2", "tm_tp": "2", "tm": "1",
                                                  "pric_cnd": "3", "trde_qty_tp": "10", "trde_prica_cnd": "0"})
                else:
                    d = cl.call("us_rank_volume", {"stex_tp": "0", "inds_cd": "", "tm": "5", "stk_tp": "1", "stk_cnd": "0",
                                                   "pric_cnd": "3", "trde_prica_cnd": "0", "trde_qty_tp": "0"})
                for i, r in enumerate((d.get("result_list") or [])[:40]):
                    out.append({"rank": i + 1, "sym": str(r.get("stk_cd", "")).strip(),
                                "name": r.get("stk_nm") or r.get("stk_enm") or r.get("stk_cd"), "price": num(r.get("cur_prc")),
                                "change": snum(r.get("pred_pre")), "change_pct": snum(r.get("flu_rt")),
                                "value": None, "volume": int(num(r.get("acc_trde_qty")))})
            return {"mkt": mkt, "kind": kind, "items": out, "at": datetime.now().strftime("%H:%M:%S")}
        return self.cache.get(("rk", mkt, kind), 10, load, stale=300)

    # ---------- 투자 홈 ----------
    def home(self):
        acc = self.rt.account
        snap = acc.snapshot() if acc.configured() else None
        holds, day_pnl = [], 0.0
        if snap and snap.get("total") is not None:
            kr_codes = [h["code"] for h in snap.get("holdings") or [] if h.get("market") != "us"]
            board = {}
            try:
                board = self.m.kr_board(kr_codes) if kr_codes else {}
            except Exception:
                pass
            for h in snap.get("holdings") or []:
                x = dict(h)
                if h.get("market") == "us":
                    try:
                        q = self.m.us_live(h["code"])
                        x["day_pct"] = q.get("change_pct")
                        day_pnl += (q.get("change") or 0) * h["qty"] * (h.get("fx") or 0)
                    except Exception:
                        x["day_pct"] = None
                else:
                    q = board.get(h["code"]) or {}
                    x["day_pct"] = q.get("change_pct")
                    if q.get("change") is not None:
                        day_pnl += q["change"] * h["qty"]
                holds.append(x)
        try:
            idx = self.m.kr_indices()
        except Exception:
            idx = []
        try:
            idx += [i for i in self.m.us_indices() if i["name"] != "다우"]
        except Exception:
            pass
        base = (snap or {}).get("total") or 0
        return {"account": snap, "holdings": holds, "day_pnl": round(day_pnl),
                "day_pct": round(day_pnl / (base - day_pnl) * 100, 2) if base and base - day_pnl else 0,
                "indices": idx, "kiwoom": self.kr() is not None,
                "alerts": self.alerts(active_only=True)}

    # ---------- 가격 알림 ----------
    def alerts(self, active_only=False):
        q = "SELECT * FROM price_alerts" + (" WHERE active=1" if active_only else "") + " ORDER BY active DESC, id DESC LIMIT 50"
        return self.rt.db.q(q)

    def add_alert(self, d):
        mkt = "us" if d.get("mkt") == "us" else "kr"
        sym = str(d.get("sym") or "").strip().upper()
        try:
            price = float(str(d.get("price")).replace(",", ""))
        except (TypeError, ValueError):
            raise MarketError("알림 가격을 숫자로 넣어 주세요")
        if not sym or price <= 0:
            raise MarketError("종목과 가격을 확인해 주세요")
        cur = (self.m.kr_quote(sym) if mkt == "kr" else self.m.us_live(sym)).get("price")
        cond = d.get("cond") if d.get("cond") in ("above", "below") else ("above" if cur is None or price >= cur else "below")
        return self.rt.db.x("INSERT INTO price_alerts(mkt,sym,name,cond,price,created_at) VALUES(?,?,?,?,?,?)",
                            (mkt, sym, (d.get("name") or sym)[:40], cond, price, datetime.now().isoformat(timespec="seconds")))

    def del_alert(self, aid):
        self.rt.db.x("DELETE FROM price_alerts WHERE id=?", (aid,))

    def check_alerts(self, force=False):
        """활성 알림의 현재가를 보고 조건이 맞으면 휴대폰 알림(ntfy) 후 끈다. 30초마다."""
        import time
        if not force and time.time() - self._alert_at < 30:
            return []
        self._alert_at = time.time()
        fired = []
        for a in self.rt.db.q("SELECT * FROM price_alerts WHERE active=1"):
            try:
                q = self.m.kr_quote(a["sym"]) if a["mkt"] == "kr" else self.m.us_live(a["sym"])
            except Exception:
                continue
            p = q.get("price")
            if p is None:
                continue
            hit = p >= a["price"] if a["cond"] == "above" else p <= a["price"]
            if not hit:
                continue
            self.rt.db.x("UPDATE price_alerts SET active=0, fired_at=?, fired_price=? WHERE id=?",
                         (datetime.now().isoformat(timespec="seconds"), p, a["id"]))
            unit = "원" if a["mkt"] == "kr" else "달러"
            pf = f"{p:,.0f}" if a["mkt"] == "kr" else f"{p:,.2f}"
            tf = f"{a['price']:,.0f}" if a["mkt"] == "kr" else f"{a['price']:,.2f}"
            self.rt.notify(f"{a['name']} {pf}{unit} — 목표 {tf}{unit} {'이상' if a['cond'] == 'above' else '이하'} 도달", "real", "목표가 알림")
            fired.append(a["id"])
        return fired
