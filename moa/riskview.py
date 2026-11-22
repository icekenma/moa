"""계좌 전체 위험 보기(읽기 전용).

내 계좌의 보유 종목을 한데 모아, 한 종목·한 업종에 얼마나 쏠렸는지, 종목들이 얼마나 같이 움직이는지,
보통 하루에 얼마나 흔들리는지, 지수가 크게 내리면 얼마쯤 잃는지를 어림한다. 주문은 내지 않는다.
과거 60거래일의 일간 수익률로 셈한 어림값이라, 앞으로의 손실을 보장하거나 한정하지 않는다.
"""
import math
from datetime import datetime

BENCH = {"kr": ("^KS11", "코스피"), "us": ("^GSPC", "S&P 500")}
N = 60


def _rets(bars):
    c = [b["c"] for b in (bars or []) if b.get("c")][-(N + 1):]
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
    return {"n": len(items), "total": round(total), "cover": round(cover * 100),
            "top": [{"name": x["name"], "w": round(x["w"] * 100, 1), "vol": x["vol"], "beta": x["beta"], "sector": x.get("sector") or ""} for x in top[:8]],
            "sectors": [{"name": k, "w": round(v * 100, 1)} for k, v in sorted(sec.items(), key=lambda kv: -kv[1])[:6]],
            "day_vol": None if day_vol is None else round(day_vol, 2),
            "day_risk": None if day_vol is None else round(total * day_vol / 100 * 1.65),      # 스무 날에 한 번쯤 넘는 하루 손실(어림)
            "beta": round(beta / cover, 2) if cover else None,
            "stress": None if not cover else {"idx": -5, "loss": round(total * beta * -0.05), "pct": round(beta * -5, 2)},
            "pairs": sorted(pairs, key=lambda p: -p["corr"])[:6],
            "flags": [f for f in (
                f"한 종목({top[0]['name']})이 {top[0]['w'] * 100:.0f}%예요" if top[0]["w"] >= 0.3 else "",
                next((f"한 업종({k})이 {v * 100:.0f}%예요" for k, v in sec.items() if v >= 0.5 and k not in ("미국 주식", "업종 모름")), ""),
                f"같이 움직이는 종목 쌍이 {len(pairs)}개예요(상관 0.7 이상)" if len(pairs) >= 2 else "") if f]}


class RiskView:
    def __init__(self, rt):
        self.rt = rt

    def snapshot(self):
        rt = self.rt
        if not rt.account.configured():
            return {"configured": False}
        a = rt.account.snapshot()
        hold = [h for h in (a.get("holdings") or []) if (h.get("value") or 0) > 0 or (h.get("qty") and h.get("price"))]
        if not hold:
            return {"configured": True, "n": 0, "error": a.get("error")}
        bars = lambda mkt, sym: (rt.invest.candles(mkt, sym, "d") or {}).get("bars")
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
                r = _rets(bars(mkt, h["code"]))
            except Exception:
                r = None
            items.append({"name": h.get("name") or h["code"], "mkt": mkt, "value": float(h.get("value") or h["qty"] * h["price"]),
                          "sector": rt.market.kr_info(h["code"]).get("sector") if mkt == "kr" else "", "rets": r})
        out = compute(items, bench)
        out.update(configured=True, at=datetime.now().isoformat(timespec="seconds"), days=N)
        return out
