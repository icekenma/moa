"""계좌 전체 위험 보기(읽기 전용).

내 계좌의 보유 종목을 한데 모아, 한 종목·한 업종에 얼마나 쏠렸는지, 종목들이 얼마나 같이 움직이는지,
보통 하루에 얼마나 흔들리는지, 지수가 크게 내리면 얼마쯤 잃는지를 어림한다. 주문은 내지 않는다.
과거 60거래일의 일간 수익률로 셈한 어림값이라, 앞으로의 손실을 보장하거나 한정하지 않는다.
"""
import math
import time

from .trading import montecarlo as MC
from datetime import datetime, timezone

BENCH = {"kr": ("^KS11", "코스피"), "us": ("^GSPC", "S&P 500")}
N = 60                  # '최근' 창(거래일)
LONG = 500              # 긴 창: 받을 수 있는 만큼(약 2년)
# 위험 요인 셋: 국내 주식시장, 미국 주식시장, 환율. 종목마다 이 요인에 얼마나 따라 움직이는지(민감도)를 구해 시나리오에 쓴다
FACTORS = {"kr": ("^KS11", "코스피"), "us": ("^GSPC", "S&P 500"), "fx": ("USDKRW=X", "원/달러")}
# 과거 위기: 그 기간에 요인이 실제로 움직인 폭을 지수 기록에서 읽어(외워 둔 숫자가 아니다) 지금 보유에 입힌다
CRISES = [("2008년 금융위기", "2008-09-12", "2008-10-27"), ("2020년 코로나 급락", "2020-02-19", "2020-03-23"),
          ("2022년 긴축 하락", "2022-01-03", "2022-09-30"), ("2024년 8월 급락", "2024-07-11", "2024-08-05")]
# 만약에: 요인 하나를 흔들고, 나머지 요인은 과거에 함께 움직인 만큼 따라 움직인다고 본다
WHATIF = [("코스피 −10%", "kr", -0.10), ("S&P 500 −10%", "us", -0.10), ("원/달러 +5%(원화 약세)", "fx", 0.05), ("원/달러 −5%(원화 강세)", "fx", -0.05)]
GAP = {"kr": 0.30, "us": 0.20}   # 한 종목 급락: 국내는 하한가(−30%), 미국은 하루 −20%로 가정


def _rets(bars):
    c = [b["c"] for b in (bars or []) if b.get("c")][-(LONG + 1):]
    return [b / a - 1 for a, b in zip(c, c[1:])] if len(c) >= 21 else None


def _cov(x, y):
    n = min(len(x), len(y))
    x, y = x[-n:], y[-n:]
    mx, my = sum(x) / n, sum(y) / n
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (n - 1)


def compute(items, bench):
    """items: [{name, value, sector, mkt, rets}], bench: {mkt: rets}. 결과는 화면에 그대로 쓰는 값들."""
    total = sum(x["value"] for x in items)
    if total <= 0:
        return {"n": 0}
    for x in items:
        x["w"] = x["value"] / total
        x["rets_all"] = x.get("rets")
        x["rets"] = x["rets"][-N:] if x.get("rets") else x.get("rets")      # 흔들림·민감도·상관은 '최근' 창으로
        r = x.get("rets")
        x["vol"] = round(math.sqrt(_cov(r, r)) * 100, 2) if r else None                     # 하루 흔들림(%)
        b = bench.get(x["mkt"])
        vb = _cov(b, b) if b else 0
        x["beta"] = round(_cov(r, b) / vb, 2) if r and b and vb > 0 else None
    known = [x for x in items if x.get("rets")]
    var = 0.0
    for a in known:
        for b in known:
            var += a["w"] * b["w"] * _cov(a["rets"], b["rets"])
    day_vol = math.sqrt(var) * 100 if var > 0 else None                                   # 계좌 전체의 하루 흔들림(%)
    pairs = []
    for i, a in enumerate(known):
        for b in known[i + 1:]:
            va, vb = _cov(a["rets"], a["rets"]), _cov(b["rets"], b["rets"])
            if va > 0 and vb > 0:
                c = _cov(a["rets"], b["rets"]) / math.sqrt(va * vb)
                if c >= 0.7:
                    pairs.append({"a": a["name"], "b": b["name"], "corr": round(c, 2)})
    sec = {}
    for x in items:
        k = x.get("sector") or ("미국 주식" if x["mkt"] == "us" else "업종 모름")
        sec[k] = sec.get(k, 0) + x["w"]
    beta = sum(x["w"] * x["beta"] for x in items if x["beta"] is not None)
    cover = sum(x["w"] for x in items if x["beta"] is not None)
    top = sorted(items, key=lambda x: -x["w"])
    # 몬테카를로: 지금 비중 그대로 20거래일을 3,000번 그려 본다(자료가 없는 종목은 움직이지 않는다고 본다 → cover로 알린다)
    # 창 두 개(긴 창 약 2년 · 최근 60일)로 그려 보고 더 나쁜 쪽을 쓴다: 조용했던 최근만 보고 위험을 작게 잡지 않으려고
    mc = None
    if known:
        cand = []
        for rs in ([x["rets_all"][-LONG:] for x in known], [x["rets"] for x in known]):
            day = MC.mix([x["w"] for x in known], rs)
            p = MC.paths(day, days=20)
            if p:
                cand.append(dict(p, window=len(day)))
        if cand:
            mc = max(cand, key=lambda p: p["var95"])
            other = min(cand, key=lambda p: p["var95"])
            mc["alt"] = None if other is mc or other["window"] == mc["window"] else {"window": other["window"], "var95": other["var95"]}
            mc.update(var_krw=round(total * mc["var95"] / 100), cvar_krw=round(total * mc["cvar95"] / 100))
    return {"n": len(items), "total": round(total), "cover": round(cover * 100),
            "top": [{"name": x["name"], "w": round(x["w"] * 100, 1), "vol": x["vol"], "beta": x["beta"], "sector": x.get("sector") or ""} for x in top[:8]],
            "sectors": [{"name": k, "w": round(v * 100, 1)} for k, v in sorted(sec.items(), key=lambda kv: -kv[1])[:6]],
            "day_vol": None if day_vol is None else round(day_vol, 2),
            "day_risk": None if day_vol is None else round(total * day_vol / 100 * 1.65),      # 스무 날에 한 번쯤 넘는 하루 손실(어림)
            "beta": round(beta / cover, 2) if cover else None,
            "stress": None if not cover else {"idx": -5, "loss": round(total * beta * -0.05), "pct": round(beta * -5, 2)},
            "mc": mc,
            "pairs": sorted(pairs, key=lambda p: -p["corr"])[:6],
            "flags": [f for f in (
                f"한 종목({top[0]['name']})이 {top[0]['w'] * 100:.0f}%예요" if top[0]["w"] >= 0.3 else "",
                next((f"한 업종({k})이 {v * 100:.0f}%예요" for k, v in sec.items() if v >= 0.5 and k not in ("미국 주식", "업종 모름")), ""),
                f"같이 움직이는 종목 쌍이 {len(pairs)}개예요(상관 0.7 이상)" if len(pairs) >= 2 else "") if f]}


def _rets_by_date(closes):
    ds = sorted(closes)
    return {b: closes[b] / closes[a] - 1 for a, b in zip(ds, ds[1:]) if closes[a]}


def _fit(y, xs):
    """y를 xs(1~2개)에 맞춘 기울기들(절편 포함 최소제곱). 날짜로 맞춘 값 목록을 받는다."""
    n = len(y)
    my = sum(y) / n
    cx = [[v - sum(x) / n for v in x] for x in xs]
    cy = [v - my for v in y]
    if len(xs) == 1:
        v = sum(a * a for a in cx[0])
        return [sum(a * b for a, b in zip(cx[0], cy)) / v] if v > 0 else [0.0]
    a11, a22 = sum(a * a for a in cx[0]), sum(a * a for a in cx[1])
    a12 = sum(a * b for a, b in zip(cx[0], cx[1]))
    b1, b2 = sum(a * b for a, b in zip(cx[0], cy)), sum(a * b for a, b in zip(cx[1], cy))
    det = a11 * a22 - a12 * a12
    if det <= 1e-18:
        return [b1 / a11 if a11 > 0 else 0.0, 0.0]
    return [(b1 * a22 - b2 * a12) / det, (b2 * a11 - b1 * a12) / det]


def _at(levels, day):
    """그 날짜(없으면 바로 앞 거래일)의 값."""
    ds = [d for d in levels if d <= day]
    return levels[max(ds)] if ds else None


def scenarios(items, levels):
    """요인 시나리오. items: [{name, mkt, value, closes{날짜: 종가}}], levels: {kr|us|fx: {날짜: 값}}.
    ① 종목마다 요인 민감도를 구한다(국내: 코스피·환율 두 요인, 미국: S&P — 원화로 보면 환율은 그대로 1배).
    ② 과거 위기: 그 기간 요인이 실제 움직인 폭을 입힌다. ③ 만약에: 요인 하나를 흔들고 나머지는 과거에 함께 움직인 만큼 따라간다고 본다.
    민감도가 큰 충격에서도 그대로라고 가정한 어림값이라, 충격이 클수록 오차가 크다."""
    total = sum(x["value"] for x in items)
    if total <= 0:
        return None
    fr = {k: _rets_by_date(v) for k, v in levels.items() if v}
    est = 0
    for x in items:
        rr = _rets_by_date(x.get("closes") or {})
        if x["mkt"] == "kr":
            ds = [d for d in rr if d in fr.get("kr", {}) and d in fr.get("fx", {})][-LONG:]
            if len(ds) >= 40:
                x["bm"], x["bfx"] = _fit([rr[d] for d in ds], [[fr["kr"][d] for d in ds], [fr["fx"][d] for d in ds]])
                est += 1
            else:
                x["bm"], x["bfx"], x["guess"] = 1.0, 0.0, True
        else:
            ds = [d for d in rr if d in fr.get("us", {})][-LONG:]
            if len(ds) >= 40:
                x["bm"] = _fit([rr[d] for d in ds], [[fr["us"][d] for d in ds]])[0]
                est += 1
            else:
                x["bm"], x["guess"] = 1.0, True
            x["bfx"] = 1.0

    def run(name, sub, s, kind):
        rows, pnl = [], 0.0
        for x in items:
            if x["mkt"] == "kr":
                r = x["bm"] * s.get("kr", 0.0) + x["bfx"] * s.get("fx", 0.0)
            else:
                r = (1 + x["bm"] * s.get("us", 0.0)) * (1 + s.get("fx", 0.0)) - 1
            r = max(r, -0.95)
            pnl += x["value"] * r
            rows.append({"name": x["name"], "pct": round(r * 100, 1), "pnl": round(x["value"] * r)})
        rows.sort(key=lambda v: v["pnl"])
        return {"name": name, "sub": sub, "kind": kind, "shock": {k: round(v * 100, 1) for k, v in s.items()}, "pnl": round(pnl), "pct": round(pnl / total * 100, 2), "items": rows[:10]}
    out = []
    for name, d0, d1 in CRISES:
        s = {}
        for k, lv in levels.items():
            a, b = _at(lv or {}, d0), _at(lv or {}, d1)
            if a and b and min(lv) <= d0:
                s[k] = b / a - 1
        if "kr" in s or "us" in s:
            out.append(run(name, f"{d0[:7].replace('-', '.')}~{d1[:7].replace('-', '.')}", s, "crisis"))
    # 요인끼리 함께 움직인 정도: 장이 열리는 시간이 달라 하루 단위로는 어긋나므로 5거래일 묶음으로 잰다
    wk = {}
    common = sorted(set.intersection(*[set(v) for v in levels.values() if v])) if all(levels.get(k) for k in FACTORS) else []
    common = common[-(LONG + 1):]
    for k in FACTORS:
        pts = [levels[k][d] for d in common][::5]
        wk[k] = [b / a - 1 for a, b in zip(pts, pts[1:]) if a]
    for name, k, size in WHATIF:
        s = {k: size}
        for j in FACTORS:
            if j != k and len(wk.get(k, [])) >= 20:
                n = min(len(wk[j]), len(wk[k]))
                s[j] = _fit(wk[j][-n:], [wk[k][-n:]])[0] * size
        out.append(run(name, "", s, "whatif"))
    worst = max(items, key=lambda x: x["value"] * GAP[x["mkt"]])
    return {"rows": out, "est": est, "n": len(items), "betas": [{"name": x["name"], "bm": round(x["bm"], 2), "bfx": round(x["bfx"], 2), "guess": bool(x.get("guess"))} for x in items],
            "gap": {"name": worst["name"], "drop": round(GAP[worst["mkt"]] * 100), "loss": round(worst["value"] * GAP[worst["mkt"]]),
                    "pct": round(worst["value"] * GAP[worst["mkt"]] / total * 100, 2)}}


class RiskView:
    def __init__(self, rt):
        self.rt = rt
        self._memo = None

    def levels(self):
        """요인(코스피·S&P 500·원/달러)의 20년 일별 값. 12시간에 한 번만 받는다. 못 받은 요인은 빈 값."""
        m, out = self.rt.market, {}
        for k, (sym, _) in FACTORS.items():
            def load(sym=sym):
                res = m._yh_chart(sym, "20y", "1d")
                off = (res.get("meta") or {}).get("gmtoffset") or 0
                cl = (((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
                return {datetime.fromtimestamp(t + off, timezone.utc).strftime("%Y-%m-%d"): c for t, c in zip(res.get("timestamp") or [], cl) if c}
            try:
                out[k] = m.cache.get(("risk-lv", sym), 12 * 3600, load, stale=72 * 3600)
            except Exception:
                out[k] = {}
        return out

    def snapshot(self):
        rt = self.rt
        if not rt.account.configured():
            return {"configured": False}
        a = rt.account.snapshot()
        hold = [h for h in (a.get("holdings") or []) if (h.get("value") or 0) > 0 or (h.get("qty") and h.get("price"))]
        if not hold:
            return {"configured": True, "n": 0, "error": a.get("error")}
        sig = tuple(sorted((h["code"], round(float(h.get("value") or 0), -3)) for h in hold))
        if self._memo and self._memo[0] == sig and time.time() - self._memo[1] < 600:
            return self._memo[2]                      # 보유가 그대로면 10분 동안은 다시 받지 않는다(종목마다 긴 일봉을 받는다)
        bars = lambda mkt, sym: (rt.invest.candles(mkt, sym, "d", 600) or {}).get("bars")
        bench = {}
        for mkt, (sym, _) in BENCH.items():
            try:
                bench[mkt] = _rets(bars("us", sym))
            except Exception:
                bench[mkt] = None
        items = []
        for h in hold:
            mkt = "us" if h.get("market") == "us" else "kr"
            try:
                bs = bars(mkt, h["code"]) or []
                r, cl = _rets(bs), {b["t"]: b["c"] for b in bs if b.get("c") and b.get("t")}
            except Exception:
                r, cl = None, {}
            items.append({"name": h.get("name") or h["code"], "mkt": mkt, "value": float(h.get("value") or h["qty"] * h["price"]), "closes": cl,
                          "sector": rt.market.kr_info(h["code"]).get("sector") if mkt == "kr" else "", "rets": r})
        out = compute(items, bench)
        try:
            out["scen"] = scenarios(items, self.levels())
        except Exception as e:
            out["scen"] = None
            rt.db.log("system", "error", f"위험 시나리오 계산 오류: {type(e).__name__}: {e}", datetime.now().isoformat(timespec="seconds"))
        out.update(configured=True, at=datetime.now().isoformat(timespec="seconds"), days=N)
        self._memo = (sig, time.time(), out)
        return out
