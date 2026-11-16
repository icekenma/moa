"""단타 진입 전략. 분봉(완성된 봉) 목록을 받아 진입 신호를 낸다.

봉 형식: {"t": "HH:MM", "o": float, "h": float, "l": float, "c": float, "v": int}
신호 형식: {"entry", "stop", "target", "r", "reason"}
목표가는 실제 체결가 기준으로 다시 잡는다: target = 체결가 + r × (체결가 - 손절가)
백테스트와 실시간 엔진이 같은 함수를 쓴다.
"""
from .costs import tick_size


def vwap(bars):
    pv = vol = 0.0
    for b in bars:
        pv += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
        vol += b["v"]
    return pv / vol if vol else bars[-1]["c"]


def atr(bars, n):
    if len(bars) < 2:
        return bars[-1]["h"] - bars[-1]["l"]
    trs = []
    for i in range(max(1, len(bars) - n), len(bars)):
        b, pc = bars[i], bars[i - 1]["c"]
        trs.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)))
    return sum(trs) / len(trs)


def orb_vwap(bars, p):
    """시가범위 돌파: 장 시작 N분의 고가를 거래량을 동반해 처음 넘을 때, 가격이 VWAP 위일 때만."""
    rm = int(p["range_min"])
    if len(bars) <= rm + 1:
        return None
    rng = bars[:rm]
    hi = max(b["h"] for b in rng)
    lo = min(b["l"] for b in rng)
    last, prev = bars[-1], bars[-2]
    if lo <= 0 or (hi - lo) / lo * 100 < p["min_range_pct"]:
        return None
    if not (last["c"] > hi >= prev["c"]):
        return None
    vw = vwap(bars)
    if last["c"] <= vw:
        return None
    look = bars[max(0, len(bars) - 21):-1]
    avg_v = sum(b["v"] for b in look) / len(look)
    if avg_v <= 0 or last["v"] < p["vol_mult"] * avg_v:
        return None
    if (last["c"] / bars[0]["o"] - 1) * 100 > p["max_day_pct"]:
        return None
    a = atr(bars, int(p["atr_n"]))
    stop = max(last["c"] - p["stop_atr"] * a, lo)
    risk = last["c"] - stop
    if risk <= 0:
        return None
    return {
        "entry": last["c"], "stop": stop, "target": last["c"] + p["target_r"] * risk, "r": p["target_r"],
        "reason": f"시가범위 {rm}분 고가 {hi:,.0f} 돌파, 거래량 {last['v'] / avg_v:.1f}배",
    }


def vwap_pullback(bars, p):
    """눌림 반등: 오늘 충분히 오른 종목이 VWAP 근처까지 눌렸다가 직전 봉 고가를 넘어설 때."""
    n = len(bars)
    lb = int(p["lookback"])
    if n < max(int(p["min_bars"]), lb + 2):
        return None
    last, prev = bars[-1], bars[-2]
    day_pct = (last["c"] / bars[0]["o"] - 1) * 100
    if day_pct < p["min_day_pct"] or day_pct > p["max_day_pct"]:
        return None
    vw = vwap(bars)
    recent = bars[-lb - 1:-1]
    low = min(b["l"] for b in recent)
    if low > vw * (1 + p["band_pct"] / 100):
        return None
    if not (last["c"] > prev["h"] and last["c"] > vw):
        return None
    stop = low - tick_size(low)
    risk = last["c"] - stop
    if risk <= 0:
        return None
    return {
        "entry": last["c"], "stop": stop, "target": last["c"] + p["target_r"] * risk, "r": p["target_r"],
        "reason": f"VWAP {vw:,.0f} 근처 눌림 후 반등, 당일 {day_pct:+.1f}%",
    }


STRATEGIES = {
    "orb_vwap": {"label": "시가범위 돌파", "fn": orb_vwap},
    "vwap_pullback": {"label": "VWAP 눌림 반등", "fn": vwap_pullback},
}


def signal(bars, strategy_cfg):
    name = strategy_cfg["name"]
    return STRATEGIES[name]["fn"](bars, strategy_cfg[name])
