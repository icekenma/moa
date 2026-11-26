"""규칙 추천 과거 시험(v5.60): AI를 부르지 않고, 규칙 추천(스윙)의 고르는 방식을 지난 일봉에 날짜별로 돌려 본다.

- 사용자 요청(2026-11-26): 'API를 안 써도 거래 테스트가 가능하게'. 비용 0, 주문을 내지 않는다(계산만).
- 고르기: 자동매매의 규칙 추천과 같은 점수(aipick.swing_score)와 같은 목표·손절(손절 = 일봉 흔들림 기준, 목표 = 손절 × RULE_RR).
  그날 장이 끝난 뒤의 일봉까지만 보고(미래 자료 없음) 다음 날 시가에 산다. 채점은 추천 성적표와 같은 aipick.judge(일봉, 갭 반영).
- 돈: 굴리는 돈 BUDGET, 동시 보유 MAX_POS종목, 한 종목에 PER_PCT%. 수수료·세금은 왕복 FEE.
- 기준선: 같은 날 점수를 받은 후보 '모두'를 같은 목표·손절로 샀다면의 거래당 평균. 규칙의 줄 세우기가 보탬이 되는지 본다.
- 한계(화면에 적는다): 종목은 '지금' 거래대금이 큰 종목이라 그 사이 사라진 종목이 빠진다(생존 편향). 학습 가산점(learn_score)은 미래 정보라 쓰지 않는다.
  단타(분봉)는 여기서 하지 않는다 — 단타 규칙은 '백테스트·조건 훑기'(키움 1분봉)에서 본다.
"""
import threading
import time
import uuid
from datetime import datetime

from . import aipick as AP

DAYS = 250        # 시험하는 거래일 수(약 1년)
TOP = 80          # 거래대금 위에서 이만큼 종목
HOLD = AP.HORIZONS["swing"]["days"]   # 최대 보유 거래일
PICKS = 3         # 하루에 고르는 수(규칙 추천과 같음)
MAX_POS = 4       # 동시 보유(스윙 플랜 기본)
PER_PCT = 25.0    # 한 종목에 쓰는 비율(스윙 플랜 기본)
TP_CAP, SL_CAP = 12.0, 6.0             # 스윙 플랜 기본 상한
BUDGET = 10_000_000
WARM = 65         # 특징값을 셈하려면 이만큼 앞선 일봉이 있어야 한다


def _agg(ts, budget):
    n = len(ts)
    if not n:
        return {"n": 0, "win": None, "avg": None, "pnl": 0, "ret": 0.0, "mdd": 0.0, "pf": None}
    eq = peak = mdd = 0.0
    for t in sorted(ts, key=lambda t: t["exit_day"]):
        eq += t["pnl"]
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    gain = sum(t["pnl"] for t in ts if t["pnl"] > 0)
    loss = -sum(t["pnl"] for t in ts if t["pnl"] < 0)
    pnl = sum(t["pnl"] for t in ts)
    return {"n": n, "win": round(sum(1 for t in ts if t["net"] > 0) / n * 100), "avg": round(sum(t["net"] for t in ts) / n, 2),
            "pnl": round(pnl), "ret": round(pnl / budget * 100, 2), "mdd": round(mdd / budget * 100, 2), "pf": round(gain / loss, 2) if loss else None}


def exits(f):
    """규칙 추천(스윙)과 같은 손절·목표(%), 스윙 플랜 상한까지."""
    _, sl = AP.swing_exits((f or {}).get("하루변동폭%"), "swing")
    sl = min(sl, SL_CAP)
    return round(min(sl * AP.RULE_RR, TP_CAP), 2), round(sl, 2)


def simulate(B, names=None, days=DAYS, fee=AP.FEE["kr"], budget=BUDGET, keep_all=False):
    """B: {종목: [{t:'YYYY-MM-DD', o,h,l,c,v}] 시간순}. 날마다 장이 끝난 뒤 점수로 줄 세워 다음 날 시가에 산다."""
    names = names or {}
    idx = {s: {b["t"][:10]: i for i, b in enumerate(bs)} for s, bs in B.items()}
    dates = sorted({d for m in idx.values() for d in m})
    test = dates[-(days + HOLD + 1):-(HOLD + 1)] if len(dates) > HOLD + 1 else []
    trades, base, held, realized = [], [], {}, 0.0      # held: 종목 → (파는 날, 손익). 판 날이 지나야 손익을 굴리는 돈에 더한다
    for D in test:
        for s in [s for s, x in held.items() if x[0] <= D]:
            realized += held.pop(s)[1]
        cands = []
        for s, bs in B.items():
            i = idx[s].get(D)
            if i is None or i < WARM or i + 1 >= len(bs):
                continue
            f = AP.daily_features(bs[max(0, i - 89):i + 1])
            cp = (bs[i]["c"] / bs[i - 1]["c"] - 1) * 100 if bs[i - 1]["c"] else 0
            sc = AP.swing_score(f, cp)
            if sc is None:
                continue
            tp, sl = exits(f)
            fw = bs[i + 1:i + 1 + HOLD]
            r = AP.judge(fw, fw[0]["o"], tp, sl, gap=True)
            if not r or not r[1]:
                continue
            res, px, out, _, _, _, n = r
            cands.append({"sym": s, "sc": sc, "tp": tp, "sl": sl, "res": res, "px": px, "out": out, "net": (out / px - 1) * 100 - fee,
                          "entry_day": fw[0]["t"][:10], "exit_day": fw[min(n, len(fw)) - 1]["t"][:10]})
        if not cands:
            continue
        base += [c["net"] for c in cands]
        cands.sort(key=lambda c: -c["sc"])
        room = MAX_POS - len(held)
        for k, c in enumerate([c for c in cands if c["sym"] not in held][:min(PICKS, max(0, room))]):
            alloc = (budget + realized) * PER_PCT / 100
            qty = int(alloc // (c["px"] * 1.002))
            if qty < 1:
                continue
            pnl = qty * c["px"] * c["net"] / 100
            held[c["sym"]] = (c["exit_day"], pnl)
            trades.append({"day": D, "sym": c["sym"], "name": names.get(c["sym"]) or c["sym"], "rank": k + 1, "result": c["res"],
                           "entry_day": c["entry_day"], "exit_day": c["exit_day"], "entry": round(c["px"], 2), "exit": round(c["out"], 2),
                           "tp": c["tp"], "sl": c["sl"], "net": round(c["net"], 2), "pnl": round(pnl), "qty": qty})
    cut = int(len(trades) * 0.7)
    by = {}
    for t in trades:
        by[t["result"]] = by.get(t["result"], 0) + 1
    ba = round(sum(base) / len(base), 2) if base else None
    A = _agg(trades, budget)
    return {"from": test[0] if test else None, "to": test[-1] if test else None, "days": len(test), "stocks": len(B),
            "all": A, "first": _agg(trades[:cut], budget), "last": _agg(trades[cut:], budget),
            "base": {"n": len(base), "avg": ba}, "edge": round(A["avg"] - ba, 2) if A["n"] and ba is not None else None,
            "results": by, "trades": trades if keep_all else trades[-30:][::-1],
            "plan": {"hold": HOLD, "picks": PICKS, "max_pos": MAX_POS, "per_pct": PER_PCT, "rr": AP.RULE_RR, "fee": fee, "budget": budget}}


class RuleBT:
    def __init__(self, rt):
        self.rt = rt
        self.jobs = {}
        self._lock = threading.Lock()

    def last(self):
        return self.rt.db.get_setting("rulebt_last")

    def start(self, days=DAYS, top=TOP):
        days, top = max(40, min(450, int(days or DAYS))), max(20, min(150, int(top or TOP)))
        if self.rt.market.kr_client() is None:
            raise ValueError("키움 계좌를 연결해야 일봉을 받을 수 있어요")
        with self._lock:
            for j in self.jobs.values():
                if j["state"] == "running" and time.time() - j["t0"] < 900:
                    raise ValueError("이미 시험하는 중이에요. 끝나면 다시 눌러 주세요")
            jid = uuid.uuid4().hex[:10]
            self.jobs = {k: v for k, v in self.jobs.items() if time.time() - v["t0"] < 3600}
            self.jobs[jid] = {"id": jid, "state": "running", "t0": time.time(), "msg": "종목을 고르는 중", "res": None, "error": None}

        def go():
            j = self.jobs[jid]
            try:
                j["res"] = self.run(days, top, j)
                j["state"] = "done"
            except Exception as e:
                j["error"], j["state"] = (str(e) if isinstance(e, ValueError) else f"시험하지 못했어요({type(e).__name__}: {e})"), "error"
        threading.Thread(target=go, name="moa-rulebt", daemon=True).start()
        return {"id": jid, "state": "running"}

    def job(self, jid):
        j = self.jobs.get(jid)
        if not j:
            raise ValueError("작업을 찾지 못했어요. 다시 눌러 주세요")
        return {"id": jid, "state": j["state"], "msg": j["msg"], "error": j["error"], "res": j["res"], "sec": round(time.time() - j["t0"])}

    def run(self, days, top, j=None):
        cl = self.rt.market.kr_client()
        if cl is None:
            raise ValueError("키움 계좌를 연결해야 일봉을 받을 수 있어요")
        uni, src = self.rt.prep._universe(cl)
        uni = sorted(uni, key=lambda x: -(x.get("value") or 0))[:top]
        B, names = {}, {}
        for i, u in enumerate(uni):
            if j is not None:
                j["msg"] = f"일봉을 받는 중 {i + 1}/{len(uni)}"
            try:
                bs = [b for b in (self.rt.invest.candles("kr", u["sym"], "d", 600) or {}).get("bars") or [] if b.get("c") and b.get("o")]
            except Exception:
                bs = []
            if len(bs) > WARM + HOLD + 5:
                B[u["sym"]], names[u["sym"]] = bs, u.get("name") or u["sym"]
        if not B:
            raise ValueError("일봉을 받은 종목이 없어요")
        if j is not None:
            j["msg"] = f"{len(B)}종목으로 날마다 골라 보는 중"
        res = simulate(B, names, days)
        res.update(src=src, at=datetime.now().isoformat(timespec="seconds"))
        self.rt.db.set_setting("rulebt_last", res)
        return res
