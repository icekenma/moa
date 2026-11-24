"""지수가 '평범했던 날'의 비율: 코스피·코스닥·S&P 500·나스닥의 하루 등락(종가 대비 종가)이 ±band% 안이었던 거래일 비율.

야후 일봉(5년)을 12시간에 한 번 받아 셈한다. 주문·매매 판단에는 쓰지 않는다(매매 기록 › 성적 분석의 참고 카드).
"""
from datetime import date, datetime, timedelta, timezone

INDICES = (("kr", "코스피", "^KS11"), ("us", "S&P 500", "^GSPC"), ("kr", "코스닥", "^KQ11"), ("us", "나스닥", "^IXIC"))


def changes(closes):
    """{날짜: 종가} → [(날짜, 등락%)] (앞 거래일 종가 대비)."""
    ds = sorted(d for d, c in closes.items() if c)
    return [(b, (closes[b] / closes[a] - 1) * 100) for a, b in zip(ds, ds[1:])]


def summarize(rows, band=1.0):
    """[(날짜, 등락%)] → 평범한 날 비율·큰 날 수·연도별·월별."""
    def agg(xs):
        n = len(xs)
        calm = sum(1 for _, p in xs if abs(p) < band)
        up = sum(1 for _, p in xs if p >= band)
        dn = sum(1 for _, p in xs if p <= -band)
        return {"n": n, "calm": calm, "up": up, "down": dn, "calm_pct": round(calm / n * 100, 1) if n else None,
                "big2": sum(1 for _, p in xs if abs(p) >= 2), "big3": sum(1 for _, p in xs if abs(p) >= 3),
                "avg_abs": round(sum(abs(p) for _, p in xs) / n, 2) if n else None}
    by_y, by_m = {}, {}
    for d, p in rows:
        by_y.setdefault(d[:4], []).append((d, p))
        by_m.setdefault(d[:7], []).append((d, p))
    return {**agg(rows), "years": [{"y": y, **agg(v)} for y, v in sorted(by_y.items())],
            "months": [{"m": m, "n": len(v), "calm_pct": agg(v)["calm_pct"]} for m, v in sorted(by_m.items())],
            "first": rows[0][0] if rows else None, "last": rows[-1][0] if rows else None,
            "last_big": next(({"day": d, "pct": round(p, 2)} for d, p in reversed(rows) if abs(p) >= band), None)}


class CalmDays:
    def __init__(self, rt):
        self.rt = rt

    def _closes(self, sym):
        m = self.rt.market

        def load():
            res = m._yh_chart(sym, "5y", "1d")
            off = (res.get("meta") or {}).get("gmtoffset") or 0
            cl = (((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
            return {datetime.fromtimestamp(t + off, timezone.utc).strftime("%Y-%m-%d"): c for t, c in zip(res.get("timestamp") or [], cl) if c}
        return m.cache.get(("calm-5y", sym), 12 * 3600, load, stale=72 * 3600)

    def view(self, years=3, band=1.0):
        years = max(1, min(5, int(years or 3)))
        band = float(band or 1.0)
        if band not in (0.5, 1.0, 2.0):
            band = 1.0
        since = (date.today() - timedelta(days=round(365.25 * years))).isoformat()
        out = []
        for mkt, name, sym in INDICES:
            try:
                rows = [(d, p) for d, p in changes(self._closes(sym)) if d >= since]
            except Exception as e:
                out.append({"mkt": mkt, "name": name, "sym": sym, "error": f"지수 기록을 받지 못했어요({type(e).__name__})"})
                continue
            if not rows:
                out.append({"mkt": mkt, "name": name, "sym": sym, "error": "지수 기록이 없어요"})
                continue
            out.append({"mkt": mkt, "name": name, "sym": sym, **summarize(rows, band)})
        return {"years": years, "band": band, "since": since, "indices": out}
