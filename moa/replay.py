"""다시 돌려 보기: 지난 실제 AI 추천을 지금(또는 바꾼) 플랜 값으로 다시 사고팔아 본다.

- 추천은 그때 실제로 받은 것(ai_picks)과 그때의 판단 기록(at_log)을 그대로 쓴다 → AI를 다시 부르지 않고(비용 0), 미래 정보가 섞이지 않는다.
- 가격 경로: 단타는 그날 5분봉(채점 때 받아 둔 것 → rp_bars, 없으면 지금 받아 보고, 그래도 없으면 그날 일봉으로 순서 추정),
  스윙·장기는 일봉. 사고파는 판정은 추천 성적표와 같은 aipick.judge(한 봉에서 둘 다 닿으면 봉 모양으로 순서 추정).
- 다시 돌리는 플랜 값: 승률·점수·손익비·익절/손절 상한·배수·추격 허용·매수 시각·관망 쉬기/크기·동시 보유·1회 비중·하루 손실 한도·연속 손절 쉬기.
  그 밖의 값(분할 매수·매도, 흔들림 맞춤, 쏠림, 위험 미리 막기, 눌림 기다리기 등)은 다시 돌리지 않는다(화면에 적는다).
- 주문은 내지 않는다. 계산만 한다.
"""
import json
import math
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone

from . import aipick as AP

KEYS = ("min_win_prob", "min_score", "min_rr", "tp_cap", "sl_cap", "exit_mult", "chase_pct", "entry_start", "entry_end",
        "skip_watch", "watch_mult", "max_positions", "per_trade_pct", "daily_loss_pct", "loss_streak")
NOT_REPLAYED = ("scale_out", "scale_in", "vol_stop", "group_max", "corr_max", "var_guard", "counter_check", "hold_watch",
                "pull_wait", "trigger_buy", "edge_size", "regime_size", "risk_pct", "idx_floor", "early_call")
# 추천 횟수·간격(cycles_per_day·cycle_gap_min·quick_gap_min)은 그때 받은 추천을 그대로 쓰므로 바꿔 볼 수 없다
KEEP_SKIP = ("junk", "counter", "crowd", "quote", "market")     # 플랜 값과 상관없이 그때 걸러진 이유는 그대로
FETCH_MAX = 80                                                 # 한 번 돌릴 때 새로 받는 시세 수(키움·야후 부담)


def _et(ts):
    u = ts - timedelta(hours=9)
    off = -4 if AP._us_dst(u.replace(tzinfo=timezone.utc)) else -5
    return u + timedelta(hours=off)


def local_hm(mkt, ts):
    return (ts if mkt == "kr" else _et(ts)).strftime("%H:%M")


def stats(trades, budget):
    """끝난 가상 거래 성적. 최대 낙폭은 굴리는 돈 대비(%)."""
    n = len(trades)
    if not n:
        return {"n": 0, "win": None, "avg": None, "pnl": 0, "pf": None, "mdd": 0.0, "ret": 0.0}
    gain = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    loss = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    eq, peak, mdd = 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda t: t["exit_dt"]):
        eq += t["pnl"]
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    pnl = sum(t["pnl"] for t in trades)
    return {"n": n, "win": round(sum(1 for t in trades if t["pnl"] > 0) / n * 100), "avg": round(sum(t["net"] for t in trades) / n, 2),
            "pnl": round(pnl, 2), "pf": round(gain / loss, 2) if loss else None, "mdd": round(mdd / budget * 100, 2) if budget else 0.0,
            "ret": round(pnl / budget * 100, 2) if budget else 0.0}


class Replay:
    def __init__(self, rt):
        self.rt = rt
        rt.db.x("""CREATE TABLE IF NOT EXISTS rp_bars(mkt TEXT NOT NULL, sym TEXT NOT NULL, day TEXT NOT NULL, tf TEXT NOT NULL,
                bars TEXT NOT NULL, src TEXT, at TEXT NOT NULL, PRIMARY KEY(mkt, sym, day, tf))""")
        self.jobs = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 시세 보관
    def keep_m5(self, mkt, sym, bars, ref):
        """채점 때 받은 5분봉(한국 시간 'MM/DD HH:MM')을 날짜별로 남겨 둔다(다음 날이면 사라지므로). 이미 있으면 합친다."""
        by = {}
        for b in bars or []:
            dt = AP._bar_dt(b.get("t") or "", ref)
            if dt and b.get("c"):
                by.setdefault(dt.strftime("%Y-%m-%d"), {})[dt.isoformat(timespec="minutes")] = [b["o"], b["h"], b["l"], b["c"]]
        now = datetime.now().isoformat(timespec="seconds")
        for d, m in by.items():
            old = self.rt.db.one("SELECT bars FROM rp_bars WHERE mkt=? AND sym=? AND day=? AND tf='m5'", (mkt, sym.upper(), d))
            if old:
                m = {**json.loads(old["bars"]), **m}
            self.rt.db.x("INSERT OR REPLACE INTO rp_bars(mkt,sym,day,tf,bars,src,at) VALUES(?,?,?,?,?,?,?)",
                         (mkt, sym.upper(), d, "m5", json.dumps(m, separators=(",", ":")), "5분봉", now))

    def _m5(self, mkt, sym, ts, budget):
        """그날(한국 시간 날짜, 미국은 정규장이 걸친 두 날) 5분봉 [(dt, o,h,l,c)]."""
        days = {ts.strftime("%Y-%m-%d"), (ts + timedelta(days=1)).strftime("%Y-%m-%d")}
        rows = self.rt.db.q(f"SELECT bars FROM rp_bars WHERE mkt=? AND sym=? AND tf='m5' AND day IN ({','.join('?' * len(days))})", (mkt, sym.upper(), *days))
        if not rows and budget[0] > 0:
            budget[0] -= 1
            try:
                self.keep_m5(mkt, sym, self.rt.invest.candles(mkt, sym, "m5").get("bars") or [], ts)
                rows = self.rt.db.q(f"SELECT bars FROM rp_bars WHERE mkt=? AND sym=? AND tf='m5' AND day IN ({','.join('?' * len(days))})", (mkt, sym.upper(), *days))
            except Exception:
                rows = []
        out = {}
        for r in rows:
            out.update(json.loads(r["bars"]))
        return sorted((datetime.fromisoformat(k), *v) for k, v in out.items())

    def _daily(self, mkt, sym, need_to, budget):
        """일봉 {날짜: (o,h,l,c)} — 12시간 안에 받은 것이 있고 필요한 날까지 있으면 그대로."""
        r = self.rt.db.one("SELECT bars, at FROM rp_bars WHERE mkt=? AND sym=? AND day='*' AND tf='d'", (mkt, sym.upper()))
        fresh = r and (datetime.now() - datetime.fromisoformat(r["at"])) < timedelta(hours=12)
        have = json.loads(r["bars"]) if r else {}
        if (not r or (not fresh and max(have or {"": 0}) < need_to)) and budget[0] > 0:
            budget[0] -= 1
            try:
                bs = self.rt.invest.candles(mkt, sym, "d", 600).get("bars") or []
                new = {b["t"][:10]: [b["o"], b["h"], b["l"], b["c"]] for b in bs if b.get("c") and b.get("t")}
                if new:
                    have = {**have, **new}
                    self.rt.db.x("INSERT OR REPLACE INTO rp_bars(mkt,sym,day,tf,bars,src,at) VALUES(?,?,?,?,?,?,?)",
                                 (mkt, sym.upper(), "*", "d", json.dumps(have, separators=(",", ":")), "일봉", datetime.now().isoformat(timespec="seconds")))
            except Exception:
                pass
        return {k: tuple(v) for k, v in have.items()}

    # ------------------------------------------------------------------ 돌릴 거리
    def sources(self):
        """다시 돌릴 수 있는 자동매매: 직접 만든 것 + 실험 묶음(맨 앞 플랜이 받은 추천을 같이 씀)."""
        at = self.rt.autotrade
        from .autotrade import PLAN_KEYS, STYLES
        out, seen = [], set()
        cnt = {r["run_id"]: (r["n"], r["d0"], r["d1"]) for r in self.rt.db.q(
            "SELECT run_id, COUNT(*) n, MIN(day) d0, MAX(day) d1 FROM at_log WHERE kind='cycle' AND data LIKE '%pick_id%' GROUP BY run_id")}
        for r in at._runs():
            if r.get("lab_id") and r.get("variant") != "base":
                continue
            if r["id"] not in cnt or (r.get("lab_id") and r["lab_id"] in seen):
                continue
            if r.get("lab_id"):
                seen.add(r["lab_id"])
            n, d0, d1 = cnt[r["id"]]
            mk = "국내" if r["mkt"] == "kr" else "미국"
            out.append({"id": r["id"], "label": f"{mk} {STYLES[r['style']]['label']} 실험(공통 추천)" if r.get("lab_id") else r["name"],
                        "mkt": r["mkt"], "style": r["style"], "mode": r["mode"], "status": r["status"], "lab": bool(r.get("lab_id")),
                        "cycles": n, "from": d0, "to": d1, "budget": r["budget"],
                        "params": {k: r["params"].get(k) for k in KEYS},
                        "keys": [{"key": k, "label": PLAN_KEYS[k][3], "lo": PLAN_KEYS[k][0], "hi": PLAN_KEYS[k][1], "type": PLAN_KEYS[k][2].__name__ if callable(PLAN_KEYS[k][2]) else PLAN_KEYS[k][2]} for k in KEYS]})
        out.sort(key=lambda s: (s["status"] == "ended", -s["cycles"]))
        return out

    def _cycles(self, run, days):
        since = (date.today() - timedelta(days=int(days) - 1)).isoformat()
        seen, out = set(), []
        for r in self.rt.db.q("SELECT ts, day, msg, data FROM at_log WHERE run_id=? AND kind='cycle' AND day>=? AND data IS NOT NULL ORDER BY id", (run["id"], since)):
            try:
                d = json.loads(r["data"])
            except ValueError:
                continue
            pid = d.get("pick_id")
            if not pid or pid in seen or "멈춘" in (r["msg"] or ""):
                continue
            seen.add(pid)
            out.append((r, d))
        picks = {}
        ids = [d["pick_id"] for _, d in out]
        for i in range(0, len(ids), 400):
            part = ids[i:i + 400]
            for p in self.rt.db.q(f"SELECT id, ts, stance, picks, horizon FROM ai_picks WHERE id IN ({','.join('?' * len(part))})", part):
                picks[p["id"]] = p
        return [(r, d, picks[d["pick_id"]]) for r, d in out if d["pick_id"] in picks]

    # ------------------------------------------------------------------ 가상 매매
    def simulate(self, run, P, cyc, budget, fetch):
        """cyc: _cycles 결과. P: 플랜 값. 반환: (거래, 거른 이유별 수, 자료 상태별 수)."""
        mkt, style = run["mkt"], run["style"]
        fee = AP.FEE[mkt]
        trades, opens, skips, srcs = [], [], {}, {}
        day_pnl, streak = {}, {}
        h = style if style in AP.HORIZONS else "day"

        def skip(c):
            skips[c] = skips.get(c, 0) + 1
        for r, d, pk in cyc:
            ts = datetime.fromisoformat(r["ts"][:19])
            day = r["day"] or ts.strftime("%Y-%m-%d")
            opens = [t for t in opens if t["exit_dt"] > ts]
            stance = pk.get("stance") or d.get("stance")
            orig = {str(q.get("sym") or "").upper(): q for q in d.get("decisions") or []}
            xs = json.loads(pk["picks"] or "[]")
            if P.get("skip_watch") and stance == "관망":
                for _ in xs:
                    skip("watch")
                continue
            hm = local_hm(mkt, ts)
            in_win = (P.get("entry_start") or "00:00") <= hm <= (P.get("entry_end") or "23:59") if style == "day" else True
            realized = sum(t["pnl"] for t in trades if t["exit_dt"] <= ts and t["day"] == day)
            for x in xs:
                sym = str(x.get("sym") or "").upper()
                o = orig.get(sym) or {}
                if x.get("auto"):
                    skip("auto"); continue
                if o.get("act") == "skip" and o.get("code") in KEEP_SKIP:
                    skip(o["code"]); continue
                if not in_win:
                    skip("time"); continue
                if realized <= -budget * float(P["daily_loss_pct"]) / 100:
                    skip("dayloss"); continue
                if streak.get(day, 0) >= int(P.get("loss_streak") or 99):
                    skip("streak"); continue
                if len({t["sym"] for t in opens}) >= int(P["max_positions"]):
                    skip("full"); continue
                wp = x.get("cal_prob") if x.get("cal_prob") is not None and (x.get("cal_n") or 0) >= 10 else x.get("win_prob")
                if (wp or 0) < float(P["min_win_prob"]):
                    skip("winprob"); continue
                if (x.get("score") or 0) < float(P["min_score"]):
                    skip("score"); continue
                em = float(P.get("exit_mult") or 1.0)
                tp = min(round(float(x.get("target_pct") or 0) * em, 2), float(P["tp_cap"]))
                sl = min(round(float(x.get("stop_pct") or 0) * em, 2), float(P["sl_cap"]))
                if tp <= 0 or sl <= 0 or tp / sl < float(P["min_rr"]):
                    skip("rr"); continue
                if tp < fee * 3:
                    skip("fee"); continue
                if any(t["sym"] == sym and (t["day"] == day) for t in trades + opens):
                    skip("dup"); continue
                ref = float(x.get("entry_price") or x.get("price") or 0)
                # 가격 경로
                if h == "day":
                    end = AP.window_end(mkt, ts, x.get("exit_by") or "")
                    m5 = [b for b in self._m5(mkt, sym, ts, fetch) if ts.replace(second=0) - timedelta(minutes=4) <= b[0] < end]
                    if m5:
                        src = "5분봉"
                        first = m5[0]
                        price = first[4] if first[0] < ts.replace(second=0) else first[1]
                        bars = [{"o": b[1], "h": b[2], "l": b[3], "c": b[4], "dt": b[0]} for b in m5]
                        if bars and bars[0]["dt"] < ts.replace(second=0):
                            bars[0] = dict(bars[0], o=bars[0]["c"], h=bars[0]["c"], l=bars[0]["c"])
                    else:
                        dd = self._daily(mkt, sym, day, fetch).get(day)
                        if not dd:
                            skip("nodata"); srcs["자료 없음"] = srcs.get("자료 없음", 0) + 1; continue
                        src = "일봉(순서 추정)"
                        price = float(x.get("price") or ref)
                        bars = [{"o": dd[0], "h": dd[1], "l": dd[2], "c": dd[3], "dt": end}]
                    if ref and price > ref * (1 + float(P["chase_pct"]) / 100):
                        skip("chase"); continue
                else:
                    ph, d0 = AP.phase_at(mkt, ts)
                    end_d = AP.swing_end(mkt, ts, AP.HORIZONS[h]["days"])[1].isoformat()
                    dly = self._daily(mkt, sym, end_d, fetch)
                    d0s = d0.isoformat()
                    later = sorted(k for k in dly if (k >= d0s if ph == "pre" else k > d0s) and k <= end_d)
                    price = float(x.get("price") or ref)
                    if not later or not price:
                        skip("nodata"); srcs["자료 없음"] = srcs.get("자료 없음", 0) + 1; continue
                    src = "일봉"
                    bars = [{"o": dly[k][0], "h": dly[k][1], "l": dly[k][2], "c": dly[k][3], "dt": self._close_dt(mkt, k)} for k in later]
                    if ref and price > ref * (1 + float(P["chase_pct"]) / 100):
                        skip("chase"); continue
                res = AP.judge(bars, price, tp, sl, "market", gap=h != "day")
                if not res or not res[1]:
                    skip("nodata"); continue
                result, entry, exit_, mfe, mae, _, nbar = res
                mult = float(P.get("watch_mult") or 0.5) if stance == "관망" else 1.0
                used = sum(t["alloc"] for t in opens)
                alloc = min(budget * float(P["per_trade_pct"]) / 100 * mult, budget - used)
                qty = math.floor(alloc / entry) if entry else 0
                if qty <= 0:
                    skip("money"); continue
                ret = (exit_ / entry - 1) * 100
                net = ret - fee
                pnl = qty * entry * net / 100
                t = {"sym": sym, "name": x.get("name") or sym, "day": day, "ts": ts.isoformat(timespec="minutes"), "hm": hm,
                     "exit_dt": bars[min(nbar, len(bars)) - 1]["dt"], "entry": round(entry, 4), "exit": round(exit_, 4), "qty": qty,
                     "alloc": qty * entry, "tp": tp, "sl": sl, "result": result, "net": round(net, 2), "pnl": round(pnl, 2),
                     "wp": wp, "score": x.get("score"), "src": src, "orig": o.get("act") or "", "mfe": round(mfe or 0, 2), "mae": round(mae or 0, 2)}
                srcs[src] = srcs.get(src, 0) + 1
                trades.append(t)
                opens.append(t)
                if result == "손절":
                    streak[day] = streak.get(day, 0) + 1
                elif net > 0:
                    streak[day] = 0
                day_pnl[day] = day_pnl.get(day, 0) + pnl
        for t in trades:
            t["exit_dt"] = t["exit_dt"].isoformat(timespec="minutes") if isinstance(t["exit_dt"], datetime) else t["exit_dt"]
        return trades, skips, srcs

    @staticmethod
    def _close_dt(mkt, d):
        """그 시장 날짜의 장 마감(한국 시간)."""
        x = datetime.fromisoformat(d)
        return x.replace(hour=15, minute=30) if mkt == "kr" else x.replace(hour=6) + timedelta(days=1)

    def compare(self, rid, days=30, change=None):
        """지금 값과 바꾼 값을 같은 추천으로 나란히. 기간을 앞 70%·뒤 30%로 나눠 뒤에서도 통하는지 본다."""
        from .autotrade import PLAN_KEYS, SKIP, _clamp_key
        at = self.rt.autotrade
        run = at._run(int(rid))
        days = max(3, min(120, int(days or 30)))
        base = {k: run["params"].get(k) for k in KEYS}
        alt = dict(base)
        ch = []
        for k, v in (change or {}).items():
            if k not in KEYS or v in (None, ""):
                continue
            try:
                v = _clamp_key(k, v)
            except Exception:
                continue
            if v != base.get(k):
                alt[k] = v
                ch.append({"key": k, "label": PLAN_KEYS[k][3], "from": base.get(k), "to": v})
        cyc = self._cycles(run, days)
        if not cyc:
            raise ValueError("이 기간에 다시 돌릴 추천 기록이 없어요. 기간을 늘려 보세요")
        fetch = [FETCH_MAX]
        budget = float(run["budget"])
        A, ska, sra = self.simulate(run, base, cyc, budget, fetch)
        B, skb, srb = self.simulate(run, alt, cyc, budget, fetch) if ch else (A, ska, sra)
        dlist = sorted({r["day"] for r, _, _ in cyc})
        cut = dlist[int(len(dlist) * 0.7)] if len(dlist) >= 4 else None
        part = lambda T, early: [t for t in T if (t["day"] < cut) == early] if cut else ([] if not early else T)
        ids = [r["id"] for r in at._runs("WHERE lab_id=?", (run["lab_id"],))] if run.get("lab_id") else [run["id"]]
        real = self.rt.db.q("SELECT pnl, pnl_pct FROM at_trades WHERE run_id=? AND status='closed' AND entry_day>=?", (run["id"], dlist[0]))
        names = dict(SKIP, time="매수 시각 밖", dayloss="하루 손실 한도", streak="연속 손절로 쉼", watch="관망이라 쉼", nodata="시세 자료 없음",
                     money="돈이 모자람", fee="목표가 비용보다 작음", dup="오늘 이미 거래")
        sk = sorted({*ska, *skb}, key=lambda c: -(ska.get(c, 0) + skb.get(c, 0)))
        out = {"run": {"id": run["id"], "name": run["name"], "mkt": run["mkt"], "style": run["style"], "lab": bool(run.get("lab_id")), "budget": budget, "plans": len(ids)},
               "days": days, "from": dlist[0], "to": dlist[-1], "cycles": len(cyc), "picks": sum(len(json.loads(p["picks"] or "[]")) for _, _, p in cyc),
               "changes": ch, "base": stats(A, budget), "alt": stats(B, budget) if ch else None,
               "split": {"cut": cut, "base": [stats(part(A, True), budget), stats(part(A, False), budget)],
                         "alt": [stats(part(B, True), budget), stats(part(B, False), budget)] if ch else None},
               "skips": [{"code": c, "label": names.get(c, c), "base": ska.get(c, 0), "alt": skb.get(c, 0) if ch else None} for c in sk][:10],
               "src": {"base": sra, "alt": srb if ch else None},
               "actual": {"n": len(real), "avg": round(sum(r["pnl_pct"] or 0 for r in real) / len(real), 2) if real else None,
                          "pnl": round(sum(r["pnl"] or 0 for r in real), 2)} if not run.get("lab_id") else None,
               "trades": {"base": sorted(A, key=lambda t: t["ts"], reverse=True)[:150], "alt": sorted(B, key=lambda t: t["ts"], reverse=True)[:150] if ch else None},
               "not_replayed": [PLAN_KEYS[k][3] for k in NOT_REPLAYED if k in PLAN_KEYS and run["params"].get(k)],
               "fetched": FETCH_MAX - fetch[0]}
        return out

    # ------------------------------------------------------------------ 따로 도는 일(시세를 받느라 오래 걸릴 수 있어)
    def start(self, rid, days=30, change=None):
        with self._lock:
            for j in list(self.jobs.values()):
                if j["state"] == "running" and time.time() - j["t0"] < 300:
                    raise ValueError("이미 다시 돌리는 중이에요. 끝나면 다시 눌러 주세요")
            jid = uuid.uuid4().hex[:10]
            self.jobs = {k: v for k, v in self.jobs.items() if time.time() - v["t0"] < 1800}
            self.jobs[jid] = {"id": jid, "state": "running", "t0": time.time(), "res": None, "error": None}

        def go():
            j = self.jobs[jid]
            try:
                j["res"] = self.compare(rid, days, change)
                j["state"] = "done"
            except Exception as e:
                j["error"], j["state"] = (str(e) if isinstance(e, ValueError) else f"다시 돌리지 못했어요({type(e).__name__}: {e})"), "error"
        threading.Thread(target=go, name="moa-replay", daemon=True).start()
        return {"id": jid, "state": "running"}

    def job(self, jid):
        j = self.jobs.get(jid)
        if not j:
            raise ValueError("작업을 찾지 못했어요. 다시 돌려 주세요")
        return {"id": jid, "state": j["state"], "error": j["error"], "res": j["res"], "sec": round(time.time() - j["t0"])}
