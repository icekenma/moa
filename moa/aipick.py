"""AI 종목 추천 + 자동 익절·손절 감시.

흐름
1. 사용자가 시장(국내/미국)과 엔진(모델)을 고르고 '추천 받기'를 누른다.
2. 앱이 그 시점의 실시간 순위·분봉 흐름·호가 잔량·지수·주문 가능 금액을 모아 Claude에게 보낸다.
3. Claude가 '오늘 안에 수익 낼 확률이 높은' 종목을 최대 3개, 목표가·손절가·정리 시각과 함께 고른다.
4. 매수는 사용자가 직접 누른다(AI는 주문하지 않음).
5. 매수할 때 '자동 익절·손절'을 켜 두면, 앱이 정해진 값(사용자가 확인한 값)에 닿을 때 시장가로 판다.
   - 정규장에서만 판다(국내 09:00~15:20, 미국 09:30~16:00 뉴욕 시간).
   - 실제 주문 여부는 기존 계좌 설정(실전 허용 등)을 그대로 따른다.
"""
import json
import re
from datetime import datetime, timedelta, timezone

PICK_SYSTEM = """너는 한국 개인 투자자의 앱(moa)에 들어 있는 당일 매매(데이트레이딩) 종목 선정 분석가야.
목표: 지금 이 시점에 매수해서 '오늘 장 마감 전까지' 수수료·세금을 빼고도 수익으로 끝날 확률과 기대수익이 가장 높은 종목을 고르는 것.

판단 원칙
- 주어진 실시간 데이터(순위, 오늘 분봉 흐름, VWAP 대비 위치, 고점 대비 위치, 최근 30분 추세·거래량, 호가 잔량 비율, 지수, 남은 장 시간)를 근거로 써. 없는 숫자를 지어내지 마.
- 웹 검색이 가능하면 후보의 오늘 뉴스·공시(재료)를 짧게 확인해. 재료가 없거나 이미 소멸한 급등은 감점.
- 피할 것: 상한가 근처(국내 +25% 이상), 거래대금이 작아 체결이 어려운 종목, 이미 고점에서 크게 밀리는 종목(추세 붕괴), 장 마감까지 시간이 너무 적어 목표에 닿기 어려운 경우, 관리·정리매매 종목, 레버리지/인버스 ETF는 정말 근거가 강할 때만.
- 좋은 후보: VWAP 위에서 눌림 후 재상승, 거래량이 붙는 돌파, 강한 재료 + 지수 우호, 호가 매수 우위.
- 비용: 국내 왕복 약 0.2%(세금 포함), 미국 왕복 약 0.25%+환전. 목표 수익률은 비용보다 충분히 커야 해.
- 손익비: 목표(target_pct)는 보통 손절(stop_pct)의 1.5배 이상. 변동성이 큰 종목은 둘 다 넓게, 작은 종목은 좁게.
- 승률(win_prob)은 '정리 시각 전에 손절보다 목표에 먼저 닿을 확률'의 솔직한 추정치(%)야. 과장하지 마. 근거가 약하면 낮게.
- 시장 전체가 나쁘거나 장이 거의 끝나 가면 stance를 '관망'으로 두되, picks는 절대 비우지 마. 그래도 가장 나은 1종목을 반드시 넣고 weight_pct는 5~10으로 작게, 목표·손절은 보수적으로 잡아.

출력 형식(반드시 지켜)
1) 먼저 한국어로 5~8줄 이내 짧은 판단: 첫 줄에 결론, 이어서 '- ' 항목으로 시장 분위기와 고른 이유.
2) 그 다음 마지막에 아래 JSON 하나를 ```json 코드 블록으로. 다른 코드 블록은 쓰지 마.
{"stance":"공격|보통|관망","market_view":"한 줄","picks":[
 {"sym":"종목코드 또는 기호","name":"이름","score":0~100,"win_prob":0~100,
  "entry_type":"limit|market","entry_price":숫자(지정가 추천가, 현재가 근처),
  "target_pct":숫자,"stop_pct":숫자(양수),"trail_pct":숫자 또는 0,
  "exit_by":"HH:MM(한국 시간, 이때까지 안 닿으면 정리)","weight_pct":주문가능금액 중 권장 비중(5~40),
  "reasons":["근거1","근거2","근거3"],"risks":["위험1","위험2"]}]}
picks는 최소 1개·최대 3개, 기대수익(승률×목표 − (1−승률)×손절)이 큰 순서로."""

KR_FEE = 0.2  # 왕복 비용(%) 대략


def kst_now():
    return datetime.now(timezone.utc) + timedelta(hours=9)


def _us_dst(utc):
    """미국 동부 서머타임 여부(3월 둘째 일요일 2시 ~ 11월 첫째 일요일 2시)."""
    y = utc.year

    def nth_sunday(month, n):
        d = datetime(y, month, 1, tzinfo=timezone.utc)
        d += timedelta(days=(6 - d.weekday()) % 7)
        return d + timedelta(days=7 * (n - 1))
    start = nth_sunday(3, 2) + timedelta(hours=7)   # 02:00 EST = 07:00 UTC
    end = nth_sunday(11, 1) + timedelta(hours=6)    # 02:00 EDT = 06:00 UTC
    return start <= utc < end


def et_now():
    u = datetime.now(timezone.utc)
    return u + timedelta(hours=-4 if _us_dst(u) else -5)


def session(mkt):
    """정규장 여부와 남은 분(서버 기준). 휴장일 표는 화면 쪽에 있고, 여기선 주말·시간만 본다."""
    if mkt == "us":
        n = et_now()
        mins = n.hour * 60 + n.minute
        open_ = n.weekday() < 5 and 570 <= mins < 960
        return {"open": open_, "left_min": 960 - mins if open_ else 0, "local": n.strftime("%Y-%m-%d %H:%M (뉴욕)"),
                "kst": kst_now().strftime("%Y-%m-%d %H:%M"), "close_kst": (kst_now() + timedelta(minutes=960 - mins)).strftime("%H:%M")}
    n = kst_now()
    mins = n.hour * 60 + n.minute
    open_ = n.weekday() < 5 and 540 <= mins < 920
    return {"open": open_, "left_min": 920 - mins if open_ else 0, "local": n.strftime("%Y-%m-%d %H:%M"),
            "kst": n.strftime("%Y-%m-%d %H:%M"), "close_kst": "15:20"}


def bar_features(bars):
    """오늘 5분봉에서 단타에 쓰는 특징값."""
    if not bars:
        return None
    day = bars[-1]["t"][:5]
    tb = [b for b in bars if b["t"][:5] == day] or bars[-30:]
    o, c = tb[0]["o"] or tb[0]["c"], tb[-1]["c"]
    hi, lo = max(b["h"] for b in tb), min(b["l"] for b in tb)
    vol = sum(b["v"] for b in tb)
    vwap = sum((b["h"] + b["l"] + b["c"]) / 3 * b["v"] for b in tb) / vol if vol else c
    last6, prev = tb[-6:], tb[:-6]
    v6 = sum(b["v"] for b in last6) / max(1, len(last6))
    vp = sum(b["v"] for b in prev) / len(prev) if prev else v6
    r = lambda a, b: round((a / b - 1) * 100, 2) if b else None
    return {"5분봉_개수": len(tb), "시가대비%": r(c, o), "VWAP대비%": r(c, vwap), "고점대비%": r(c, hi), "저점대비%": r(c, lo),
            "최근30분%": r(c, last6[0]["o"] or last6[0]["c"]), "최근30분_거래량배수": round(v6 / vp, 2) if vp else None,
            "VWAP": round(vwap, 2), "고가": hi, "저가": lo}


class AIPick:
    def __init__(self, rt):
        self.rt = rt
        rt.db.x("""CREATE TABLE IF NOT EXISTS ai_picks(
            id INTEGER PRIMARY KEY, ts TEXT NOT NULL, mkt TEXT NOT NULL, model TEXT NOT NULL, usd REAL,
            stance TEXT, view TEXT, picks TEXT NOT NULL, text TEXT)""")
        rt.db.x("""CREATE TABLE IF NOT EXISTS ai_guards(
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, mkt TEXT NOT NULL, engine TEXT NOT NULL,
            sym TEXT NOT NULL, name TEXT, exch TEXT, qty INTEGER NOT NULL, entry REAL NOT NULL,
            target_pct REAL NOT NULL, stop_pct REAL NOT NULL, trail_pct REAL NOT NULL DEFAULT 0,
            exit_by TEXT, status TEXT NOT NULL DEFAULT 'watching', base REAL, high REAL, last REAL,
            exit_reason TEXT, exit_price REAL, closed_at TEXT, pick_id INTEGER, msg TEXT)""")
        self._last = 0.0
        self._cands = {}

    # ---------- 추천 자료 ----------
    def context(self, d):
        rt, inv = self.rt, self.rt.invest
        mkt = "us" if d.get("mkt") == "us" else "kr"
        if inv.kr() is None:
            raise ValueError("실시간 순위가 필요해서 키움 계좌 연결(설정 → 계좌 연결)이 있어야 추천할 수 있어요")
        ss = session(mkt)
        kinds = ("value", "up", "volume") if mkt == "kr" else ("up", "volume")
        cand, seen = [], set()
        rank_dump = {}
        for k in kinds:
            try:
                r = inv.ranking(mkt, k)
            except Exception as e:
                rank_dump[k] = f"조회 실패: {e}"
                continue
            rank_dump[k] = [{x: it.get(x) for x in ("rank", "sym", "name", "price", "change_pct", "value", "volume")}
                            for it in r["items"][:20]]
            for it in r["items"][:20]:
                if it["sym"] in seen or not it.get("price"):
                    continue
                cp = it.get("change_pct") or 0
                if mkt == "kr" and (cp >= 25 or it["price"] < 1000):
                    continue
                if mkt == "us" and it["price"] < 2:
                    continue
                seen.add(it["sym"])
                cand.append(it)
        # 거래대금·등락 고르게: 앞쪽 12개만 깊게 본다
        deep = []
        for it in cand[:12]:
            row = {"sym": it["sym"], "name": it["name"], "현재가": it["price"], "등락률%": it.get("change_pct")}
            try:
                row["오늘_5분봉"] = bar_features(inv.candles(mkt, it["sym"], "m5").get("bars"))
            except Exception as e:
                row["오늘_5분봉"] = f"없음({e})"
            if mkt == "kr" and len(deep) < 8:
                try:
                    b = inv.orderbook(mkt, it["sym"])
                    row["호가_매수잔량/매도잔량"] = round(b["tot_bid"] / b["tot_ask"], 2) if b.get("tot_ask") else None
                except Exception:
                    pass
            deep.append(row)
        if not deep:
            raise ValueError("지금은 순위 자료가 비어 있어요(장 시작 전일 수 있어요)")
        self._cands[mkt] = deep
        try:
            idx = rt.market.kr_indices() if mkt == "kr" else rt.market.us_indices()
            idx = [{k: i.get(k) for k in ("name", "price", "change_pct")} for i in idx]
        except Exception:
            idx = []
        cash = None
        try:
            snap = rt.account.snapshot() if rt.account.configured() else None
            if snap:
                cash = snap.get("orderable") if mkt == "kr" else (snap.get("us") or {}).get("usd")
        except Exception:
            pass
        data = {"시장": "국내(KRX)" if mkt == "kr" else "미국", "기준시각(한국)": ss["kst"], "현지시각": ss["local"],
                "정규장_진행중": ss["open"], "정규장_남은분": ss["left_min"], "정규장_마감(한국시간)": ss["close_kst"],
                "지수": idx, "주문가능금액": cash, "통화": "원" if mkt == "kr" else "달러",
                "후보_상세(오늘 흐름)": deep, "실시간 순위": rank_dump}
        ask = (f"지금({ss['kst']} 한국시간) {'국내' if mkt == 'kr' else '미국'} 시장에서 바로 매수해 오늘 안에 수익으로 끝낼 "
               "확률이 가장 높은 종목을 골라 줘. 후보_상세를 중심으로 판단하고, 필요하면 오늘 뉴스·공시를 확인해.")
        if not ss["open"]:
            ask += " (지금은 정규장이 아니야. 다음 정규장 시작 직후 기준으로 판단하고, 그 점을 첫 줄에 밝혀.)"
        return f"AI 종목 추천({'국내' if mkt == 'kr' else '미국'})", ask, data

    def estimate(self, mkt):
        """모델별 예상 비용: 지난 실제 사용 평균이 있으면 그걸, 없으면 토큰 추정."""
        from .ai import MODELS, SEARCH_USD, FX_DEFAULT
        fx = FX_DEFAULT
        try:
            fx = float(self.rt.market.yh_quote("USDKRW=X")["price"]) or FX_DEFAULT
        except Exception:
            pass
        out = []
        for mid, (label, pi, po) in MODELS.items():
            rows = self.rt.db.q("SELECT usd FROM ai_usage WHERE kind='pick' AND model=? ORDER BY id DESC LIMIT 10", (mid,))
            lo = (14000 * pi + 1800 * po) / 1e6           # 검색 없이
            hi = (45000 * pi + 3000 * po) / 1e6 + 3 * SEARCH_USD  # 검색 3회(검색 결과도 입력 토큰)
            avg = sum(r["usd"] for r in rows) / len(rows) if rows else None
            out.append({"id": mid, "label": label, "usd_lo": round(lo, 4), "usd_hi": round(hi, 4),
                        "krw_lo": int(lo * fx), "krw_hi": int(hi * fx),
                        "avg_krw": int(avg * fx) if avg is not None else None, "n": len(rows)})
        c = self.rt.ai.conf()
        return {"models": out, "fx": fx, "default": c.get("model") or "claude-sonnet-5-5",
                "configured": self.rt.ai.configured(), "web_search": c.get("web_search", True),
                "session": session(mkt), "kiwoom": self.rt.invest.kr() is not None}

    # ---------- 결과 정리 ----------
    def parse(self, text, mkt, model, usd, cut=False):
        m = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or re.findall(r"(\{\s*\"stance\".*\})", text, re.S)
        j, note = None, ""
        if m:
            try:
                j = json.loads(m[-1])
            except ValueError:
                j = None
        if j is None:
            why = "AI 답이 길어 중간에 끊겼어요" if cut else "AI 답이 비어 있었어요" if not text.strip() else "AI 답에서 추천표를 읽지 못했어요"
            j, note = {"stance": "관망", "market_view": "", "picks": []}, f"{why}. 앱이 실시간 지표로 골랐어요"
            self.rt.db.log("system", "info", f"AI 추천 원문(끝 400자): {text[-400:]}", datetime.now().isoformat(timespec="seconds"))
        picks = []
        for p in (j.get("picks") or [])[:3]:
            try:
                sym = str(p.get("sym") or "").strip().upper()
                if mkt == "kr":
                    sym = re.sub(r"\D", "", sym)[-6:]
                if not sym:
                    continue
                t, s_ = abs(float(p.get("target_pct") or 0)), abs(float(p.get("stop_pct") or 0))
                if not t or not s_:
                    continue
                q = self.quote(mkt, sym)
                picks.append({"sym": sym, "name": p.get("name") or q.get("name") or sym, "price": q.get("price"),
                              "change_pct": q.get("change_pct"), "exch": q.get("exch"),
                              "score": int(float(p.get("score") or 0)), "win_prob": int(float(p.get("win_prob") or 0)),
                              "entry_type": "market" if p.get("entry_type") == "market" else "limit",
                              "entry_price": float(p.get("entry_price") or q.get("price") or 0),
                              "target_pct": round(min(t, 30), 2), "stop_pct": round(min(s_, 15), 2),
                              "trail_pct": round(abs(float(p.get("trail_pct") or 0)), 2),
                              "exit_by": str(p.get("exit_by") or "")[:5], "weight_pct": float(p.get("weight_pct") or 10),
                              "reasons": [str(x) for x in (p.get("reasons") or [])][:4],
                              "risks": [str(x) for x in (p.get("risks") or [])][:3]})
            except Exception:
                continue
        if not picks:  # 관망이어도 최소 1종목: 지표로 가장 나은 후보를 고른다
            fb = self.fallback(mkt)
            if fb:
                picks.append(fb)
                note = note or "AI가 종목을 비워 와서 앱이 실시간 지표로 1종목을 골랐어요"
            else:
                note = note or "조건에 맞는 후보가 없었어요(등락 −3%~+15% 사이, 오늘 분봉 자료가 있는 종목)"
        body = text.split("```json")[0].strip()
        pid = self.rt.db.x("INSERT INTO ai_picks(ts,mkt,model,usd,stance,view,picks,text) VALUES(?,?,?,?,?,?,?,?)",
                           (datetime.now().isoformat(timespec="seconds"), mkt, model, usd, j.get("stance"),
                            j.get("market_view"), json.dumps(picks, ensure_ascii=False), body))
        return {"id": pid, "stance": j.get("stance"), "view": j.get("market_view"), "picks": picks, "mkt": mkt, "note": note}

    def fallback(self, mkt):
        best, bs = None, -1e9
        for c in self._cands.get(mkt) or []:
            f = c.get("오늘_5분봉")
            if not isinstance(f, dict):
                f = {}
            cp = c.get("등락률%") or 0
            if cp < -5 or cp > 20:
                continue
            sc = (f.get("VWAP대비%") or 0) * 2 + (f.get("최근30분%") or 0) * 1.5 + (f.get("고점대비%") or 0) \
                + min(2.0, f.get("최근30분_거래량배수") or 0) + (c.get("호가_매수잔량/매도잔량") or 1) - 1 \
                - abs(cp - 4) * 0.15  # 너무 급등·급락한 종목보다 +4% 안팎을 선호
            if sc > bs:
                best, bs = c, sc
        if not best:
            return None
        q = self.quote(mkt, best["sym"])
        price = q.get("price") or best.get("현재가")
        return {"sym": best["sym"], "name": best.get("name") or best["sym"], "price": price, "change_pct": q.get("change_pct", best.get("등락률%")),
                "exch": q.get("exch"), "score": 40, "win_prob": 45, "entry_type": "limit", "entry_price": price,
                "target_pct": 1.8, "stop_pct": 1.0, "trail_pct": 0.8, "exit_by": "15:00" if mkt == "kr" else "",
                "weight_pct": 5, "reasons": ["AI는 관망을 권했지만, 실시간 지표(VWAP 위치·최근 30분 흐름·거래량·호가)로 가장 나은 후보를 골랐어요",
                                             "비중 5%, 목표·손절을 좁게 잡은 보수적 진입이에요"],
                "risks": ["시장 분위기가 약해 승률이 낮아요"], "auto": True}

    def today(self):
        d = datetime.now().strftime("%Y-%m-%d")
        w = self.rt.db.one("SELECT COUNT(*) n FROM ai_guards WHERE status='watching'")["n"]
        done = self.rt.db.q("SELECT base, entry, exit_price FROM ai_guards WHERE status='done' AND closed_at LIKE ?", (d + "%",))
        pl = [(r["exit_price"] / (r["base"] or r["entry"]) - 1) * 100 for r in done if r["exit_price"] and (r["base"] or r["entry"])]
        n = self.rt.db.one("SELECT COUNT(*) n FROM ai_picks WHERE ts LIKE ?", (d + "%",))["n"]
        return {"watching": w, "done_today": len(done), "avg_pct": round(sum(pl) / len(pl), 2) if pl else 0, "picks_today": n}

    def history(self):
        rows = self.rt.db.q("SELECT id,ts,mkt,model,usd,stance,view,picks FROM ai_picks ORDER BY id DESC LIMIT 15")
        for r in rows:
            r["picks"] = json.loads(r["picks"] or "[]")
        return rows

    def quote(self, mkt, sym):
        m = self.rt.market
        try:
            return dict(m.kr_quote(sym) if mkt == "kr" else m.us_live(sym))
        except Exception:
            return {}

    # ---------- 자동 익절·손절 ----------
    def add_guard(self, d):
        mkt = "us" if d.get("mkt") == "us" else "kr"
        eng = d.get("engine") or "scalp"
        if self.rt.brokers.get(eng) is None:
            raise ValueError("계좌를 확인해 주세요")
        sym = str(d.get("sym") or "").strip().upper()
        qty = int(float(d.get("qty") or 0))
        entry = float(str(d.get("entry") or 0).replace(",", ""))
        tp, sl = abs(float(d.get("target_pct") or 0)), abs(float(d.get("stop_pct") or 0))
        if not sym or qty <= 0 or entry <= 0:
            raise ValueError("종목·수량·매수가를 확인해 주세요")
        if not (0.2 <= tp <= 50) or not (0.2 <= sl <= 30):
            raise ValueError("익절은 0.2~50%, 손절은 0.2~30% 사이로 넣어 주세요")
        exit_by = str(d.get("exit_by") or "")[:5]
        if exit_by and not re.match(r"^\d\d:\d\d$", exit_by):
            exit_by = ""
        gid = self.rt.db.x(
            "INSERT INTO ai_guards(created_at,mkt,engine,sym,name,exch,qty,entry,target_pct,stop_pct,trail_pct,exit_by,"
            "base,high,pick_id,msg) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), mkt, eng, sym, d.get("name") or sym, d.get("exch") or "",
             qty, entry, tp, sl, abs(float(d.get("trail_pct") or 0)), exit_by, entry, entry, d.get("pick_id"),
             "체결을 기다리는 중"))
        return self.rt.db.one("SELECT * FROM ai_guards WHERE id=?", (gid,))

    def cancel_guard(self, gid):
        self.rt.db.x("UPDATE ai_guards SET status='cancelled', closed_at=?, msg='감시를 껐어요' WHERE id=? AND status='watching'",
                     (datetime.now().isoformat(timespec="seconds"), gid))
        return {"ok": True}

    def guards(self, live=True):
        rows = self.rt.db.q("SELECT * FROM ai_guards ORDER BY CASE status WHEN 'watching' THEN 0 ELSE 1 END, id DESC LIMIT 30")
        for g in rows:
            b = g["base"] or g["entry"]
            g["tp_price"] = round(b * (1 + g["target_pct"] / 100), 2)
            g["sl_price"] = round(b * (1 - g["stop_pct"] / 100), 2)
            if live and g["status"] == "watching":
                p = self.quote(g["mkt"], g["sym"]).get("price")
                if p:
                    g["last"] = p
            px = g["exit_price"] if g["status"] == "done" else g["last"]
            g["pnl_pct"] = round((px / b - 1) * 100, 2) if px and b else None
        return rows

    def _position(self, g):
        b = self.rt.brokers.get(g["engine"])
        if b is None:
            return None
        if g["mkt"] == "us":
            cl = getattr(b, "client", None)
            if cl is None:
                return None
            for h in cl.us_holdings():
                if h["code"].upper() == g["sym"]:
                    return h
            return {"qty": 0}
        for h in b.positions():
            if h["code"] == g["sym"]:
                return h
        return {"qty": 0}

    def _sell(self, g, qty, reason, price):
        rt = self.rt
        b = rt.brokers.get(g["engine"])
        now = datetime.now().isoformat(timespec="seconds")
        try:
            if g["mkt"] == "us":
                no, exch = b.client.us_order("sell", g["sym"], qty, None, g["exch"] or None)
                res = {"status": "accepted", "fill_price": price, "broker_no": f"{no}|{exch}", "name": g["name"]}
            else:
                res = b.sell(g["sym"], qty, None)
            rt.db.x("INSERT INTO orders(ts,engine,mode,code,name,side,qty,price,status,fill_price,broker_no,msg) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now, g["engine"], b.mode, g["sym"], res.get("name") or g["name"], "sell", qty, None, res["status"],
                     res.get("fill_price") or price, res.get("broker_no", ""), f"AI {reason}"))
            rt.db.x("UPDATE ai_guards SET status='done', exit_reason=?, exit_price=?, closed_at=?, msg=? WHERE id=?",
                    (reason, res.get("fill_price") or price, now, f"{reason} 주문을 넣었어요", g["id"]))
            pl = (price / (g["base"] or g["entry"]) - 1) * 100
            rt.notify(f"[moa AI] {g['name']} {qty}주 {reason} 매도 주문 · 약 {pl:+.2f}%")
        except Exception as e:
            rt.db.x("UPDATE ai_guards SET msg=? WHERE id=?", (f"{reason} 매도 실패: {e} (다음 확인 때 다시)", g["id"]))
            rt.db.log("system", "error", f"AI 자동 {reason} 실패 {g['sym']}: {e}", now)

    def check(self, force=False):
        """감시 중인 종목을 돌며 익절·손절·추적손절·시간 정리."""
        import time as _t
        if not force and _t.time() - self._last < 5:
            return
        self._last = _t.time()
        rows = self.rt.db.q("SELECT * FROM ai_guards WHERE status='watching'")
        for g in rows:
            ss = session(g["mkt"])
            if not ss["open"]:
                continue
            q = self.quote(g["mkt"], g["sym"])
            price = q.get("price")
            if not price:
                continue
            try:
                pos = self._position(g)
            except Exception:
                pos = None
            if pos is None:
                continue
            have = int(pos.get("sellable") or pos.get("qty") or 0)
            base = g["base"] or g["entry"]
            if have <= 0:
                # 아직 체결 전: 정리 시각이 지나면 감시만 끝낸다
                if g["exit_by"] and kst_now().strftime("%H:%M") >= g["exit_by"] and g["mkt"] == "kr":
                    self.rt.db.x("UPDATE ai_guards SET status='expired', closed_at=?, msg='체결되지 않아 감시를 끝냈어요' WHERE id=?",
                                 (datetime.now().isoformat(timespec="seconds"), g["id"]))
                else:
                    self.rt.db.x("UPDATE ai_guards SET last=?, msg='체결을 기다리는 중' WHERE id=?", (price, g["id"]))
                continue
            if pos.get("avg_price") and g["msg"] == "체결을 기다리는 중":
                base = float(pos["avg_price"])  # 실제 평균 단가 기준으로 다시 맞춤
            high = max(g["high"] or base, price)
            qty = min(have, g["qty"])
            self.rt.db.x("UPDATE ai_guards SET base=?, high=?, last=?, msg='감시 중' WHERE id=?", (base, high, price, g["id"]))
            g.update(base=base, high=high)
            chg = (price / base - 1) * 100
            reason = None
            if chg >= g["target_pct"]:
                reason = "익절"
            elif chg <= -g["stop_pct"]:
                reason = "손절"
            elif g["trail_pct"] and high > base * (1 + g["trail_pct"] / 100) and price <= high * (1 - g["trail_pct"] / 100) and price > base:
                reason = "추적 익절"
            else:
                now_hm = kst_now().strftime("%H:%M")
                close_soon = ss["left_min"] <= 8  # 정규장 마감 8분 전이면 정리
                if (g["exit_by"] and g["mkt"] == "kr" and now_hm >= g["exit_by"]) or close_soon:
                    reason = "시간 정리"
            if reason:
                self._sell(g, qty, reason, price)
