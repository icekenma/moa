"""분봉 백테스트. 실시간 엔진과 같은 전략·청산·비중·진입 규칙을 쓴다.

체결 가정
- 신호는 봉이 끝났을 때 보고, 다음 봉 시가에 슬리피지를 더해 산다.
- 같은 봉에서 손절과 익절이 모두 닿으면 손절로 친다(보수적).
- 손절·익절 가격에서 팔 때도 슬리피지를 뺀다.
"""
import csv
import io
from collections import defaultdict
from datetime import date, timedelta

from . import costs as C
from .risk import check_exit, edge_ok, entry_gate, position_size
from .strategy import signal


class DataError(ValueError):
    pass


def parse_csv(text):
    """CSV -> {날짜: [봉...]}. 열: date,time,open,high,low,close,volume (또는 datetime 한 열)."""
    rows = list(csv.reader(io.StringIO(text.strip())))
    if len(rows) < 2:
        raise DataError("CSV에 데이터가 없어요")
    head = [h.strip().lower() for h in rows[0]]

    def col(*names):
        for n in names:
            if n in head:
                return head.index(n)
        return None

    i_dt, i_d, i_t = col("datetime", "일시"), col("date", "일자", "날짜"), col("time", "시간")
    i_o, i_h, i_l = col("open", "시가"), col("high", "고가"), col("low", "저가")
    i_c, i_v = col("close", "종가", "현재가"), col("volume", "거래량")
    if None in (i_o, i_h, i_l, i_c, i_v) or (i_dt is None and (i_d is None or i_t is None)):
        raise DataError("열 이름이 맞지 않아요. date,time,open,high,low,close,volume 이 필요해요")
    days = defaultdict(list)
    for n, r in enumerate(rows[1:], start=2):
        if not r or not "".join(r).strip():
            continue
        try:
            if i_dt is not None:
                s = "".join(ch for ch in r[i_dt] if ch.isdigit())
                d, t = s[:8], s[8:12]
            else:
                d = "".join(ch for ch in r[i_d] if ch.isdigit())
                t = "".join(ch for ch in r[i_t] if ch.isdigit()).zfill(4)[:4]
            num = lambda k: abs(float(r[k].replace(",", "")))
            days[f"{d[:4]}-{d[4:6]}-{d[6:8]}"].append({
                "t": f"{t[:2]}:{t[2:4]}", "o": num(i_o), "h": num(i_h), "l": num(i_l),
                "c": num(i_c), "v": int(num(i_v))})
        except (ValueError, IndexError):
            raise DataError(f"{n}번째 줄을 읽지 못했어요")
    for d in days:
        days[d].sort(key=lambda b: b["t"])
        days[d] = [b for b in days[d] if "09:00" <= b["t"] < "15:30"]
    return {d: b for d, b in sorted(days.items()) if b}


def demo_dataset(codes, n_days, end=None):
    from ..broker.demo import demo_day
    end = end or date.today()
    days, d = [], end
    while len(days) < n_days:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            days.append(d)
    days.reverse()
    data = {}
    for code in codes:
        prev = None
        for d in days:
            bars = demo_day(code, d, prev)
            prev = bars[-1]["c"]
            data.setdefault(d.isoformat(), {})[code] = bars
    return data


def run(data, cfg, start_cash=None):
    """data: {날짜: {종목: [봉...]}}, cfg: 설정 전체(strategy, exit, risk, costs)."""
    st, ex, rk, cs = cfg["strategy"], cfg["exit"], dict(cfg["risk"]), cfg["costs"]
    cash = float(start_cash or rk["capital"])
    trades, daily = [], []
    equity, peak, mdd = cash, cash, 0.0
    for day in sorted(data):
        by_code = data[day]
        times = sorted({b["t"] for bars in by_code.values() for b in bars})
        idx = {code: {b["t"]: i for i, b in enumerate(bars)} for code, bars in by_code.items()}
        open_pos, closed, done_codes, pending = {}, [], set(), {}
        day_start = cash
        for t in times:
            # 1) 직전 봉에서 나온 신호를 이 봉 시가에 체결
            for code, sig in list(pending.items()):
                i = idx[code].get(t)
                if i is None:
                    continue
                bar = by_code[code][i]
                fill = C.buy_fill(bar["o"], cs)
                del pending[code]
                if fill <= sig["stop"]:
                    continue
                qty = position_size(fill, sig["stop"], cash, rk, cs)
                if qty <= 0:
                    continue
                cash -= fill * qty + C.buy_fee(fill * qty, cs)
                open_pos[code] = {"entry": fill, "stop": sig["stop"], "stop0": sig["stop"],
                                  "target": fill + sig["r"] * (fill - sig["stop"]), "entry_t": t, "qty": qty,
                                  "reason_in": sig["reason"]}
            # 2) 보유 종목 청산 확인(이 봉의 고가·저가)
            for code, pos in list(open_pos.items()):
                i = idx[code].get(t)
                if i is None:
                    continue
                bar = by_code[code][i]
                hit = check_exit(pos, bar["h"], bar["l"], bar["c"], t, ex, rk)
                if hit:
                    reason, px = hit
                    if reason in ("손절", "본전 청산") and bar["o"] < pos["stop"]:
                        px = bar["o"]  # 갭으로 손절가 아래에서 시작하면 시가에 판다
                    fill = C.sell_fill(px, cs)
                    pnl, fees = C.trade_pnl(pos["entry"], fill, pos["qty"], cs)
                    cash += fill * pos["qty"] - C.sell_fee(fill * pos["qty"], cs)
                    trades.append({"date": day, "code": code, "qty": pos["qty"], "entry_t": pos["entry_t"],
                                   "exit_t": t, "entry": round(pos["entry"]), "exit": round(fill),
                                   "pnl": pnl, "fees": fees, "reason": reason, "why": pos["reason_in"]})
                    closed.append({"pnl": pnl, "exit_t": t})
                    del open_pos[code]
            # 3) 이 봉이 끝난 시점의 신호
            for code, bars in by_code.items():
                i = idx[code].get(t)
                if i is None or code in open_pos or code in done_codes or code in pending:
                    continue
                ok, _ = entry_gate(t, closed, len(open_pos) + len(pending), rk)
                if not ok:
                    continue
                sig = signal(bars[:i + 1], st)
                if not sig:
                    continue
                if not edge_ok(sig, rk, cs)[0]:
                    continue
                pending[code] = sig
                done_codes.add(code)
        # 장 끝까지 남은 포지션은 마지막 종가에 정리
        for code, pos in open_pos.items():
            last = by_code[code][-1]
            fill = C.sell_fill(last["c"], cs)
            pnl, fees = C.trade_pnl(pos["entry"], fill, pos["qty"], cs)
            cash += fill * pos["qty"] - C.sell_fee(fill * pos["qty"], cs)
            trades.append({"date": day, "code": code, "qty": pos["qty"], "entry_t": pos["entry_t"],
                           "exit_t": last["t"], "entry": round(pos["entry"]), "exit": round(fill),
                           "pnl": pnl, "fees": fees, "reason": "장마감 청산", "why": pos["reason_in"]})
        equity = cash
        peak = max(peak, equity)
        mdd = max(mdd, (peak - equity) / peak * 100 if peak else 0)
        daily.append({"date": day, "pnl": int(round(cash - day_start)), "equity": int(round(equity))})
    return {"trades": trades, "daily": daily, "stats": stats(trades, daily, start_cash or rk["capital"], mdd)}


def stats(trades, daily, start, mdd):
    n = len(trades)
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    net = sum(t["pnl"] for t in trades)
    by_reason = defaultdict(int)
    for t in trades:
        by_reason[t["reason"]] += 1
    return {
        "days": len(daily), "trades": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n * 100, 1) if n else 0,
        "net": net, "net_pct": round(net / start * 100, 2) if start else 0,
        "fees": sum(t["fees"] for t in trades),
        "avg_win": int(gross_win / len(wins)) if wins else 0,
        "avg_loss": int(-gross_loss / len(losses)) if losses else 0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "expectancy": int(net / n) if n else 0,
        "mdd_pct": round(mdd, 2),
        "positive_days": sum(1 for d in daily if d["pnl"] > 0),
        "by_reason": dict(by_reason),
    }
