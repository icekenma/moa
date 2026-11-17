"""단타 전략 조건 대량 훑기(파라미터 스윕).

1단계(넓게, 빠르게): vectorbt로 수백~수천 개 조건을 한 번에 돌린다.
  - 진입 신호는 실제 엔진(strategy.py)과 같은 규칙을 numpy로 다시 계산한다.
  - 손절·익절·시간 청산·장마감 청산을 반영한다. 본전 이동과 계좌 단위 한도(동시 보유·하루 손실)는 빼고 본다.
  - 앞쪽 70% 날짜(학습)로 순위를 매기고, 뒤쪽 30%(검증)는 순위에 쓰지 않고 결과만 보여 준다(과최적화 확인).
2단계(정확하게): 상위 조건만 moa 백테스터(backtest.py, 실제 엔진과 같은 규칙)로 다시 돌려 확인한다.

분봉은 키움 1분봉(ka10080)을 받아 data/bars에 저장해 두고 다시 쓴다.
"""
import importlib
import itertools
import json
import math
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

from . import backtest as BT
from .costs import tick_size
from .risk import hm

# ---------- 훑을 범위(기본값) ----------
GRIDS = {
    "orb_vwap": {
        "range_min": [5, 10, 15, 30],
        "vol_mult": [1.2, 1.5, 2.0, 3.0],
        "min_range_pct": [0.2, 0.4, 0.8],
        "stop_atr": [1.0, 1.5, 2.0],
        "target_r": [1.0, 1.5, 2.0, 3.0],
        "max_hold_min": [30, 90],
    },
    "vwap_pullback": {
        "min_day_pct": [1.0, 2.0, 3.0, 5.0],
        "band_pct": [0.2, 0.3, 0.5],
        "lookback": [3, 5, 10],
        "target_r": [1.0, 1.5, 2.0, 3.0],
        "max_hold_min": [30, 90],
    },
}
FIXED = {"orb_vwap": {"atr_n": 14, "max_day_pct": 8.0}, "vwap_pullback": {"min_bars": 30, "max_day_pct": 12.0}}
LABELS = {"range_min": "시가범위(분)", "vol_mult": "거래량 배수", "min_range_pct": "최소 범위(%)",
          "stop_atr": "손절(ATR배)", "target_r": "목표(R)", "max_hold_min": "최대 보유(분)",
          "min_day_pct": "당일 최소 상승(%)", "band_pct": "VWAP 근접(%)", "lookback": "눌림 확인 봉"}
CHUNK = 48       # vectorbt 한 번에 돌릴 조건 수(메모리 보호)
IS_RATIO = 0.7   # 앞쪽 70% 날짜로 순위, 뒤쪽 30%로 검증


class SweepError(RuntimeError):
    pass


np = None  # numpy는 훑기를 처음 쓸 때 불러온다(설치 전에도 서버는 떠야 하므로)


def _load_np():
    global np
    if np is None:
        np = importlib.import_module("numpy")
    return np


def ensure_vectorbt(progress=None):
    """vectorbt가 없으면 처음 한 번 설치(몇 분). 설치 실패 시 이유를 알려 준다."""
    try:
        vbt = importlib.import_module("vectorbt")
        _load_np()
        return vbt
    except ImportError:
        pass
    if progress:
        progress(1, "vectorbt 설치 중이에요(처음 한 번, 몇 분 걸려요)")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "vectorbt"],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise SweepError("vectorbt 설치에 실패했어요: " + (r.stderr or r.stdout)[-300:])
    importlib.invalidate_caches()
    vbt = importlib.import_module("vectorbt")
    _load_np()
    return vbt


# ---------- 분봉 받기·저장 ----------
def _bars_path(data_dir, code):
    d = os.path.join(data_dir, "bars")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{code}.json")


def load_cached(data_dir, code):
    try:
        with open(_bars_path(data_dir, code), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def fetch_kiwoom_days(client, code, n_days, data_dir, now=None):
    """키움 1분봉을 거꾸로 이어 받아 날짜별로 저장. 이미 받은 날은 다시 받지 않는다(오늘은 장 끝난 뒤만 저장)."""
    from ..broker.kiwoom import num
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    have = load_cached(data_dir, code)
    done_today = now.strftime("%H:%M") >= "15:35"
    want_days = n_days
    complete = sorted(d for d in have if d != today or done_today)
    if len(complete) >= want_days and complete and complete[-1] >= _last_trading_day(now, done_today):
        return {d: have[d] for d in complete[-want_days:]}
    got, key = {}, None
    for _ in range(80):  # 한 번에 약 900봉(2일 남짓)
        d = client.call("minute", {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1",
                                   "base_dt": now.strftime("%Y%m%d")}, cont_key=key)
        rows = d.get("stk_min_pole_chart_qry") or []
        for r in rows:
            ts = str(r.get("cntr_tm", ""))
            if len(ts) < 12:
                continue
            day = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
            t = f"{ts[8:10]}:{ts[10:12]}"
            if not ("09:00" <= t < "15:30"):
                continue
            got.setdefault(day, {})[t] = {"t": t, "o": num(r.get("open_pric")), "h": num(r.get("high_pric")),
                                          "l": num(r.get("low_pric")), "c": num(r.get("cur_prc")),
                                          "v": int(num(r.get("trde_qty")))}
        days_now = sorted(x for x in set(got) | set(have) if x != today or done_today)
        oldest_new = min(got) if got else None
        if (len(days_now) >= want_days + 1 and oldest_new and oldest_new <= days_now[-want_days]) or not rows:
            break
        if client.last_headers.get("cont-yn") != "Y" or not client.last_headers.get("next-key"):
            break
        key = client.last_headers["next-key"]
    for day, m in got.items():
        if day == today and not done_today:
            continue
        bars = sorted(m.values(), key=lambda b: b["t"])
        if len(bars) >= 300 or day not in have:  # 다 받은 날만 덮어쓴다
            have[day] = bars
    if got:
        # 맨 앞(가장 오래된) 날은 중간부터 잘렸을 수 있어 뺀다
        oldest = min(got)
        if oldest in have and len(have[oldest]) < 300:
            have.pop(oldest, None)
    try:
        with open(_bars_path(data_dir, code), "w", encoding="utf-8") as f:
            json.dump(have, f)
    except OSError:
        pass
    complete = sorted(d for d in have if d != today or done_today)
    return {d: have[d] for d in complete[-want_days:]}


def _last_trading_day(now, done_today):
    from datetime import timedelta
    d = now.date() if done_today else now.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


# ---------- 신호 계산(엔진과 같은 규칙을 numpy로) ----------
class Series:
    """한 종목의 여러 날 분봉을 이어 붙인 배열과 날짜별 보조값."""

    def __init__(self, days):
        self.days = sorted(days)
        o, h, l, c, v, day_i, k_in, tmin, last = [], [], [], [], [], [], [], [], []
        for di, d in enumerate(self.days):
            bars = days[d]
            for k, b in enumerate(bars):
                o.append(b["o"]); h.append(b["h"]); l.append(b["l"]); c.append(b["c"]); v.append(b["v"])
                day_i.append(di); k_in.append(k); tmin.append(hm(b["t"])); last.append(k == len(bars) - 1)
        self.o, self.h, self.l, self.c = (np.asarray(x, float) for x in (o, h, l, c))
        self.v = np.asarray(v, float)
        self.day, self.k, self.tmin, self.last = (np.asarray(day_i), np.asarray(k_in), np.asarray(tmin),
                                                  np.asarray(last, bool))
        n = len(self.c)
        self.n = n
        self.day_open = np.empty(n); self.vwap = np.empty(n); self.prev_c = np.full(n, np.nan)
        self.prev_h = np.full(n, np.nan); self.avg_v = np.zeros(n); self.tr = np.empty(n)
        starts = np.flatnonzero(self.k == 0)
        self.starts = starts
        self.ends = np.append(starts[1:], n)
        for s, e in zip(starts, self.ends):
            self.day_open[s:e] = self.o[s]
            tp = (self.h[s:e] + self.l[s:e] + self.c[s:e]) / 3
            cv = np.cumsum(self.v[s:e])
            with np.errstate(divide="ignore", invalid="ignore"):
                self.vwap[s:e] = np.where(cv > 0, np.cumsum(tp * self.v[s:e]) / np.where(cv > 0, cv, 1), self.c[s:e])
            self.prev_c[s + 1:e] = self.c[s:e - 1]
            self.prev_h[s + 1:e] = self.h[s:e - 1]
            # 직전 20봉 평균 거래량(같은 날 안에서)
            cs = np.concatenate([[0.0], np.cumsum(self.v[s:e])])
            idx = np.arange(e - s)
            lo = np.maximum(0, idx - 20)
            cnt = idx - lo
            with np.errstate(divide="ignore", invalid="ignore"):
                self.avg_v[s:e] = np.where(cnt > 0, (cs[idx] - cs[lo]) / np.where(cnt > 0, cnt, 1), 0)
            tr = self.h[s:e] - self.l[s:e]
            pc = self.c[s:e - 1]
            tr[1:] = np.maximum.reduce([tr[1:], np.abs(self.h[s + 1:e] - pc), np.abs(self.l[s + 1:e] - pc)])
            self.tr[s:e] = tr
        self._atr = {}

    def atr(self, n):
        """strategy.atr과 같음: 마지막 n개 TR 평균(같은 날, 첫 봉 TR 제외). 봉이 하나뿐이면 고저폭."""
        if n in self._atr:
            return self._atr[n]
        out = np.empty(self.n)
        for s, e in zip(self.starts, self.ends):
            tr = self.tr[s:e].copy()
            tr[0] = 0.0
            cs = np.concatenate([[0.0], np.cumsum(tr)])
            idx = np.arange(e - s)
            lo = np.maximum(1, idx + 1 - n)
            cnt = idx + 1 - lo
            with np.errstate(divide="ignore", invalid="ignore"):
                a = np.where(cnt > 0, (cs[idx + 1] - cs[lo]) / np.where(cnt > 0, cnt, 1), 0)
            a[0] = self.h[s] - self.l[s]
            out[s:e] = a
        self._atr[n] = out
        return out

    def orb_range(self, rm):
        hi = np.full(self.n, np.nan); lo = np.full(self.n, np.nan)
        for s, e in zip(self.starts, self.ends):
            if e - s >= rm:
                hi[s:e] = self.h[s:s + rm].max(); lo[s:e] = self.l[s:s + rm].min()
        return hi, lo

    def low_lookback(self, lb):
        """직전 lb개 봉(마지막 봉 제외)의 최저가."""
        out = np.full(self.n, np.nan)
        for s, e in zip(self.starts, self.ends):
            for i in range(s + lb, e):
                out[i] = self.l[i - lb:i].min()
        return out


def _first_per_day(sig, ser):
    """종목마다 하루 첫 신호만(엔진의 done_codes와 같음)."""
    out = np.zeros_like(sig)
    for s, e in zip(ser.starts, ser.ends):
        w = np.flatnonzero(sig[s:e])
        if len(w):
            out[s + w[0]] = True
    return out


def signals(ser, name, p, risk, costs, cache):
    """조건 하나의 신호 위치와 손절가. 반환: (신호 bool 배열, 손절가 배열)."""
    c, n = ser.c, ser.n
    t_ok = (ser.tmin >= hm(risk["entry_start"])) & (ser.tmin <= hm(risk["entry_end"]))
    day_pct = (c / ser.day_open - 1) * 100
    if name == "orb_vwap":
        rm = int(p["range_min"])
        key = ("orb", rm)
        if key not in cache:
            cache[key] = ser.orb_range(rm)
        hi, lo = cache[key]
        with np.errstate(invalid="ignore", divide="ignore"):
            rng_pct = (hi - lo) / lo * 100
            sig = ((ser.k + 1 > rm + 1) & (lo > 0) & (rng_pct >= p["min_range_pct"]) & (c > hi) & (hi >= ser.prev_c)
                   & (c > ser.vwap) & (ser.avg_v > 0) & (ser.v >= p["vol_mult"] * ser.avg_v)
                   & (day_pct <= p["max_day_pct"]))
        stop = np.maximum(c - p["stop_atr"] * ser.atr(int(p["atr_n"])), lo)
    else:
        lb = int(p["lookback"])
        key = ("low", lb)
        if key not in cache:
            cache[key] = ser.low_lookback(lb)
        low = cache[key]
        with np.errstate(invalid="ignore"):
            sig = ((ser.k + 1 >= max(int(p["min_bars"]), lb + 2)) & (day_pct >= p["min_day_pct"])
                   & (day_pct <= p["max_day_pct"]) & (low <= ser.vwap * (1 + p["band_pct"] / 100))
                   & (c > ser.prev_h) & (c > ser.vwap))
        ticks = np.vectorize(tick_size, otypes=[float])(np.nan_to_num(low, nan=1.0))
        stop = low - ticks
    sig = sig & t_ok & np.isfinite(stop) & (c - stop > 0)
    # 목표 수익이 왕복 비용의 min_edge_mult배 이상(risk.edge_ok와 같음)
    tick_c = np.vectorize(tick_size, otypes=[float])(c)
    rt_pct = (2 * costs["commission"] + costs["tax"] + 2 * costs["slippage_ticks"] * tick_c / c) * 100
    with np.errstate(invalid="ignore", divide="ignore"):
        gain_pct = p["target_r"] * (c - stop) / c * 100
    sig = sig & (gain_pct >= rt_pct * risk["min_edge_mult"])
    return _first_per_day(np.nan_to_num(sig, nan=0).astype(bool), ser), stop


def combos(name, grid=None):
    g = dict(GRIDS[name], **(grid or {}))
    keys = list(g)
    for vals in itertools.product(*(g[k] for k in keys)):
        yield dict(FIXED[name], **dict(zip(keys, vals)))


# ---------- 1단계: vectorbt로 넓게 ----------
def screen(data, name, cfg, grid=None, progress=None, should_stop=None):
    """data: {날짜: {종목: [봉]}}. 조건별 학습·검증 통계 목록을 돌려준다."""
    vbt = ensure_vectorbt(progress)
    import pandas as pd
    risk, costs = cfg["risk"], cfg["costs"]
    days = sorted(data)
    if len(days) < 5:
        raise SweepError("분봉이 5일 이상 있어야 해요")
    split_day = days[max(1, int(len(days) * IS_RATIO)) - 1]
    codes = sorted({code for d in days for code in data[d]})
    params = list(combos(name, grid))
    per_combo = [{"trades": [], "p": p} for p in params]
    total_steps = len(codes) * math.ceil(len(params) / CHUNK)
    step = 0
    fe = hm(risk["force_exit"])
    half_fee = (2 * costs["commission"] + costs["tax"]) / 2
    for code in codes:
        ser = Series({d: data[d][code] for d in days if code in data[d] and len(data[d][code]) > 5})
        if ser.n < 50:
            step += math.ceil(len(params) / CHUNK)
            continue
        cache = {}
        slip = np.vectorize(tick_size, otypes=[float])(ser.o) * costs["slippage_ticks"] / ser.o
        day_exit = (ser.tmin >= fe) | ser.last
        idx = pd.RangeIndex(ser.n)
        for c0 in range(0, len(params), CHUNK):
            if should_stop and should_stop():
                raise SweepError("멈췄어요")
            chunk = params[c0:c0 + CHUNK]
            m = len(chunk)
            ent = np.zeros((ser.n, m), bool); ex = np.zeros((ser.n, m), bool)
            sl = np.full((ser.n, m), np.nan); tp = np.full((ser.n, m), np.nan)
            price = np.repeat(ser.c[:, None], m, axis=1)
            for j, p in enumerate(chunk):
                sig, stop = signals(ser, name, p, risk, costs, cache)
                for i in np.flatnonzero(sig):
                    e = i + 1  # 다음 봉 시가에 산다(같은 날 안에서만)
                    if e >= ser.n or ser.day[e] != ser.day[i]:
                        continue
                    fill = ser.o[e] * (1 + slip[e])
                    if fill <= stop[i]:
                        continue
                    ent[e, j] = True
                    price[e, j] = ser.o[e]
                    risk_frac = (fill - stop[i]) / fill
                    sl[e, j] = risk_frac
                    tp[e, j] = p["target_r"] * risk_frac
                    # 시간 청산: 진입 후 max_hold_min분이 지난 첫 봉 종가
                    if p.get("max_hold_min"):
                        s_end = ser.ends[ser.day[e]]
                        later = np.flatnonzero(ser.tmin[e:s_end] - ser.tmin[e] >= p["max_hold_min"])
                        if len(later):
                            ex[e + later[0], j] = True
                ex[:, j] |= day_exit
            cols = pd.RangeIndex(m)
            pf = vbt.Portfolio.from_signals(
                close=pd.DataFrame(np.repeat(ser.c[:, None], m, axis=1), index=idx, columns=cols),
                entries=pd.DataFrame(ent, index=idx, columns=cols), exits=pd.DataFrame(ex, index=idx, columns=cols),
                price=pd.DataFrame(price, index=idx, columns=cols),
                open=pd.DataFrame(np.repeat(ser.o[:, None], m, axis=1), index=idx, columns=cols),
                high=pd.DataFrame(np.repeat(ser.h[:, None], m, axis=1), index=idx, columns=cols),
                low=pd.DataFrame(np.repeat(ser.l[:, None], m, axis=1), index=idx, columns=cols),
                sl_stop=pd.DataFrame(sl, index=idx, columns=cols), tp_stop=pd.DataFrame(tp, index=idx, columns=cols),
                size=1.0, fees=half_fee, slippage=pd.DataFrame(np.repeat(slip[:, None], m, axis=1), index=idx, columns=cols),
                init_cash=1e12, freq="1min")
            rec = pf.trades.records_arr
            for r in rec:
                if int(r["status"]) != 1:  # 닫힌 거래만
                    continue
                col = int(r["col"])
                xi = int(r["exit_idx"])
                per_combo[c0 + col]["trades"].append(
                    (days[ser.day[xi]], float(r["return"]) * 100, code))
            step += 1
            if progress:
                progress(5 + int(80 * step / max(1, total_steps)), f"{code} 조건 {min(c0 + m, len(params))}/{len(params)}")
    out = []
    for k, pc in enumerate(per_combo):
        tr = pc["trades"]
        is_r = [x[1] for x in tr if x[0] <= split_day]
        oos_r = [x[1] for x in tr if x[0] > split_day]
        out.append({"id": k, "params": pc["p"], "is": _stats(is_r, tr, lambda d: d <= split_day),
                    "oos": _stats(oos_r, tr, lambda d: d > split_day)})
    return out, {"days": len(days), "codes": codes, "split_day": split_day, "first": days[0], "last": days[-1],
                 "combos": len(params)}


def _stats(rets, trades, in_period):
    n = len(rets)
    if not n:
        return {"trades": 0, "win_rate": 0, "avg": 0, "sum": 0, "pf": None, "mdd": 0, "score": -1e9}
    arr = np.asarray(rets)
    pos, neg = arr[arr > 0].sum(), -arr[arr <= 0].sum()
    eq = np.cumsum([x[1] for x in sorted((t for t in trades if in_period(t[0])), key=lambda t: t[0])])
    mdd = float(np.max(np.maximum.accumulate(np.concatenate([[0], eq])) - np.concatenate([[0], eq])))
    sd = float(arr.std(ddof=1)) if n > 1 else 0.0
    score = float(arr.mean() / sd * math.sqrt(n)) if sd > 0 else float(arr.mean())
    return {"trades": n, "win_rate": round(float((arr > 0).mean() * 100), 1), "avg": round(float(arr.mean()), 3),
            "sum": round(float(arr.sum()), 2), "pf": round(float(pos / neg), 2) if neg > 0 else None,
            "mdd": round(mdd, 2), "score": round(score, 3)}


def rank(results, min_trades=30, top=20):
    """학습 구간 점수(평균 수익 ÷ 변동 × √거래수)로 순위. 거래가 적은 조건은 운으로 보고 뺀다."""
    ok = [r for r in results if r["is"]["trades"] >= min_trades and r["is"]["avg"] > 0]
    ok.sort(key=lambda r: r["is"]["score"], reverse=True)
    return ok[:top]


# ---------- 2단계: moa 백테스터로 정확히 ----------
def validate(data, name, cfg, top, split_day, n=5):
    days = sorted(data)
    oos = {d: data[d] for d in days if d > split_day}
    out = []
    for r in top[:n]:
        p = r["params"]
        c2 = json.loads(json.dumps(cfg))
        c2["strategy"]["name"] = name
        c2["strategy"][name] = {k: v for k, v in p.items() if k != "max_hold_min"}
        if p.get("max_hold_min"):
            c2["exit"]["max_hold_min"] = p["max_hold_min"]
        full = BT.run(data, c2)["stats"]
        test = BT.run(oos, c2)["stats"] if oos else None
        out.append({"id": r["id"], "full": full, "oos": test})
    return out


# ---------- 작업 관리(뒤에서 돌리고 진행률 보여 주기) ----------
class SweepJob:
    def __init__(self, rt, data_dir):
        self.rt, self.data_dir = rt, data_dir
        self.lock = threading.Lock()
        self.state = {"status": "idle"}
        self._stop = False
        self.path = os.path.join(data_dir, "sweep_last.json")
        try:
            with open(self.path, encoding="utf-8") as f:
                self.state = json.load(f)
                if self.state.get("status") == "running":
                    self.state["status"] = "error"
                    self.state["error"] = "서버를 다시 켜서 중단됐어요"
        except (OSError, ValueError):
            pass

    def status(self):
        with self.lock:
            return dict(self.state)

    def start(self, req):
        with self.lock:
            if self.state.get("status") == "running":
                raise SweepError("이미 훑는 중이에요")
            name = req.get("strategy") if req.get("strategy") in GRIDS else "orb_vwap"
            n_days = max(10, min(120, int(req.get("days") or 40)))
            src = req.get("source") if req.get("source") in ("kiwoom", "demo") else "kiwoom"
            codes = [c for c in (req.get("codes") or self.rt.settings.get("watchlist")) if str(c).isdigit() and len(str(c)) == 6][:12]
            if not codes:
                raise SweepError("종목을 하나 이상 넣어 주세요")
            self._stop = False
            self.state = {"status": "running", "progress": 0, "msg": "준비 중", "strategy": name, "days": n_days,
                          "source": src, "codes": codes, "started": datetime.now().isoformat(timespec="seconds")}
        threading.Thread(target=self._run, args=(name, n_days, src, codes), daemon=True).start()
        return self.status()

    def stop(self):
        self._stop = True

    def _progress(self, pct, msg):
        with self.lock:
            self.state.update(progress=pct, msg=msg)

    def _run(self, name, n_days, src, codes):
        t0 = time.time()
        try:
            ensure_vectorbt(self._progress)
            self._progress(2, "분봉 모으는 중")
            data = self.collect(src, codes, n_days)
            s = self.rt.settings.all()
            cfg = {k: json.loads(json.dumps(s[k])) for k in ("strategy", "exit", "risk", "costs")}
            results, meta = screen(data, name, cfg, progress=self._progress, should_stop=lambda: self._stop)
            top = rank(results)
            self._progress(88, f"상위 {min(5, len(top))}개를 실제 엔진 규칙으로 다시 확인 중")
            checked = validate(data, name, cfg, top, meta["split_day"])
            cur = cfg["strategy"].get(name, {})
            current = next((r for r in results if all(abs(float(r["params"][k]) - float(cur.get(k, r["params"][k]))) < 1e-9
                                                      for k in r["params"] if k != "max_hold_min")
                            and r["params"].get("max_hold_min") == cfg["exit"].get("max_hold_min")), None)
            done = {"status": "done", "progress": 100, "msg": "끝났어요", "strategy": name, "source": src,
                    "meta": meta, "top": top, "checked": checked, "current": current, "labels": LABELS,
                    "grid": GRIDS[name], "elapsed": round(time.time() - t0, 1),
                    "finished": datetime.now().isoformat(timespec="seconds"),
                    "tested_ok": sum(1 for r in results if r["is"]["trades"] >= 30),
                    "positive_is": sum(1 for r in results if r["is"]["trades"] >= 30 and r["is"]["avg"] > 0)}
            with self.lock:
                self.state = done
        except Exception as e:  # 진행 화면에 이유를 보여 준다
            with self.lock:
                self.state = {"status": "error", "error": str(e), "progress": 0, "strategy": name}
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.status(), f, ensure_ascii=False)
        except OSError:
            pass

    def collect(self, src, codes, n_days):
        if src == "demo":
            return BT.demo_dataset(codes, n_days)
        cl = self.rt.market.kr_client()
        if cl is None:
            raise SweepError("키움을 연결해야 실제 분봉을 받을 수 있어요. 설정 → 계좌 연결에서 키움 키를 넣거나 '가상 시세'로 해 보세요")
        data = {}
        for i, code in enumerate(codes):
            self._progress(2 + int(3 * i / len(codes)), f"{code} 분봉 받는 중 ({i + 1}/{len(codes)})")
            for day, bars in fetch_kiwoom_days(cl, code, n_days, self.data_dir).items():
                data.setdefault(day, {})[code] = bars
        if not data:
            raise SweepError("받은 분봉이 없어요")
        return data
