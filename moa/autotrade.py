"""AI 자동매매(투자 → 자동매매).

사용자가 정하는 것: 쓸 돈, 시장(국내·미국), 방식(단타·스윙·장기), 엔진(Claude 모델), 기간, 모의/실전.
앱이 하는 것
1. 플랜: 위 값으로 종목 수·1회 비중·익절/손절 상한·손실 한도·하루 추천 횟수를 정한다(make_plan).
2. 매매: 정해진 시간에 AI 추천(aipick)을 받아 조건에 맞는 종목만 사고, 목표·손절·기간에 닿으면 판다.
3. 기록: 살 때의 근거·지표, 보유 중 최고·최저, 팔 때의 사유·손익을 at_trades에, 판단 과정을 at_log에 남긴다.
4. 분석: 표본이 쌓이면 조건별 성적으로 개선안(at_proposals)을 만든다. 개선안은 사용자가 승인해야 반영된다.

안전장치: 쓸 돈 상한(그 안에서만 삼), 하루 손실 한도(그날 새 매수 중지), 전체 손실 한도(전부 정리 후 종료),
연속 손절 시 그날 쉬기, 같은 종목 하루 한 번, 긴급 종료(전부 청산).
모의는 앱 안 가상 장부(실제 시세)로만 돌고 주문이 나가지 않는다. 실전은 설정의 주문 계좌(키움)로 주문한다.
"""
import copy
import csv
import io
import json
import math
import re
import threading
import time
from datetime import datetime, timedelta

from . import aipick as AP
from .trading import costs as C

STYLES = {
    "day": {"label": "단타", "sub": "오늘 사서 오늘 정리"},
    "swing": {"label": "스윙", "sub": "약 1주 보유"},
    "month": {"label": "장기", "sub": "약 1달 보유"},
}
MIN_BUDGET = {"kr": 300_000, "us": 300}
US_FEE = 0.25          # 미국 왕복 비용(%) 대략
US_SLIP = 0.0005       # 모의 체결 슬리피지(미국)
# 추천받은 종목을 사지 않은 이유(기회 분석에 쓴다)
SKIP = {"winprob": "승률 기준 미달", "score": "점수 기준 미달", "rr": "손익비 기준 미달", "fee": "목표가 비용보다 작음",
        "chase": "추천가보다 올라 쫓지 않음", "qty": "배정 금액으로 1주를 못 삼", "full": "보유 자리 없음", "dup": "오늘 이미 거래",
        "auto": "앱이 고른 예비 종목", "watch": "AI가 관망 권함", "quote": "시세 없음", "fail": "매수 실패", "etc": "기타"}
_SKIP_TEXT = (("승률", "winprob"), ("점수", "score"), ("손익비", "rr"), ("비용", "fee"), ("쫓아가지", "chase"), ("1주를 못", "qty"),
              ("꽉 참", "full"), ("이미 거래", "dup"), ("예비 종목", "auto"), ("시세", "quote"), ("매수 실패", "fail"))
SHARE_SEC = 600        # 같은 조건의 추천을 함께 쓰는 시간(초)
QUICK_MIN = 10         # 빠른 재추천의 가장 짧은 간격(분)
# 정밀 추천 엔진 사다리. 성과가 나쁘면 한 칸씩 올린다(Sonnet → Opus → Fable). 하루 AI 비용 한도는 그대로 지킨다(필요하면 횟수를 줄여서)
ENGINE_LADDER = ["claude-haiku-4-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-2"]
ESC_N = 20             # 엔진을 판단하는 데 필요한 정밀 추천 거래 수(지난번 바꾼 뒤부터)
ESC_WIN = 45           # 이익 거래 비율이 이보다 낮거나, 한 번 평균이 0% 이하면 '성과가 나쁨'
# 매매 기록에 함께 보여 주는 지수(시장마다 첫째가 대표 지수: '그날 지수' 기준별 성적에 쓴다)
IDX = {"kr": (("코스피", "^KS11"), ("코스닥", "^KQ11")), "us": (("S&P 500", "^GSPC"), ("나스닥", "^IXIC"))}
QUICK_GAP = {"day": 20, "swing": 60, "month": 90}   # 모의의 빠른 재추천 간격(분): 단타는 분봉이 바뀌는 속도, 스윙·장기는 하루 몇 번
OPP_MIN = 8            # 건너뛴 종목의 실제 결과가 이만큼은 쌓여야 기준을 낮춘다


def skip_code(dec):
    """판단 기록 한 줄의 건너뛴 이유 코드(예전 기록은 글에서 알아낸다)."""
    if dec.get("code") in SKIP:
        return dec["code"]
    why = str(dec.get("why") or "")
    return next((c for t, c in _SKIP_TEXT if t in why), "etc")
ACTIVE = ("running", "paused", "closing")

# 개선안으로 바꿀 수 있는 값과 범위(이 밖의 값은 AI가 내놔도 버린다)
PLAN_KEYS = {
    "max_positions": (1, 6, int, "동시 보유 종목 수"),
    "per_trade_pct": (5, 50, float, "1회 매수 비중(%)"),
    "min_win_prob": (40, 80, float, "최소 승률(%)"),
    "min_score": (30, 90, float, "최소 점수"),
    "tp_cap": (0.5, 60, float, "익절 상한(%)"),
    "sl_cap": (0.3, 20, float, "손절 상한(%)"),
    "min_rr": (1.0, 3.0, float, "최소 손익비"),
    "cycles_per_day": (1, 6, int, "하루 정밀 추천 횟수"),
    "cycle_gap_min": (20, 240, int, "정밀 추천 간격(분)"),
    "quick_gap_min": (0, 120, int, "빠른 재추천 간격(분, 0=끔)"),
    "entry_start": (None, None, "hm", "매수 시작 시각"),
    "entry_end": (None, None, "hm", "매수 끝 시각"),
    "daily_loss_pct": (0.5, 10, float, "하루 손실 한도(%)"),
    "total_loss_pct": (2, 30, float, "전체 손실 한도(%)"),
    "loss_streak": (2, 6, int, "연속 손절 시 쉬기(번)"),
    "chase_pct": (0.1, 3, float, "추천가 대비 추격 허용(%)"),
    "skip_watch": (None, None, bool, "관망이면 매수 안 함"),
    "watch_mult": (0.1, 1.0, float, "관망일 때 매수 크기(배)"),
    "idx_floor": (-5.0, 0.0, float, "지수가 이만큼 내리면 AI를 부르지 않음(%, 0=끔)"),
    "exit_mult": (0.3, 3.0, float, "AI가 낸 익절·손절 폭에 곱하는 배수"),
}


def _short_model(model):
    from .ai import MODELS
    return MODELS[model][0].split(" · ")[0] if model in MODELS else str(model or "")


# 예전 판 자동매매에 없던 플랜 값 → 그 판의 동작과 같은 값
PARAM_OLD = {"exit_mult": 1.0, "watch_mult": 0.5, "idx_floor": 0.0, "chase_pct": 0.5, "loss_streak": 3}


def _clamp_key(key, val):
    lo, hi, typ, _ = PLAN_KEYS[key]
    if typ is bool:
        if isinstance(val, str):
            return val.strip().lower() in ("1", "true", "yes", "on", "예")
        return bool(val)
    if typ == "hm":
        s = str(val).strip()
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", s):
            raise ValueError("시각은 HH:MM으로 넣어 주세요")
        return s
    v = float(str(val).replace(",", ""))
    if math.isnan(v) or math.isinf(v):
        raise ValueError("숫자를 확인해 주세요")
    v = min(hi, max(lo, v))
    if key == "quick_gap_min" and 0 < v < QUICK_MIN:
        v = QUICK_MIN                            # 너무 잦으면 같은 자료를 다시 묻게 된다
    return int(round(v)) if typ is int else round(v, 2)


def make_plan(budget, mkt, style, days, mode="real"):
    """쓸 돈·시장·방식·기간으로 매매 플랜을 만든다. 돈이 적을수록 종목 수를 줄여 1주도 못 사는 일을 막는다."""
    unit = 1_000_000 if mkt == "kr" else 1_000
    small = budget < unit
    if style == "day":
        p = {"max_positions": 2 if small else 3, "min_win_prob": 55, "min_score": 60, "tp_cap": 4.0, "sl_cap": 2.0,
             "min_rr": 1.5, "cycles_per_day": 3, "cycle_gap_min": 60, "daily_loss_pct": 2.0, "total_loss_pct": 8.0,
             "entry_start": "09:10" if mkt == "kr" else "09:40", "entry_end": "14:30" if mkt == "kr" else "15:00"}
    elif style == "swing":
        p = {"max_positions": 2 if small else 4, "min_win_prob": 50, "min_score": 60, "tp_cap": 12.0, "sl_cap": 6.0,
             "min_rr": 1.6, "cycles_per_day": 1, "cycle_gap_min": 120, "daily_loss_pct": 3.0, "total_loss_pct": 10.0,
             "entry_start": "09:30" if mkt == "kr" else "09:45", "entry_end": "15:00" if mkt == "kr" else "15:30"}
    else:
        p = {"max_positions": 3 if small else 5, "min_win_prob": 50, "min_score": 60, "tp_cap": 25.0, "sl_cap": 10.0,
             "min_rr": 1.6, "cycles_per_day": 1, "cycle_gap_min": 180, "daily_loss_pct": 4.0, "total_loss_pct": 12.0,
             "entry_start": "09:30" if mkt == "kr" else "09:45", "entry_end": "15:00" if mkt == "kr" else "15:30"}
    p["per_trade_pct"] = round(min(100 / p["max_positions"], {"day": 33.0, "swing": 25.0, "month": 20.0}[style]), 1)
    # 관망: 실전은 쉰다. 모의는 자료를 모으는 것이 목적이라 AI가 낸 종목을 작게 산다
    # 지수가 크게 내린 날: 실전은 AI를 부르기 전에 앱이 먼저 걸러 비용을 아낀다.
    # 모의는 약한 장도 시험해야 할 자료라 거르지 않는다(비용은 하루 상한과 '관망 뒤 쉬기'로 묶는다)
    p.update(loss_streak=3, chase_pct=0.5, skip_watch=mode == "real", watch_mult=0.5,
             idx_floor={"day": -1.0, "swing": -1.5, "month": -2.0}[style] if mode == "real" else 0.0,
             warmup_trades=10, warmup_mult=0.5,
             review_every=30, review_days=5, auto_apply=False, ai_review=True, ai_daily_usd=1.0, web_search=True,
             # 빠른 재추천: 정밀 추천(좋은 엔진·뉴스 검색) 사이사이에, 가장 싼 엔진으로 달라진 흐름만 다시 묻는다.
             # 모의는 자료를 빨리 모으려고 기본으로 켠다. 실전은 사용자가 플랜에서 켠다
             quick_gap_min=QUICK_GAP[style] if mode != "real" else 0)
    return p


# 모의 실험실: 같은 AI 추천을 여러 플랜에 동시에 흘려, 어떤 조건이 실제로 나은지 비교한다(추천 비용은 한 번만 든다)
VARIANTS = {
    "base": ("기본", "처음 플랜 그대로", {}),
    "loose": ("느슨한 기준", "기본보다 승률 -7 · 점수 -10 · 손익비 -0.3", {"min_win_prob": "-7", "min_score": "-10", "min_rr": "-0.3", "chase_pct": "+0.5"}),
    "strict": ("엄격한 기준", "기본보다 승률 +5 · 점수 +10 · 손익비 +0.3", {"min_win_prob": "+5", "min_score": "+10", "min_rr": "+0.3"}),
    "all": ("가리지 않고 사기", "AI가 낸 종목은 거의 다 사 봄(AI 추천 자체의 성적을 재는 기준선)", {"min_win_prob": 40, "min_score": 30, "min_rr": 1.0, "chase_pct": 2.0, "skip_watch": False}),
    "watch_skip": ("관망엔 쉬기", "AI가 관망이면 사지 않음(실전 기본)", {"skip_watch": True}),
    "watch_full": ("관망 무시", "관망이어도 평소 크기로 매수", {"skip_watch": False, "watch_mult": 1.0}),
    "tight": ("짧게 끊기", "AI가 낸 익절·손절 폭의 절반에서 팔기", {"exit_mult": 0.5}),
    "wide": ("길게 끌기", "AI가 낸 익절·손절 폭의 1.5배까지 기다리기", {"exit_mult": 1.5, "tp_cap": "*1.5", "sl_cap": "*1.5"}),
    "focus": ("한 종목 집중", "1종목에 절반씩", {"max_positions": 1, "per_trade_pct": 50}),
    "spread": ("여러 종목 분산", "5종목에 20%씩", {"max_positions": 5, "per_trade_pct": 20}),
}
def apply_variant(p, key, keep=()):
    """플랜 값에 실험 플랜의 차이를 입힌다. '*1.5'는 곱하기, '+5'·'-7'은 더하기·빼기, 그 밖은 그 값으로."""
    for k, v in VARIANTS[key][2].items():
        if k in keep:
            continue
        if isinstance(v, str) and v[:1] == "*":
            v = p[k] * float(v[1:])
        elif isinstance(v, str) and v[:1] in "+-":
            v = p[k] + float(v)
        p[k] = _clamp_key(k, v)
    return p


READY_DAYS = 10        # 그리고 그 거래가 이만큼의 거래일에 걸쳐 있어야 한다(하루 이틀의 운을 거른다)
READY_N = 30           # 실전을 권하려면 모의에서 끝난 거래가 이만큼은 있어야 한다
MONTH_DAYS = 21        # 한 달 거래일
COST_PCT = 1.0         # 직접 만든 자동매매: 한 달 AI 비용 한도 = 쓸 돈(시드머니)의 이 비율(%) …
RUN_DAY_MIN_USD = 3.0  # … 다만 하루 이 금액(달러)보다는 적게 잡지 않는다(시드가 작아도 좋은 엔진·뉴스 검색을 쓰게)
PAPER_DAY_USD = 0.6    # 실험 묶음을 직접 만들 때 하루 AI 비용 목표(달러). 자동 테스트는 AUTO_SET
# 모의 자동 테스트: 사용자는 켜고 끄기만 한다. 시장·방식별 실험 묶음을 앱이 만들고, 끝나면 다시 만든다.
# (시장, 방식) → (하루 AI 비용 상한 달러, 플랜마다 쓰는 가상 돈). 모두 합쳐 하루 AUTO_DAY_USD(3달러)
# 가상 돈은 넉넉히 준다: 값이 비싼 종목도 플랜대로 살 수 있어야 '플랜'을 시험할 수 있다(돈이 모자라 못 사는 것은 시험이 아니다)
AUTO_DAY_USD = 3.0     # 모의 자동 테스트 전체의 하루 AI 비용 목표(달러). 단타 35%씩, 스윙 15%씩 나눈다
AUTO_SET = {("kr", "day"): (round(AUTO_DAY_USD * .35, 2), 10_000_000), ("us", "day"): (round(AUTO_DAY_USD * .35, 2), 10_000),
            ("kr", "swing"): (round(AUTO_DAY_USD * .15, 2), 10_000_000), ("us", "swing"): (round(AUTO_DAY_USD * .15, 2), 10_000)}
AUTO_DAYS = 90


def plan_text(run):
    """플랜을 사람이 읽는 줄글 목록으로."""
    p, cur = run["params"], "원" if run["mkt"] == "kr" else "달러"
    b = run["budget"]

    def money(v):
        return f"{v:,.0f}{cur}" if run["mkt"] == "kr" else f"${v:,.2f}"
    st = run["style"]
    hold = {"day": "그날 장 마감 전에 모두 정리", "swing": "최대 5거래일 보유", "month": "최대 20거래일 보유"}[st]
    return [
        {"t": "1단계 · 검증", "d": f"처음 {p['warmup_trades']}번은 정해진 비중의 {int(p['warmup_mult'] * 100)}%만 써서 전략이 통하는지 확인해요."},
        {"t": "2단계 · 본 운용", "d": f"한 번에 굴리는 돈의 {p['per_trade_pct']:g}%(약 {money(b * p['per_trade_pct'] / 100)})씩, 최대 {p['max_positions']}종목. {hold}."},
        {"t": "3단계 · 복리", "d": "번 돈은 굴리는 돈에 더해 다음 매수 금액이 함께 커져요. 잃으면 매수 금액도 같이 줄어요."},
        {"t": "고르는 기준", "d": f"AI 승률 {p['min_win_prob']:g}% 이상 · 점수 {p['min_score']:g} 이상 · 손익비 {p['min_rr']:g} 이상만 매수"
                              + (" · AI가 관망이면 쉬어요" if p["skip_watch"] else f" · AI가 관망이면 {p.get('watch_mult', 0.5):g}배 크기로만 사요") + "."},
        {"t": "비용 아끼기", "d": (f"지수가 {p['idx_floor']:g}% 넘게 내린 날은 AI를 부르지 않고 쉬어요(무료 점검). " if p.get("idx_floor") else "")
                                + ("관망을 받으면 지수가 회복되거나 추천 간격의 두 배가 지날 때까지 다시 묻지 않아요." if p["skip_watch"] or not p.get("quick_gap_min")
                                   else "이미 보유했거나 오늘 거래한 종목은 후보에서 빼고 물어, 같은 답에 돈을 쓰지 않아요.")},
        {"t": "추천 엔진", "d": (f"정밀 추천은 {_short_model(run.get('model'))}" if run.get("model") else "정밀 추천 엔진은 비용 한도 안에서 앱이 골라요")
                               + f". 정밀 추천으로 산 거래가 {ESC_N}건 쌓일 때마다 성과를 보고, 나쁘면(평균 0% 이하·이익 {ESC_WIN}% 미만) Opus → Fable로 한 칸씩 올려요"
                               + "(하루 AI 비용 한도 안에서, 나빠지면 되돌림)."},
        {"t": "파는 기준", "d": f"익절 최대 +{p['tp_cap']:g}%, 손절 최대 -{p['sl_cap']:g}% 안에서 AI가 종목마다 정해요."},
        {"t": "추천 받기", "d": (f"정밀 추천(뉴스 검색 포함) 하루 최대 {p['cycles_per_day']}번 + 그 사이 {p['quick_gap_min']}분마다 빠른 재추천(가장 싼 엔진, 이미 다룬 종목은 빼고 새 후보만). "
                                 if p.get("quick_gap_min") else f"하루 최대 {p['cycles_per_day']}번, ")
                                + f"{p['entry_start']}~{p['entry_end']}({'한국' if run['mkt'] == 'kr' else '뉴욕'} 시간) 사이, 자리가 비었을 때만."},
        {"t": "손실 한도", "d": f"하루 -{p['daily_loss_pct']:g}%(약 {money(b * p['daily_loss_pct'] / 100)})면 그날 매수 중지, "
                              f"누적 -{p['total_loss_pct']:g}%(약 {money(b * p['total_loss_pct'] / 100)})면 전부 정리하고 종료. 연속 {p['loss_streak']}번 손절이면 그날 쉬어요."},
        {"t": "개선", "d": f"매매 {p['review_every']}건 또는 {p.get('review_days', 5)}거래일마다 성적을 분석해 개선안을 내요. 매수가 너무 적으면 건너뛴 종목이 그 뒤 실제로 어땠는지 보고 "
                           "기준을 낮추는 안도 내요. " + ("모의라서 개선안을 스스로 반영해요(플랜에서 끌 수 있어요)." if p.get("auto_apply") and run.get("mode") != "real" else "승인해야 반영돼요.")},
    ]


def _why(raw):
    """거래에 적어 둔 AI의 근거(JSON)를 화면용으로: {"reasons": [...], "risks": [...]}"""
    try:
        d = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        return {"reasons": [str(raw)[:200]] if raw else [], "risks": []}
    if not isinstance(d, dict):
        return {"reasons": [str(x)[:200] for x in d][:4] if isinstance(d, list) else [], "risks": []}
    return {"reasons": [str(x)[:200] for x in (d.get("reasons") or [])][:4], "risks": [str(x)[:200] for x in (d.get("risks") or [])][:3]}


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _pctile(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))]


def _agg(rows):
    """거래 묶음의 성적."""
    n = len(rows)
    if not n:
        return {"n": 0}
    pcts = [r["pnl_pct"] or 0 for r in rows]
    wins = [x for x in pcts if x > 0]
    losses = [x for x in pcts if x <= 0]
    gain = sum(r["pnl"] for r in rows if (r["pnl"] or 0) > 0)
    loss = -sum(r["pnl"] for r in rows if (r["pnl"] or 0) < 0)
    return {"n": n, "win": len(wins), "win_rate": round(len(wins) / n * 100, 1), "avg_pct": round(sum(pcts) / n, 3),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0, "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "pnl": round(sum(r["pnl"] or 0 for r in rows), 2), "pf": round(gain / loss, 2) if loss > 0 else (None if not gain else 99.0)}


class AutoTrade:
    def __init__(self, rt):
        self.rt = rt
        db = rt.db
        db.x("""CREATE TABLE IF NOT EXISTS at_runs(
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, name TEXT NOT NULL, mode TEXT NOT NULL, mkt TEXT NOT NULL,
            style TEXT NOT NULL, model TEXT NOT NULL, budget REAL NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
            status TEXT NOT NULL, status_msg TEXT NOT NULL DEFAULT '', params TEXT NOT NULL, ver INTEGER NOT NULL DEFAULT 1,
            last_cycle REAL NOT NULL DEFAULT 0, last_review_n INTEGER NOT NULL DEFAULT 0, ended_at TEXT)""")
        db.x("""CREATE TABLE IF NOT EXISTS at_trades(
            id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, ver INTEGER NOT NULL, mkt TEXT NOT NULL, sym TEXT NOT NULL,
            name TEXT NOT NULL, exch TEXT NOT NULL DEFAULT '', qty INTEGER NOT NULL, status TEXT NOT NULL,
            entry_ts TEXT NOT NULL, entry_day TEXT NOT NULL, entry_hm TEXT NOT NULL, entry_price REAL NOT NULL,
            target_pct REAL NOT NULL, stop_pct REAL NOT NULL, trail_pct REAL NOT NULL DEFAULT 0,
            exit_by TEXT NOT NULL DEFAULT '', exit_date TEXT NOT NULL DEFAULT '',
            high REAL, low REAL, last REAL, exit_ts TEXT, exit_price REAL, reason TEXT,
            pnl REAL, pnl_pct REAL, fees REAL, mfe_pct REAL, mae_pct REAL, hold_min INTEGER,
            pick_id INTEGER, score REAL, win_prob REAL, cal_prob REAL, stance TEXT, feat TEXT, why TEXT,
            buy_no TEXT NOT NULL DEFAULT '', sell_no TEXT NOT NULL DEFAULT '', fill_ok INTEGER NOT NULL DEFAULT 1,
            exit_ok INTEGER NOT NULL DEFAULT 1, exit_day TEXT)""")
        db.x("CREATE INDEX IF NOT EXISTS ix_at_trades_run ON at_trades(run_id, status)")
        db.x("""CREATE TABLE IF NOT EXISTS at_log(
            id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, ts TEXT NOT NULL, day TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL, msg TEXT NOT NULL, data TEXT)""")
        db.x("CREATE INDEX IF NOT EXISTS ix_at_log_run ON at_log(run_id, id)")
        db.x("""CREATE TABLE IF NOT EXISTS at_equity(
            run_id INTEGER NOT NULL, day TEXT NOT NULL, equity REAL NOT NULL, PRIMARY KEY(run_id, day))""")
        db.x("""CREATE TABLE IF NOT EXISTS at_proposals(
            id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, ts TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL,
            summary TEXT NOT NULL, changes TEXT NOT NULL, stats TEXT, text TEXT, decided_at TEXT)""")
        db.x("""CREATE TABLE IF NOT EXISTS at_changes(
            id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, ts TEXT NOT NULL, ver INTEGER NOT NULL, source TEXT NOT NULL,
            changes TEXT NOT NULL, stats TEXT)""")
        AP._col(db, "at_runs", (("last_review_day", "TEXT"), ("lab_id", "INTEGER"), ("variant", "TEXT"), ("last_full", "REAL NOT NULL DEFAULT 0")))
        self._idx = {}        # 시장 → (받은 시각, 지수 등락률)
        self._idx_at = {}     # 지수 기호 → 날짜별 등락을 마지막으로 받은 시각
        db.x("CREATE TABLE IF NOT EXISTS idx_daily(sym TEXT NOT NULL, day TEXT NOT NULL, close REAL, pct REAL, hi REAL, lo REAL, PRIMARY KEY(sym, day))")
        self._last = 0.0
        self._busy = {}       # run_id → 추천을 받는 중인 스레드
        self._prog = {}       # run_id → 추천을 받는 동안의 진행 상태(화면에 단계별로 보여 준다)
        self._share = {}      # (시장, 방식, 모델, 웹 검색) → 방금 받은 추천(같은 조건의 자동매매끼리 함께 쓴다)
        self._share_lock = threading.Lock()
        self._day_note = {}   # (run_id, 날짜, 사유) → 같은 안내를 하루 한 번만 남기려고
        self._mkt = {}        # run_id → 시장(기록에 그 시장의 날짜를 붙이려고)

    # ------------------------------------------------------------------ 공통
    @staticmethod
    def _now():
        return datetime.now().isoformat(timespec="seconds")

    def _local(self, mkt):
        return AP.et_now() if mkt == "us" else AP.kst_now()

    def _session(self, mkt):
        ss = AP.session(mkt)
        if mkt == "kr" and ss["open"] and AP.kst_now().strftime("%Y-%m-%d") in (self.rt.settings.get("holidays") or []):
            ss = dict(ss, open=False, left_min=0)
        return ss

    def _quote(self, mkt, sym):
        return self.rt.aipick.quote(mkt, sym)

    @staticmethod
    def _params(raw):
        """저장된 플랜 값. 예전 판에서 만든 자동매매에 없는 값은 그때의 동작과 같은 값으로 채운다(화면에 'undefined'가 나오지 않게).
        빠른 재추천 간격은 채우지 않는다(없으면 예전 판이라는 표시로 _upgrade가 한 번 맞춘다)."""
        p = json.loads(raw)
        for k, v in PARAM_OLD.items():
            p.setdefault(k, v)
        return p

    def _run(self, rid):
        r = self.rt.db.one("SELECT * FROM at_runs WHERE id=?", (rid,))
        if not r:
            raise ValueError("자동매매를 찾지 못했어요")
        r["params"] = self._params(r["params"])
        return r

    def _runs(self, where="", args=()):
        rows = self.rt.db.q("SELECT * FROM at_runs " + where + " ORDER BY id DESC", args)
        for r in rows:
            r["params"] = self._params(r["params"])
        return rows

    def log(self, rid, kind, msg, data=None):
        if rid not in self._mkt:
            r = self.rt.db.one("SELECT mkt FROM at_runs WHERE id=?", (rid,))
            self._mkt[rid] = r["mkt"] if r else "kr"
        self.rt.db.x("INSERT INTO at_log(run_id,ts,day,kind,msg,data) VALUES(?,?,?,?,?,?)",
                     (rid, self._now(), AP.mkt_date(self._mkt[rid]), kind, msg,
                      json.dumps(data, ensure_ascii=False, default=str) if data is not None else None))

    def _note_once(self, run, why):
        """같은 사유의 '쉬는 중' 안내는 하루 한 번만 기록한다."""
        key = (run["id"], AP.mkt_date(run["mkt"]), why)
        if key in self._day_note:
            return
        if len(self._day_note) > 500:
            self._day_note.clear()
        self._day_note[key] = 1
        self.log(run["id"], "skip", why)

    def _set_status(self, run, status, msg=""):
        ended = self._now() if status == "ended" else None
        self.rt.db.x("UPDATE at_runs SET status=?, status_msg=?, ended_at=COALESCE(?, ended_at) WHERE id=?",
                     (status, msg, ended, run["id"]))
        run["status"], run["status_msg"] = status, msg

    # ------------------------------------------------------------------ 돈 계산
    def _open(self, rid):
        return self.rt.db.q("SELECT * FROM at_trades WHERE run_id=? AND status='open' ORDER BY id", (rid,))

    def _closed(self, rid):
        return self.rt.db.q("SELECT * FROM at_trades WHERE run_id=? AND status='closed' ORDER BY exit_ts, id", (rid,))

    def money(self, run, live=False):
        """굴리는 돈(예산 + 실현손익), 남은 현금, 보유 평가."""
        realized = self.rt.db.one("SELECT COALESCE(SUM(pnl),0) s FROM at_trades WHERE run_id=? AND status='closed'", (run["id"],))["s"]
        opens = self._open(run["id"])
        cost = sum(t["qty"] * t["entry_price"] for t in opens)
        value = 0.0
        for t in opens:
            px = t["last"] or t["entry_price"]
            if live:
                px = self._quote(t["mkt"], t["sym"]).get("price") or px
            value += t["qty"] * px
        base = run["budget"] + realized
        return {"budget": run["budget"], "realized": round(realized, 2), "base": round(base, 2), "cash": round(base - cost, 2),
                "open_cost": round(cost, 2), "open_value": round(value, 2), "unrealized": round(value - cost, 2),
                "equity": round(base - cost + value, 2),
                "ret_pct": round((base - cost + value) / run["budget"] * 100 - 100, 2) if run["budget"] else 0}

    def _fees(self, mkt, entry, exit_, qty):
        """왕복 비용과 손익(시장 통화)."""
        if mkt == "kr":
            pnl, fees = C.trade_pnl(entry, exit_, qty, self.rt.settings.get("costs"))
            return float(pnl), float(fees)
        fees = round((entry + exit_) * qty * US_FEE / 200, 2)
        return round((exit_ - entry) * qty - fees, 2), fees

    # ------------------------------------------------------------------ 준비 상태
    def real_broker(self):
        """실전 주문에 쓸 계좌(설정의 기본 주문 계좌). (브로커, 못 쓰는 이유)"""
        b = self.rt.brokers.get("scalp")
        if b is None:
            return None, self.rt.errors.get("scalp") or "주문 계좌가 준비되지 않았어요"
        if getattr(b, "client", None) is None:
            return None, "실전은 키움 주문 계좌가 필요해요. 설정 → 계좌에서 주문 방식을 '키움으로 주문'으로 바꿔 주세요"
        return b, ""

    def ready(self):
        b, why = self.real_broker()
        return {"ai": self.rt.ai.configured(), "data": self.rt.invest.kr() is not None,
                "real": {"ok": b is not None, "label": getattr(b, "label", None), "why": why}}

    # ------------------------------------------------------------------ 만들기·조작
    def _read_form(self, d):
        mkt = "us" if d.get("mkt") == "us" else "kr"
        style = d.get("style") if d.get("style") in STYLES else "day"
        try:
            budget = float(str(d.get("budget") or 0).replace(",", ""))
            days = int(float(d.get("days") or 0))
        except ValueError:
            raise ValueError("금액과 기간은 숫자로 넣어 주세요")
        if budget < MIN_BUDGET[mkt]:
            raise ValueError(f"쓸 돈은 {'30만 원' if mkt == 'kr' else '300달러'} 이상으로 넣어 주세요")
        if budget > (5_000_000_000 if mkt == "kr" else 5_000_000):
            raise ValueError("금액을 확인해 주세요")
        if not 1 <= days <= 365:
            raise ValueError("기간은 1일~365일 사이로 넣어 주세요")
        if style != "day" and days < 7:
            raise ValueError("스윙·장기는 기간을 7일 이상으로 넣어 주세요")
        from .ai import MODELS
        model = d.get("model") if d.get("model") in MODELS else "auto" if d.get("model") == "auto" else (self.rt.ai.conf().get("model") or "claude-sonnet-5-5")
        return mkt, style, budget, days, model

    def _per_usd(self, style, model, web):
        m = self.rt.ai.meter("pick" if style == "day" else "pickw", model, web)
        return (m["avg_usd"] if m.get("avg_usd") is not None else (m["usd_lo"] + m["usd_hi"]) / 2), m

    def _quick_usd(self):
        """빠른 재추천 한 번의 비용(달러). 써 본 평균이 있으면 그 값."""
        from .ai import LITE_MODEL
        m = self.rt.ai.meter("pickq", LITE_MODEL, False)
        return m["avg_usd"] if m.get("avg_usd") is not None else (m["usd_lo"] + m["usd_hi"]) / 2

    @staticmethod
    def _window(p):
        """매수 시간의 길이(분)."""
        (h1, m1), (h2, m2) = (map(int, p[k].split(":")) for k in ("entry_start", "entry_end"))
        return max(0, (h2 * 60 + m2) - (h1 * 60 + m1))

    def day_cap(self, budget, mkt):
        """직접 만든 자동매매의 하루 AI 비용 한도(달러): 시드머니에 비례(한 달 COST_PCT%), 최소 하루 RUN_DAY_MIN_USD."""
        usd = budget / (self.rt.ai.fx() if mkt == "kr" else 1.0)
        return round(max(RUN_DAY_MIN_USD, usd * COST_PCT / 100 / MONTH_DAYS), 2)

    def fit_cost(self, budget, mkt, style, mode, p, model, day_usd=None, ceil=None):
        """AI 비용을 쓸 돈에 맞춘다. 직접 만든 자동매매(실전·모의)는 하루 한도 = 시드머니에 비례(한 달 1%), 최소 $3.
        실험 묶음은 day_usd(자동 테스트가 정한 몫)를 넘지 않게. 모델·하루 횟수·웹 검색을 고른다(model이 'auto'일 때).
        직접 고른 모델이면 계산만 해서 알려 준다.
        빠른 재추천이 켜져 있으면 그 몫을 먼저 떼고, 남은 돈으로 정밀 추천(좋은 엔진·뉴스 검색)을 맞춘다.
        반환: (모델, 바뀐 플랜 값, 안내)"""
        from .ai import MODELS
        fx = self.rt.ai.fx()
        lab = day_usd is not None
        cap_day = float(day_usd) if lab else self.day_cap(budget, mkt)
        cap_month = cap_day * MONTH_DAYS
        conf = self.rt.ai.conf().get("model") or "claude-sonnet-5-5"
        win, q_per = self._window(p), self._quick_usd()

        def info(mdl, cycles, web, qg=0):
            per, _ = self._per_usd(style, mdl, web)
            nq = max(0, win // qg - cycles) if qg else 0             # 정밀 추천을 받는 차례에는 빠른 재추천을 받지 않는다
            day = per * cycles + q_per * nq
            month = day * MONTH_DAYS
            return {"model": mdl, "label": MODELS[mdl][0], "cycles": cycles, "web": web, "per_usd": round(per, 3), "month_usd": round(month, 2),
                    "month_cur": round(month * (fx if mkt == "kr" else 1.0)), "pct": round(month / (budget / (fx if mkt == "kr" else 1.0)) * 100, 2),
                    "cap_usd": round(cap_month, 2), "ok": month <= cap_month + 1e-6, "fx": fx,
                    "quick_gap": qg, "quick_n": nq, "quick_usd": round(q_per, 4), "day_usd": round(day, 3)}

        def limit(c):
            return math.ceil(c["day_usd"] * 1.4 * 10) / 10
        qg0 = int(p.get("quick_gap_min") or 0)
        if model != "auto":
            c = info(model, p["cycles_per_day"], bool(p.get("web_search", True)), qg0)
            c["auto"] = False
            c["cap_day"] = cap_day
            return model, {"ai_daily_usd": cap_day if not lab else max(1.0, limit(c))}, c
        order = sorted(MODELS, key=lambda k: -MODELS[k][1])                     # 비싼(좋은) 모델부터
        # 쓸 수 있는 가장 좋은 엔진: 설정한 엔진까지. 성과가 나빠 올린 단계(ceil)가 있으면 거기까지(Opus·Fable)
        top = max(MODELS.get(conf, MODELS["claude-sonnet-5-5"])[1], MODELS[ceil][1] if ceil in MODELS else 0)
        order = [m for m in order if MODELS[m][1] <= top] or order[-1:]

        def search(order):
            if qg0:
                # 자주 묻는 일은 빠른 재추천이 맡으니, 정밀 추천은 하루 2번(시작할 때·한가운데)까지만 받고
                # 뉴스 검색 → 횟수 → 좋은 엔진 순으로 지킨다. 그래도 넘으면 빠른 재추천 간격을 넓힌다
                for qg in [g for g in (qg0, 30, 45, 60, 90, 120) if g >= qg0]:
                    for web in (True, False):
                        for cycles in range(min(p["cycles_per_day"], 2), 0, -1):
                            for mdl in order:
                                c = info(mdl, cycles, web, qg)
                                if c["ok"]:
                                    return c
                return None
            for cycles in range(p["cycles_per_day"], 0, -1):        # 하루 횟수 → 웹 검색(뉴스) → 좋은 모델 순으로 지킨다
                for web in (True, False):
                    for mdl in order:
                        c = info(mdl, cycles, web)
                        if c["ok"]:
                            return c
            return None
        # 엔진을 올린 단계면 그 엔진을 먼저 지킨다(횟수를 줄여서라도). 한도 안에 못 들면 평소대로
        up = ceil in MODELS and MODELS[ceil][1] > MODELS.get(conf, MODELS["claude-sonnet-5-5"])[1]
        best = (search([ceil]) if up else None) or search(order)
        if not best:
            best = info(order[-1], 1, False, 120 if qg0 else 0)                  # 가장 싼 조합으로도 넘는다 → 알리고 그 조합으로
        best["auto"] = True
        best["cap_day"] = cap_day
        # 하루 한도: 직접 만든 것은 시드에 비례한 한도 그대로(쓸 수 있는 만큼), 실험은 예상 비용의 1.4배
        patch = {"cycles_per_day": best["cycles"], "web_search": best["web"], "ai_daily_usd": cap_day if not lab else max(0.3, limit(best))}
        if qg0:
            # 정밀 추천은 매수 시간에 고르게 편다(예: 2번이면 시작할 때와 한가운데)
            patch.update(quick_gap_min=best["quick_gap"], cycle_gap_min=int(min(240, max(60, win // best["cycles"]))))
        elif best["cycles"] < p["cycles_per_day"]:
            patch["cycle_gap_min"] = max(p["cycle_gap_min"], 120)
        return best["model"], patch, best

    def readiness(self, mkt, style):
        """실전을 권할 만한가: 같은 시장·방식의 모의(실험 포함)에서 끝난 거래 30건 이상이고, AI 비용까지 뺀 손익이 플러스인 플랜이 있는가."""
        fx = self.rt.ai.fx() if mkt == "kr" else 1.0
        best, total = None, 0
        for r in self._runs("WHERE mode='paper' AND mkt=? AND style=?", (mkt, style)):
            a = self.rt.db.one("SELECT COUNT(*) n, COALESCE(SUM(pnl),0) pnl FROM at_trades WHERE run_id=? AND status='closed'", (r["id"],))
            total += a["n"]
            if a["n"] < READY_N:
                continue
            net = a["pnl"] - self._ai_usd(r["id"]) * fx
            x = {"id": r["id"], "name": r["name"], "n": a["n"], "net": round(net, 2), "net_pct": round(net / r["budget"] * 100, 2)}
            if best is None or x["net_pct"] > best["net_pct"]:
                best = x
        ok = bool(best and best["net"] > 0)
        if ok:
            text = f"모의 '{best['name']}'가 {best['n']}건에서 AI 비용까지 빼고 {best['net_pct']:+.2f}%였어요. 같은 플랜으로 작게 시작해 보세요."
        elif best:
            text = f"모의에서 {best['n']}건을 해 봤지만 AI 비용까지 빼면 {best['net_pct']:+.2f}%예요. 아직 실전을 권하지 않아요."
        else:
            text = f"모의에서 끝난 거래가 한 플랜에 {READY_N}건은 쌓여야 판단할 수 있어요(지금 모두 합쳐 {total}건). 아직 실전을 권하지 않아요."
        return {"ok": ok, "need": READY_N, "total": total, "best": best, "text": text}

    def _ai_usd(self, rid):
        return sum(float((json.loads(r["data"]) or {}).get("usd") or 0) for r in self.rt.db.q("SELECT data FROM at_log WHERE run_id=? AND kind='cycle' AND data IS NOT NULL", (rid,)))

    def preview(self, d):
        mkt, style, budget, days, model = self._read_form(d)
        mode = "real" if d.get("mode") == "real" else "paper"
        p = make_plan(budget, mkt, style, days, mode)
        for k in ("daily_loss_pct", "total_loss_pct"):
            if d.get(k) not in (None, ""):
                p[k] = _clamp_key(k, d[k])
        model, patch, cost = self.fit_cost(budget, mkt, style, mode, p, model, d.get("day_usd"))
        p.update(patch)
        var = d.get("variant") if d.get("variant") in VARIANTS else None
        if var:                                      # 모의 실험에서 검증한 플랜을 그대로 가져온다(실전은 관망에 쉬는 것을 지킨다)
            apply_variant(p, var, keep=("skip_watch", "watch_mult") if mode == "real" else ())
        run = {"mkt": mkt, "style": style, "budget": budget, "params": p, "mode": mode, "model": model}
        _, m = self._per_usd(style, model, p["web_search"])
        warn = None
        if cost["ok"] and mode == "real" and cost["pct"] > COST_PCT * 1.5:
            warn = (f"AI 비용이 한 달에 약 {cost['month_cur']:,.0f}{'원' if mkt == 'kr' else '달러'}로, 쓸 돈의 {cost['pct']:g}%예요. "
                    f"하루 최소 ${RUN_DAY_MIN_USD:g}를 쓰게 해 두어서 쓸 돈이 적으면 비중이 커요. 매매로 이만큼을 더 벌어야 본전이에요.")
        if not cost["ok"]:
            warn = (f"AI 비용이 한 달에 약 {cost['month_cur']:,.0f}{'원' if mkt == 'kr' else '달러'}로, 쓸 돈의 {cost['pct']:g}%예요. 매매로 이만큼을 더 벌어야 본전이에요."
                    if mode == "real" else f"AI 비용이 한 달에 약 ${cost['month_usd']:g}예요(모의도 AI 비용은 실제로 나가요).")
        return {"mkt": mkt, "style": style, "budget": budget, "days": days, "model": model, "params": p, "plan": plan_text(run), "mode": mode,
                "cost": dict(cost, warn=warn), "ready": self.readiness(mkt, style) if mode == "real" else None,
                "variant": {"key": var, "label": VARIANTS[var][0]} if var else None,
                "ai_cost": {"per_usd": cost["per_usd"], "day_usd": round(min(cost["day_usd"], p["ai_daily_usd"]), 2),
                            "fx": m.get("fx"), "budget_usd": m.get("budget_usd"), "month_usd": m.get("month_usd")}}

    def create(self, d):
        pv = self.preview(d)
        mode = "real" if d.get("mode") == "real" else "paper"
        rd = self.ready()
        if not rd["ai"]:
            raise ValueError("설정 → AI에서 Claude API 키를 먼저 넣어 주세요")
        if not rd["data"]:
            raise ValueError("실시간 순위가 필요해요. 설정 → 계좌에서 키움 계좌를 먼저 연결해 주세요")
        if mode == "real":
            if not rd["real"]["ok"]:
                raise ValueError(rd["real"]["why"])
            if d.get("confirm") != "실전":
                raise ValueError("실전은 실제 주문이 나가요. 확인란에 '실전'을 적어 주세요")
        if self._runs("WHERE status IN ('running','paused','closing') AND mode=? AND mkt=? AND lab_id IS NULL", (mode, pv["mkt"])):
            raise ValueError(f"{'실전' if mode == 'real' else '모의'} {'국내' if pv['mkt'] == 'kr' else '미국'} 자동매매가 이미 돌고 있어요. 먼저 종료해 주세요")
        today = self._local(pv["mkt"]).date()
        end = today + timedelta(days=pv["days"] - 1)
        name = str(d.get("name") or "").strip()[:30] or \
            f"{'국내' if pv['mkt'] == 'kr' else '미국'} {STYLES[pv['style']]['label']} {'실전' if mode == 'real' else '모의'}"
        rid = self.rt.db.x(
            "INSERT INTO at_runs(created_at,name,mode,mkt,style,model,budget,start_date,end_date,status,params) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (self._now(), name, mode, pv["mkt"], pv["style"], pv["model"], pv["budget"], today.isoformat(), end.isoformat(),
             "running", json.dumps(dict(pv["params"], cap_v=2), ensure_ascii=False)))
        self.rt.db.x("INSERT INTO at_changes(run_id,ts,ver,source,changes,stats) VALUES(?,?,?,?,?,?)",
                     (rid, self._now(), 1, "start", json.dumps([], ensure_ascii=False), None))
        self.log(rid, "start", f"자동매매를 시작했어요 · {name}", {"params": pv["params"]})
        self.rt.notify(f"[moa 자동매매] {name} 시작 · {pv['budget']:,.0f}{'원' if pv['mkt'] == 'kr' else '달러'}")
        return self.detail(rid)

    def control(self, rid, action):
        run = self._run(rid)
        if run["status"] == "ended":
            raise ValueError("이미 끝난 자동매매예요")
        if action == "pause":
            if run["status"] == "running":
                self._set_status(run, "paused", "새 매수를 멈췄어요. 들고 있는 종목은 계속 지켜봐요")
                self.log(rid, "control", "새 매수 멈춤")
        elif action == "resume":
            if run["status"] == "paused":
                self._set_status(run, "running", "")
                self.log(rid, "control", "다시 시작")
        elif action == "end":
            if self._open(rid):
                self._set_status(run, "closing", "전부 정리하는 중이에요(장이 열려 있을 때 팔아요)")
                self.log(rid, "control", "종료 요청 · 보유 종목 정리 시작")
                self._manage(run)
            else:
                self._finish(run, "사용자가 종료했어요")
        else:
            raise ValueError("알 수 없는 동작이에요")
        return self.detail(rid)

    def _finish(self, run, why):
        m = self.money(run)
        self._set_status(run, "ended", why)
        self._snap(run)
        self.log(run["id"], "end", f"종료 · {why} · 수익률 {m['ret_pct']:+.2f}%", m)
        self.rt.notify(f"[moa 자동매매] {run['name']} 종료 · {why} · {m['ret_pct']:+.2f}%")

    def busy(self):
        """추천을 받아 주문하려는 중인지(업데이트·되돌리기를 잠깐 미룰 때 본다)."""
        return any(t.is_alive() for t in self._busy.values())

    def active(self):
        return bool(self.rt.db.one("SELECT 1 x FROM at_runs WHERE status IN ('running','paused','closing') LIMIT 1"))

    # ------------------------------------------------------------------ 반복(엔진)
    def tick(self, force=False):
        if not force and time.time() - self._last < 5:
            return
        self._last = time.time()
        try:
            self._upgrade()
            self._cap_upgrade()
            self._engine_watch()
            self._autopilot()
        except Exception as e:
            self.rt.db.log("system", "error", f"모의 자동 테스트 오류: {type(e).__name__}: {e}", self._now())
        for run in self._runs("WHERE status IN ('running','paused','closing')"):
            try:
                self._manage(run)
                if run["status"] == "running":
                    self._maybe_cycle(run)
            except Exception as e:
                self.rt.db.log("system", "error", f"자동매매({run['id']}) 처리 오류: {type(e).__name__}: {e}", self._now())

    # ----- 보유 종목 지켜보기·팔기
    def _manage(self, run):
        mkt, p = run["mkt"], run["params"]
        ss = self._session(mkt)
        today = AP.mkt_date(mkt)
        opens = self._open(run["id"])
        if run["mode"] == "real":
            self._reconcile(run)
        past_end = today > run["end_date"]
        last_day = today >= run["end_date"]
        if ss["open"]:
            close_soon = ss["left_min"] <= 8
            now_hm = AP.kst_now().strftime("%H:%M")
            if run["status"] != "closing" and opens and (past_end or (last_day and close_soon)):
                self._set_status(run, "closing", "기간이 끝나 보유 종목을 정리하는 중이에요")
                self.log(run["id"], "control", "기간 종료 · 보유 종목 정리")
            for t in opens:
                if not t["fill_ok"]:
                    continue  # 실전: 매수 체결을 아직 확인하지 못함
                price = self._quote(mkt, t["sym"]).get("price")
                if not price:
                    continue
                high, low = max(t["high"] or t["entry_price"], price), min(t["low"] or t["entry_price"], price)
                self.rt.db.x("UPDATE at_trades SET high=?, low=?, last=? WHERE id=?", (high, low, price, t["id"]))
                t.update(high=high, low=low, last=price)
                chg = (price / t["entry_price"] - 1) * 100
                reason = None
                if run["status"] == "closing":
                    reason = "종료 정리"
                elif chg >= t["target_pct"]:
                    reason = "익절"
                elif chg <= -t["stop_pct"]:
                    reason = "손절"
                elif t["trail_pct"] and high > t["entry_price"] * (1 + t["trail_pct"] / 100) \
                        and price <= high * (1 - t["trail_pct"] / 100) and price > t["entry_price"]:
                    reason = "추적 익절"
                elif run["style"] == "day":
                    if close_soon or (t["exit_by"] and mkt == "kr" and now_hm >= t["exit_by"]) or t["entry_day"] < today:
                        reason = "시간 정리"
                elif t["exit_date"] and today >= t["exit_date"] and close_soon:
                    reason = "기간 정리"
                if reason:
                    self._sell(run, t, price, reason)
        opens = self._open(run["id"])
        if run["status"] == "closing" and not opens:
            msg = run["status_msg"] or ""
            self._finish(run, "기간이 끝났어요" if msg.startswith("기간") else "손실 한도에 닿았어요" if "손실" in msg else "사용자가 종료했어요")
            return
        if past_end and run["status"] in ("running", "paused") and not opens:
            self._finish(run, "기간이 끝났어요")
            return
        # 전체 손실 한도: 평가손실 포함
        if run["status"] in ("running", "paused"):
            m = self.money(run)
            if m["equity"] <= run["budget"] * (1 - p["total_loss_pct"] / 100):
                self.log(run["id"], "risk", f"전체 손실 한도(-{p['total_loss_pct']:g}%)에 닿아 전부 정리하고 종료해요", m)
                self.rt.notify(f"[moa 자동매매] {run['name']} 전체 손실 한도 도달 · 전부 정리 후 종료")
                if opens:
                    self._set_status(run, "closing", "손실 한도에 닿아 전부 정리하는 중이에요")
                    self._manage(run)
                else:
                    self._finish(run, "손실 한도에 닿았어요")
                return
        if not ss["open"]:
            ph, day = AP.phase_at(mkt, AP.kst_now().replace(tzinfo=None))
            if ph == "post" and not self.rt.db.one("SELECT 1 x FROM at_equity WHERE run_id=? AND day=?", (run["id"], day.isoformat())):
                self._snap(run, day.isoformat())  # 하루 한 번, 장 마감 뒤 평가액
                if run["status"] in ("running", "paused"):
                    try:
                        self._daily_review(run, day.isoformat())
                    except Exception as e:
                        self.rt.db.log("system", "error", f"자동매매 하루 점검 오류: {type(e).__name__}: {e}", self._now())

    def _snap(self, run, day=None):
        self.rt.db.x("INSERT INTO at_equity(run_id,day,equity) VALUES(?,?,?) ON CONFLICT(run_id,day) DO UPDATE SET equity=excluded.equity",
                     (run["id"], day or AP.mkt_date(run["mkt"]), self.money(run)["equity"]))

    def _sell(self, run, t, price, reason):
        mkt, now = run["mkt"], self._now()
        sell_no, exit_ok = "", 1
        if run["mode"] == "real":
            b, why = self.real_broker()
            try:
                if b is None:
                    raise RuntimeError(why)
                if mkt == "us":
                    no, exch = b.client.us_order("sell", t["sym"], t["qty"], None, t["exch"] or None)
                    sell_no = f"{no}|{exch}"
                else:
                    sell_no = b.sell(t["sym"], t["qty"], None).get("broker_no", "")
                self.rt.db.x("INSERT INTO orders(ts,engine,mode,code,name,side,qty,price,status,fill_price,broker_no,msg) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                             (now, "scalp", b.mode, t["sym"], t["name"], "sell", t["qty"], None, "accepted", price, sell_no, f"AI 자동매매 {reason}"))
                exit_ok = 0
            except Exception as e:
                self._note_once(run, f"{t['name']} {reason} 매도 실패: {e} (다음 확인 때 다시 시도)")
                return
            fill = price
        else:
            fill = C.sell_fill(price, self.rt.settings.get("costs")) if mkt == "kr" else round(price * (1 - US_SLIP), 4)
        self._close(run, t, fill, reason, sell_no, exit_ok)

    def _close(self, run, t, fill, reason, sell_no="", exit_ok=1):
        pnl, fees = self._fees(run["mkt"], t["entry_price"], fill, t["qty"])
        cost = t["entry_price"] * t["qty"]
        hi, lo = max(t["high"] or t["entry_price"], fill), min(t["low"] or t["entry_price"], fill)
        try:
            hold = int((datetime.now() - datetime.fromisoformat(t["entry_ts"])).total_seconds() // 60)
        except Exception:
            hold = None
        self.rt.db.x("UPDATE at_trades SET status='closed', exit_ts=?, exit_price=?, reason=?, pnl=?, pnl_pct=?, fees=?, mfe_pct=?, mae_pct=?, "
                     "hold_min=?, sell_no=?, exit_ok=?, high=?, low=?, last=?, exit_day=? WHERE id=?",
                     (self._now(), fill, reason, pnl, round(pnl / cost * 100, 3) if cost else 0, fees,
                      round((hi / t["entry_price"] - 1) * 100, 3), round((lo / t["entry_price"] - 1) * 100, 3), hold,
                      sell_no, exit_ok, hi, lo, fill, AP.mkt_date(run["mkt"]), t["id"]))
        unit = "원" if run["mkt"] == "kr" else "달러"
        self.log(run["id"], "sell", f"{t['name']} {t['qty']}주 {reason} · {pnl:+,.0f}{unit} ({pnl / cost * 100:+.2f}%)" if run["mkt"] == "kr"
                 else f"{t['name']} {t['qty']}주 {reason} · {pnl:+,.2f}{unit} ({pnl / cost * 100:+.2f}%)", {"trade": t["id"]})
        self.rt.notify(f"[moa 자동매매] {t['name']} {reason} {pnl / cost * 100:+.2f}%")
        self._after_close(run)

    def _after_close(self, run):
        """표본이 쌓였으면 분석해서 개선안을 만든다."""
        p = run["params"]
        n = self.rt.db.one("SELECT COUNT(*) n FROM at_trades WHERE run_id=? AND status='closed'", (run["id"],))["n"]
        if n - (run.get("last_review_n") or 0) < int(p.get("review_every") or 30):
            return
        self.rt.db.x("UPDATE at_runs SET last_review_n=? WHERE id=?", (n, run["id"]))
        run["last_review_n"] = n
        try:
            pr = self.review(run["id"], auto=True)
            if pr and pr.get("changes") and not self._auto_apply(run, pr, f"거래 {n}건 분석"):
                self.rt.notify(f"[moa 자동매매] {run['name']} 개선안이 나왔어요({len(pr['changes'])}건). 앱에서 확인해 주세요")
        except Exception as e:
            self.rt.db.log("system", "error", f"자동매매 분석 오류: {e}", self._now())
        if p.get("ai_review") and self.rt.ai.configured():
            threading.Thread(target=self._ai_review_bg, args=(run["id"],), daemon=True).start()

    # ----- 실전: 체결 맞추기
    def _reconcile(self, run):
        rows = self.rt.db.q("SELECT * FROM at_trades WHERE run_id=? AND ((status='open' AND fill_ok=0) OR (status='closed' AND exit_ok=0))", (run["id"],))
        if not rows:
            return
        b, _ = self.real_broker()
        if b is None:
            return
        try:
            fills = b.client.us_fills_today() if run["mkt"] == "us" else b.client.fills_today()
        except Exception:
            fills = None
        for t in rows:
            buying = t["status"] == "open"
            no = str((t["buy_no"] if buying else t["sell_no"]) or "").split("|")[0].lstrip("0")
            ts = datetime.fromisoformat(t["entry_ts"] if buying else t["exit_ts"])
            age = (datetime.now() - ts).total_seconds()
            f = (fills or {}).get(no) if no else None
            if buying:
                if f and f["qty"] > 0:
                    if f["qty"] >= t["qty"] or age > 120:
                        self.rt.db.x("UPDATE at_trades SET qty=?, entry_price=?, high=?, low=?, fill_ok=1 WHERE id=?",
                                     (min(t["qty"], f["qty"]), f["price"], f["price"], f["price"], t["id"]))
                        if f["qty"] < t["qty"]:
                            self._cancel_rest(run, b, t, no)
                elif age > 180:
                    # 3분이 지나도 체결 내역이 없으면: 남은 주문을 취소하고 이 거래는 없던 것으로 한다
                    self._cancel_rest(run, b, t, no)
                    held = self._held_qty(run, b, t["sym"])
                    if fills is not None and not held:
                        self.rt.db.x("UPDATE at_trades SET status='cancelled', reason='체결 안 됨' WHERE id=?", (t["id"],))
                        self.log(run["id"], "skip", f"{t['name']} 매수 주문이 체결되지 않아 취소했어요")
                    elif held:
                        self.rt.db.x("UPDATE at_trades SET qty=?, fill_ok=1 WHERE id=?", (min(t["qty"], held), t["id"]))
                    elif age > 900:  # 체결 조회가 계속 안 되고 잔고에도 없음
                        self.rt.db.x("UPDATE at_trades SET status='cancelled', reason='체결 확인 실패' WHERE id=?", (t["id"],))
                        self.log(run["id"], "skip", f"{t['name']} 매수 체결을 확인하지 못해 기록에서 뺐어요. 키움 앱에서 잔고를 확인해 주세요")
                        self.rt.notify(f"[moa 자동매매] {t['name']} 매수 체결 확인 실패. 키움 잔고를 확인해 주세요")
            else:
                if f and f["qty"] > 0:
                    pnl, fees = self._fees(run["mkt"], t["entry_price"], f["price"], t["qty"])
                    cost = t["entry_price"] * t["qty"]
                    self.rt.db.x("UPDATE at_trades SET exit_price=?, pnl=?, pnl_pct=?, fees=?, exit_ok=1 WHERE id=?",
                                 (f["price"], pnl, round(pnl / cost * 100, 3) if cost else 0, fees, t["id"]))
                elif age > 6 * 3600:
                    self.rt.db.x("UPDATE at_trades SET exit_ok=1 WHERE id=?", (t["id"],))  # 예상가로 확정

    def _held_qty(self, run, b, sym):
        try:
            rows = b.client.us_holdings() if run["mkt"] == "us" else b.positions()
            return next((int(h.get("qty") or 0) for h in rows if str(h["code"]).upper() == sym), 0)
        except Exception:
            return 0

    def _cancel_rest(self, run, b, t, no):
        if not no:
            return
        try:
            if run["mkt"] == "us":
                b.client.us_cancel(no, t["sym"], t["exch"] or None)
            else:
                b.cancel(no, t["sym"])
        except Exception:
            pass

    # ----- 새 매수: 조건이 되면 AI 추천을 받는다
    def _today_closed(self, run):
        return self.rt.db.q("SELECT pnl, exit_ts FROM at_trades WHERE run_id=? AND status='closed' AND exit_day=? ORDER BY exit_ts, id",
                            (run["id"], AP.mkt_date(run["mkt"])))

    def _sibs(self, run):
        """실험 묶음이면 함께 도는 실험들(돌고 있는 것만, 번호순). 아니면 None."""
        if not run.get("lab_id"):
            return None
        return sorted(self._runs("WHERE lab_id=? AND status='running'", (run["lab_id"],)), key=lambda r: r["id"])

    def _room(self, run):
        """이 자동매매가 지금 새로 살 수 없는 자기 사정(자리·손실 한도·연속 손절·현금). 없으면 None."""
        p, mkt = run["params"], run["mkt"]
        opens = self._open(run["id"])
        if len(opens) >= p["max_positions"]:
            return "full", "보유 종목 수가 꽉 찼어요"
        closed = self._today_closed(run)
        day_pnl = sum(c["pnl"] or 0 for c in closed) + sum(t["qty"] * ((t["last"] or t["entry_price"]) - t["entry_price"]) for t in opens)
        if day_pnl <= -run["budget"] * p["daily_loss_pct"] / 100:
            return "dayloss", f"하루 손실 한도(-{p['daily_loss_pct']:g}%)에 닿아 오늘은 새로 사지 않아요"
        k = int(p["loss_streak"])
        if k and len(closed) >= k and all((c["pnl"] or 0) < 0 for c in closed[-k:]):
            return "streak", f"오늘 연속 {k}번 손절이라 새로 사지 않아요"
        if self.money(run)["cash"] < (1000 if mkt == "kr" else 2):
            return "cash", "남은 현금이 없어요"
        return None

    def index_pct(self, mkt):
        """시장 대표 지수의 오늘 등락률(%) 평균. 1분 동안은 받아 둔 값을 쓴다. 못 받으면 None."""
        at, v = self._idx.get(mkt, (0, None))
        if time.time() - at < 60:
            return v
        try:
            rows = (self.rt.market.kr_indices() if mkt == "kr" else self.rt.market.us_indices())[:2]
            xs = [float(r["change_pct"]) for r in rows if r.get("price") and r.get("change_pct") is not None]
            v = round(sum(xs) / len(xs), 2) if xs else None
        except Exception:
            v = None
        self._idx[mkt] = (time.time(), v)
        return v

    def _due(self, run):
        """지금 받을 추천의 종류와 다음 차례. kind: full(정밀 — 좋은 엔진·뉴스 검색)·quick(빠른 재추천 — 가장 싼 엔진)·None(아직).
        빠른 재추천이 꺼져 있으면 예전 그대로 '하루 횟수 + 간격'만 본다."""
        p, now = run["params"], time.time()
        rows = self.rt.db.q("SELECT ts, data FROM at_log WHERE run_id=? AND kind='cycle' AND day=? ORDER BY id", (run["id"], AP.mkt_date(run["mkt"])))
        n_quick = sum(1 for r in rows if r["data"] and '"lite": true' in r["data"])
        n_full = len(rows) - n_quick
        qg, fg = int(p.get("quick_gap_min") or 0), int(p["cycle_gap_min"])
        since = now - (run["last_cycle"] or 0)
        out = {"rows": rows, "n_full": n_full, "n_quick": n_quick, "quick": qg, "since": since, "gap": qg or fg, "kind": None, "code": "gap", "left": 0, "span": (qg or fg) * 60}
        if not qg:
            if n_full >= p["cycles_per_day"]:
                out["code"] = "cycles"
            elif since >= fg * 60:
                out["kind"] = "full"
            else:
                out["left"] = fg * 60 - since
            return out
        # 그날 첫 추천과, 정해 둔 간격이 지난 뒤의 추천은 정밀로(하루 횟수 안에서). 그 밖에는 빠른 재추천.
        # 빠른 재추천을 받은 직후에 정밀 추천이 겹치지 않게 반 간격은 띄운다
        t_quick = qg * 60 - since
        t_full = float("inf")
        if n_full < p["cycles_per_day"]:
            t_full = max(fg * 60 - (now - (run.get("last_full") or 0)) if n_full else 0, min(qg, fg) * 30 - since)
        out["next"] = "full" if t_full <= max(t_quick, 0) else "quick"     # 둘 다 차례면 정밀이 먼저
        out["left"] = max(0, min(t_quick, t_full))
        if out["left"] <= 0:
            out["kind"] = out["next"]
        return out

    def gate(self, run):
        """지금 새로 사도 되는지. (허용, 사유)"""
        ok, _, why = self.gate_code(run)
        return ok, why

    def gate_code(self, run):
        """지금 새로 사도 되는지와 그 사유의 종류. (허용, 코드, 사유) — 코드는 화면에서 '다음에 언제 움직이는지'를 계산할 때 쓴다."""
        p, mkt = run["params"], run["mkt"]
        ss = self._session(mkt)
        if not ss["open"]:
            return False, "closed", "장이 열려 있지 않아요"
        if AP.mkt_date(mkt) > run["end_date"]:
            return False, "over", "기간이 끝났어요"
        hm = self._local(mkt).strftime("%H:%M")
        es, ee = self._entry(run, self._local(mkt).date())
        if not (es <= hm <= ee):
            return False, "time", f"매수 시간({es}~{ee})이 아니에요"
        sibs = self._sibs(run)
        if sibs is None:
            r = self._room(run)
            if r:
                return (False,) + r
        elif not any(self._room(x) is None for x in sibs):
            return False, "full", "모든 실험이 자리가 없거나 오늘은 쉬는 중이에요"
        day = AP.mkt_date(mkt)
        D = self._due(run)
        cycles, since = D["rows"], D["since"]
        if not D["kind"]:
            if D["code"] == "cycles":
                return False, "cycles", "오늘 추천 횟수를 다 썼어요"
            return False, "gap", "다음 추천 시간을 기다리는 중이에요"
        ids = [x["id"] for x in sibs] if sibs else [run["id"]]
        usd = sum(float((json.loads(c["data"]) if c["data"] else {}).get("usd") or 0) for c in self.rt.db.q(
            f"SELECT data FROM at_log WHERE run_id IN ({','.join('?' * len(ids))}) AND kind='cycle' AND day=?", (*ids, day)))
        if usd >= float(p.get("ai_daily_usd") or 1):
            return False, "aicost", "오늘 AI 추천 비용 한도를 다 썼어요"
        # 돈을 내고 AI에게 묻기 전에, 앱이 먼저 장을 본다(무료)
        idx = self.index_pct(mkt)
        floor = float(p.get("idx_floor") or 0)
        if floor < 0 and idx is not None and idx <= floor:
            return False, "market", f"지수가 {idx:+.2f}%로 약해 AI를 부르지 않고 쉬어요(기준 {floor:g}%, 비용 없음)"
        # 관망 뒤 쉬기: 관망이면 사지 않는 플랜이거나 추천이 비싼(빠른 재추천이 꺼진) 플랜에서만. 관망에도 작게 사 보는 플랜은 싼 재추천을 그대로 이어 간다
        gap = D["gap"] * 60
        last = json.loads(cycles[-1]["data"]) if cycles and cycles[-1]["data"] else None
        if (p.get("skip_watch") or not D["quick"]) and last and (last.get("watch") or last.get("stance") == "관망") and since < gap * 2:
            was = last.get("idx")
            if not (idx is not None and was is not None and idx >= was + 0.3):
                return False, "watchwait", (f"직전 추천이 관망이라, 지수가 회복되거나 {int(gap * 2 / 60)}분이 지날 때까지 다시 묻지 않아요"
                                            + (f"(그때 {was:+.2f}% → 지금 {idx:+.2f}%)" if idx is not None and was is not None else ""))
        if not self.rt.ai.configured():
            return False, "nokey", "AI 키가 없어요"
        c = self.rt.ai.conf()
        if c.get("budget_usd") and self.rt.ai.month_usd()[0] >= float(c["budget_usd"]):
            return False, "budget", "이번 달 AI 예산을 다 썼어요"
        return True, "", ""

    # ------------------------------------------------------------------ 지금 상태(화면용)
    HOURS = {"kr": ("09:00", "15:20"), "us": ("09:30", "16:00")}   # 그 시장 시각 기준 정규장
    WD = "월화수목금토일"

    def _hours(self, mkt, d):
        """그날의 정규장 시작·끝(그 시장 시각). 수능일처럼 늦게 여는 날은 밀린 시각."""
        o, c = self.HOURS[mkt]
        so, sc = AP.kr_shift(d) if mkt == "kr" else (0, 0)
        return AP.hm_add(o, so), AP.hm_add(c, sc)

    def _entry(self, run, d):
        """그날의 매수 시작·끝 시각. 플랜 값은 평소 기준이고, 장이 늦게 여는 날은 그만큼 밀어 쓴다."""
        p = run["params"]
        so, sc = AP.kr_shift(d) if run["mkt"] == "kr" else (0, 0)
        return AP.hm_add(p["entry_start"], so), AP.hm_add(p["entry_end"], sc)

    def _trading_day(self, mkt, d):
        if d.weekday() >= 5:
            return False
        return not (mkt == "kr" and d.isoformat() in (self.rt.settings.get("holidays") or []))

    def _next_day(self, mkt, d):
        for _ in range(15):
            d += timedelta(days=1)
            if self._trading_day(mkt, d):
                return d
        return d

    def _kst(self, mkt, local_dt):
        """그 시장 시각(naive) → 한국 시각(naive)."""
        if mkt != "us":
            return local_dt
        off = AP.kst_now().replace(tzinfo=None) - AP.et_now().replace(tzinfo=None)
        return local_dt + timedelta(hours=round(off.total_seconds() / 3600))

    def _when(self, mkt, local_dt):
        """다음 일이 일어날 때를 화면에 쓰기 좋게: 한국 시각 기준 '오늘 09:10'·'내일 09:10'·'11/23(월) 09:10'과 남은 분."""
        k, now = self._kst(mkt, local_dt), AP.kst_now().replace(tzinfo=None)
        days = (k.date() - now.date()).days
        day = "오늘" if days == 0 else "내일" if days == 1 else f"{k.month}/{k.day}({self.WD[k.weekday()]})"
        out = {"at": k.isoformat(timespec="minutes"), "text": f"{day} {k:%H:%M}", "in_min": max(0, round((k - now).total_seconds() / 60)),
               "in_sec": max(0, round((k - now).total_seconds()))}
        if mkt == "us":
            out["local"] = f"뉴욕 {local_dt:%H:%M}"
        return out

    def _next(self, run, loc, busy):
        """다음에 무엇을 언제 하는지. kind: soon(곧 추천)·at(정해진 시각)·slot(자리가 나면)·hold(보유만 지켜봄)·blocked(막힘)·busy·paused·closing·ended"""
        p, mkt, st = run["params"], run["mkt"], run["status"]
        if st == "ended":
            return {"kind": "ended", "label": run["status_msg"] or "끝났어요"}
        if st == "closing":
            return {"kind": "closing", "label": run["status_msg"] or "보유 종목을 정리하는 중이에요"}
        if st == "paused":
            return {"kind": "paused", "label": "새 매수를 멈춘 상태예요. 들고 있는 종목은 목표·손절에 닿으면 팔아요"}
        if busy:
            lite = bool((self._prog.get(run["id"]) or {}).get("lite"))
            return {"kind": "busy", "label": "빠른 재추천을 받는 중이에요. 10~20초쯤 걸려요" if lite else "AI 추천을 받는 중이에요. 1~2분쯤 걸려요"}
        ok, code, why = self.gate_code(run)
        if ok:
            return {"kind": "soon", "label": "조건이 맞아 곧 AI 추천을 받아요" if self._due(run)["kind"] != "quick" else "곧 빠른 재추천을 받아요"}
        today, hm = loc.date(), loc.strftime("%H:%M")
        end = datetime.strptime(run["end_date"], "%Y-%m-%d").date()

        def entry(d):
            h, m = map(int, self._entry(run, d)[0].split(":"))
            return datetime(d.year, d.month, d.day, h, m)

        def nxt(label, d, today_why=None):
            if d > end:
                if today_why:      # 오늘 더 못 사는 진짜 이유를 말한다(기간 탓으로 뭉뚱그리지 않는다)
                    return {"kind": "hold", "label": f"{today_why}. 오늘이 기간 마지막 날이라 더 사지 않아요", "code": code}
                return {"kind": "hold", "label": "기간의 마지막 날이라 새로 사지 않아요. 들고 있는 종목만 정리해요" if self._open(run["id"]) else "기간이 끝나 가요. 새로 사지 않아요"}
            return {"kind": "at", "label": label, **self._when(mkt, entry(d))}
        tday = self._trading_day(mkt, today)
        if code in ("closed", "time"):
            if not tday:
                return nxt("오늘은 장이 쉬어요. 다음 거래일에 AI 추천을 받아 매수를 시작해요", self._next_day(mkt, today))
            if hm < self._entry(run, today)[0]:
                late = AP.kr_note(today) if mkt == "kr" else ""
                return nxt((late + ". " if late else "") + ("장 시작 전이에요. 매수 시작 시각에 AI 추천을 받아요" if hm < self._hours(mkt, today)[0] else "장은 열렸고, 매수 시작 시각을 기다려요"), today)
            return nxt("오늘 매수 시간은 끝났어요. 다음 거래일에 이어 가요" if mkt == "kr" else "지금은 미국장이 닫혀 있어요. 다음 장이 열리면 이어 가요", self._next_day(mkt, today))
        if code == "over":
            return {"kind": "hold", "label": why}
        if code in ("dayloss", "streak", "cycles", "aicost"):
            if code == "aicost" and run.get("lab_id"):      # 실험은 비용 상한을 앱이 정한다(사용자가 올릴 일이 없다)
                return nxt("오늘 쓸 AI 비용을 다 썼어요. 다음 거래일에 이어 가요", self._next_day(mkt, today), today_why="오늘 쓸 AI 비용을 다 썼어요")
            if code == "aicost":
                why = f"오늘 AI 추천 비용 한도(${float(p.get('ai_daily_usd') or 1):g})를 다 썼어요. 플랜에서 한도를 올리면 바로 다시 받아요"
                return dict(nxt(why.replace("바로 다시 받아요", "오늘도 더 받아요") + ". 그대로 두면 다음 거래일에 다시 시작해요", self._next_day(mkt, today), today_why=why), code=code)
            return nxt(why + ". 다음 거래일에 다시 시작해요" if code != "cycles" else "오늘 추천 횟수를 다 썼어요. 다음 거래일에 다시 받아요", self._next_day(mkt, today), today_why=why)
        if code == "gap":
            D = self._due(run)
            left = max(0, round(D["left"]))
            at = loc + timedelta(minutes=math.ceil(left / 60))
            if at.strftime("%H:%M") > self._entry(run, today)[1]:
                return nxt("오늘은 매수 시간이 끝나기 전에 다음 추천 차례가 오지 않아요", self._next_day(mkt, today))
            label = (f"추천 간격({D['gap']}분)을 기다리는 중이에요" if not D["quick"] else "정밀 추천(뉴스 검색 포함) 차례를 기다리는 중이에요" if D.get("next") == "full"
                     else f"{D['gap']}분마다 빠른 재추천을 받아요. 다음 차례를 기다리는 중이에요")
            return {"kind": "at", "label": label, **self._when(mkt, at), "in_sec": left, "span_sec": int(D["span"])}
        if code in ("market", "watchwait"):
            return {"kind": "wait", "label": why, "code": code}
        if code in ("full", "cash"):
            return {"kind": "slot", "label": ("보유 종목 수가 꽉 찼어요" if code == "full" else "남은 현금이 없어요") + ". 들고 있는 종목이 팔려 자리가 나면 다시 추천을 받아요"}
        return {"kind": "blocked", "label": why + (". 설정 → AI에서 확인해 주세요" if code in ("nokey", "budget") else "")}

    def now(self, run):
        """지금 무엇을 하고 있고, 오늘 무엇을 했고, 다음에 언제 움직이는지."""
        p, mkt, rid = run["params"], run["mkt"], run["id"]
        loc = self._local(mkt).replace(tzinfo=None)
        today, hm = loc.date(), loc.strftime("%H:%M")
        day = AP.mkt_date(mkt)
        ss = self._session(mkt)
        o_hm, c_hm = self._hours(mkt, today)
        tday = self._trading_day(mkt, today)
        phase = "open" if ss["open"] else "off" if not tday else "pre" if hm < o_hm else "post"
        opens = self._open(rid)
        closed = self._today_closed(run)
        th = self._busy.get(rid)
        busy = bool(th and th.is_alive())
        m = self.money(run)
        logs = self.rt.db.q("SELECT ts, msg, data FROM at_log WHERE run_id=? AND kind='cycle' ORDER BY id", (rid,))
        usd_all = usd_day = 0.0
        cyc_today, last, n_quick = [], None, 0
        for x in logs:
            d = json.loads(x["data"]) if x["data"] else {}
            usd_all += float(d.get("usd") or 0)
            last = (x, d)
        for x in self.rt.db.q("SELECT ts, msg, data FROM at_log WHERE run_id=? AND kind='cycle' AND day=? ORDER BY id", (rid, day)):
            d = json.loads(x["data"]) if x["data"] else {}
            usd_day += float(d.get("usd") or 0)
            n_quick += 1 if d.get("lite") else 0
            cyc_today.append({"ts": x["ts"], "msg": x["msg"]})
        last_cycle = None
        if last:
            x, d = last
            dec = d.get("decisions") or []
            last_cycle = {"ts": x["ts"], "msg": x["msg"], "stance": d.get("stance"), "view": d.get("view"), "picks": len(dec), "lite": bool(d.get("lite")),
                          "bought": [q.get("name") or q.get("sym") for q in dec if q.get("act") == "buy"],
                          "skipped": [{"name": q.get("name") or q.get("sym"), "why": q.get("why")} for q in dec if q.get("act") == "skip"][:5]}
        buys = self.rt.db.one("SELECT COUNT(*) n FROM at_trades WHERE run_id=? AND entry_day=? AND status!='cancelled'", (rid, day))["n"]
        day_real = sum(c["pnl"] or 0 for c in closed)
        day_unreal = sum(t["qty"] * ((t["last"] or t["entry_price"]) - t["entry_price"]) for t in opens)
        streak = 0
        for c in reversed(closed):
            if (c["pnl"] or 0) < 0:
                streak += 1
            else:
                break
        n_closed = self.rt.db.one("SELECT COUNT(*) n FROM at_trades WHERE run_id=? AND status='closed'", (rid,))["n"]
        warm = int(p.get("warmup_trades") or 0)
        mult = float(p.get("warmup_mult") or 1) if n_closed < warm else 1.0
        start = datetime.strptime(run["start_date"], "%Y-%m-%d").date()
        end = datetime.strptime(run["end_date"], "%Y-%m-%d").date()
        total_days = (end - start).days + 1
        # 오늘(쉬는 날·장 마감 뒤면 다음 거래일)의 시간표. 시각은 그 시장 기준이고, 미국은 한국 시각을 함께 준다
        show = today if tday and phase != "post" else self._next_day(mkt, today)
        s_o, s_c = self._hours(mkt, show)
        s_es, s_ee = self._entry(run, show)
        soon = (datetime.strptime(s_c, "%H:%M") - timedelta(minutes=8)).strftime("%H:%M")
        rows = [(s_o, "장 시작", "보유 종목 감시 시작"), (s_es, "매수 시작", (f"AI 추천을 받아요(정밀 하루 {p['cycles_per_day']}번 + {p['quick_gap_min']}분마다 빠른 재추천)" if p.get("quick_gap_min")
                                                                                      else f"AI 추천을 받아요(하루 최대 {p['cycles_per_day']}번, {p['cycle_gap_min']}분 간격)")),
                (s_ee, "매수 끝", "이 뒤로는 새로 사지 않아요")]
        if run["style"] == "day":
            rows.append((soon, "전부 정리", "들고 있는 종목을 모두 팔아요"))
        rows.append((s_c, "장 마감", "오늘 평가액을 기록해요"))
        steps = []
        for t, name, sub in sorted(rows):
            h, mi = map(int, t.split(":"))
            k = self._kst(mkt, datetime(show.year, show.month, show.day, h, mi))
            steps.append({"hm": f"{k:%H:%M}", "local": t if mkt == "us" else None, "name": name, "sub": sub, "done": show == today and hm >= t})
        eng = round(time.time() - self._last) if self._last else None
        return {
            "phase": phase, "busy": busy, "scan": self.scan(rid) if busy else None, "next": self._next(run, loc, busy),
            "clock": {"kst": f"{AP.kst_now():%H:%M}", "local": f"{loc:%H:%M}" if mkt == "us" else None, "left_min": ss.get("left_min") or 0},
            "steps": {"day": show.isoformat(), "today": show == today, "label": "오늘" if show == today else f"{show.month}/{show.day}({self.WD[show.weekday()]})", "rows": steps,
                      "note": (AP.kr_note(show).replace("오늘은", "오늘은" if show == today else "이날은") if mkt == "kr" else "")},
            "today": {"cycles": len(cyc_today), "cycles_max": p["cycles_per_day"], "full": len(cyc_today) - n_quick, "quick": n_quick, "quick_gap": int(p.get("quick_gap_min") or 0),
                      "buys": buys, "sells": len(closed),
                      "wins": sum(1 for c in closed if (c["pnl"] or 0) > 0), "pnl": round(day_real, 2), "unreal": round(day_unreal, 2),
                      "limit": round(run["budget"] * p["daily_loss_pct"] / 100, 2), "streak": streak, "streak_max": int(p["loss_streak"])},
            "risk": {"loss": round(max(0.0, run["budget"] - m["equity"]), 2), "limit": round(run["budget"] * p["total_loss_pct"] / 100, 2)},
            "size": {"alloc": round(min(m["base"] * p["per_trade_pct"] / 100 * mult, max(0.0, m["cash"])), 2), "pct": p["per_trade_pct"], "mult": mult,
                     "warm_done": min(n_closed, warm), "warm_need": warm, "slots_used": len(opens), "slots": p["max_positions"]},
            "period": {"day": min(max((today - start).days + 1, 1), total_days), "days": total_days, "left": max(0, (end - today).days), "end": run["end_date"]},
            "ai": {"today_usd": round(usd_day, 3), "limit_usd": float(p.get("ai_daily_usd") or 1), "total_usd": round(usd_all, 3), "cycles_all": len(logs)},
            "last_cycle": last_cycle,
            "engine": {"sec": eng, "ok": eng is not None and eng < 90},
        }

    def _maybe_cycle(self, run):
        th = self._busy.get(run["id"])
        if th is not None and th.is_alive():
            return
        sibs = self._sibs(run)
        if sibs is not None and (not sibs or sibs[0]["id"] != run["id"]):
            return                                   # 실험 묶음은 맨 앞 하나만 추천을 받는다
        ok, code, why = self.gate_code(run)
        if not ok:
            if code in ("dayloss", "streak", "aicost", "market", "watchwait"):
                for x in sibs or [run]:
                    self._note_once(x, why if code != "market" else why.split("(")[0].strip() + " · 비용 없음")
            return
        ids = [x["id"] for x in sibs] if sibs else [run["id"]]
        lite = self._due(run)["kind"] == "quick"
        q = ",".join("?" * len(ids))
        self.rt.db.x(f"UPDATE at_runs SET last_cycle=?{'' if lite else ', last_full=?'} WHERE id IN ({q})", (*((time.time(),) * (1 if lite else 2)), *ids))
        P = self._step(run["id"], 0, "실시간 순위·분봉·호가를 모으는 중", new=True, lite=lite)
        th = threading.Thread(target=self._cycle, args=(run["id"], ids, lite), name=f"moa-auto-{run['id']}", daemon=True)
        for i in ids:
            self._busy[i] = th
            self._prog[i] = P                       # 같은 진행 상태를 함께 본다
        th.start()

    SCAN = ("시장 자료 모으기", "AI 분석", "추천 정리", "매수 판단")

    def _step(self, rid, step=None, msg=None, new=False, **add):
        """추천을 받는 동안의 진행 상태를 적어 둔다. 단계는 뒤로 가지 않는다."""
        now = time.time()
        if new or rid not in self._prog:
            self._prog[rid] = {"start": now, "step": 0, "step_at": now, "msg": "", "search": 0, "sources": 0, "chars": 0, "src": "", "picks": None}
        P = self._prog[rid]
        if step is not None and step > P["step"]:
            P["step"], P["step_at"] = step, now
        if msg:
            P["msg"] = msg
        for k, v in add.items():
            P[k] = P.get(k, 0) + v if k in ("search", "sources", "chars") else v
        P["at"] = now
        return P

    def scan(self, rid):
        """화면용 진행 상태. 추천을 받는 중이 아니면 None."""
        th, P = self._busy.get(rid), self._prog.get(rid)
        if not (th and th.is_alive()) or not P:
            return None
        now = time.time()
        return {"step": P["step"], "steps": list(self.SCAN), "msg": P["msg"], "elapsed": round(now - P["start"]), "step_sec": round(now - P["step_at"]), "lite": bool(P.get("lite")),
                "search": P["search"], "sources": P["sources"], "chars": P["chars"], "src": P["src"], "picks": P["picks"]}

    def _seen(self, ids, mkt):
        """이 자동매매(실험이면 묶음 전체)가 들고 있거나 오늘 거래한 종목. 다시 추천받아도 살 수 없으니 후보에서 뺀다."""
        q = ",".join("?" * len(ids))
        return sorted({r["sym"] for r in self.rt.db.q(
            f"SELECT DISTINCT sym FROM at_trades WHERE run_id IN ({q}) AND (status='open' OR entry_day=?) AND status!='cancelled'", (*ids, AP.mkt_date(mkt)))})

    def _ask_ai(self, run, lite=False, ids=None):
        """AI 추천을 끝까지 받아 결과만 돌려준다(스트리밍을 서버에서 소비). 받는 동안 어디까지 왔는지 적어 둔다.
        lite: 빠른 재추천(가장 싼 엔진, 검색 없이, 줄인 자료)."""
        rid = run["id"]
        d = {"mkt": run["mkt"], "horizon": run["style"], "model": run["model"], "web_search": bool(run["params"].get("web_search", True))}
        cap = self._price_cap(run)
        if cap:
            d["max_price"] = cap
        if lite:
            from .ai import LITE_MODEL
            d.update(lite=True, model=LITE_MODEL, web_search=False)
        ex = self._seen(ids or [rid], run["mkt"])
        if ex:
            d["exclude"] = ex
        # 시장·방식·모델이 같은 자동매매(예: 모의와 실전)가 거의 같은 때에 추천을 받으면 한 번만 받아 함께 쓴다(비용은 받은 쪽에만 든다)
        key = (d["mkt"], d["horizon"], d["model"], d["web_search"], d.get("max_price"), bool(lite))
        with self._share_lock:
            lock = self._share.setdefault(key, {"lock": threading.Lock(), "ts": 0.0, "out": None, "used": set()})
        if not lock["lock"].acquire(blocking=False):
            self._step(rid, 1, "같은 조건의 다른 자동매매가 받는 추천을 함께 기다려요")
            lock["lock"].acquire()
        try:
            if lock["out"] and time.time() - lock["ts"] < SHARE_SEC and lock["out"][0] and rid not in lock["used"]:
                lock["used"].add(rid)
                res, _, done = lock["out"]
                self._step(rid, 3, f"방금 받은 추천 {len(res.get('picks') or [])}종목을 함께 써요", picks=len(res.get("picks") or []))
                return res, None, dict(done, usd=0.0, shared=True)
            out = self._ask_ai_once(run, d)
            lock.update(ts=time.time(), out=out, used={rid})
            return out
        finally:
            lock["lock"].release()

    def _price_cap(self, run):
        """이 자동매매가 살 수 있는 1주 값의 상한. AI가 못 살 종목을 고르느라 추천을 버리지 않게, 후보를 고를 때부터 쓴다.
        실전: 지금 한 번에 쓰는 돈(검증 단계면 그 절반). 모의: 남은 현금(1주는 사 보므로). 실험 묶음: 플랜마다 달라 두지 않는다."""
        if run.get("lab_id"):
            return None
        p, m = run["params"], self.money(run)
        if run["mode"] == "paper" and p.get("min_one", True):
            return round(m["cash"] / 1.003, 2) or None
        n_closed = self.rt.db.one("SELECT COUNT(*) n FROM at_trades WHERE run_id=? AND status='closed'", (run["id"],))["n"]
        mult = float(p.get("warmup_mult") or 1) if n_closed < int(p.get("warmup_trades") or 0) else 1.0
        return round(min(m["base"] * p["per_trade_pct"] / 100 * mult, m["cash"]) / 1.003, 2) or None

    def _ask_ai_once(self, run, d):
        res, err, done = None, None, {}
        rid = run["id"]
        for ev in self.rt.ai.stream("pick", d):
            t = ev.get("t")
            if t == "picks":
                res = ev
                self._step(rid, 3, f"추천 {len(ev.get('picks') or [])}종목을 플랜 기준으로 거르는 중", picks=len(ev.get("picks") or []))
            elif t == "error":
                err = ev.get("msg")
            elif t == "done":
                done = ev
            elif t == "status":
                m = str(ev.get("msg") or "")
                if "웹에서" in m:
                    self._step(rid, 1, "뉴스·공시를 찾아보는 중", search=1)
                elif "보내는 중" in m:
                    self._step(rid, 1, "AI가 자료를 읽고 있어요")
                else:
                    self._step(rid, 1, m[:60])
            elif t == "source":
                self._step(rid, 1, "찾은 자료를 읽는 중", sources=1, src=str(ev.get("title") or ev.get("url") or "")[:48])
            elif t == "text":
                n = len(ev.get("text") or "")
                P = self._step(rid, None, None, chars=n)
                if P["chars"] >= 150:
                    self._step(rid, 2, "AI가 추천 종목과 근거를 쓰는 중")
        return res, err, done

    def _cycle(self, rid, ids=None, lite=False):
        ids = ids or [rid]
        try:
            run = self._run(rid)
            res, err, done = self._ask_ai(run, lite, ids)
            done = dict(done or {}, lite=True) if lite else done
            with self.rt._lock:
                if err or not res:
                    for i in ids:
                        self.log(i, "cycle", f"{'빠른 재추천' if lite else '추천'}을 받지 못했어요: {err or '결과 없음'}", {"lite": True} if lite else None)
                    return
                # 실험 묶음: 같은 추천을 각자의 플랜으로 거른다. AI 비용은 똑같이 나눠 적는다
                share = dict(done or {}, usd=round(float((done or {}).get("usd") or 0) / len(ids), 4))
                for i in ids:
                    r = self._run(i)
                    why = self._room(r) if len(ids) > 1 and r["status"] == "running" else None
                    if why and why[0] != "full":
                        self.log(i, "cycle", f"추천은 받았지만 {why[1]}", {"pick_id": res.get("id"), "stance": res.get("stance"), "view": res.get("view"), "usd": share.get("usd"), **({"lite": True} if lite else {})})
                        continue
                    self._enter(r, res, share)
        except Exception as e:
            try:
                self.log(rid, "cycle", f"추천 처리 중 오류: {type(e).__name__}: {e}")
            except Exception:
                pass
        finally:
            for i in ids:
                self._prog.pop(i, None)

    def _enter(self, run, res, done=None):
        """추천 결과를 플랜 조건으로 걸러 매수한다. 판단은 모두 기록한다."""
        p, mkt, rid = run["params"], run["mkt"], run["id"]
        picks = res.get("picks") or []
        decisions, bought = [], 0
        head = {"pick_id": res.get("id"), "stance": res.get("stance"), "view": res.get("view"), "usd": (done or {}).get("usd"), "idx": self.index_pct(mkt)}
        lite = bool((done or {}).get("lite"))
        if lite:
            head["lite"] = True                      # 빠른 재추천으로 받은 것(정밀 추천과 성적을 따로 보려고 남긴다)
        if run["status"] != "running":
            self.log(rid, "cycle", "추천은 받았지만 멈춘 상태라 사지 않았어요", head)
            return 0
        if p.get("skip_watch") and res.get("stance") == "관망":
            self.log(rid, "cycle", f"AI가 관망을 권해 쉬어요 · {res.get('view') or ''}"[:200], dict(head, watch=True,
                                                                                                picks=[{"sym": str(x.get("sym") or "").upper(), "name": x.get("name"), "score": x.get("score"),
                                                                                                        "win_prob": x.get("win_prob"), "auto": bool(x.get("auto"))} for x in picks]))
            return 0
        n_closed = self.rt.db.one("SELECT COUNT(*) n FROM at_trades WHERE run_id=? AND status='closed'", (rid,))["n"]
        mult = float(p.get("warmup_mult") or 1) if n_closed < int(p.get("warmup_trades") or 0) else 1.0
        if res.get("stance") == "관망":
            mult *= float(p.get("watch_mult") or 0.5)      # 관망인데도 사는 플랜(모의 기본)은 작게 산다
        today = AP.mkt_date(mkt)
        fee = AP.FEE[mkt]
        for x in picks:
            name, sym = x.get("name") or x.get("sym"), str(x.get("sym") or "").upper()
            dec = {"sym": sym, "name": name, "score": x.get("score"), "win_prob": x.get("win_prob"), "cal_prob": x.get("cal_prob")}
            decisions.append(dec)

            def skip(code, why):
                dec.update(act="skip", code=code, why=why)
            opens = self._open(rid)
            if len(opens) >= p["max_positions"]:
                skip("full", "보유 종목 수가 꽉 참")
                continue
            if x.get("auto"):
                skip("auto", "AI가 아니라 앱이 지표로 고른 예비 종목")
                continue
            wp = x.get("cal_prob") if x.get("cal_prob") is not None and (x.get("cal_n") or 0) >= 10 else x.get("win_prob")
            if (wp or 0) < p["min_win_prob"]:
                dec["wp"] = wp
                skip("winprob", f"승률 {wp or 0:g}% < 기준 {p['min_win_prob']:g}%")
                continue
            if (x.get("score") or 0) < p["min_score"]:
                skip("score", f"점수 {x.get('score') or 0:g} < 기준 {p['min_score']:g}")
                continue
            em = float(p.get("exit_mult") or 1.0)        # 플랜이 AI의 익절·손절 폭을 좁히거나 넓힌다(손익비는 그대로)
            tp, sl = min(round(float(x.get("target_pct") or 0) * em, 2), p["tp_cap"]), min(round(float(x.get("stop_pct") or 0) * em, 2), p["sl_cap"])
            if tp <= 0 or sl <= 0 or tp / sl < p["min_rr"]:
                skip("rr", f"손익비 {tp / sl if sl else 0:.2f} < 기준 {p['min_rr']:g}")
                continue
            if tp < fee * 3:
                skip("fee", f"목표 {tp:g}%가 비용({fee:g}%)의 3배보다 작음")
                continue
            if self.rt.db.one("SELECT 1 x FROM at_trades WHERE run_id=? AND sym=? AND (status='open' OR entry_day=?) AND status!='cancelled'", (rid, sym, today)):
                skip("dup", "오늘 이미 거래했거나 보유 중")
                continue
            q = self._quote(mkt, sym)
            price = q.get("price")
            if not price:
                skip("quote", "시세를 받지 못함")
                continue
            ref = float(x.get("entry_price") or 0)
            if ref and price > ref * (1 + p["chase_pct"] / 100):
                skip("chase", f"추천가보다 {((price / ref) - 1) * 100:.2f}% 올라 쫓아가지 않음")
                continue
            m = self.money(run)
            alloc = min(m["base"] * p["per_trade_pct"] / 100 * mult, m["cash"])
            buy_px = C.buy_fill(price, self.rt.settings.get("costs")) if mkt == "kr" else round(price * (1 + US_SLIP), 4)
            qty = int(alloc // (buy_px * 1.002))
            if qty < 1 and run["mode"] == "paper" and p.get("min_one", True) and m["cash"] >= buy_px * 1.002:
                qty = 1                                 # 모의: 배정 금액이 모자라도 현금이 되면 1주는 사서 시험한다
                dec["min1"] = True
            if qty < 1:
                dec["alloc"], dec["px"] = round(alloc), price
                skip("qty", f"배정 금액({alloc:,.0f})으로 1주를 못 삼")
                continue
            try:
                tid = self._buy(run, x, sym, name, q, qty, price, buy_px, tp, sl, dict(res, lite=lite))
            except Exception as e:
                skip("fail", f"매수 실패: {e}")
                continue
            bought += 1
            dec.update(act="buy", qty=qty, price=buy_px, target_pct=tp, stop_pct=sl, trade=tid)
        self.log(rid, "cycle", f"{'빠른 재추천' if lite else '추천'} {len(picks)}종목 중 {bought}종목 매수" + (f" · {res.get('view')}" if res.get("view") else ""),
                 dict(head, mult=mult, decisions=decisions))
        return bought

    def _buy(self, run, x, sym, name, q, qty, price, buy_px, tp, sl, res):
        mkt, now = run["mkt"], datetime.now()
        exch, buy_no, fill_ok = q.get("exch") or x.get("exch") or "", "", 1
        if run["mode"] == "real":
            b, why = self.real_broker()
            if b is None:
                raise RuntimeError(why)
            need = price * qty * 1.003
            try:
                have = b.client.us_cash()["usd_orderable"] if mkt == "us" else b.cash()
            except Exception:
                have = None  # 조회가 안 되면 주문을 넣어 보고 증권사 판단에 맡긴다
            if have is not None and have < need:
                raise RuntimeError(f"계좌의 주문 가능 금액({have:,.0f})이 모자라요")
            if mkt == "us":
                limit = round(price * 1.003, 2)  # 시장가 대신 조금 위 지정가(체결가 상한)
                no, exch = b.client.us_order("buy", sym, qty, limit, exch or None)
                buy_no = f"{no}|{exch}"
            else:
                buy_no = b.buy(sym, qty, None).get("broker_no", "")
            self.rt.db.x("INSERT INTO orders(ts,engine,mode,code,name,side,qty,price,status,fill_price,broker_no,msg) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         (now.isoformat(timespec="seconds"), "scalp", b.mode, sym, name, "buy", qty, None, "accepted", price, buy_no, "AI 자동매매 매수"))
            fill_ok, buy_px = 0, price
        style = run["style"]
        exit_by, exit_date = "", ""
        if style == "day":
            eb = str(x.get("exit_by") or "")[:5]
            exit_by = eb if re.match(r"^\d\d:\d\d$", eb) else ""
        else:
            hold = int(min(max(float(x.get("hold_days") or AP.HORIZONS[style]["days"]), 1), AP.HORIZONS[style]["days"]))
            exit_date = min(AP.swing_end(mkt, AP.kst_now().replace(tzinfo=None), hold)[1].isoformat(), run["end_date"])
        why = {"reasons": x.get("reasons"), "risks": x.get("risks"), "catalyst": x.get("catalyst"), "theme": x.get("theme"),
               "view": res.get("view"), "entry_type": x.get("entry_type"), "ref_price": x.get("entry_price"),
               "ai_target": x.get("target_pct"), "ai_stop": x.get("stop_pct"), "change_pct": q.get("change_pct"), "lite": bool(res.get("lite"))}
        tid = self.rt.db.x(
            "INSERT INTO at_trades(run_id,ver,mkt,sym,name,exch,qty,status,entry_ts,entry_day,entry_hm,entry_price,target_pct,stop_pct,"
            "trail_pct,exit_by,exit_date,high,low,last,pick_id,score,win_prob,cal_prob,stance,feat,why,buy_no,fill_ok) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run["id"], run["ver"], mkt, sym, name, exch, qty, "open", now.isoformat(timespec="seconds"), AP.mkt_date(mkt),
             self._local(mkt).strftime("%H:%M"), buy_px, tp, sl, abs(float(x.get("trail_pct") or 0)), exit_by, exit_date,
             buy_px, buy_px, price, res.get("id"), x.get("score"), x.get("win_prob"), x.get("cal_prob"), res.get("stance"),
             json.dumps(x.get("feat"), ensure_ascii=False, default=str), json.dumps(why, ensure_ascii=False, default=str), buy_no, fill_ok))
        unit = "원" if mkt == "kr" else "달러"
        self.log(run["id"], "buy", f"{name} {qty}주 매수 · {buy_px:,.{0 if mkt == 'kr' else 2}f}{unit} · 목표 +{tp:g}% / 손절 -{sl:g}%", {"trade": tid})
        self.rt.notify(f"[moa 자동매매] {name} {qty}주 매수 · 목표 +{tp:g}% 손절 -{sl:g}%")
        return tid

    # ------------------------------------------------------------------ 분석
    def stats(self, rid, rows=None):
        run = self._run(rid)
        rows = self._closed(rid) if rows is None else rows
        out = {"all": _agg(rows)}
        if not rows:
            return out
        eq, peak, mdd, curve = run["budget"], run["budget"], 0.0, []
        for r in rows:
            eq += r["pnl"] or 0
            peak = max(peak, eq)
            mdd = max(mdd, (peak - eq) / peak * 100 if peak else 0)
            curve.append(round(eq, 2))
        out["all"].update(mdd_pct=round(mdd, 2), mfe_med=_median([r["mfe_pct"] for r in rows]), mae_med=_median([r["mae_pct"] for r in rows]),
                          hold_med=_median([r["hold_min"] for r in rows]),
                          fees=round(sum(r["fees"] or 0 for r in rows), 2))
        out["curve"] = curve

        def group(label, fn):
            g = {}
            for r in rows:
                try:
                    k = fn(r)
                except Exception:
                    k = None
                if k is not None:
                    g.setdefault(k, []).append(r)
            return {"label": label, "rows": [dict(_agg(v), key=k) for k, v in g.items()]}

        def wp(r):
            v = r["cal_prob"] if r["cal_prob"] is not None else r["win_prob"]
            return None if v is None else "55% 미만" if v < 55 else "55~65%" if v < 65 else "65% 이상"

        def tod(r):
            return "장 초반(~10시)" if r["entry_hm"] < "10:00" else "오전(10~12시)" if r["entry_hm"] < "12:00" else "오후(12시~)"

        def score(r):
            return None if r["score"] is None else "70 미만" if r["score"] < 70 else "70~80" if r["score"] < 80 else "80 이상"
        out["groups"] = [group("파는 사유", lambda r: r["reason"]), group("AI 승률", wp), group("AI 점수", score),
                         group("산 시간", tod), group("AI 분위기", lambda r: r["stance"] or None),
                         group("플랜 판", lambda r: f"{r['ver']}판")]
        return out

    def _since_ver(self, run):
        """지금 플랜 판으로 한 거래만(없으면 전체)."""
        rows = self._closed(run["id"])
        cur = [r for r in rows if r["ver"] == run["ver"]]
        return cur if len(cur) >= 10 else rows

    def rule_changes(self, run, rows):
        """조건별 성적으로 고칠 값을 찾는다. 반환: [{key, from, to, why}]"""
        p, out = run["params"], []
        n = len(rows)
        if n < 10:
            return out
        a = _agg(rows)

        def add(key, to, why):
            try:
                to = _clamp_key(key, to)
            except Exception:
                return
            if isinstance(to, float) and to == int(to) and not isinstance(to, bool):
                to = int(to)                               # 50.0 → 50 (화면에 깔끔하게)
            if to != p.get(key) and not any(c["key"] == key for c in out):
                out.append({"key": key, "label": PLAN_KEYS[key][3], "from": p.get(key), "to": to, "why": why})

        # 1) 승률 기준: 낮은 승률대가 손실이고 높은 쪽이 이익이면 기준을 올린다
        for step in (5, 10):
            t = p["min_win_prob"] + step
            lo = [r for r in rows if (r["cal_prob"] if r["cal_prob"] is not None else r["win_prob"] or 0) < t]
            hi = [r for r in rows if (r["cal_prob"] if r["cal_prob"] is not None else r["win_prob"] or 0) >= t]
            if len(lo) >= 8 and len(hi) >= 8:
                al, ah = _agg(lo), _agg(hi)
                if al["avg_pct"] < 0 < ah["avg_pct"]:
                    add("min_win_prob", t, f"승률 {t:g}% 미만 {al['n']}건은 평균 {al['avg_pct']:+.2f}%, 이상 {ah['n']}건은 {ah['avg_pct']:+.2f}%")
                    break
        # 2) 점수 기준
        t = p["min_score"] + 10
        lo, hi = [r for r in rows if (r["score"] or 0) < t], [r for r in rows if (r["score"] or 0) >= t]
        if len(lo) >= 8 and len(hi) >= 8 and _agg(lo)["avg_pct"] < 0 < _agg(hi)["avg_pct"]:
            add("min_score", t, f"점수 {t:g} 미만 {len(lo)}건은 평균 {_agg(lo)['avg_pct']:+.2f}%, 이상 {len(hi)}건은 {_agg(hi)['avg_pct']:+.2f}%")
        # 3) 산 시간: 장 초반·오후 끝이 손실이면 그 시간대를 뺀다
        early = [r for r in rows if r["entry_hm"] < "10:00"]
        rest = [r for r in rows if r["entry_hm"] >= "10:00"]
        if len(early) >= 8 and len(rest) >= 8 and _agg(early)["avg_pct"] < 0 < _agg(rest)["avg_pct"] and p["entry_start"] < "10:00":
            add("entry_start", "10:00", f"10시 전에 산 {len(early)}건은 평균 {_agg(early)['avg_pct']:+.2f}%, 그 뒤 {len(rest)}건은 {_agg(rest)['avg_pct']:+.2f}%")
        late = [r for r in rows if r["entry_hm"] >= "13:30"]
        rest = [r for r in rows if r["entry_hm"] < "13:30"]
        if run["style"] == "day" and len(late) >= 8 and len(rest) >= 8 and _agg(late)["avg_pct"] < 0 < _agg(rest)["avg_pct"] and p["entry_end"] > "13:30":
            add("entry_end", "13:30", f"13시 30분 뒤에 산 {len(late)}건은 평균 {_agg(late)['avg_pct']:+.2f}%, 그 전 {len(rest)}건은 {_agg(rest)['avg_pct']:+.2f}%")
        # 4) 익절 폭: 목표에 거의 못 닿고 오르다 마는 경우 목표를 실제 오름폭에 맞춘다
        tp_hit = sum(1 for r in rows if r["reason"] == "익절") / n
        mfe70 = _pctile([r["mfe_pct"] for r in rows], 0.7)
        if tp_hit < 0.15 and mfe70 is not None and 0 < mfe70 < p["tp_cap"] * 0.7:
            to = max(round(mfe70, 1), round(p["sl_cap"] * 1.2, 1), AP.FEE[run["mkt"]] * 4)
            if to < p["tp_cap"]:
                add("tp_cap", to, f"익절까지 간 거래가 {tp_hit * 100:.0f}%뿐이고, 보유 중 최고 오름폭은 10건 중 7건이 +{mfe70:.1f}% 이하")
                if to / p["sl_cap"] < p["min_rr"]:
                    add("min_rr", max(1.0, round(to / p["sl_cap"], 1)), "익절 상한을 낮추면서 손익비 기준도 함께 맞춤")
        # 5) 비중: 충분한 표본에서 꾸준히 벌면 조금 늘리고, 잃고 있으면 줄인다
        st = self.stats(run["id"], rows)["all"]
        cap = {"day": 40.0, "swing": 30.0, "month": 25.0}[run["style"]]
        if n >= 30 and (a["pf"] or 0) >= 1.3 and a["avg_pct"] > 0 and st["mdd_pct"] < p["total_loss_pct"] / 2 and p["per_trade_pct"] < cap:
            add("per_trade_pct", min(cap, p["per_trade_pct"] + 5),
                f"{n}건에서 손익비율(PF) {a['pf']:g}, 최대 낙폭 {st['mdd_pct']:.1f}%로 안정적이라 비중을 조금 늘림")
        elif n >= 20 and a["pf"] is not None and a["pf"] < 0.8 and p["per_trade_pct"] > 5:
            add("per_trade_pct", max(5, round(p["per_trade_pct"] / 2, 1)),
                f"{n}건 평균 {a['avg_pct']:+.2f}%, 손익비율(PF) {a['pf']:g}로 잃는 구간이라 비중을 절반으로 줄임")
        return out

    # ----- 기회 분석: 추천받은 종목을 얼마나 샀고, 건너뛴 종목은 그 뒤 실제로 어땠나
    def opportunity(self, run):
        """지금 플랜 판에서 받은 추천을 모아, 건너뛴 이유별 건수와 그 종목들의 실제 결과(추천 성적표)를 붙인다.
        결과는 AI가 낸 목표·손절대로 샀다고 쳤을 때의 비용 뺀 수익률이라, 플랜의 익절·손절 상한과는 조금 다를 수 있다."""
        rid = run["id"]
        last = self.rt.db.one("SELECT ts FROM at_changes WHERE run_id=? ORDER BY id DESC LIMIT 1", (rid,))
        logs = self.rt.db.q("SELECT ts, day, data FROM at_log WHERE run_id=? AND kind='cycle' AND data IS NOT NULL ORDER BY id", (rid,))
        cur = [x for x in logs if not last or x["ts"] >= last["ts"]]
        scope = "지금 플랜" if len(cur) >= 3 else "전체"
        if len(cur) < 3:
            cur = logs
        items, days, cycles = [], set(), 0
        for x in cur:
            d = json.loads(x["data"])
            dec = d.get("decisions")
            if dec is None and not d.get("watch"):
                continue                                   # 추천을 받지 못했거나 멈춘 상태였던 기록
            cycles += 1
            days.add(x["day"] or x["ts"][:10])
            if dec is None:
                dec = [dict(q, act="skip", code="auto" if q.get("auto") else "watch") for q in d.get("picks") or []]
            for q in dec:
                items.append({"pick_id": d.get("pick_id"), "sym": str(q.get("sym") or "").upper(), "name": q.get("name") or q.get("sym"),
                              "act": q.get("act"), "code": None if q.get("act") == "buy" else skip_code(q), "score": q.get("score"),
                              "wp": q.get("wp") if q.get("wp") is not None else q.get("cal_prob") if q.get("cal_prob") is not None else q.get("win_prob"),
                              "why": q.get("why"), "alloc": q.get("alloc"), "px": q.get("px")})
        ids = sorted({i["pick_id"] for i in items if i["pick_id"]})
        res = {}
        if ids:
            try:
                for r in self.rt.db.q(f"SELECT pick_id, sym, net_pct, result FROM ai_outcomes WHERE status='done' AND net_pct IS NOT NULL "
                                      f"AND pick_id IN ({','.join('?' * len(ids))})", ids):
                    if r["result"] not in ("미체결", "자료 없음"):
                        res[(r["pick_id"], str(r["sym"]).upper())] = r["net_pct"]
            except Exception:
                res = {}
        for i in items:
            i["net"] = res.get((i["pick_id"], i["sym"]))
        skipped = [i for i in items if i["act"] != "buy"]
        bought = len(items) - len(skipped)

        def agg(xs):
            g = [i["net"] for i in xs if i["net"] is not None]
            return {"n": len(xs), "graded": len(g), "avg": round(sum(g) / len(g), 2) if g else None,
                    "win": round(sum(1 for v in g if v > 0) / len(g) * 100) if g else None}
        reasons = []
        for code in SKIP:
            xs = [i for i in skipped if i["code"] == code]
            if xs:
                reasons.append(dict(agg(xs), code=code, label=SKIP[code]))
        reasons.sort(key=lambda r: -r["n"])
        return {"scope": scope, "days": len(days), "cycles": cycles, "picks": len(items), "bought": bought,
                "buy_rate": round(bought / len(items) * 100) if items else 0, "per_day": round(bought / len(days), 1) if days else 0,
                "reasons": reasons, "skipped": agg(skipped), "items": items}

    def opp_changes(self, run, O, rows):
        """매수가 너무 적을 때 고칠 값을 찾는다. 기준을 낮추는 것은 건너뛴 종목의 실제 결과가 뒷받침할 때만 한다.
        반환: (바꿀 값 목록, 알림 글 목록)"""
        p, out, notes = run["params"], [], []
        if O["cycles"] < 3 or not O["picks"]:
            return out, notes
        base = make_plan(run["budget"], run["mkt"], run["style"], 30)      # 낮추는 데는 바닥이 있다(처음 플랜 기준)
        sk = [i for i in O["items"] if i["act"] != "buy"]
        real = [i for i in sk if i["code"] not in ("full", "dup")]         # 자리가 없어서·이미 사서 건너뛴 것은 '기회 부족'이 아니다
        low = O["buy_rate"] < 25 and len(real) >= 3 and (O["days"] >= 2 or O["cycles"] >= 6)
        fee = AP.FEE[run["mkt"]]

        def add(key, to, why):
            try:
                to = _clamp_key(key, to)
            except Exception:
                return
            if isinstance(to, float) and to == int(to) and not isinstance(to, bool):
                to = int(to)                               # 50.0 → 50 (화면에 깔끔하게)
            if to != p.get(key) and not any(c["key"] == key for c in out):
                out.append({"key": key, "label": PLAN_KEYS[key][3], "from": p.get(key), "to": to, "why": why})

        def judged(xs):
            g = [i["net"] for i in xs if i["net"] is not None]
            return len(g), (sum(g) / len(g) if g else 0.0), (sum(1 for v in g if v > 0) / len(g) * 100 if g else 0.0)

        def good(xs, need=OPP_MIN, avg_min=None, win_min=50):
            n, avg, win = judged(xs)
            return n >= need and avg >= (fee / 2 if avg_min is None else avg_min) and win >= win_min

        def said(xs):
            n, avg, win = judged(xs)
            return f"그 뒤 결과가 나온 {n}건은 평균 {avg:+.2f}%(비용 뺀 값), {win:.0f}%가 이익"
        # 1) 돈이 모자라 1주를 못 삼: 결과와 상관없는 구조 문제 → 종목 수를 줄여 한 번에 쓰는 돈을 키운다
        q = [i for i in sk if i["code"] == "qty"]
        if len(q) >= 3 and len(q) >= len(real) * 0.25:
            px = sorted(i["px"] for i in q if i.get("px"))
            mid = px[len(px) // 2] if px else None
            unit = "원" if run["mkt"] == "kr" else "달러"
            if p["max_positions"] > 1 and p["per_trade_pct"] < 50:
                n2 = p["max_positions"] - 1
                add("max_positions", n2, f"배정 금액으로 1주를 못 사 건너뛴 종목이 {len(q)}개" + (f"(주가 중간값 {mid:,.0f}{unit})" if mid else "") + ". 종목 수를 줄여 한 번에 쓰는 돈을 키움")
                add("per_trade_pct", min(50.0, round(100 / n2, 1)), "종목 수를 줄인 만큼 1회 비중을 올림(손절 폭은 그대로)")
            else:
                notes.append(f"배정 금액으로 1주를 못 사 건너뛴 종목이 {len(q)}개예요. 종목 수·비중은 더 조정할 수 없어, 쓸 돈을 늘려야 해요.")
        if not low:
            if O["picks"] and not out:
                notes.append(f"추천 {O['picks']}종목 중 {O['bought']}종목을 샀어요. 매수가 모자란 상태는 아니에요.")
            return out, notes
        notes.append(f"{O['days']}거래일 동안 추천 {O['picks']}종목 중 {O['bought']}종목만 샀어요(하루 {O['per_day']:g}건). 무엇에 걸렸는지 봤어요.")
        # 잃고 있는 중에는 기준을 낮추지 않는다
        a = _agg(rows) if rows else None
        if a and a["n"] >= 8 and a["avg_pct"] < 0:
            notes.append(f"다만 지금까지 산 {a['n']}건이 평균 {a['avg_pct']:+.2f}%로 잃는 구간이라, 기준을 낮추지 않아요.")
            return out, notes
        # 2) 승률 기준: 기준 바로 아래 5%p 구간이 실제로 벌었으면 5%p 낮춘다
        w = [i for i in sk if i["code"] == "winprob"]
        if w:
            t = p["min_win_prob"]
            band = [i for i in w if (i["wp"] or 0) >= t - 5]
            floor = max(40, base["min_win_prob"] - 10)
            if good(band) and t - 5 >= floor:
                add("min_win_prob", t - 5, f"승률 {t - 5:g}~{t:g}%라 건너뛴 {len(band)}종목: {said(band)}")
            elif judged(band)[0] >= OPP_MIN:
                notes.append(f"승률 기준에 걸린 종목은 {said(band)}이라, 지금 기준을 유지하는 게 맞아요.")
            else:
                notes.append(f"승률 기준에 걸린 종목이 {len(w)}개인데, 결과가 나온 것이 {judged(band)[0]}건이라 아직 판단하지 않아요({OPP_MIN}건부터).")
        # 3) 점수 기준: 기준 바로 아래 10점 구간
        sc = [i for i in sk if i["code"] == "score"]
        if sc:
            t = p["min_score"]
            band = [i for i in sc if (i["score"] or 0) >= t - 10]
            floor = max(30, base["min_score"] - 15)
            if good(band) and t - 5 >= floor:
                add("min_score", t - 5, f"점수 {t - 10:g}~{t:g}점이라 건너뛴 {len(band)}종목: {said(band)}")
            elif judged(band)[0] >= OPP_MIN:
                notes.append(f"점수 기준에 걸린 종목은 {said(band)}이라, 지금 기준을 유지해요.")
        # 4) 손익비 기준
        rr = [i for i in sk if i["code"] == "rr"]
        if rr and good(rr) and p["min_rr"] - 0.2 >= max(1.2, base["min_rr"] - 0.4):
            add("min_rr", round(p["min_rr"] - 0.2, 1), f"손익비가 모자라 건너뛴 {len(rr)}종목: {said(rr)}")
        # 5) 추천가보다 올라 쫓지 않은 종목: 그래도 올랐으면 허용 폭을 조금 넓힌다
        ch = [i for i in sk if i["code"] == "chase"]
        if ch and good(ch, need=6) and p["chase_pct"] + 0.3 <= base["chase_pct"] + 1.0:
            add("chase_pct", round(p["chase_pct"] + 0.3, 1), f"추천가보다 올라 쫓지 않은 {len(ch)}종목: {said(ch)}")
        # 6) AI가 관망이라 쉰 날: 쉰 종목들이 뚜렷이 벌었을 때만 관망에도 사게 한다(문턱을 높게 둔다)
        wt = [i for i in sk if i["code"] == "watch"]
        if wt and p.get("skip_watch"):
            if good(wt, need=12, avg_min=max(0.3, fee), win_min=55):
                add("skip_watch", False, f"AI가 관망이라 쉰 {len(wt)}종목: {said(wt)}")
            elif judged(wt)[0] >= OPP_MIN:
                notes.append(f"관망이라 쉰 종목은 {said(wt)}이라, 쉬는 게 맞았어요.")
        if not out and not any("판단하지" in n or "유지" in n or "맞았" in n for n in notes):
            notes.append("기준을 낮출 만한 근거는 아직 없어요. 결과가 더 쌓이면 다시 봐요.")
        return out, notes

    def review(self, rid, auto=False):
        """규칙 기반 분석 → 개선안. 끝난 거래의 성적(조이는 쪽)과 기회 분석(매수가 너무 적을 때 푸는 쪽)을 함께 본다.
        바꿀 것이 없어도 분석 결과는 남긴다. 같은 값을 두 쪽이 다르게 말하면 실제 거래 성적을 따른다."""
        run = self._run(rid)
        rows = self._since_ver(run)
        O = self.opportunity(run)
        if len(rows) < 10 and O["cycles"] < 3:
            raise ValueError(f"끝난 거래 10건이나 추천 3번은 쌓여야 분석할 수 있어요(지금 거래 {len(rows)}건, 추천 {O['cycles']}번)")
        ch = self.rule_changes(run, rows) if len(rows) >= 10 else []
        loose, notes = self.opp_changes(run, O, rows)
        for c in loose:
            if not any(x["key"] == c["key"] for x in ch):
                ch.append(dict(c, loosen=True))
        a = _agg(rows) if rows else None
        small = len(rows) < int(run["params"].get("review_every") or 30)
        head = f"{a['n']}건 · 승률 {a['win_rate']:g}% · 평균 {a['avg_pct']:+.2f}%" if a else f"추천 {O['picks']}종목 중 {O['bought']}종목 매수"
        summary = f"{head} · " + (f"고칠 값 {len(ch)}개" if ch else "지금 값 유지") + (" · 표본이 적어 참고용" if a and small and not auto and not loose else "")
        return self._save_proposal(run, "rule", summary, ch, {"all": a, "small": small, "opp": {k: v for k, v in O.items() if k != "items"}, "notes": notes})

    def _auto_apply(self, run, pr, why):
        """모의이고 사용자가 켜 둔 경우에만, 승인 대기 개선안을 스스로 반영한다. 실전은 언제나 사람이 승인한다."""
        if not pr or pr.get("status") != "pending" or run["mode"] != "paper" or not run["params"].get("auto_apply"):
            return False
        try:
            self.decide(pr["id"], "apply")
        except ValueError:
            return False
        self.log(run["id"], "review", f"모의라서 개선안을 스스로 반영했어요({why})", {"proposal": pr["id"], "auto": True})
        self.rt.notify(f"[moa 자동매매] {run['name']} 개선안 {len(pr['changes'])}건을 스스로 반영했어요(모의)")
        return True

    def _daily_review(self, run, day):
        """장이 끝난 뒤 하루 한 번 본다. 정해진 거래일마다, 또는 이틀째 한 건도 못 샀으면 바로 분석한다(거래 건수와 상관없이)."""
        p, rid = run["params"], run["id"]
        since = run.get("last_review_day") or ""
        rows = self.rt.db.q("SELECT day, data FROM at_log WHERE run_id=? AND kind='cycle' AND data IS NOT NULL AND day>? ORDER BY id", (rid, since))
        days = sorted({r["day"] for r in rows})
        if not days:
            return None
        buys = self.rt.db.one("SELECT COUNT(*) n FROM at_trades WHERE run_id=? AND entry_day>? AND status!='cancelled'", (rid, since))["n"]
        every = int(p.get("review_days") or 5)
        if not (len(days) >= every or (len(days) >= 2 and len(rows) >= 3 and buys == 0)):
            return None
        self.rt.db.x("UPDATE at_runs SET last_review_day=? WHERE id=?", (day, rid))
        run["last_review_day"] = day
        try:
            pr = self.review(rid, auto=True)
        except ValueError:
            return None
        if pr.get("changes") and not self._auto_apply(run, pr, f"{len(days)}거래일 점검"):
            self.rt.notify(f"[moa 자동매매] {run['name']} 개선안이 나왔어요({len(pr['changes'])}건). 앱에서 확인해 주세요")
        return pr

    def _save_proposal(self, run, source, summary, changes, stats=None, text=None):
        # 같은 출처의 승인 대기 개선안은 새것으로 바꾼다
        self.rt.db.x("UPDATE at_proposals SET status='replaced', decided_at=? WHERE run_id=? AND source=? AND status='pending'",
                     (self._now(), run["id"], source))
        pid = self.rt.db.x("INSERT INTO at_proposals(run_id,ts,source,status,summary,changes,stats,text) VALUES(?,?,?,?,?,?,?,?)",
                           (run["id"], self._now(), source, "pending" if changes else "none", summary[:300],
                            json.dumps(changes, ensure_ascii=False), json.dumps(stats, ensure_ascii=False, default=str) if stats else None, text))
        self.log(run["id"], "review", f"{'AI' if source == 'ai' else '자동'} 분석 · {summary}"[:300], {"proposal": pid})
        return self._proposal(pid)

    def _proposal(self, pid):
        r = self.rt.db.one("SELECT * FROM at_proposals WHERE id=?", (pid,))
        if not r:
            raise ValueError("개선안을 찾지 못했어요")
        r["changes"] = json.loads(r["changes"] or "[]")
        r["stats"] = json.loads(r["stats"]) if r["stats"] else None
        return r

    def decide(self, pid, action, keys=None):
        """개선안 승인(반영) 또는 거절. keys를 주면 그 항목만 반영한다."""
        pr = self._proposal(pid)
        if pr["status"] != "pending":
            raise ValueError("이미 처리한 개선안이에요")
        run = self._run(pr["run_id"])
        if action == "reject":
            self.rt.db.x("UPDATE at_proposals SET status='rejected', decided_at=? WHERE id=?", (self._now(), pid))
            self.log(run["id"], "review", "개선안을 반영하지 않기로 했어요", {"proposal": pid})
            return self.detail(run["id"])
        if action != "apply":
            raise ValueError("알 수 없는 동작이에요")
        if run["status"] == "ended":
            raise ValueError("끝난 자동매매에는 반영할 수 없어요")
        p, applied = copy.deepcopy(run["params"]), []
        for c in pr["changes"]:
            if c["key"] not in PLAN_KEYS or (keys is not None and c["key"] not in keys):
                continue
            to = _clamp_key(c["key"], c["to"])
            if to == p.get(c["key"]):
                continue
            applied.append({"key": c["key"], "label": PLAN_KEYS[c["key"]][3], "from": p.get(c["key"]), "to": to, "why": c.get("why", "")})
            p[c["key"]] = to
        if not applied:
            raise ValueError("반영할 항목이 없어요")
        if p["entry_start"] >= p["entry_end"]:
            raise ValueError("매수 시작 시각이 끝 시각보다 늦어요")
        ver = run["ver"] + 1
        self.rt.db.x("UPDATE at_runs SET params=?, ver=? WHERE id=?", (json.dumps(p, ensure_ascii=False), ver, run["id"]))
        self.rt.db.x("UPDATE at_proposals SET status='applied', decided_at=? WHERE id=?", (self._now(), pid))
        self.rt.db.x("INSERT INTO at_changes(run_id,ts,ver,source,changes,stats) VALUES(?,?,?,?,?,?)",
                     (run["id"], self._now(), ver, pr["source"], json.dumps(applied, ensure_ascii=False),
                      json.dumps(pr["stats"], ensure_ascii=False) if pr["stats"] else None))
        self.log(run["id"], "review", f"개선안 {len(applied)}건을 반영했어요 · 플랜 {ver}판", {"proposal": pid, "changes": applied})
        return self.detail(run["id"])

    def budget(self, rid, d):
        """돌고 있는 자동매매의 쓸 돈을 늘리거나 줄인다. dry면 바꾸지 않고 바꾼 뒤 모습만 돌려준다.
        손익(원)은 그대로 두고 쓸 돈만 바뀐다. 수익률·1회 매수 금액·손실 한도는 새 쓸 돈 기준으로 다시 계산된다."""
        run = self._run(rid)
        if run["status"] in ("ended", "closing"):
            raise ValueError("끝났거나 정리 중인 자동매매는 쓸 돈을 바꿀 수 없어요")
        mkt, p, old = run["mkt"], run["params"], float(run["budget"])
        kr = mkt == "kr"
        mon = (lambda v: f"{v:,.0f}원") if kr else (lambda v: f"${v:,.2f}")
        try:
            new = float(str(d.get("budget") or 0).replace(",", ""))
        except ValueError:
            raise ValueError("금액은 숫자로 넣어 주세요")
        new = round(new) if kr else round(new, 2)
        m = self.money(run)
        loss = max(0.0, -(m["realized"] + m["unrealized"]))
        # 줄일 수 있는 바닥: 최소 금액, 이미 주식에 들어가 있는 돈, 그리고 지금 손실이 새 손실 한도를 넘지 않는 금액
        floors = [(MIN_BUDGET[mkt], f"최소 금액 {mon(MIN_BUDGET[mkt])} 이상으로 넣어 주세요"),
                  (m["open_cost"] - m["realized"], f"지금 주식에 들어가 있는 돈({mon(m['open_cost'])}) 때문에 {{}} 아래로는 줄일 수 없어요. 종목이 정리된 뒤에 줄여 주세요")]
        if loss > 0:
            floors.append((loss / (p["total_loss_pct"] / 100) * 1.02, f"지금 손실({mon(loss)})이 전체 손실 한도({p['total_loss_pct']:g}%)를 넘어 바로 종료돼요. {{}} 이상으로 넣어 주세요"))
        lo, lo_why = max(floors, key=lambda x: x[0])
        lo = math.ceil(lo / 10000) * 10000 if kr else math.ceil(lo)
        hi = 5_000_000_000 if kr else 5_000_000
        out = {"old": old, "new": new, "min": lo, "max": hi, "diff": round(new - old, 2), "error": None, "notes": []}
        if new < lo:
            out["error"] = lo_why.format(mon(lo)) if "{}" in lo_why else lo_why
        elif new > hi:
            out["error"] = "금액을 확인해 주세요"
        elif new == old:
            out["error"] = "지금과 같은 금액이에요"
        after = dict(run, budget=new)
        m2 = self.money(after)
        n_closed = self.rt.db.one("SELECT COUNT(*) n FROM at_trades WHERE run_id=? AND status='closed'", (rid,))["n"]
        mult = float(p.get("warmup_mult") or 1) if n_closed < int(p.get("warmup_trades") or 0) else 1.0
        size = lambda mm: round(min(mm["base"] * p["per_trade_pct"] / 100 * mult, max(0.0, mm["cash"])), 2)
        out.update(cash=[m["cash"], m2["cash"]], equity=[m["equity"], m2["equity"]], ret_pct=[m["ret_pct"], m2["ret_pct"]], per_trade=[size(m), size(m2)],
                   day_limit=[round(old * p["daily_loss_pct"] / 100, 2), round(new * p["daily_loss_pct"] / 100, 2)],
                   total_limit=[round(old * p["total_loss_pct"] / 100, 2), round(new * p["total_loss_pct"] / 100, 2)], warmup=mult < 1)
        # 금액대가 바뀌면 종목 수·1회 비중의 기본값도 달라진다. 지금 값이 예전 기본값 그대로일 때만 맞추기를 권한다
        d0, d1 = make_plan(old, mkt, run["style"], 30), make_plan(new, mkt, run["style"], 30)
        fit = None
        if (d0["max_positions"], d0["per_trade_pct"]) != (d1["max_positions"], d1["per_trade_pct"]) \
                and (p["max_positions"], p["per_trade_pct"]) == (d0["max_positions"], d0["per_trade_pct"]):
            fit = {"max_positions": [p["max_positions"], d1["max_positions"]], "per_trade_pct": [p["per_trade_pct"], d1["per_trade_pct"]]}
        out["fit"] = fit
        if run["mode"] == "real" and new > old:
            out["notes"].append("실전이라 늘린 금액만큼 실제 주문이 더 나갈 수 있어요. 계좌의 주문 가능 금액을 확인해 주세요.")
        if new < old and m2["cash"] < m2["base"] * p["per_trade_pct"] / 100 * mult:
            out["notes"].append("줄이면 남는 현금이 1회 매수 금액보다 적어, 종목이 정리될 때까지는 남은 현금만큼만 살 수 있어요.")
        day_pnl = sum(c["pnl"] or 0 for c in self._today_closed(run)) + sum(t["qty"] * ((t["last"] or t["entry_price"]) - t["entry_price"]) for t in self._open(rid))
        if new < old and day_pnl <= -new * p["daily_loss_pct"] / 100:
            out["notes"].append("오늘 손실이 새 하루 손실 한도를 넘어, 오늘은 새로 사지 않게 돼요.")
        if d.get("dry"):
            return out
        if out["error"]:
            raise ValueError(out["error"])
        if run["mode"] == "real" and new > old and str(d.get("confirm") or "").strip() != "실전":
            raise ValueError("실전은 실제 주문이 나가요. 늘리려면 확인란에 '실전'을 적어 주세요")
        self.rt.db.x("UPDATE at_runs SET budget=? WHERE id=?", (new, rid))
        self.log(rid, "control", f"쓸 돈 {mon(old)} → {mon(new)}({'+' if new > old else '-'}{mon(abs(new - old))})", {"budget": {"from": old, "to": new}})
        cap = self.day_cap(new, mkt)
        if not run.get("lab_id") and cap > float(p.get("ai_daily_usd") or 0):     # 시드가 늘면 하루 AI 비용 한도도 따라 늘린다(줄이지는 않는다)
            self.rt.db.x("UPDATE at_runs SET params=json_set(params,'$.ai_daily_usd',?) WHERE id=?", (cap, rid))
            self.log(rid, "control", f"쓸 돈에 맞춰 하루 AI 비용 한도를 ${cap:g}로 올렸어요", {"ai_daily_usd": cap})
        if fit and d.get("fit"):
            self.edit(rid, {"max_positions": fit["max_positions"][1], "per_trade_pct": fit["per_trade_pct"][1]})
        return self.detail(rid)

    def edit(self, rid, patch):
        """사용자가 플랜 값을 직접 고친다(판이 하나 올라가고 이력에 남는다)."""
        run = self._run(rid)
        if run["status"] == "ended":
            raise ValueError("끝난 자동매매는 고칠 수 없어요")
        p, applied = copy.deepcopy(run["params"]), []
        opts = {}
        if "auto_apply" in (patch or {}):          # 개선안 자동 반영: 모의에서만, 사용자만 켤 수 있다(AI 개선안으로는 못 바꾼다)
            on = bool(patch["auto_apply"])
            if on and run["mode"] != "paper":
                raise ValueError("실전은 개선안을 직접 승인해야 반영돼요")
            opts["auto_apply"] = on
        if (patch or {}).get("ai_daily_usd") not in (None, ""):     # 하루 AI 비용 한도: 사용자만 바꾼다
            opts["ai_daily_usd"] = round(min(max(float(patch["ai_daily_usd"]), 0.2), 200.0), 2)
        if (patch or {}).get("review_days") not in (None, ""):
            opts["review_days"] = int(min(max(float(patch["review_days"]), 2), 20))
        opts = {k: v for k, v in opts.items() if p.get(k) != v}
        if opts:
            p.update(opts)
            self.rt.db.x("UPDATE at_runs SET params=? WHERE id=?", (json.dumps(p, ensure_ascii=False), rid))
            self.log(rid, "review", " · ".join(f"개선안 자동 반영 {'켬' if v else '끔'}" if k == "auto_apply" else f"하루 AI 비용 한도 ${v:g}" if k == "ai_daily_usd" else f"점검 주기 {v}거래일" for k, v in opts.items()))
        for k, v in (patch or {}).items():
            if k not in PLAN_KEYS or v in (None, ""):
                continue
            to = _clamp_key(k, v)
            if to != p.get(k):
                applied.append({"key": k, "label": PLAN_KEYS[k][3], "from": p.get(k), "to": to, "why": "직접 고침"})
                p[k] = to
        if not applied:
            return self.detail(rid)
        if p["entry_start"] >= p["entry_end"]:
            raise ValueError("매수 시작 시각이 끝 시각보다 늦어요")
        ver = run["ver"] + 1
        self.rt.db.x("UPDATE at_runs SET params=?, ver=? WHERE id=?", (json.dumps(p, ensure_ascii=False), ver, rid))
        self.rt.db.x("INSERT INTO at_changes(run_id,ts,ver,source,changes,stats) VALUES(?,?,?,?,?,?)",
                     (rid, self._now(), ver, "user", json.dumps(applied, ensure_ascii=False), None))
        self.log(rid, "review", f"플랜 값 {len(applied)}개를 직접 고쳤어요 · 플랜 {ver}판", {"changes": applied})
        return self.detail(rid)

    # ----- AI 복기(Claude가 기록을 읽고 개선안을 낸다)
    def review_context(self, d):
        run = self._run(int(d.get("run_id") or 0))
        rows = self._closed(run["id"])
        if len(rows) < 5:
            raise ValueError(f"끝난 거래가 5건은 있어야 AI가 복기할 수 있어요(지금 {len(rows)}건)")
        st = self.stats(run["id"], rows)
        st.pop("curve", None)
        trades = []
        for r in rows[-60:]:
            why = json.loads(r["why"] or "{}")
            trades.append({"종목": r["name"], "산 날": r["entry_day"], "산 시각": r["entry_hm"], "판": r["ver"], "수익률%": r["pnl_pct"],
                           "사유": r["reason"], "목표%": r["target_pct"], "손절%": r["stop_pct"], "보유 중 최고%": r["mfe_pct"],
                           "보유 중 최저%": r["mae_pct"], "보유(분)": r["hold_min"], "AI승률": r["win_prob"], "보정승률": r["cal_prob"],
                           "점수": r["score"], "분위기": r["stance"], "근거": (why.get("reasons") or [])[:2]})
        skips = self.rt.db.q("SELECT ts, msg FROM at_log WHERE run_id=? AND kind IN ('cycle','skip','risk') ORDER BY id DESC LIMIT 25", (run["id"],))
        O = self.opportunity(run)
        opp = {"범위": O["scope"], "거래일": O["days"], "추천 횟수": O["cycles"], "추천 종목": O["picks"], "산 종목": O["bought"], "매수율%": O["buy_rate"],
               "건너뛴 이유별(결과 = 그 뒤 실제로 어땠는지, 비용 뺀 수익률)": [
                   {"이유": r["label"], "건수": r["n"], "결과 나온 건수": r["graded"], "평균%": r["avg"], "이익 비율%": r["win"]} for r in O["reasons"]],
               "앱의 판단": self.opp_changes(run, O, self._since_ver(run))[1]}
        keys = {k: {"설명": v[3], "범위": "참/거짓" if v[2] is bool else "HH:MM" if v[2] == "hm" else f"{v[0]}~{v[1]}", "지금": run["params"].get(k)}
                for k, v in PLAN_KEYS.items()}
        ask = (f"내 AI 자동매매({run['name']}, {'국내' if run['mkt'] == 'kr' else '미국'} {STYLES[run['style']]['label']}, "
               f"{'실전' if run['mode'] == 'real' else '모의'})의 매매 기록을 복기해 줘.\n"
               "1) 먼저 한국어로 짧게: 첫 줄에 결론, 이어서 '- ' 항목으로 잘된 점, 손실 원인(종목 선정·산 시간·목표와 손절 폭·비중), 눈에 띄는 패턴.\n"
               "2) 그다음 플랜에서 바꿀 값을 제안해. 표본이 8건 미만인 조건만 보고 바꾸지 마. 근거가 약하면 changes를 비워. 한 번에 최대 3개.\n"
               "   매수가 너무 적으면 '기회 분석'을 봐: 건너뛴 종목이 그 뒤 실제로 벌었을 때(결과 8건 이상)만 그 기준을 한 단계 낮추고, "
               "돈이 모자라 1주를 못 산 경우가 많으면 종목 수를 줄여 1회 비중을 키우는 쪽을 봐. 잃고 있는 중에는 기준을 낮추지 마.\n"
               "3) 마지막에 아래 JSON 하나를 ```json 코드 블록으로. key는 '바꿀 수 있는 값'에 있는 것만, to는 범위 안에서.\n"
               '{"summary":"한 줄 요약","changes":[{"key":"min_win_prob","to":60,"why":"숫자를 넣은 근거 한 줄"}]}\n'
               "수익을 장담하는 말은 하지 마. 웹 검색은 하지 마.")
        data = {"기준시각": self._now(), "운용": {"쓸 돈": run["budget"], "통화": "원" if run["mkt"] == "kr" else "달러", "기간": f"{run['start_date']}~{run['end_date']}",
                                             "플랜 판": run["ver"], "지금 돈": self.money(run)},
                "성적": st, "기회 분석": opp, "바꿀 수 있는 값": keys, "끝난 거래(최근 60건)": trades, "최근 판단 기록": skips,
                "플랜 변경 이력": self.changes(run["id"])}
        return f"자동매매 복기 · {run['name']}", ask, data

    def save_review(self, text, d, model, usd):
        """AI 복기 답에서 개선안을 읽어 승인 대기로 남긴다."""
        run = self._run(int(d.get("run_id") or 0))
        m = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        j = {}
        if m:
            try:
                j = json.loads(m[-1])
            except ValueError:
                j = {}
        changes = []
        for c in (j.get("changes") or [])[:3]:
            key = c.get("key") if isinstance(c, dict) else None
            if key not in PLAN_KEYS or any(x["key"] == key for x in changes):
                continue
            try:
                to = _clamp_key(key, c.get("to"))
            except Exception:
                continue
            if to != run["params"].get(key):
                changes.append({"key": key, "label": PLAN_KEYS[key][3], "from": run["params"].get(key), "to": to, "why": str(c.get("why") or "")[:200]})
        body = text.split("```json")[0].strip()
        summary = str(j.get("summary") or "").strip() or (body.splitlines()[0] if body else "AI 복기")
        pr = self._save_proposal(run, "ai", summary, changes, {"model": model, "usd": usd}, body[:6000])
        if d.get("auto"):
            self._auto_apply(run, pr, "AI 복기")
        return pr

    def _ai_review_bg(self, rid):
        try:
            for _ in self.rt.ai.stream("auto", {"run_id": rid, "auto": True}):
                pass
        except Exception as e:
            self.rt.db.log("system", "error", f"자동매매 AI 복기 오류: {e}", self._now())

    # ------------------------------------------------------------------ 화면용
    def changes(self, rid):
        rows = self.rt.db.q("SELECT ts, ver, source, changes FROM at_changes WHERE run_id=? ORDER BY id", (rid,))
        for r in rows:
            r["changes"] = json.loads(r["changes"] or "[]")
        return rows

    def _summary(self, run, live=False):
        m = self.money(run, live=live)
        n = self.rt.db.one("SELECT COUNT(*) n, COALESCE(SUM(pnl>0),0) w FROM at_trades WHERE run_id=? AND status='closed'", (run["id"],))
        pend = self.rt.db.one("SELECT COUNT(*) n FROM at_proposals WHERE run_id=? AND status='pending'", (run["id"],))["n"]
        usd = self._ai_usd(run["id"])
        cost = round(usd * (self.rt.ai.fx() if run["mkt"] == "kr" else 1.0), 2)
        m.update(ai_usd=round(usd, 3), ai_cost=cost, net_pnl=round(m["equity"] - run["budget"] - cost, 2),
                 net_ret_pct=round((m["equity"] - run["budget"] - cost) / run["budget"] * 100, 2) if run["budget"] else 0)
        return {k: run[k] for k in ("id", "name", "mode", "mkt", "style", "model", "budget", "start_date", "end_date", "status",
                                    "status_msg", "ver", "created_at", "ended_at")} | {
            "money": m, "closed": n["n"], "wins": int(n["w"] or 0), "open": len(self._open(run["id"])), "pending": pend,
            "style_label": STYLES[run["style"]]["label"], "lab_id": run.get("lab_id"), "variant": run.get("variant")}

    # ------------------------------------------------------------------ 모의 자동 테스트(켜고 끄기만)
    @staticmethod
    def _short(model):
        from .ai import MODELS
        return MODELS[model][0].split(" · ")[0] if model in MODELS else str(model)

    def _engine_watch(self):
        """정밀 추천 엔진의 성과를 하루 한 번 본다(자동매매마다, 실험은 묶음마다).
        지난번 엔진을 바꾼 뒤 정밀 추천으로 산 거래가 ESC_N건 쌓였는데 성과가 나쁘면 엔진을 한 칸 올린다(Sonnet → Opus → Fable).
        올렸는데 앞 엔진보다 더 나빠졌으면 앞 엔진으로 되돌리고 더는 올리지 않는다(비싼 엔진에 돈만 쓰지 않게).
        하루 AI 비용 한도는 그대로 지킨다: 비싼 엔진은 정밀 추천 횟수를 줄여서라도 한도 안에 맞춘다."""
        groups = {}
        for r in self._runs("WHERE status='running'"):
            groups.setdefault(("lab", r["lab_id"]) if r["lab_id"] else ("run", r["id"]), []).append(r)
        for (kind, _), rs in groups.items():
            b = next((x for x in rs if x["variant"] == "base"), rs[0])
            day = AP.mkt_date(b["mkt"])
            if b["params"].get("engine_day") == day:
                continue
            try:
                self._engine_check(rs, b, day, kind == "lab")
            except Exception as e:
                self.rt.db.log("system", "error", f"추천 엔진 점검 실패({b['id']}): {type(e).__name__}: {e}", self._now())
                for r in rs:
                    self.rt.db.x("UPDATE at_runs SET params=json_set(params,'$.engine_day',?) WHERE id=?", (day, r["id"]))

    def engine_stats(self, rs, since):
        """정밀 추천(빠른 재추천 제외)으로 산 끝난 거래의 성적. rs: 같은 엔진을 쓰는 자동매매들."""
        ids = [r["id"] for r in rs]
        rows = self.rt.db.q(f"SELECT pnl_pct, why FROM at_trades WHERE run_id IN ({','.join('?' * len(ids))}) AND status='closed' AND COALESCE(exit_ts,'')>=?", (*ids, since))
        xs = [float(t["pnl_pct"] or 0) for t in rows if '"lite": true' not in (t["why"] or "")]
        if not xs:
            return {"n": 0, "avg": None, "win": None}
        return {"n": len(xs), "avg": round(sum(xs) / len(xs), 2), "win": round(sum(1 for x in xs if x > 0) / len(xs) * 100)}

    def _engine_check(self, rs, b, day, lab):
        p = b["params"]
        since = p.get("engine_since") or b["created_at"]
        st = self.engine_stats([b] if lab else rs, since)        # 실험은 '기본' 플랜으로 본다(같은 추천을 열 플랜이 나눠 써서 건수만 부풀지 않게)
        patch, ceil, up, lock, why = {"engine_day": day}, None, False, bool(p.get("engine_lock")), ""
        cur = b["model"]
        hist = list(p.get("engine_hist") or [])
        if st["n"] >= ESC_N and not lock:
            poor = st["avg"] <= 0 or st["win"] < ESC_WIN
            prev = hist[-1] if hist else None
            line = f"{self._short(cur)} {st['n']}건 · 한 번 평균 {st['avg']:+.2f}% · 이익 {st['win']}%"
            if prev and prev.get("up") and st["avg"] < prev["avg"]:
                ceil, lock = prev["model"], True
                why = f"{line}로, 올리기 전 {self._short(prev['model'])}({prev['avg']:+.2f}%)보다 나빠 되돌렸어요. 더는 올리지 않아요"
            elif poor:
                i = ENGINE_LADDER.index(cur) if cur in ENGINE_LADDER else 1
                if i + 1 < len(ENGINE_LADDER):
                    ceil, up = ENGINE_LADDER[i + 1], True
                    why = f"{line}로 성과가 나빠 정밀 추천 엔진을 {self._short(ceil)}로 올려요"
            if ceil:
                hist.append({"model": cur, "n": st["n"], "avg": st["avg"], "win": st["win"], "at": self._now(), "up": up})
        if ceil:
            p0 = dict(p, cycles_per_day=max(int(p.get("cycles_per_day") or 1), make_plan(b["budget"], b["mkt"], b["style"], 30, b["mode"])["cycles_per_day"]))
            model, fit, c = self.fit_cost(b["budget"], b["mkt"], b["style"], b["mode"], p0, "auto", p.get("lab_day_usd") if lab else None, ceil=ceil)
            if not lab:
                fit["ai_daily_usd"] = max(float(p.get("ai_daily_usd") or 0), fit["ai_daily_usd"])     # 한도는 내리지 않는다
            patch.update(fit, engine_ceil=ceil, engine_since=self._now(), engine_hist=hist[-8:], engine_lock=lock)
            if up and model != ceil:
                why += f" — 다만 하루 AI 비용 한도(${c.get('cap_day', 0):g}) 안에 들지 않아 {self._short(model)}로 두었어요"
            why += f" · 정밀 추천 {self._short(model)} 하루 {fit.get('cycles_per_day')}번{'' if fit.get('web_search', True) else '(뉴스 검색 없이)'}"
        else:
            model = cur
        for r in rs:
            self.rt.db.x("UPDATE at_runs SET params=?, model=? WHERE id=?", (json.dumps(dict(r["params"], **patch), ensure_ascii=False), model, r["id"]))
        if why:
            self.log(b["id"], "control", f"추천 엔진 점검 · {why}", {"engine": {"from": cur, "to": model, "ceil": ceil, "stats": st, "lock": lock}})
            self.rt.notify(f"[moa 자동매매] {'실험 ' if lab else ''}{b['name']}: {why}")

    def _cap_upgrade(self):
        """시드머니에 비례한 하루 AI 비용 한도(최소 $3)를 예전 판에서 시작한 직접 만든 자동매매에 한 번 적용한다.
        한도는 내리지 않는다. 엔진·정밀 추천 횟수·뉴스 검색은 새 한도에 맞춰 다시 고른다(빠른 재추천 간격은 그대로)."""
        from .ai import MODELS
        for r in self._runs("WHERE lab_id IS NULL AND status IN ('running','paused')"):
            p = r["params"]
            if p.get("cap_v") == 2:
                continue
            cap = self.day_cap(r["budget"], r["mkt"])
            try:
                p0 = dict(p, cycles_per_day=max(int(p.get("cycles_per_day") or 1), make_plan(r["budget"], r["mkt"], r["style"], 30, r["mode"])["cycles_per_day"]))
                model, patch, c = self.fit_cost(r["budget"], r["mkt"], r["style"], r["mode"], p0, "auto", ceil=p.get("engine_ceil"))
            except Exception as e:
                self.rt.db.log("system", "error", f"AI 비용 한도 맞추기 실패({r['id']}): {type(e).__name__}: {e}", self._now())
                continue
            patch["ai_daily_usd"] = max(float(p.get("ai_daily_usd") or 0), cap)
            patch["cap_v"] = 2
            self.rt.db.x("UPDATE at_runs SET params=?, model=? WHERE id=?", (json.dumps(dict(p, **patch), ensure_ascii=False), model, r["id"]))
            self.log(r["id"], "control", f"하루 AI 비용 한도를 ${float(p.get('ai_daily_usd') or 1):g} → ${patch['ai_daily_usd']:g}로 올렸어요(시드머니에 비례, 최소 ${RUN_DAY_MIN_USD:g}) · "
                                         f"정밀 추천 {MODELS[model][0].split(' · ')[0]} 하루 {patch.get('cycles_per_day', p.get('cycles_per_day'))}번"
                                         f"{'' if patch.get('web_search', True) else '(뉴스 검색 없이)'}", {"patch": patch, "model": model})

    def _upgrade(self):
        """예전 판에서 시작한 자동매매에 '빠른 재추천'을 붙인다(자동매매마다 한 번).
        모의·실험은 켠다(실험은 하루 비용 상한 안에서 정밀 추천 횟수까지 다시 맞춘다). 실전은 꺼 둔 채로 두고, 사용자가 플랜에서 켠다."""
        rows = [r for r in self._runs("WHERE status IN ('running','paused','closing')") if "quick_gap_min" not in r["params"]]
        labs = {}
        for r in rows:
            if r["lab_id"]:
                labs.setdefault(r["lab_id"], []).append(r)
                continue
            qg = QUICK_GAP[r["style"]] if r["mode"] == "paper" else 0
            self.rt.db.x("UPDATE at_runs SET params=? WHERE id=?", (json.dumps(dict(r["params"], quick_gap_min=qg), ensure_ascii=False), r["id"]))
            if qg:
                self.log(r["id"], "control", f"추천을 더 자주 받게 바꿨어요 · 정밀 추천 사이에 {qg}분마다 빠른 재추천(가장 싼 엔진, 한 번에 약 10원)", {"quick_gap_min": qg})
        for lab, rs in labs.items():
            b = next((x for x in rs if x["variant"] == "base"), rs[0])
            qg = QUICK_GAP[b["style"]]
            patch, model = {"quick_gap_min": qg}, b["model"]
            try:
                p0 = dict(b["params"], quick_gap_min=qg, cycles_per_day=make_plan(b["budget"], b["mkt"], b["style"], 30, "paper")["cycles_per_day"])
                model, patch, _ = self.fit_cost(b["budget"], b["mkt"], b["style"], "paper", p0, "auto", AUTO_SET.get((b["mkt"], b["style"]), (PAPER_DAY_USD,))[0],
                                                ceil=b["params"].get("engine_ceil"))
            except Exception as e:
                self.rt.db.log("system", "error", f"실험 추천 주기 맞추기 실패: {type(e).__name__}: {e}", self._now())
            patch["lab_day_usd"] = AUTO_SET.get((b["mkt"], b["style"]), (PAPER_DAY_USD,))[0]
            for r in rs:
                self.rt.db.x("UPDATE at_runs SET params=?, model=? WHERE id=?", (json.dumps(dict(r["params"], **patch), ensure_ascii=False), model, r["id"]))
            self.log(b["id"], "control", f"추천을 더 자주 받게 바꿨어요 · 정밀 추천 하루 {patch.get('cycles_per_day', b['params']['cycles_per_day'])}번 + {patch['quick_gap_min']}분마다 빠른 재추천(하루 비용 상한은 그대로)", {"patch": patch})

    def auto_on(self):
        return bool((self.rt.settings.get("autolab") or {}).get("on"))

    def auto_set(self, on):
        """켜면 시장·방식별 실험을 앱이 만들어 돌리고, 끄면 새 매수를 멈춘다(들고 있는 종목은 계속 지켜본다)."""
        on = bool(on)
        if on:
            rd = self.ready()
            if not rd["ai"]:
                raise ValueError("설정 → AI에서 Claude API 키를 먼저 넣어 주세요")
            if not rd["data"]:
                raise ValueError("실시간 순위가 필요해요. 설정 → 계좌에서 키움 계좌를 먼저 연결해 주세요")
        self.rt.settings.update({"autolab": {"on": on}})
        for r in self._runs("WHERE lab_id IS NOT NULL AND status IN ('running','paused')"):
            try:
                self.control(r["id"], "resume" if on else "pause")
            except ValueError:
                pass
        self._auto_at = 0
        if on:
            self._autopilot()
        return self.auto_state()

    def _autopilot(self):
        """켜져 있으면, 비어 있는 (시장, 방식) 칸에 실험 묶음을 만든다. 5분에 한 번만 본다."""
        if not self.auto_on() or time.time() - getattr(self, "_auto_at", 0) < 300:
            return
        self._auto_at = time.time()
        # 예전 판에서 만든 실험을 지금 설계에 맞춘다(바뀐 것이 있을 때만 한 번 쓴다)
        for r in self._runs("WHERE lab_id IS NOT NULL AND status IN ('running','paused')"):
            p, old = r["params"], json.dumps(r["params"], sort_keys=True)
            if float(p.get("idx_floor") or 0) < 0:
                p["idx_floor"] = 0.0                                   # 모의는 약한 장에도 쉬지 않는다
            if r["variant"] in ("loose", "strict"):                    # 고정값이라 스윙에서는 기본과 같았다 → 기본 대비로
                base = self.rt.db.one("SELECT params FROM at_runs WHERE lab_id=? AND variant='base'", (r["lab_id"],))
                if base:
                    want = apply_variant(json.loads(base["params"]), r["variant"])
                    p.update({k: want[k] for k in VARIANTS[r["variant"]][2]})
            em = VARIANTS.get(r["variant"] or "", ("", "", {}))[2].get("exit_mult")
            if em:                                                     # 짧게·길게는 상한만 바꿔 차이가 없었다 → 폭 자체를 바꾼다
                p["exit_mult"] = em
            p["warmup_trades"] = 0                                     # 실험은 처음부터 정해진 크기로
            if json.dumps(p, sort_keys=True) != old:
                self.rt.db.x("UPDATE at_runs SET params=? WHERE id=?", (json.dumps(p, ensure_ascii=False), r["id"]))
            want = AUTO_SET.get((r["mkt"], r["style"]), (0, 0))[1]
            if want and r["budget"] < want:                            # 가상 돈이 모자라 못 사는 일이 없게 늘린다
                self.rt.db.x("UPDATE at_runs SET budget=? WHERE id=?", (want, r["id"]))
                self.log(r["id"], "control", f"가상 돈을 {r['budget']:,.0f} → {want:,.0f}로 늘렸어요(돈이 모자라 못 사는 일을 없애려고)", {"budget": {"from": r["budget"], "to": want}})
        self._lab_budget()
        for lab in {r["lab_id"] for r in self._runs("WHERE lab_id IS NOT NULL AND status IN ('running','paused')")}:
            have = {r["variant"]: r for r in self._runs("WHERE lab_id=?", (lab,))}
            b = have.get("base")
            for k in VARIANTS:
                if k in have or not b or b["status"] == "ended":
                    continue
                p = apply_variant(copy.deepcopy(b["params"]), k)
                rid = self.rt.db.x(
                    "INSERT INTO at_runs(created_at,name,mode,mkt,style,model,budget,start_date,end_date,status,params,lab_id,variant,last_cycle) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self._now(), f"실험 · {VARIANTS[k][0]}", "paper", b["mkt"], b["style"], b["model"], b["budget"], b["start_date"], b["end_date"], b["status"],
                     json.dumps(p, ensure_ascii=False), lab, k, b["last_cycle"]))
                self.rt.db.x("INSERT INTO at_changes(run_id,ts,ver,source,changes,stats) VALUES(?,?,?,?,?,?)", (rid, self._now(), 1, "start", "[]", None))
                self.log(rid, "start", f"실험에 플랜을 보탰어요 · {VARIANTS[k][0]}", {"params": p, "lab": lab})
        rd = self.ready()
        if not (rd["ai"] and rd["data"]):
            return
        for (mkt, style), (usd, budget) in AUTO_SET.items():
            if self._runs("WHERE status IN ('running','paused','closing') AND lab_id IS NOT NULL AND mkt=? AND style=?", (mkt, style)):
                continue
            try:
                self.lab_create({"mkt": mkt, "style": style, "budget": budget, "days": AUTO_DAYS, "model": "auto", "day_usd": usd})
            except ValueError as e:
                self.rt.db.log("system", "error", f"모의 자동 테스트({mkt} {style}) 시작 실패: {e}", self._now())

    def _lab_budget(self):
        """하루 AI 비용 목표가 바뀌면 돌고 있는 실험의 엔진·정밀 추천 횟수·빠른 재추천 간격을 다시 맞춘다(묶음마다 한 번)."""
        labs = {}
        for r in self._runs("WHERE lab_id IS NOT NULL AND status IN ('running','paused')"):
            labs.setdefault(r["lab_id"], []).append(r)
        for lab, rs in labs.items():
            b = next((x for x in rs if x["variant"] == "base"), rs[0])
            want = AUTO_SET.get((b["mkt"], b["style"]), (PAPER_DAY_USD,))[0]
            if "quick_gap_min" not in b["params"] or b["params"].get("lab_day_usd") == want:
                continue                              # 예전 판(빠른 재추천 전)은 _upgrade가 맞춘다
            was = b["params"].get("lab_day_usd")
            p0 = dict(b["params"], quick_gap_min=QUICK_GAP[b["style"]], cycles_per_day=make_plan(b["budget"], b["mkt"], b["style"], 30, "paper")["cycles_per_day"])
            try:
                model, patch, c = self.fit_cost(b["budget"], b["mkt"], b["style"], "paper", p0, "auto", want, ceil=b["params"].get("engine_ceil"))
            except Exception as e:
                self.rt.db.log("system", "error", f"실험 비용 다시 맞추기 실패: {type(e).__name__}: {e}", self._now())
                continue
            patch["lab_day_usd"] = want
            for r in rs:
                self.rt.db.x("UPDATE at_runs SET params=?, model=? WHERE id=?", (json.dumps(dict(r["params"], **patch), ensure_ascii=False), model, r["id"]))
            from .ai import MODELS
            self.log(b["id"], "control", f"하루 AI 비용 목표를 {'$' + format(was, 'g') if was else '예전 값'} → ${want:g}로 바꿔 다시 맞췄어요 · 정밀 추천 {MODELS[model][0].split(' · ')[0]} "
                                         f"하루 {patch['cycles_per_day']}번{'' if patch.get('web_search') else '(뉴스 검색 없이)'} + {patch.get('quick_gap_min')}분마다 빠른 재추천",
                     {"patch": patch, "model": model, "day_usd": want})

    def advice(self):
        """지금까지의 모의 결과에서 실전 후보를 뽑는다. (시장, 방식, 플랜)별로 모든 실험의 거래를 합쳐,
        30건 이상이고 AI 비용까지 빼고 플러스인 것 중 가장 나은 플랜."""
        agg = {}
        for r in self._runs("WHERE mode='paper' AND lab_id IS NOT NULL"):
            a = self.rt.db.one("SELECT COUNT(*) n, COALESCE(SUM(pnl),0) pnl, COALESCE(SUM(pnl>0),0) w, COALESCE(SUM(pnl_pct),0) sp FROM at_trades WHERE run_id=? AND status='closed'", (r["id"],))
            g = agg.setdefault((r["mkt"], r["style"], r["variant"]), {"n": 0, "pnl": 0.0, "w": 0, "sp": 0.0, "usd": 0.0, "budget": r["budget"], "days": set()})
            g["n"] += a["n"]; g["pnl"] += a["pnl"]; g["w"] += int(a["w"] or 0); g["sp"] += a["sp"]; g["usd"] += self._ai_usd(r["id"])
            g["days"].update(x["entry_day"] for x in self.rt.db.q("SELECT DISTINCT entry_day FROM at_trades WHERE run_id=?", (r["id"],)))
        out = []
        for mkt, style in AUTO_SET:
            fx = self.rt.ai.fx() if mkt == "kr" else 1.0
            rows = []
            for (m, s, v), g in agg.items():
                if (m, s) != (mkt, style) or not g["n"]:
                    continue
                net = g["pnl"] - g["usd"] * fx
                rows.append({"variant": v, "label": VARIANTS.get(v, (v,))[0], "n": g["n"], "win_rate": round(g["w"] / g["n"] * 100), "avg_pct": round(g["sp"] / g["n"], 2),
                             "net_pct": round(net / g["budget"] * 100, 2), "days": len(g["days"]), "enough": g["n"] >= READY_N and len(g["days"]) >= READY_DAYS})
            rows.sort(key=lambda x: (-x["enough"], -x["net_pct"]))
            n_all = sum(x["n"] for x in rows)
            best = next((x for x in rows if x["enough"] and x["net_pct"] > 0), None)
            if best:
                text = f"'{best['label']}' 플랜이 {best['n']}건({best['days']}거래일)에서 AI 비용까지 빼고 {best['net_pct']:+.2f}%였어요. 실전 후보예요."
            elif any(x["enough"] for x in rows):
                text = "충분히 해 본 플랜이 있지만, AI 비용까지 빼면 플러스인 것이 아직 없어요. 실전을 권하지 않아요."
            else:
                top = max(rows, key=lambda x: x["n"], default=None)
                text = f"아직 자료를 모으는 중이에요(가장 많이 한 플랜 {top['n'] if top else 0}/{READY_N}건 · {top['days'] if top else 0}/{READY_DAYS}거래일)."
            out.append({"mkt": mkt, "style": style, "style_label": STYLES[style]["label"], "trades": n_all, "best": best, "text": text, "top": rows[:3]})
        return out

    def auto_state(self):
        day_cap = sum(v[0] for v in AUTO_SET.values())
        usd_today = usd_all = 0.0
        today = {m: AP.mkt_date(m) for m in ("kr", "us")}
        for r in self.rt.db.q("SELECT l.day, l.data, r.mkt FROM at_log l JOIN at_runs r ON r.id=l.run_id WHERE r.lab_id IS NOT NULL AND l.kind='cycle' AND l.data IS NOT NULL"):
            u = float((json.loads(r["data"]) or {}).get("usd") or 0)
            usd_all += u
            if r["day"] == today[r["mkt"]]:
                usd_today += u
        fx = self.rt.ai.fx()
        return {"on": self.auto_on(), "day_cap_usd": round(day_cap, 2), "month_cap_cur": round(day_cap * MONTH_DAYS * fx), "usd_today": round(usd_today, 2), "usd_all": round(usd_all, 2),
                "cur_all": round(usd_all * fx), "sets": [{"mkt": m, "style": s, "style_label": STYLES[s]["label"]} for m, s in AUTO_SET], "advice": self.advice()}

    # ------------------------------------------------------------------ 모의 실험실
    def lab_create(self, d):
        """같은 시장·방식에 플랜만 다른 모의 자동매매 여러 개를 한꺼번에 시작한다. 추천은 한 번만 받아 모두에게 나눈다."""
        d = dict(d or {}, mode="paper", model=d.get("model") or "auto")
        pv = self.preview(d)
        rd = self.ready()
        if not rd["ai"]:
            raise ValueError("설정 → AI에서 Claude API 키를 먼저 넣어 주세요")
        if not rd["data"]:
            raise ValueError("실시간 순위가 필요해요. 설정 → 계좌에서 키움 계좌를 먼저 연결해 주세요")
        keys = [k for k in (d.get("variants") or list(VARIANTS)) if k in VARIANTS]
        if "base" not in keys:
            keys.insert(0, "base")
        if len(keys) < 2:
            raise ValueError("비교하려면 실험을 두 개 이상 골라 주세요")
        if self._runs("WHERE status IN ('running','paused','closing') AND lab_id IS NOT NULL AND mkt=? AND style=?", (pv["mkt"], pv["style"])):
            raise ValueError(f"{'국내' if pv['mkt'] == 'kr' else '미국'} {STYLES[pv['style']]['label']} 실험이 이미 돌고 있어요. 먼저 끝내 주세요")
        today = self._local(pv["mkt"]).date()
        end = today + timedelta(days=pv["days"] - 1)
        lab = None
        for k in keys:
            label, _, patch = VARIANTS[k]
            p = apply_variant(copy.deepcopy(pv["params"]), k)
            p["warmup_trades"] = 0                      # 실험은 처음부터 정해진 크기로 산다(검증 단계는 실전을 위한 것)
            p["lab_day_usd"] = float(d.get("day_usd") or PAPER_DAY_USD)   # 이 값으로 비용을 맞췄다(목표가 바뀌면 _lab_budget이 다시 맞춘다)
            name = f"실험 · {label}"
            rid = self.rt.db.x(
                "INSERT INTO at_runs(created_at,name,mode,mkt,style,model,budget,start_date,end_date,status,params,lab_id,variant) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self._now(), name, "paper", pv["mkt"], pv["style"], pv["model"], pv["budget"], today.isoformat(), end.isoformat(),
                 "running", json.dumps(p, ensure_ascii=False), lab, k))
            if lab is None:
                lab = rid
                self.rt.db.x("UPDATE at_runs SET lab_id=? WHERE id=?", (lab, rid))
            self.rt.db.x("INSERT INTO at_changes(run_id,ts,ver,source,changes,stats) VALUES(?,?,?,?,?,?)", (rid, self._now(), 1, "start", "[]", None))
            self.log(rid, "start", f"실험을 시작했어요 · {label}", {"params": p, "lab": lab})
        self.rt.notify(f"[moa 자동매매] 모의 실험 {len(keys)}개를 시작했어요")
        return self.labs()

    def lab_control(self, lab_id, action):
        runs = self._runs("WHERE lab_id=? AND status!='ended'", (lab_id,))
        if not runs:
            raise ValueError("돌고 있는 실험이 없어요")
        if action not in ("pause", "resume", "end"):
            raise ValueError("알 수 없는 동작이에요")
        for r in runs:
            try:
                self.control(r["id"], action)
            except ValueError:
                pass
        return self.labs()

    # ------------------------------------------------------------------ 매매 기록(지난 자료를 날짜·전략·여러 기준으로)
    @staticmethod
    def _hold_band(m):
        if m is None:
            return None
        return "30분 미만" if m < 30 else "30분~2시간" if m < 120 else "2시간~하루" if m < 60 * 24 else "1~3일" if m < 60 * 24 * 3 else "3일 넘게"

    def _strat(self, r):
        """전략(비교 단위): 실험은 시장·방식·플랜이 같으면 하나로 묶고(실험 묶음이 바뀌어도 이어 본다), 직접 만든 것은 자동매매마다."""
        mk = "국내" if r["mkt"] == "kr" else "미국"
        if r.get("lab_id"):
            v = r.get("variant") or "base"
            return f"lab:{r['mkt']}:{r['style']}:{v}", {"label": VARIANTS.get(v, (v,))[0], "group": f"{mk} {STYLES[r['style']]['label']} 실험", "mode": "paper",
                                                          "mkt": r["mkt"], "style": r["style"], "variant": v, "lab": True}
        return f"run:{r['id']}", {"label": r["name"], "group": f"{mk} {STYLES[r['style']]['label']} {'실전' if r['mode'] == 'real' else '모의'}",
                                  "mode": r["mode"], "mkt": r["mkt"], "style": r["style"], "run": r["id"], "lab": False}

    def index_days(self, mkts, since):
        """지수의 날짜별 종가·등락(그 거래소의 날짜). 받은 값은 저장해 두고 함께 쓴다(6개월이 넘은 날, 받기에 실패한 때를 위해).
        반환: {날짜: [{mkt, name, sym, close, pct, range}]}"""
        now, syms = time.time(), []
        for m in mkts:
            for name, ysym in IDX[m]:
                syms.append((m, name, ysym))
                if now - self._idx_at.get(ysym, 0) < 600:
                    continue
                self._idx_at[ysym] = now
                try:
                    bars = self.rt.invest.candles("us", ysym, "d").get("bars") or []
                except Exception:
                    continue
                for a, b in zip(bars, bars[1:]):
                    if a.get("c") and b.get("c"):
                        self.rt.db.x("INSERT OR REPLACE INTO idx_daily(sym,day,close,pct,hi,lo) VALUES(?,?,?,?,?,?)",
                                     (ysym, b["t"][:10], b["c"], round((b["c"] / a["c"] - 1) * 100, 2),
                                      round((b["h"] / a["c"] - 1) * 100, 2) if b.get("h") else None, round((b["l"] / a["c"] - 1) * 100, 2) if b.get("l") else None))
        if not syms:
            return {}
        meta = {y: (m, n) for m, n, y in syms}
        out = {}
        for r in self.rt.db.q(f"SELECT * FROM idx_daily WHERE sym IN ({','.join('?' * len(meta))}) AND day>=? ORDER BY day", (*meta, since)):
            m, n = meta[r["sym"]]
            out.setdefault(r["day"], []).append({"mkt": m, "name": n, "sym": r["sym"], "close": r["close"], "pct": r["pct"], "hi": r["hi"], "lo": r["lo"]})
        order = [y for _, _, y in syms]
        for v in out.values():
            v.sort(key=lambda x: order.index(x["sym"]))
        return out

    def history(self, q=None):
        """지난 자동매매를 다각도로: 날짜별 · 전략별 · 여러 기준별 성적과 거래 목록.
        q: days(7·30·90·0=전체), mkt(kr·us·''), mode(real·paper·''), strat(전략 키), day(그날 자세히)
        돈은 모두 원화(미국은 지금 환율로 바꿈). 수익률은 거래마다의 % 평균."""
        q = q or {}
        days = int(q.get("days") or 30)
        mkt, mode, sk, one = q.get("mkt") or "", q.get("mode") or "", q.get("strat") or "", q.get("day") or ""
        fx = self.rt.ai.fx()
        krw = lambda v, m: (v or 0) * (fx if m == "us" else 1.0)
        since = (AP.kst_now().replace(tzinfo=None) - timedelta(days=days - 1)).strftime("%Y-%m-%d") if days > 0 else "0000"
        runs = {r["id"]: r for r in self._runs() if (not mkt or r["mkt"] == mkt) and (not mode or r["mode"] == mode)}
        strats, key_of = {}, {}
        for r in runs.values():
            k, meta = self._strat(r)
            key_of[r["id"]] = k
            S = strats.setdefault(k, dict(meta, key=k, runs=[], budget=0.0, status="ended"))
            S["runs"].append(r["id"])
            S["budget"] = max(S["budget"], krw(r["budget"], r["mkt"]))
            if r["status"] in ACTIVE:
                S["status"] = r["status"] if S["status"] != "running" else "running"
        if sk:
            runs = {i: r for i, r in runs.items() if key_of[i] == sk}
        ids = list(runs)
        empty = {"filters": {"days": days, "mkt": mkt, "mode": mode, "strat": sk}, "fx": fx, "strategies": [], "days": [], "groups": [], "trades": [], "total": {"n": 0}}
        if not ids:
            empty["strat_list"] = [{"key": k, "label": v["label"], "group": v["group"]} for k, v in strats.items()]
            return empty
        qm = ",".join("?" * len(ids))
        trades = self.rt.db.q(f"SELECT * FROM at_trades WHERE run_id IN ({qm}) AND status IN ('open','closed') AND (entry_day>=? OR COALESCE(exit_day,'')>=?) ORDER BY entry_ts", (*ids, since, since))
        logs = self.rt.db.q(f"SELECT run_id, ts, day, msg, data FROM at_log WHERE run_id IN ({qm}) AND kind='cycle' AND day>=? ORDER BY id", (*ids, since))
        for t in trades:
            r = runs[t["run_id"]]
            try:
                why = json.loads(t["why"]) if t.get("why") else {}
            except ValueError:
                why = {}
            why = why if isinstance(why, dict) else {}
            t.update(strat=key_of[t["run_id"]], mode=r["mode"], style=r["style"], lite=bool(why.get("lite")), pnl_krw=round(krw(t["pnl"], t["mkt"]), 2),
                     hour=(t["entry_hm"] or "")[:2], dow="월화수목금토일"[datetime.strptime(t["entry_day"], "%Y-%m-%d").weekday()],
                     catalyst=why.get("catalyst") or "", reasons=(why.get("reasons") or [])[:2])
        pids = sorted({t["pick_id"] for t in trades if t.get("pick_id")})
        eng = {}
        for i in range(0, len(pids), 500):
            ch = pids[i:i + 500]
            eng.update({r["id"]: r["model"] for r in self.rt.db.q(f"SELECT id, model FROM ai_picks WHERE id IN ({','.join('?' * len(ch))})", ch)})
        for t in trades:
            t["engine"] = _short_model(eng.get(t.get("pick_id"))) if eng.get(t.get("pick_id")) else None
        closed = [t for t in trades if t["status"] == "closed" and (t["exit_day"] or t["entry_day"]) >= since]

        def agg(rows):
            a = _agg(rows)
            if not a["n"]:
                return a
            pk = [r["pnl_krw"] for r in rows]
            a.update(pnl_krw=round(sum(pk)), hold_med=_median([r["hold_min"] for r in rows]), mfe_med=_median([r["mfe_pct"] for r in rows]),
                     mae_med=_median([r["mae_pct"] for r in rows]), best=round(max(r["pnl_pct"] or 0 for r in rows), 2), worst=round(min(r["pnl_pct"] or 0 for r in rows), 2))
            a.pop("pnl", None)
            return a
        # AI 비용·추천 횟수(날짜·전략별). 실험은 한 번 받은 추천의 비용을 플랜들이 나눠 적었으니 더하면 실제 비용이다
        cost_d, cost_s, cyc_d = {}, {}, {}
        for x in logs:
            try:
                d = json.loads(x["data"]) if x["data"] else {}
            except ValueError:
                d = {}
            u = float(d.get("usd") or 0) * fx
            cost_d[x["day"]] = cost_d.get(x["day"], 0) + u
            k = key_of[x["run_id"]]
            cost_s[k] = cost_s.get(k, 0) + u
            if d.get("pick_id") or d.get("decisions") is not None:
                c = cyc_d.setdefault(x["day"], {})
                pid = (x["run_id"] if not runs[x["run_id"]].get("lab_id") else runs[x["run_id"]]["lab_id"], x["ts"][:16])
                c[pid] = bool(d.get("lite"))
        # 전략별
        out_s = []
        for k, S in strats.items():
            rows = [t for t in closed if t["strat"] == k]
            if sk and k != sk:
                continue
            opens = [t for t in trades if t["strat"] == k and t["status"] == "open"]
            if not rows and not opens and not cost_s.get(k):
                continue
            a = agg(rows)
            cost = round(cost_s.get(k, 0))
            net = (a.get("pnl_krw") or 0) - cost
            reasons = {}
            for t in rows:
                reasons[t["reason"] or "기타"] = reasons.get(t["reason"] or "기타", 0) + 1
            per_day = {}
            for t in rows:
                per_day.setdefault(t["exit_day"] or t["entry_day"], []).append(t)
            out_s.append({k2: S[k2] for k2 in ("key", "label", "group", "mode", "mkt", "style", "lab", "status")} | {
                "stats": a, "open": len(opens), "ai_cost": cost, "net": round(net), "net_pct": round(net / S["budget"] * 100, 2) if S["budget"] else None,
                "budget": round(S["budget"]), "days": len(per_day), "reasons": sorted(reasons.items(), key=lambda kv: -kv[1]),
                "by_day": [{"day": d, "n": len(v), "win": sum(1 for t in v if (t["pnl"] or 0) > 0), "pnl": round(sum(t["pnl_krw"] for t in v)),
                            "avg": round(sum(t["pnl_pct"] or 0 for t in v) / len(v), 2)} for d, v in sorted(per_day.items(), reverse=True)][:30]})
        out_s.sort(key=lambda x: (-(x["stats"].get("n", 0) >= 10), -x["net"]))
        # 지수(그 시장 날짜 기준). 시장을 골랐으면 그 시장만
        try:
            ixd = self.index_days([mkt] if mkt else ["kr", "us"], since)
        except Exception as e:
            self.rt.db.log("system", "error", f"매매 기록 지수 받기 실패: {type(e).__name__}: {e}", self._now())
            ixd = {}
        main = {m: IDX[m][0][1] for m in IDX}
        for t in trades:
            ix = next((x for x in ixd.get(t["entry_day"], []) if x["sym"] == main[t["mkt"]]), None)
            t["idx_pct"] = ix["pct"] if ix else None
        # 날짜별
        dset = {t["entry_day"] for t in trades if t["entry_day"] >= since} | {t["exit_day"] for t in closed if t["exit_day"]} | set(cost_d)
        out_d = []
        for d in sorted(dset, reverse=True):
            cl = [t for t in closed if (t["exit_day"] or t["entry_day"]) == d]
            buys = [t for t in trades if t["entry_day"] == d]
            a = agg(cl)
            cost = round(cost_d.get(d, 0))
            cyc = cyc_d.get(d, {})
            top = {}
            for t in cl:
                top[t["strat"]] = top.get(t["strat"], 0) + t["pnl_krw"]
            best = max(top.items(), key=lambda kv: kv[1]) if top else None
            out_d.append({"day": d, "dow": "월화수목금토일"[datetime.strptime(d, "%Y-%m-%d").weekday()], "buys": len(buys), "sells": len(cl), "stats": a,
                          "ai_cost": cost, "net": round((a.get("pnl_krw") or 0) - cost), "cycles": len(cyc), "quick": sum(1 for v in cyc.values() if v),
                          "mkts": sorted({t["mkt"] for t in buys + cl}),
                          "idx": [x for x in ixd.get(d, []) if mkt or not {t["mkt"] for t in buys + cl} or x["mkt"] in {t["mkt"] for t in buys + cl}],
                          "best": {"key": best[0], "label": strats[best[0]]["label"], "group": strats[best[0]]["group"], "pnl": round(best[1])} if best else None})

        def group(label, fn, order=None):
            g = {}
            for t in closed:
                try:
                    k = fn(t)
                except Exception:
                    k = None
                if k is not None:
                    g.setdefault(k, []).append(t)
            rows = [dict(agg(v), key=k) for k, v in g.items()]
            rows.sort(key=(lambda x: order.index(x["key"]) if x["key"] in order else 99) if order else (lambda x: -x["n"]))
            return {"label": label, "rows": rows}

        def wp(t):
            v = t["cal_prob"] if t["cal_prob"] is not None else t["win_prob"]
            return None if v is None else "55% 미만" if v < 55 else "55~65%" if v < 65 else "65% 이상"

        def sc(t):
            return None if t["score"] is None else "70 미만" if t["score"] < 70 else "70~80" if t["score"] < 80 else "80 이상"
        groups = [group("파는 사유", lambda t: t["reason"] or "기타"),
                  group("추천 종류", lambda t: "빠른 재추천" if t["lite"] else "정밀 추천", ["정밀 추천", "빠른 재추천"]),
                  group("정밀 추천 엔진", lambda t: None if t["lite"] else t["engine"], ["Haiku 4.5", "Sonnet 5.5", "Opus 5.5", "Fable 5.2"]),
                  group("시장 · 방식", lambda t: f"{'국내' if t['mkt'] == 'kr' else '미국'} {STYLES[t['style']]['label']}"),
                  group("실전 · 모의", lambda t: "실전" if t["mode"] == "real" else "모의", ["실전", "모의"]),
                  group("산 시각(그 시장 기준)", lambda t: f"{t['hour']}시" if t["hour"] else None, [f"{h:02d}시" for h in range(24)]),
                  group("요일", lambda t: t["dow"], list("월화수목금")),
                  group("보유 시간", lambda t: self._hold_band(t["hold_min"]), ["30분 미만", "30분~2시간", "2시간~하루", "1~3일", "3일 넘게"]),
                  group("그날 지수(코스피·S&P 500)", lambda t: None if t["idx_pct"] is None else "+1% 넘게 오른 날" if t["idx_pct"] > 1 else "0~+1%" if t["idx_pct"] >= 0
                        else "−1~0%" if t["idx_pct"] >= -1 else "−1% 넘게 내린 날", ["+1% 넘게 오른 날", "0~+1%", "−1~0%", "−1% 넘게 내린 날"]),
                  group("AI 분위기", lambda t: t["stance"] or None, ["공격", "보통", "관망"]),
                  group("AI 승률", wp, ["55% 미만", "55~65%", "65% 이상"]),
                  group("AI 점수", sc, ["70 미만", "70~80", "80 이상"])]
        groups = [g for g in groups if len(g["rows"]) > 1 or (g["rows"] and g["label"] == "파는 사유")]
        # 종목별(자주 거래한 종목)
        by_sym = {}
        for t in closed:
            by_sym.setdefault((t["mkt"], t["sym"]), []).append(t)
        syms = sorted(([dict(agg(v), mkt=m, sym=sy, name=v[-1]["name"]) for (m, sy), v in by_sym.items()]), key=lambda x: -x["n"])[:15]
        tot = agg(closed)
        cost_all = round(sum(cost_d.values()))
        tot.update(ai_cost=cost_all, net=round((tot.get("pnl_krw") or 0) - cost_all), open=sum(1 for t in trades if t["status"] == "open"),
                   buys=sum(1 for t in trades if t["entry_day"] >= since), days=len(out_d))
        keep = ("id", "strat", "mode", "mkt", "style", "sym", "name", "qty", "status", "entry_ts", "entry_day", "entry_hm", "entry_price", "exit_ts", "exit_day",
                "exit_price", "last", "reason", "pnl", "pnl_krw", "pnl_pct", "hold_min", "mfe_pct", "mae_pct", "target_pct", "stop_pct", "score", "win_prob", "stance",
                "lite", "catalyst", "reasons", "idx_pct", "engine")
        sel = [t for t in trades if (not one or t["entry_day"] == one or t.get("exit_day") == one)]
        sel.sort(key=lambda t: t.get("exit_ts") or t["entry_ts"], reverse=True)
        out = dict(empty, strategies=out_s, days=out_d, groups=groups, symbols=syms, total=tot,
                   trades=[{k: t.get(k) for k in keep} | {"strat_label": strats[t["strat"]]["label"], "strat_group": strats[t["strat"]]["group"]} for t in sel[:400]],
                   trades_n=len(sel), strat_list=[{"key": k, "label": v["label"], "group": v["group"]} for k, v in strats.items()])
        if one:                                          # 그날 받은 추천(시각·종류·분위기·산 종목)
            recs, seen = [], set()
            for x in logs:
                if x["day"] != one or not x["data"]:
                    continue
                d = json.loads(x["data"])
                r = runs[x["run_id"]]
                key = (r.get("lab_id") or r["id"], x["ts"][:16])
                dec = d.get("decisions") or []
                if key in seen:
                    rec = next(z for z in recs if z["_k"] == key)
                    rec["bought"] += sum(1 for z in dec if z.get("act") == "buy")
                    rec["usd"] += float(d.get("usd") or 0)
                    continue
                seen.add(key)
                recs.append({"_k": key, "ts": x["ts"], "group": strats[key_of[x["run_id"]]]["group"], "lite": bool(d.get("lite")), "stance": d.get("stance"),
                             "view": d.get("view") or "", "picks": [z.get("name") or z.get("sym") for z in dec][:3], "bought": sum(1 for z in dec if z.get("act") == "buy"),
                             "usd": float(d.get("usd") or 0), "msg": x["msg"] if not dec else ""})
            for z in recs:
                z.pop("_k")
                z["cost"] = round(z.pop("usd") * fx)
            out["day"] = {"day": one, "recs": recs[::-1][:60]}
        return out

    def marks(self, mkt, sym, days=30):
        """이 종목에서 자동매매가 한 일(차트에 찍을 것): AI가 추천한 때, 플랜별로 산 때·판 때. 실전과 모의를 가른다."""
        sym = str(sym or "").upper()
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        evs = []
        try:
            for r in self.rt.db.q("SELECT id, ts, stance, picks FROM ai_picks WHERE mkt=? AND ts>=? ORDER BY ts", (mkt, since)):
                for x in json.loads(r["picks"] or "[]"):
                    if str(x.get("sym") or "").upper() == sym:
                        evs.append({"kind": "rec", "ts": r["ts"], "price": x.get("entry_price"), "stance": r["stance"], "score": x.get("score"),
                                    "win_prob": x.get("win_prob"), "target_pct": x.get("target_pct"), "stop_pct": x.get("stop_pct"), "pick_id": r["id"]})
        except Exception:
            pass
        for t in self.rt.db.q("SELECT t.*, r.mode, r.name run_name, r.variant, r.lab_id FROM at_trades t JOIN at_runs r ON r.id=t.run_id "
                              "WHERE t.mkt=? AND t.sym=? AND t.entry_ts>=? AND t.status!='cancelled' ORDER BY t.entry_ts", (mkt, sym, since)):
            plan = VARIANTS[t["variant"]][0] if t["variant"] in VARIANTS else t["run_name"]
            base = {"mode": t["mode"], "plan": plan, "run": t["run_id"], "lab": bool(t["lab_id"]), "qty": t["qty"]}
            evs.append(dict(base, kind="buy", ts=t["entry_ts"], price=t["entry_price"],
                            target=round(t["entry_price"] * (1 + t["target_pct"] / 100), 2), stop=round(t["entry_price"] * (1 - t["stop_pct"] / 100), 2),
                            target_pct=t["target_pct"], stop_pct=t["stop_pct"], open=t["status"] == "open"))
            if t["status"] == "closed" and t["exit_ts"]:
                evs.append(dict(base, kind="sell", ts=t["exit_ts"], price=t["exit_price"], reason=t["reason"], pct=t["pnl_pct"], pnl=t["pnl"]))
        evs.sort(key=lambda e: e["ts"])
        return {"mkt": mkt, "sym": sym, "events": evs,
                "has": {"real": any(e.get("mode") == "real" for e in evs), "paper": any(e.get("mode") == "paper" for e in evs), "rec": any(e["kind"] == "rec" for e in evs)}}

    def _lab_book(self, L):
        """실험 묶음의 장부: 지금 들고 있는 종목(같은 종목은 한 줄로, 플랜별 내역 포함)과 최근 판 기록."""
        ids = [x["id"] for x in L["runs"]]
        lab = {x["id"]: x["label"] for x in L["runs"]}
        q = ",".join("?" * len(ids))
        hold = {"day": "오늘 장 마감 전에 모두 팔아요", "swing": "늦어도 5거래일 안에 팔아요", "month": "늦어도 20거래일 안에 팔아요"}[L["style"]]
        pos = {}
        rows = self.rt.db.q(f"SELECT * FROM at_trades WHERE run_id IN ({q}) AND status='open' ORDER BY entry_ts", ids)
        now_px = {}
        for t in sorted(rows, key=lambda t: -t["run_id"]):       # 번호가 작은 플랜이 한 바퀴의 맨 끝에 시세를 받는다 → 가장 새 값
            if t["last"]:
                now_px[t["sym"]] = t["last"]
        for t in rows:
            last = now_px.get(t["sym"]) or t["last"] or t["entry_price"]
            g = pos.setdefault(t["sym"], {"sym": t["sym"], "name": t["name"], "last": last, "first_ts": t["entry_ts"], "qty": 0, "cost": 0.0, "value": 0.0, "plans": [], "why": _why(t.get("why"))})
            g["qty"] += t["qty"]; g["cost"] += t["qty"] * t["entry_price"]; g["value"] += t["qty"] * last; g["last"] = last
            g["plans"].append({"run": t["run_id"], "label": lab.get(t["run_id"], ""), "ts": t["entry_ts"], "qty": t["qty"], "price": t["entry_price"],
                               "pct": round((last / t["entry_price"] - 1) * 100, 2), "target": round(t["entry_price"] * (1 + t["target_pct"] / 100), 2),
                               "stop": round(t["entry_price"] * (1 - t["stop_pct"] / 100), 2), "target_pct": t["target_pct"], "stop_pct": t["stop_pct"],
                               "trail_pct": t["trail_pct"], "exit_by": t["exit_by"], "exit_date": t["exit_date"]})
        out = []
        for g in pos.values():
            g["avg"] = round(g["cost"] / g["qty"], 2)
            g["pnl"] = round(g["value"] - g["cost"], 2)
            g["pct"] = round((g["value"] / g["cost"] - 1) * 100, 2) if g["cost"] else 0
            g["hold"] = hold
            out.append(g)
        out.sort(key=lambda g: -g["value"])
        sold = [{"run": t["run_id"], "label": lab.get(t["run_id"], ""), "sym": t["sym"], "name": t["name"], "qty": t["qty"], "entry_ts": t["entry_ts"], "entry": t["entry_price"],
                 "exit_ts": t["exit_ts"], "exit": t["exit_price"], "reason": t["reason"], "pnl": t["pnl"], "pct": t["pnl_pct"]}
                for t in self.rt.db.q(f"SELECT * FROM at_trades WHERE run_id IN ({q}) AND status='closed' ORDER BY exit_ts DESC LIMIT 20", ids)]
        return out, sold

    def _lab_last(self, L):
        """가장 최근 추천에서 종목마다 몇 개 플랜이 샀고, 나머지는 왜 건너뛰었는지."""
        ids = [x["id"] for x in L["runs"]]
        q = ",".join("?" * len(ids))
        rows = [r for r in self.rt.db.q(f"SELECT ts, data FROM at_log WHERE run_id IN ({q}) AND kind='cycle' AND data IS NOT NULL ORDER BY id DESC LIMIT {len(ids) * 2}", ids)
                if '"pick_id"' in r["data"]]
        if not rows:
            return None
        first = json.loads(rows[0]["data"])
        pid = first.get("pick_id")
        out = {"ts": rows[0]["ts"], "stance": first.get("stance"), "view": first.get("view"), "lite": bool(first.get("lite")), "picks": {}}
        for r in rows:
            d = json.loads(r["data"])
            if d.get("pick_id") != pid:
                continue
            dec = d.get("decisions")
            if dec is None:
                dec = [dict(x, act="skip", code="auto" if x.get("auto") else "watch") for x in d.get("picks") or []]
            for x in dec:
                g = out["picks"].setdefault(x.get("sym"), {"sym": x.get("sym"), "name": x.get("name") or x.get("sym"), "score": x.get("score"), "win_prob": x.get("win_prob"), "bought": 0, "skips": {}})
                if x.get("act") == "buy":
                    g["bought"] += 1
                else:
                    k = SKIP.get(skip_code(x), "기타")
                    g["skips"][k] = g["skips"].get(k, 0) + 1
        out["picks"] = [dict(g, skips=[{"why": k, "n": v} for k, v in sorted(g["skips"].items(), key=lambda kv: -kv[1])]) for g in out["picks"].values()]
        return out

    def labs(self):
        """실험 묶음별 성적표: 같은 추천을 받은 플랜들을 AI 비용까지 뺀 수익률 순으로."""
        out = {}
        for r in self._runs("WHERE lab_id IS NOT NULL"):
            L = out.setdefault(r["lab_id"], {"id": r["lab_id"], "mkt": r["mkt"], "style": r["style"], "style_label": STYLES[r["style"]]["label"],
                                             "model": r["model"], "budget": r["budget"], "start_date": r["start_date"], "end_date": r["end_date"], "runs": []})
            sm = self._summary(r)
            a = self.rt.db.one("SELECT COUNT(*) n, COALESCE(SUM(pnl>0),0) w, COALESCE(AVG(pnl_pct),0) avg FROM at_trades WHERE run_id=? AND status='closed'", (r["id"],))
            buys = self.rt.db.one("SELECT COUNT(*) n FROM at_trades WHERE run_id=? AND status!='cancelled'", (r["id"],))["n"]
            label, desc, patch = VARIANTS.get(r["variant"] or "", (r["name"], "", {}))
            L["runs"].append({"id": r["id"], "variant": r["variant"], "label": label, "desc": desc, "status": r["status"], "equity": sm["money"]["equity"],
                              "ret_pct": sm["money"]["ret_pct"], "net_ret_pct": sm["money"]["net_ret_pct"], "ai_cost": sm["money"]["ai_cost"], "ai_usd": sm["money"]["ai_usd"],
                              "closed": a["n"], "wins": int(a["w"] or 0), "win_rate": round(a["w"] / a["n"] * 100) if a["n"] else None,
                              "avg_pct": round(a["avg"], 2) if a["n"] else None, "buys": buys, "open": sm["open"], "enough": a["n"] >= READY_N,
                              "open_qty": sum(t["qty"] for t in self._open(r["id"])), "open_pct": round(sm["money"]["open_cost"] / r["budget"] * 100, 1) if r["budget"] else 0})
        labs = []
        for L in sorted(out.values(), key=lambda x: -x["id"]):
            L["runs"].sort(key=lambda x: (-(x["closed"] >= 10), -(x["closed"] > 0), -x["net_ret_pct"]))   # 10건 넘은 플랜끼리 먼저 겨룬다
            act = [x for x in L["runs"] if x["status"] != "ended"]
            L["status"] = "running" if any(x["status"] == "running" for x in act) else "paused" if act else "ended"
            L["usd"] = round(sum(x["ai_usd"] for x in L["runs"]), 2)
            L["cycles"] = self.rt.db.one("SELECT COUNT(*) n FROM at_log WHERE run_id=? AND kind='cycle' AND data IS NOT NULL", (L["id"],))["n"]
            L["quick"] = self.rt.db.one("SELECT COUNT(*) n FROM at_log WHERE run_id=? AND kind='cycle' AND data LIKE '%\"lite\": true%'", (L["id"],))["n"]
            L["plan"] = next(({k: r["params"].get(k) for k in ("cycles_per_day", "quick_gap_min", "web_search")} for r in self._runs("WHERE id=?", (L["id"],))), None)
            L["closed"] = sum(x["closed"] for x in L["runs"])
            L["open"] = sum(x["open"] for x in L["runs"])
            L["holders"] = sum(1 for x in L["runs"] if x["open"])
            L["bought"] = sum(x["buys"] for x in L["runs"])
            L["positions"], L["trades"] = self._lab_book(L)
            L["last"] = self._lab_last(L)
            L["stocks"] = len(L["positions"])                       # 들고 있는 '종목' 수(같은 종목을 여러 플랜이 들어도 하나)
            la = L["last"]
            if la and la["picks"]:
                got = [x for x in la["picks"] if x["bought"]]
                why = {}
                for x in la["picks"]:
                    for k in x["skips"]:
                        why[k["why"]] = why.get(k["why"], 0) + k["n"]
                top = max(why, key=why.get) if why else None
                L["last_line"] = (f"최근 {'빠른 재추천' if la.get('lite') else '추천'}({la['ts'][11:16]}{' · ' + la['stance'] if la.get('stance') else ''}): {len(la['picks'])}종목 중 "
                                  + (f"{', '.join(x['name'] for x in got[:2])}을(를) 플랜 {max(x['bought'] for x in got)}개가 샀어요" if got else "산 플랜이 없어요")
                                  + (f". 안 산 이유는 주로 '{top}'" if top else ""))
            else:
                L["last_line"] = None
            lead = next((x for x in L["runs"] if x["closed"] >= 10), None)
            L["lead"] = lead["label"] if lead else None
            L["note"] = (f"지금은 '{lead['label']}'이 앞서요(AI 비용 빼고 {lead['net_ret_pct']:+.2f}%, {lead['closed']}건)." + ("" if lead["enough"] else f" {READY_N}건이 쌓여야 믿을 만해요.")
                         if lead else "아직 거래가 적어 비교하기 일러요. 10건부터 순위를 말해요.")
            if act:
                try:
                    n = self.now(self._run(L["id"] if any(x["id"] == L["id"] for x in act) else act[0]["id"]))
                    L["now"] = {k: n[k] for k in ("phase", "busy", "scan", "next", "today", "engine")}
                except Exception:
                    L["now"] = None
            labs.append(L)
        return {"labs": labs, "presets": [{"key": k, "label": v[0], "desc": v[1]} for k, v in VARIANTS.items()], "auto": self.auto_state()}

    def brief(self):
        """홈 화면용: 돌고 있는 자동매매의 지금 상태를 가볍게(시세를 새로 묻지 않는다)."""
        out = []
        for run in self._runs("WHERE status IN ('running','paused','closing') AND lab_id IS NULL"):
            try:
                n = self.now(run)
                out.append(dict(self._summary(run), now={k: n[k] for k in ("phase", "busy", "scan", "next", "today", "size", "period", "engine")}))
            except Exception as e:
                self.rt.db.log("system", "error", f"자동매매 요약 오류: {type(e).__name__}: {e}", self._now())
        labs = []
        try:
            labs = [{k: L.get(k) for k in ("id", "mkt", "style_label", "status", "lead", "note", "closed", "usd", "now", "open", "holders", "bought", "cycles", "quick", "plan", "stocks", "last_line")} | {"hold": [{"name": g["name"], "pct": g["pct"]} for g in L.get("positions", [])[:4]]} | {"n": len(L["runs"])}
                    | {"race": [{"label": r["label"], "variant": r["variant"], "net": r["net_ret_pct"], "n": r["closed"], "open": r["open"]} for r in L["runs"]]}
                    for L in self.labs()["labs"] if L["status"] != "ended"]
        except Exception as e:
            self.rt.db.log("system", "error", f"실험 요약 오류: {type(e).__name__}: {e}", self._now())
        return {"runs": out, "labs": labs, "ended": self.rt.db.one("SELECT COUNT(*) n FROM at_runs WHERE status='ended'")["n"]}

    def overview(self):
        from .ai import MODELS
        c = self.rt.ai.conf()
        runs = []
        for r in self._runs("WHERE lab_id IS NULL")[:30]:
            x = self._summary(r)
            if r["status"] != "ended":
                n = self.now(r)
                x["now"] = {k: n[k] for k in ("phase", "busy", "scan", "next", "today", "size", "period", "engine")}
            runs.append(x)
        return {"ready": self.ready(), "runs": runs,
                "models": [{"id": k, "label": v[0]} for k, v in MODELS.items()], "default_model": c.get("model") or "claude-sonnet-5-5",
                "styles": [{"id": k, **v} for k, v in STYLES.items()], "min_budget": MIN_BUDGET,
                "sessions": {"kr": self._session("kr"), "us": self._session("us")}}

    # ------------------------------------------------------------------ 흐름도(지금 이 플랜이 어디까지 왔는지)
    GATES = (("closed", "장이 열렸나"), ("over", "기간 안인가"), ("time", "매수 시간인가"), ("full", "자리가 있나"), ("dayloss", "오늘 손실 한도 안인가"),
             ("streak", "연속 손절 아닌가"), ("cash", "현금이 있나"), ("turn", "추천 받을 차례인가"), ("aicost", "오늘 AI 비용 한도 안인가"),
             ("market", "지수가 기준보다 강한가"), ("watchwait", "관망 뒤 쉬는 중이 아닌가"), ("nokey", "AI 키가 있나"), ("budget", "이번 달 AI 예산 안인가"))
    FILTERS = (("full", "자리"), ("watch", "관망"), ("auto", "AI가 고른 종목"), ("winprob", "승률"), ("score", "점수"), ("rr", "손익비"), ("fee", "비용"), ("dup", "오늘 이미 거래"),
               ("quote", "시세"), ("chase", "쫓지 않기"), ("qty", "1주 이상"), ("fail", "주문"), ("etc", "기타"))

    def flow(self, rid):
        """이 플랜이 한 바퀴 도는 전 과정(점검 → AI 추천 → 거르기 → 크기 → 주문 → 들고 있기 → 팔기 → 배우기)을
        지금 값·오늘 숫자·현재 위치와 함께. 흐름도 화면용."""
        run = self._run(rid)
        p, mkt, today = run["params"], run["mkt"], AP.mkt_date(run["mkt"])
        N = self.now(run) if run["status"] in ACTIVE else None
        M = (lambda v: f"${v:,.2f}") if mkt == "us" else (lambda v: f"{v:,.0f}원")
        # 1) 점검: 앞에서부터 처음 막힌 곳까지
        if run["status"] == "running":
            ok, code, why = self.gate_code(run)
            code = "turn" if code in ("gap", "cycles") else code
        else:
            ok, code, why = False, "status", {"paused": "새 매수를 멈춰 두었어요", "closing": "정리 중이에요", "ended": "끝난 자동매매예요"}.get(run["status"], "")
        es, ee = self._entry(run, self._local(mkt).date())
        idx = self.index_pct(mkt)
        D = self._due(run)
        cond = {"closed": "그 시장 정규장 시간에만", "over": f"{run['start_date']} ~ {run['end_date']}", "time": f"{es}~{ee}({'한국' if mkt == 'kr' else '뉴욕'} 시간)",
                "full": f"최대 {p['max_positions']}종목", "dayloss": f"하루 -{p['daily_loss_pct']:g}%({M(run['budget'] * p['daily_loss_pct'] / 100)})까지",
                "streak": f"연속 {p['loss_streak']}번 손절이면 그날 쉼", "cash": "남은 현금이 있어야",
                "turn": (f"정밀 하루 {p['cycles_per_day']}번 · {p['cycle_gap_min']}분 간격 + 빠른 재추천 {D['quick']}분마다" if D["quick"]
                         else f"하루 {p['cycles_per_day']}번 · {p['cycle_gap_min']}분 간격"),
                "aicost": f"하루 ${float(p.get('ai_daily_usd') or 1):g}까지", "market": (f"지수 {float(p.get('idx_floor') or 0):g}% 아래면 AI를 안 부름(무료 점검) · 지금 {idx:+.2f}%" if idx is not None else f"지수 {float(p.get('idx_floor') or 0):g}% 아래면 쉼")
                if float(p.get("idx_floor") or 0) < 0 else "모의는 지수와 상관없이 물어요",
                "watchwait": "관망이면 지수가 회복되거나 간격 두 배가 지날 때까지" if (p.get("skip_watch") or not D["quick"]) else "관망이어도 작게 사 보며 계속 물어요",
                "nokey": "설정 → AI", "budget": "설정 → AI의 월 예산"}
        order = [g for g, _ in self.GATES]
        stop = order.index(code) if code in order else (-1 if ok else len(order))
        gates = [{"code": g, "t": t, "d": cond.get(g, ""), "st": "pass" if (ok or i < stop) else "stop" if i == stop else "wait",
                  "why": why if i == stop else ""} for i, (g, t) in enumerate(self.GATES)]
        if code == "status":
            gates.insert(0, {"code": "status", "t": "가동 중인가", "d": "", "st": "stop", "why": why})
        # 2) AI 추천
        rows = self.rt.db.q("SELECT ts, data FROM at_log WHERE run_id=? AND kind='cycle' AND day=? AND data IS NOT NULL ORDER BY id", (rid, today))
        decs, n_full, n_quick, usd = [], 0, 0, 0.0
        for r in rows:
            d = json.loads(r["data"])
            usd += float(d.get("usd") or 0)
            if d.get("pick_id") or d.get("decisions") is not None:
                n_quick += 1 if d.get("lite") else 0
                n_full += 0 if d.get("lite") else 1
            for x in d.get("decisions") or []:
                decs.append(dict(x, ts=r["ts"], lite=bool(d.get("lite"))))
        busy = bool(N and N.get("busy"))
        ai = {"model": run["model"], "model_label": _short_model(run["model"]), "web": bool(p.get("web_search", True)), "quick_gap": D["quick"],
              "quick_model": "Haiku 4.5", "full": n_full, "full_max": p["cycles_per_day"], "quick": n_quick, "usd": round(usd, 3), "cap": float(p.get("ai_daily_usd") or 1),
              "exclude": len(self._seen([rid], mkt)), "price_cap": self._price_cap(run), "next_kind": D.get("next") or D.get("kind") or "full",
              "busy": busy, "engine": {"lock": bool(p.get("engine_lock")), "ceil": p.get("engine_ceil"),
              "ladder": [{"model": m, "label": _short_model(m), "on": m == run["model"]} for m in ENGINE_LADDER]}}
        # 3) 거르기: 오늘 받은 종목이 어디서 걸렸나
        fee = AP.FEE[mkt]
        rule = {"full": f"보유 {p['max_positions']}종목 미만", "auto": "앱이 고른 예비 종목은 사지 않음", "winprob": f"AI 승률 {p['min_win_prob']:g}% 이상",
                "score": f"점수 {p['min_score']:g} 이상", "rr": f"익절÷손절 {p['min_rr']:g} 이상", "fee": f"목표가 비용({fee:g}%)의 3배 이상", "dup": "같은 종목은 하루 한 번",
                "quote": "지금 시세가 있어야", "chase": f"추천가보다 {p['chase_pct']:g}% 넘게 오르면 안 쫓음", "qty": "배정 금액으로 1주 이상" + (" (모의는 현금이 되면 1주)" if run["mode"] == "paper" and p.get("min_one", True) else ""),
                "fail": "주문이 들어가야", "watch": "AI가 관망을 권하면 " + ("사지 않음" if p.get("skip_watch") else f"×{float(p.get('watch_mult') or 0.5):g}로 작게"),
                "etc": "그 밖의 이유"}
        cnt = {}
        for x in decs:
            if x.get("act") == "skip":
                c = skip_code(x)
                cnt[c] = cnt.get(c, 0) + 1
        left, steps = len(decs), []
        for c, t in self.FILTERS:
            n = cnt.get(c, 0)
            steps.append({"code": c, "t": t, "rule": rule[c], "out": n, "in": left})
            left -= n
        funnel = {"picks": len(decs), "buy": sum(1 for x in decs if x.get("act") == "buy"), "steps": steps, "etc": cnt.get("etc", 0),
                  "recent": [{k: x.get(k) for k in ("name", "sym", "act", "why", "win_prob", "score", "lite")} | {"ts": x["ts"][11:16], "code": skip_code(x) if x.get("act") == "skip" else ""}
                             for x in decs[-8:]][::-1]}
        # 4) 크기
        m = self.money(run)
        n_closed = self.rt.db.one("SELECT COUNT(*) n FROM at_trades WHERE run_id=? AND status='closed'", (rid,))["n"]
        warm = n_closed < int(p.get("warmup_trades") or 0)
        size = {"base": round(m["base"], 2), "cash": round(m["cash"], 2), "pct": p["per_trade_pct"], "alloc": round(m["base"] * p["per_trade_pct"] / 100, 2),
                "warm": warm, "warm_mult": float(p.get("warmup_mult") or 1), "warm_left": max(0, int(p.get("warmup_trades") or 0) - n_closed),
                "watch_mult": float(p.get("watch_mult") or 0.5), "skip_watch": bool(p.get("skip_watch")), "min_one": run["mode"] == "paper" and bool(p.get("min_one", True)),
                "exit_mult": float(p.get("exit_mult") or 1), "tp_cap": p["tp_cap"], "sl_cap": p["sl_cap"]}
        # 5) 주문 · 6) 들고 있기
        buys = self.rt.db.one("SELECT COUNT(*) n FROM at_trades WHERE run_id=? AND entry_day=? AND status!='cancelled'", (rid, today))["n"]
        hold = [{"sym": t["sym"], "name": t["name"], "qty": t["qty"], "pct": round(((t["last"] or t["entry_price"]) / t["entry_price"] - 1) * 100, 2),
                 "tp": t["target_pct"], "sl": t["stop_pct"], "trail": t["trail_pct"] or 0, "high": round(((t["high"] or t["entry_price"]) / t["entry_price"] - 1) * 100, 2),
                 "exit": t["exit_by"] if run["style"] == "day" else t["exit_date"], "fill_ok": bool(t["fill_ok"])} for t in self._open(rid)]
        # 7) 팔기: 오늘 판 것
        ex = {}
        for t in self.rt.db.q("SELECT pnl, reason FROM at_trades WHERE run_id=? AND status='closed' AND exit_day=?", (rid, today)):
            e = ex.setdefault(t["reason"] or "기타", {"reason": t["reason"] or "기타", "n": 0, "pnl": 0.0, "win": 0})
            e["n"] += 1
            e["pnl"] += t["pnl"] or 0
            e["win"] += 1 if (t["pnl"] or 0) > 0 else 0
        # 8) 배우기
        since = p.get("engine_since") or run["created_at"]
        est = self.engine_stats([run], since)
        rv_days = len({r["day"] for r in self.rt.db.q("SELECT day FROM at_log WHERE run_id=? AND kind='cycle' AND data IS NOT NULL AND day>?", (rid, run.get("last_review_day") or ""))})
        learn = {"review": {"n": n_closed - (run.get("last_review_n") or 0), "every": int(p.get("review_every") or 30), "days": rv_days, "every_days": int(p.get("review_days") or 5),
                            "auto": bool(p.get("auto_apply")) and run["mode"] == "paper", "ai": bool(p.get("ai_review", True)),
                            "model": _short_model(self.rt.ai.review_model())},
                 "engine": {"n": est["n"], "need": ESC_N, "avg": est["avg"], "win": est["win"], "rule": f"평균 0% 이하 또는 이익 {ESC_WIN}% 미만이면 한 칸 올림", "lock": bool(p.get("engine_lock"))},
                 "pending": self.rt.db.one("SELECT COUNT(*) n FROM at_proposals WHERE run_id=? AND status='pending'", (rid,))["n"]}
        where = ("ai" if busy else "hold" if (not ok and hold and code in ("full",)) else "gate")
        return {"now": N, "run": {"id": rid, "name": run["name"], "mode": run["mode"], "mkt": mkt, "style": run["style"], "status": run["status"], "ver": run["ver"],
                        "budget": run["budget"], "lab": bool(run.get("lab_id"))},
                "where": where, "ok": ok, "code": code, "why": why, "gates": gates, "ai": ai, "funnel": funnel, "size": size,
                "order": {"mode": run["mode"], "today": buys, "slip": "가상 체결(호가 미끄러짐 반영)" if run["mode"] == "paper" else "키움 실주문(체결 확인 후 보유로)"},
                "hold": hold, "exits": sorted(ex.values(), key=lambda e: -e["n"]), "learn": learn,
                "money": {"equity": round(m.get("equity", 0), 2), "cash": round(m["cash"], 2), "ret_pct": m.get("ret_pct")}}

    def detail(self, rid):
        run = self._run(rid)
        opens = self._open(rid)
        for t in opens:
            px = self._quote(t["mkt"], t["sym"]).get("price") or t["last"] or t["entry_price"]
            t["last"] = px
            t["pnl_pct"] = round((px / t["entry_price"] - 1) * 100, 2)
            t["pnl"] = round((px - t["entry_price"]) * t["qty"], 2)
            t["why"] = json.loads(t["why"] or "{}")
            t.pop("feat", None)
        closed = self._closed(rid)
        recent = [dict(r, why=json.loads(r["why"] or "{}"), feat=None) for r in closed[-60:]][::-1]
        st = self.stats(rid, closed)
        log = self.rt.db.q("SELECT id, ts, kind, msg, data FROM at_log WHERE run_id=? ORDER BY id DESC LIMIT 60", (rid,))
        for x in log:
            x["data"] = json.loads(x["data"]) if x["data"] else None
        props = self.rt.db.q("SELECT id FROM at_proposals WHERE run_id=? AND status IN ('pending','applied','rejected','none') ORDER BY id DESC LIMIT 12", (rid,))
        ok, why = (False, "") if run["status"] != "running" else self.gate(run)
        th = self._busy.get(rid)
        s = self._summary(run)
        m = s["money"]  # 보유 평가는 방금 받은 시세로 다시 계산
        m["open_value"] = round(sum(t["qty"] * t["last"] for t in opens), 2)
        m["unrealized"] = round(m["open_value"] - m["open_cost"], 2)
        m["equity"] = round(m["cash"] + m["open_value"], 2)
        m["ret_pct"] = round(m["equity"] / run["budget"] * 100 - 100, 2)
        b = self.real_broker()[0] if run["mode"] == "real" else None
        O = self.opportunity(run)
        return {"run": s, "params": dict({"quick_gap_min": 0}, **run["params"]), "plan": plan_text(run), "open": opens, "closed": recent, "stats": st,
                "equity": self.rt.db.q("SELECT day, equity FROM at_equity WHERE run_id=? ORDER BY day", (rid,)),
                "log": log, "proposals": [self._proposal(x["id"]) for x in props], "changes": self.changes(rid),
                "next": {"ok": ok, "why": why, "busy": bool(th and th.is_alive())},
                "now": self.now(run) if run["status"] != "ended" else None, "ready": self.ready(),
                "opp": dict({k: v for k, v in O.items() if k != "items"}, notes=self.opp_changes(run, O, self._since_ver(run))[1]),
                "broker": getattr(b, "label", None), "session": self._session(run["mkt"]),
                "keys": [[k, v[3]] for k, v in PLAN_KEYS.items()]}      # 순서를 지키려고 목록으로(사전은 JSON에서 이름순으로 바뀐다)

    def export_csv(self, rid):
        """매매 기록 전체를 CSV로(엑셀에서 바로 열리게 BOM 포함)."""
        self._run(rid)
        rows = self.rt.db.q("SELECT * FROM at_trades WHERE run_id=? ORDER BY id", (rid,))
        cols = ["id", "ver", "mkt", "sym", "name", "qty", "status", "entry_ts", "entry_hm", "entry_price", "target_pct", "stop_pct", "trail_pct",
                "exit_ts", "exit_price", "reason", "pnl", "pnl_pct", "fees", "mfe_pct", "mae_pct", "hold_min", "score", "win_prob",
                "cal_prob", "stance", "pick_id", "feat", "why"]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c) for c in cols])
        return ("\ufeff" + buf.getvalue()).encode("utf-8")
