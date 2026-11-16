"""청산 규칙, 비중 계산, 진입 허용 판단. 백테스트와 실시간 엔진이 함께 쓴다."""
import math

from .costs import round_trip_pct


def hm(t):
    """'HH:MM' -> 분."""
    h, m = t.split(":")[:2]
    return int(h) * 60 + int(m)


def check_exit(pos, high, low, close, now_t, exit_cfg, risk_cfg):
    """보유 포지션의 청산 여부. 손절을 익절보다 먼저 본다(같은 봉에서 둘 다 닿으면 손절로 처리).

    pos: {"entry", "stop", "stop0", "target", "entry_t"} — stop은 이 함수가 본전으로 올릴 수 있다.
    반환: (사유, 가격) 또는 None
    """
    if low <= pos["stop"]:
        reason = "본전 청산" if pos["stop"] >= pos["entry"] else "손절"
        return reason, pos["stop"]
    if high >= pos["target"]:
        return "익절", pos["target"]
    if hm(now_t) >= hm(risk_cfg["force_exit"]):
        return "장마감 청산", close
    if exit_cfg["max_hold_min"] and hm(now_t) - hm(pos["entry_t"]) >= exit_cfg["max_hold_min"]:
        return "시간 청산", close
    be = exit_cfg["breakeven_r"]
    if be and high >= pos["entry"] + be * (pos["entry"] - pos["stop0"]):
        pos["stop"] = max(pos["stop"], pos["entry"])
    return None


def position_size(entry, stop, cash, risk_cfg, costs):
    """1회 손실이 자본의 risk_pct%가 되도록 수량을 정한다."""
    per_share = entry - stop
    if per_share <= 0 or entry <= 0:
        return 0
    risk_amt = risk_cfg["capital"] * risk_cfg["risk_pct"] / 100
    qty = math.floor(risk_amt / per_share)
    qty = min(qty, math.floor(risk_cfg["max_position_krw"] / entry))
    qty = min(qty, math.floor(cash / (entry * (1 + costs["commission"]))))
    return max(0, qty)


def edge_ok(sig, risk_cfg, costs):
    """목표 수익이 왕복 비용의 min_edge_mult배 이상일 때만 진입."""
    gain_pct = (sig["target"] - sig["entry"]) / sig["entry"] * 100
    need = round_trip_pct(sig["entry"], costs) * risk_cfg["min_edge_mult"]
    return gain_pct >= need, gain_pct, need


def entry_gate(now_t, closed_today, open_count, risk_cfg):
    """오늘 더 진입해도 되는지. closed_today: 시간순 [{"pnl", "exit_t"}]. 반환: (허용, 사유)"""
    t = hm(now_t)
    if t < hm(risk_cfg["entry_start"]) or t > hm(risk_cfg["entry_end"]):
        return False, "진입 시간이 아니에요"
    realized = sum(c["pnl"] for c in closed_today)
    if realized <= -abs(risk_cfg["daily_loss_krw"]):
        return False, "하루 손실 한도에 닿았어요"
    if open_count >= risk_cfg["max_positions"]:
        return False, "동시 보유 종목 수가 꽉 찼어요"
    if len(closed_today) + open_count >= risk_cfg["max_trades"]:
        return False, "오늘 거래 횟수를 다 썼어요"
    k = int(risk_cfg["loss_streak"])
    if k and len(closed_today) >= k and all(c["pnl"] < 0 for c in closed_today[-k:]):
        if t - hm(closed_today[-1]["exit_t"]) < risk_cfg["cooldown_min"]:
            return False, f"연속 손절 {k}번이라 쉬는 중이에요"
    return True, ""
