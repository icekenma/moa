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
FACTORS = {"kr": ("^KS11", "코스피"), "us": ("^GSPC", "S&P 500"), "fx": ("USDKRW=X", "원/달러"), "rate": ("^TNX", "미 10년 금리")}
CORE = {"kr": "코스피", "us": "S&P 500", "fx": "원/달러"}
# 업종 요인: 그 업종을 따르는 상장 ETF의 움직임으로 본다. 코드는 외워 적지 않고 종목 목록에서 이름으로 찾는다(없으면 그 업종은 뺀다)
SECTOR_ETF = {"반도체": "KODEX 반도체", "2차전지": "TIGER 2차전지테마", "바이오": "KODEX 바이오", "은행": "KODEX 은행", "자동차": "KODEX 자동차"}
IMPLY_MIN = 0.2         # '만약에'에서 다른 요인이 따라 움직인다고 볼 최소 상관(주 단위)
SEC_MIN = 0.3           # 시장을 뺀 나머지 움직임이 업종과 이만큼은 닮아야 그 업종에 든다고 본다(상관)
# 과거 위기: 그 기간에 요인이 실제로 움직인 폭을 지수 기록에서 읽어(외워 둔 숫자가 아니다) 지금 보유에 입힌다
CRISES = [("2008년 금융위기", "2008-09-12", "2008-10-27"), ("2020년 코로나 급락", "2020-02-19", "2020-03-23"),
          ("2022년 긴축 하락", "2022-01-03", "2022-09-30"), ("2024년 8월 급락", "2024-07-11", "2024-08-05")]
# 만약에: 요인 하나를 흔들고, 나머지 요인은 과거에 함께 움직인 만큼 따라 움직인다고 본다
WHATIF = [("코스피 −10%", "kr", -0.10), ("S&P 500 −10%", "us", -0.10), ("원/달러 +5%(원화 약세)", "fx", 0.05), ("원/달러 −5%(원화 강세)", "fx", -0.05),
          ("미국 10년 금리 +1%p", "rate", 1.0)]
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


def _rets_by_date(closes, diff=False):
    """날짜별 하루 변화. 주가·지수·환율은 비율, 금리는 차이(%p)."""
    ds = sorted(closes)
    if diff:
        return {b: closes[b] - closes[a] for a, b in zip(ds, ds[1:])}
    return {b: closes[b] / closes[a] - 1 for a, b in zip(ds, ds[1:]) if closes[a]}


def _fit(y, xs):
    """y를 xs(여러 개)에 맞춘 기울기들(절편 포함 최소제곱). 날짜로 맞춘 값 목록을 받는다. 풀 수 없으면 0."""
    n, k = len(y), len(xs)
    my = sum(y) / n
    cx = [[v - sum(x) / n for v in x] for x in xs]
    cy = [v - my for v in y]
    A = [[sum(p * q for p, q in zip(cx[i], cx[j])) for j in range(k)] + [sum(p * q for p, q in zip(cx[i], cy))] for i in range(k)]
    for i in range(k):                                   # 가우스 소거(작은 수로 나누지 않게 가장 큰 줄을 올린다)
        p = max(range(i, k), key=lambda q: abs(A[q][i]))
        if abs(A[p][i]) < 1e-18:
            return [0.0] * k
        A[i], A[p] = A[p], A[i]
        for q in range(k):
            if q != i:
                f = A[q][i] / A[i][i]
                A[q] = [u - f * v for u, v in zip(A[q], A[i])]
    return [A[i][k] / A[i][i] for i in range(k)]


def _corr(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va, vb = sum((x - ma) ** 2 for x in a), sum((x - mb) ** 2 for x in b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb) if va > 0 and vb > 0 else 0.0


def _at(levels, day):
    """그 날짜(없으면 바로 앞 거래일)의 값."""
    ds = [d for d in levels if d <= day]
    return levels[max(ds)] if ds else None


def _shock_txt(s):
    f = lambda v, u="%": f"{'+' if v > 0 else '−'}{abs(v):g}{u}"
    out = [f"{CORE[k]} {f(round(s[k] * 100, 1))}" for k in CORE if k in s]
    if "rate" in s:
        out.append(f"미 10년 금리 {f(round(s['rate'], 2), '%p')}")
    out += [f"{k[4:]} 업종(시장 대비) {f(round(v * 100, 1))}" for k, v in s.items() if k.startswith("sec:")]
    return " · ".join(out)


def scenarios(items, levels):
    """요인 시나리오. items: [{name, mkt, value, closes{날짜: 종가}}], levels: {kr|us|fx|rate|sec:<업종>: {날짜: 값}}.
    ① 종목마다 요인 민감도를 구한다. 국내: 코스피·환율·미국 금리 + (시장을 뺀 나머지 움직임이 가장 닮은) 업종 하나. 미국: S&P·금리 — 원화로 보면 환율은 그대로 1배.
    ② 과거 위기: 그 기간 요인이 실제 움직인 폭을 입힌다. ③ 만약에: 요인 하나를 흔들고 나머지는 과거에 함께 움직인 만큼 따라간다고 본다.
    민감도가 큰 충격에서도 그대로라고 가정한 어림값이라, 충격이 클수록 오차가 크다."""
    total = sum(x["value"] for x in items)
    if total <= 0:
        return None
    levels = {k: v for k, v in levels.items() if v}
    fr = {k: _rets_by_date(v, diff=k == "rate") for k, v in levels.items()}
    secs = [k for k in fr if k.startswith("sec:")]
    # 업종의 '시장 대비' 움직임: 업종 수익률에서 코스피로 설명되는 몫을 뺀다
    sx = {}
    for k in secs:
        ds = [d for d in fr[k] if d in fr.get("kr", {})][-LONG:]
        if len(ds) >= 60:
            bk = _fit([fr[k][d] for d in ds], [[fr["kr"][d] for d in ds]])[0]
            sx[k] = (bk, {d: fr[k][d] - bk * fr["kr"][d] for d in ds})
    est = 0
    for x in items:
        rr = _rets_by_date(x.get("closes") or {})
        base = ["kr", "fx", "rate"] if x["mkt"] == "kr" else ["us", "rate"]
        use = [k for k in base if k in fr]
        ds = [d for d in rr if all(d in fr[k] for k in use)][-LONG:]
        x["b"] = {}
        if len(ds) >= 40 and base[0] in use:
            bs = _fit([rr[d] for d in ds], [[fr[k][d] for d in ds] for k in use])
            x["b"] = dict(zip(use, bs))
            est += 1
            if x["mkt"] == "kr" and sx:
                res = {d: rr[d] - sum(b * fr[k][d] for k, b in x["b"].items()) for d in ds}
                best = None
                for k, (_, ex) in sx.items():
                    dd = [d for d in ds if d in ex]
                    if len(dd) >= 40:
                        c = _corr([res[d] for d in dd], [ex[d] for d in dd])
                        if c >= SEC_MIN and (best is None or c > best[1]):
                            best = (k, c, dd)
                if best:
                    k, c, dd = best
                    x["sec"], x["b"][k] = k, _fit([res[d] for d in dd], [[sx[k][1][d] for d in dd]])[0]
        else:
            x["b"], x["guess"] = {base[0]: 1.0}, True
        if x["mkt"] == "us":
            x["b"]["fx"] = 1.0
    held = {x["sec"] for x in items if x.get("sec")}

    def run(name, sub, s, kind):
        rows, pnl = [], 0.0
        for x in items:
            # 민감도 × 충격을 요인마다 더한다(금리는 1%p당, 나머지는 비율). 미국 종목의 환율은 아래에서 곱으로 따로 넣는다
            core = sum(b * s.get(k, 0.0) for k, b in x["b"].items() if k != "fx" or x["mkt"] == "kr")
            r = core if x["mkt"] == "kr" else (1 + core) * (1 + s.get("fx", 0.0)) - 1
            r = max(r, -0.95)
            pnl += x["value"] * r
            rows.append({"name": x["name"], "pct": round(r * 100, 1), "pnl": round(x["value"] * r)})
        rows.sort(key=lambda v: v["pnl"])
        shown = {k: v for k, v in s.items() if not k.startswith("sec:") or k in held}
        return {"name": name, "sub": sub, "kind": kind, "shock": {k: round(v * (1 if k == "rate" else 100), 2 if k == "rate" else 1) for k, v in shown.items()},
                "shock_txt": _shock_txt(shown), "pnl": round(pnl), "pct": round(pnl / total * 100, 2), "items": rows[:10]}
    out = []
    for name, d0, d1 in CRISES:
        s = {}
        for k, lv in levels.items():
            a, b = _at(lv, d0), _at(lv, d1)
            if a and b and min(lv) <= d0:
                s[k] = (b - a) if k == "rate" else b / a - 1
        for k in secs:                                   # 업종은 '시장 대비'로 바꾼다(기록이 닿는 위기만)
            if k in s:
                tot = s.pop(k)
                if k in sx and "kr" in s:
                    s[k] = tot - sx[k][0] * s["kr"]
        if "kr" in s or "us" in s:
            out.append(run(name, f"{d0[:7].replace('-', '.')}~{d1[:7].replace('-', '.')}", s, "crisis"))
    # 요인끼리 함께 움직인 정도: 장이 열리는 시간이 달라 하루 단위로는 어긋나므로 5거래일 묶음으로 잰다
    def weekly(keys):
        common = sorted(set.intersection(*[set(levels[k]) for k in keys]))[-(LONG + 1):]
        w = {}
        for k in keys:
            pts = [levels[k][d] for d in common][::5]
            w[k] = [(b - a) if k == "rate" else (b / a - 1) for a, b in zip(pts, pts[1:]) if a]
        return w
    plain = [k for k in ("kr", "us", "fx", "rate") if k in levels]
    what = [w for w in WHATIF if w[1] in levels] + [(f"{k[4:]} 업종 −15%", k, -0.15) for k in sorted(held)]
    for name, k, size in what:
        keys = plain + ([k] if k not in plain else [])
        wk = weekly(keys) if all(c in levels for c in CORE) else {}
        s = {k: size}
        for j in plain:
            if j != k and len(wk.get(k, [])) >= 20:
                n = min(len(wk[j]), len(wk[k]))
                if abs(_corr(wk[j][-n:], wk[k][-n:])) >= IMPLY_MIN:      # 함께 움직인 증거가 약하면 따라 움직이지 않는다고 본다(우연한 기울기를 믿지 않으려고)
                    s[j] = _fit(wk[j][-n:], [wk[k][-n:]])[0] * size
        if k.startswith("sec:"):                         # 업종 전체 움직임 → 시장 대비 움직임
            s[k] = size - sx[k][0] * s.get("kr", 0.0)
        out.append(run(name, "", s, "whatif"))
    worst = max(items, key=lambda x: x["value"] * GAP[x["mkt"]])

    def btxt(x):
        b, nm = x["b"], {"kr": "지수", "us": "지수", "fx": "환율", "rate": "금리 1%p당"}
        parts = [f"{nm[k]} {round(b[k] * (100 if k == 'rate' else 1), 1 if k == 'rate' else 2):g}{'%' if k == 'rate' else ''}" for k in ("kr", "us", "fx", "rate") if k in b]
        if x.get("sec"):
            parts.append(f"{x['sec'][4:]} 업종 {round(b[x['sec']], 2):g}")
        return " · ".join(parts) + (" · 기록이 짧아 가정값" if x.get("guess") else "")
    return {"rows": out, "est": est, "n": len(items), "factors": [CORE.get(k) or ("미 10년 금리" if k == "rate" else k[4:] + " 업종") for k in levels],
            "betas": [{"name": x["name"], "bm": round(x["b"].get("kr", x["b"].get("us", 1.0)), 2), "bfx": round(x["b"].get("fx", 0.0), 2), "guess": bool(x.get("guess")),
                       "sec": (x.get("sec") or "")[4:], "txt": btxt(x)} for x in items],
            "gap": {"name": worst["name"], "drop": round(GAP[worst["mkt"]] * 100), "loss": round(worst["value"] * GAP[worst["mkt"]]),
                    "pct": round(worst["value"] * GAP[worst["mkt"]] / total * 100, 2)}}


class RiskView:
    def __init__(self, rt):
        self.rt = rt
        self._memo = None

    def levels(self):
        """요인(코스피·S&P 500·원/달러·미 10년 금리)의 20년 일별 값과 업종 ETF의 약 2년 값. 12시간에 한 번만 받는다. 못 받은 요인은 빈 값."""
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
        try:
            names = {v.get("name"): c for c, v in (m.kr_master() or {}).items() if isinstance(v, dict)}
        except Exception:
            names = {}
        for sec, etf in SECTOR_ETF.items():
            code = names.get(etf)
            if not code:
                continue

            def load_s(code=code):
                bs = (self.rt.invest.candles("kr", code, "d", 600) or {}).get("bars") or []
                return {b["t"]: b["c"] for b in bs if b.get("c") and b.get("t")}
            try:
                v = m.cache.get(("risk-sec", code), 12 * 3600, load_s, stale=72 * 3600)
                if len(v) >= 100:
                    out["sec:" + sec] = v
            except Exception:
                pass
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
