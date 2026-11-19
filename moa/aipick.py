"""AI 종목 추천 + 자동 익절·손절 감시.

흐름
1. 사용자가 시장(국내/미국)과 엔진(모델)을 고르고 '추천 받기'를 누른다.
2. 앱이 그 시점의 실시간 순위·분봉 흐름·호가 잔량·지수·주문 가능 금액을 모아 Claude에게 보낸다.
3. Claude가 '오늘 안에 수익 낼 확률이 높은' 종목을 최대 3개, 목표가·손절가·정리 시각과 함께 고른다.
4. 매수는 사용자가 직접 누른다(AI는 주문하지 않음).
5. 매수할 때 '자동 익절·손절'을 켜 두면, 앱이 정해진 값(사용자가 확인한 값)에 닿을 때 시장가로 판다.
   - 정규장에서만 판다(국내 09:00~15:20, 미국 09:30~16:00 뉴욕 시간).
   - 실제 주문 여부는 기존 계좌 설정(실전 허용 등)을 그대로 따른다.
"""
import json
import re
from datetime import datetime, timedelta, timezone

PICK_SYSTEM = """너는 한국 개인 투자자의 앱(moa)에 들어 있는 당일 매매(데이트레이딩) 종목 선정 분석가야.
목표: 지금 이 시점에 매수해서 '오늘 장 마감 전까지' 수수료·세금을 빼고도 수익으로 끝날 확률과 기대수익이 가장 높은 종목을 고르는 것.

판단 원칙
- 주어진 실시간 데이터(순위, 오늘 분봉 흐름, VWAP 대비 위치, 고점 대비 위치, 최근 30분 추세·거래량, 호가 잔량 비율, 지수, 남은 장 시간)를 근거로 써. 없는 숫자를 지어내지 마.
- 웹 검색이 가능하면 후보의 오늘 뉴스·공시(재료)를 짧게 확인해. 재료가 없거나 이미 소멸한 급등은 감점.
- 피할 것: 상한가 근처(국내 +25% 이상), 거래대금이 작아 체결이 어려운 종목, 이미 고점에서 크게 밀리는 종목(추세 붕괴), 장 마감까지 시간이 너무 적어 목표에 닿기 어려운 경우, 관리·정리매매 종목, 레버리지/인버스 ETF는 정말 근거가 강할 때만.
- 좋은 후보: VWAP 위에서 눌림 후 재상승, 거래량이 붙는 돌파, 강한 재료 + 지수 우호, 호가 매수 우위.
- 비용: 국내 왕복 약 0.2%(세금 포함), 미국 왕복 약 0.25%+환전. 목표 수익률은 비용보다 충분히 커야 해.
- 손익비: 목표(target_pct)는 보통 손절(stop_pct)의 1.5배 이상. 변동성이 큰 종목은 둘 다 넓게, 작은 종목은 좁게.
- 승률(win_prob)은 '정리 시각 전에 손절보다 목표에 먼저 닿을 확률'의 솔직한 추정치(%)야. 과장하지 마. 근거가 약하면 낮게.
- 시장 전체가 나쁘거나 장이 거의 끝나 가면 stance를 '관망'으로 두되, picks는 절대 비우지 마. 그래도 가장 나은 1종목을 반드시 넣고 weight_pct는 5~10으로 작게, 목표·손절은 보수적으로 잡아.

출력 형식(반드시 지켜)
1) 먼저 한국어로 5~8줄 이내 짧은 판단: 첫 줄에 결론, 이어서 '- ' 항목으로 시장 분위기와 고른 이유.
2) 그 다음 마지막에 아래 JSON 하나를 ```json 코드 블록으로. 다른 코드 블록은 쓰지 마.
{"stance":"공격|보통|관망","market_view":"한 줄","picks":[
 {"sym":"종목코드 또는 기호","name":"이름","score":0~100,"win_prob":0~100,
  "entry_type":"limit|market","entry_price":숫자(지정가 추천가, 현재가 근처),
  "target_pct":숫자,"stop_pct":숫자(양수),"trail_pct":숫자 또는 0,
  "exit_by":"HH:MM(한국 시간, 이때까지 안 닿으면 정리)","weight_pct":주문가능금액 중 권장 비중(5~40),
  "reasons":["근거1","근거2","근거3"],"risks":["위험1","위험2"]}]}
picks는 최소 1개·최대 3개, 기대수익(승률×목표 − (1−승률)×손절)이 큰 순서로."""

KR_FEE = 0.2  # 왕복 비용(%) 대략

# 추천 종류(보유 기간). day=오늘 안에 정리, swing=약 1주(5거래일), month=약 1달(20거래일)
HORIZONS = {
    "day": {"label": "당일", "days": 0, "tp_max": 30, "sl_max": 15, "min": 8, "step": 15, "gap": 0.5},
    "swing": {"label": "1주", "days": 5, "tp_max": 40, "sl_max": 15, "min": 6, "step": 8, "gap": 1.5},
    "month": {"label": "1달", "days": 20, "tp_max": 60, "sl_max": 20, "min": 5, "step": 6, "gap": 2.5},
}
CATS = ("정책", "실적", "수급", "이벤트", "업황", "기술적")


def hz(h):
    return h if h in HORIZONS else "day"


SWING_SYSTEM = """너는 한국 개인 투자자의 앱(moa)에 들어 있는 중기(스윙) 종목 선정 분석가야.
목표: 지금 매수해서 앞으로 약 <DAYS>거래일(<LABEL>) 동안 들고 갔을 때, 수수료·세금을 빼고도 수익으로 끝날 확률과 기대수익이 가장 높은 종목을 고르는 것. 몇 분~몇 시간짜리 단타가 아니야.

판단 순서
1) 재료 찾기: 웹 검색이 가능하면 '앞으로 <LABEL> 안에 주가를 움직일 재료'부터 찾아. 정책·예산·법안, 정부 발표 예정, 실적 발표, 수주·공급 계약, 신제품, 날짜가 정해진 이벤트, 업황 지표, 외국인·기관 수급. 이미 다 알려진 뉴스보다 '이제 막 논의가 시작돼 아직 가격에 덜 반영된 재료'를 우선해.
2) 수혜 종목 찾기: 재료마다 실제로 매출·이익이 이어지는 종목을 골라. 후보_상세에 없는 종목도 추천할 수 있어(국내는 6자리 종목코드를 정확히. 코드가 확실하지 않으면 name을 정확한 종목명으로 적어). 이름만 비슷하거나 실체 없이 편승한 종목은 빼.
3) 가격 위치 확인: 후보_상세의 일봉 특징(최근 5·20·60일 등락, 20·60일선 대비, 60일 고점 대비, 5일 거래량 배수, 하루 변동폭)으로 이미 얼마나 올랐는지 봐. 며칠 연속 급등해 재료가 가격에 다 반영된 종목, 상한가 직후 종목은 감점하거나 눌림 지정가로 잡아.
4) 재료의 수명: 발표 한 번으로 끝나는 재료인지, <LABEL> 동안 후속 뉴스(예산 확정·시행·실적 확인)가 이어질 재료인지 구분해. 정부가 번복·부인할 수 있는 재료는 risks에 꼭 적어.

원칙
- 주어진 숫자와 검색으로 확인한 사실만 근거로 써. 없는 숫자·뉴스를 지어내지 마.
- 비용: 국내 왕복 약 0.2%(세금 포함), 미국 왕복 약 0.25%+환전.
- 목표·손절은 하루 변동폭(ATR%)과 보유 기간에 맞춰. 목표(target_pct)는 보통 손절(stop_pct)의 1.8배 이상. 하루 변동폭보다 좁은 손절은 잡음에 걸려.
- win_prob는 '보유 기간 안에 손절보다 목표에 먼저 닿을 확률'의 솔직한 추정치(%)야. 테마주·급등주는 낮게.
- 가능하면 한 테마에 몰지 말고 성격이 다른 종목으로 나눠.
- 시장이 나쁘면 stance를 '관망'으로 두되 picks는 절대 비우지 마. 가장 나은 1종목을 weight_pct 5~10으로 넣어.

출력 형식(반드시 지켜)
1) 먼저 한국어로 6~10줄 이내 짧은 판단: 첫 줄에 결론, 이어서 '- ' 항목으로 찾은 재료, 시장 국면, 고른 이유.
2) 그 다음 마지막에 아래 JSON 하나를 ```json 코드 블록으로. 다른 코드 블록은 쓰지 마.
{"stance":"공격|보통|관망","market_view":"한 줄","picks":[
 {"sym":"종목코드 또는 기호","name":"이름","score":0~100,"win_prob":0~100,
  "entry_type":"limit|market","entry_price":숫자(지정가 추천가),
  "target_pct":숫자,"stop_pct":숫자(양수),"trail_pct":숫자 또는 0,
  "hold_days":예상 보유 거래일(1~<DAYS>),"weight_pct":주문가능금액 중 권장 비중(5~30),
  "theme":"테마 이름(짧게)","catalyst":"재료 한 줄","catalyst_type":"정책|실적|수급|이벤트|업황|기술적",
  "catalyst_date":"재료가 확인될 날짜나 시기(모르면 빈 문자열)",
  "reasons":["근거1","근거2","근거3"],"risks":["위험1","위험2"]}]}
picks는 최소 1개·최대 3개, 기대수익(승률×목표 − (1−승률)×손절)이 큰 순서로."""


def system_prompt(h):
    h = hz(h)
    if h == "day":
        return PICK_SYSTEM
    return SWING_SYSTEM.replace("<DAYS>", str(HORIZONS[h]["days"])).replace("<LABEL>", HORIZONS[h]["label"])


def kst_now():
    return datetime.now(timezone.utc) + timedelta(hours=9)


def _us_dst(utc):
    """미국 동부 서머타임 여부(3월 둘째 일요일 2시 ~ 11월 첫째 일요일 2시)."""
    y = utc.year

    def nth_sunday(month, n):
        d = datetime(y, month, 1, tzinfo=timezone.utc)
        d += timedelta(days=(6 - d.weekday()) % 7)
        return d + timedelta(days=7 * (n - 1))
    start = nth_sunday(3, 2) + timedelta(hours=7)   # 02:00 EST = 07:00 UTC
    end = nth_sunday(11, 1) + timedelta(hours=6)    # 02:00 EDT = 06:00 UTC
    return start <= utc < end


def et_now():
    u = datetime.now(timezone.utc)
    return u + timedelta(hours=-4 if _us_dst(u) else -5)


KR_LATE = {}   # 날짜별 덮어쓰기: {"YYYY-MM-DD": (개장 늦춤 분, 마감 늦춤 분)}. 규칙과 다른 해가 생기면 여기에 넣는다


def suneung(year):
    """수능일: 11월 셋째 주(일요일 시작) 목요일. 2021~2026년 실제 날짜와 맞는다."""
    from datetime import date
    nov1 = date(year, 11, 1)
    return nov1 - timedelta(days=(nov1.weekday() + 1) % 7) + timedelta(days=18)


def kr_shift(d):
    """그날 국내 정규장이 평소보다 늦는 분: (개장, 마감). 수능일은 (60, 60), 새해 첫 거래일은 (60, 0), 보통 날은 (0, 0)."""
    from datetime import date
    if isinstance(d, datetime):
        d = d.date()
    if d.isoformat() in KR_LATE:
        return tuple(KR_LATE[d.isoformat()])
    if d == suneung(d.year):
        return 60, 60
    if d.month == 1 and d.day <= 4:
        first = date(d.year, 1, 2)
        while first.weekday() >= 5:
            first += timedelta(days=1)
        if d == first:
            return 60, 0
    return 0, 0


def kr_note(d):
    so, sc = kr_shift(d)
    if not so and not sc:
        return ""
    f = lambda m: f"{m // 60:02d}:{m % 60:02d}"
    return f"오늘은 {'수능일' if sc else '새해 첫 거래일'}이라 정규장이 {f(540 + so)}~{f(930 + sc)}예요(평소 09:00~15:30)"


def hm_add(hm, minutes):
    h, m = map(int, hm.split(":"))
    t = min(23 * 60 + 59, max(0, h * 60 + m + minutes))
    return f"{t // 60:02d}:{t % 60:02d}"


def session(mkt):
    """정규장 여부와 남은 분(서버 기준). 휴장일 표는 화면 쪽에 있고, 여기선 주말·시간과 늦게 여는 날(수능일 등)만 본다."""
    if mkt == "us":
        n = et_now()
        mins = n.hour * 60 + n.minute
        open_ = n.weekday() < 5 and 570 <= mins < 960
        return {"open": open_, "left_min": 960 - mins if open_ else 0, "local": n.strftime("%Y-%m-%d %H:%M (뉴욕)"),
                "kst": kst_now().strftime("%Y-%m-%d %H:%M"), "close_kst": (kst_now() + timedelta(minutes=960 - mins)).strftime("%H:%M")}
    n = kst_now()
    mins = n.hour * 60 + n.minute
    so, sc = kr_shift(n)
    o, c = 540 + so, 920 + sc
    open_ = n.weekday() < 5 and o <= mins < c
    out = {"open": open_, "left_min": c - mins if open_ else 0, "local": n.strftime("%Y-%m-%d %H:%M"),
           "kst": n.strftime("%Y-%m-%d %H:%M"), "close_kst": f"{c // 60:02d}:{c % 60:02d}"}
    if so or sc:
        out["note"] = kr_note(n)
    return out


def bar_features(bars):
    """오늘 5분봉에서 단타에 쓰는 특징값."""
    if not bars:
        return None
    day = bars[-1]["t"][:5]
    tb = [b for b in bars if b["t"][:5] == day] or bars[-30:]
    o, c = tb[0]["o"] or tb[0]["c"], tb[-1]["c"]
    hi, lo = max(b["h"] for b in tb), min(b["l"] for b in tb)
    vol = sum(b["v"] for b in tb)
    vwap = sum((b["h"] + b["l"] + b["c"]) / 3 * b["v"] for b in tb) / vol if vol else c
    last6, prev = tb[-6:], tb[:-6]
    v6 = sum(b["v"] for b in last6) / max(1, len(last6))
    vp = sum(b["v"] for b in prev) / len(prev) if prev else v6
    r = lambda a, b: round((a / b - 1) * 100, 2) if b else None
    return {"5분봉_개수": len(tb), "시가대비%": r(c, o), "VWAP대비%": r(c, vwap), "고점대비%": r(c, hi), "저점대비%": r(c, lo),
            "최근30분%": r(c, last6[0]["o"] or last6[0]["c"]), "최근30분_거래량배수": round(v6 / vp, 2) if vp else None,
            "VWAP": round(vwap, 2), "고가": hi, "저가": lo}


def daily_features(bars):
    """일봉에서 중기(1주·1달) 추천에 쓰는 특징값."""
    if not bars or len(bars) < 6:
        return None
    c = [b["c"] for b in bars]
    last = c[-1]
    r = lambda n: round((last / c[-1 - n] - 1) * 100, 2) if len(c) > n and c[-1 - n] else None
    ma = lambda n: sum(c[-n:]) / n if len(c) >= n else None
    gap = lambda m: round((last / m - 1) * 100, 2) if m else None
    w = bars[-60:]
    hi, lo = max(b["h"] for b in w), min(b["l"] for b in w)
    v5 = sum(b["v"] for b in bars[-5:]) / 5
    pv = bars[-25:-5]
    v20 = sum(b["v"] for b in pv) / len(pv) if pv else 0
    trs = []
    for i in range(max(1, len(bars) - 14), len(bars)):
        pc, b = bars[i - 1]["c"], bars[i]
        if pc:
            trs.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)) / pc * 100)
    streak = 0
    for i in range(len(c) - 1, 0, -1):
        if c[i] > c[i - 1]:
            streak += 1
        else:
            break
    return {"일봉_개수": len(bars), "5일%": r(5), "20일%": r(20), "60일%": r(60), "20일선대비%": gap(ma(20)), "60일선대비%": gap(ma(60)),
            "60일고점대비%": gap(hi), "60일저점대비%": gap(lo), "5일거래량배수": round(v5 / v20, 2) if v20 else None,
            "하루변동폭%": round(sum(trs) / len(trs), 2) if trs else None, "연속상승일": streak}


def phase_at(mkt, ts):
    """한국 시간(naive) ts가 그 시장 날짜의 장 전(pre)·장 중(open)·장 뒤(post) 중 어디인지와 그 시장의 날짜."""
    if mkt == "us":
        u = ts - timedelta(hours=9)
        loc = u + timedelta(hours=-4 if _us_dst(u.replace(tzinfo=timezone.utc)) else -5)
        o, c = 570, 960
    else:
        so, sc = kr_shift(ts)
        loc, o, c = ts, 540 + so, 930 + sc
    if loc.weekday() >= 5:
        return "post", loc.date()
    mins = loc.hour * 60 + loc.minute
    return ("pre" if mins < o else "open" if mins < c else "post"), loc.date()


def add_weekdays(d, n):
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def swing_end(mkt, ts, days):
    """중기 추천을 평가할 마지막 시각(한국 시간)과 그 시장의 마지막 날짜. 장 전에 받았으면 그날이 1일째."""
    ph, d0 = phase_at(mkt, ts)
    end_d = add_weekdays(d0, days - 1 if ph == "pre" else days)
    if mkt == "us":
        return datetime(end_d.year, end_d.month, end_d.day, 16, 10) + timedelta(hours=14), end_d
    return datetime(end_d.year, end_d.month, end_d.day, 15, 40), end_d


def mkt_date(mkt):
    return (et_now() if mkt == "us" else kst_now()).strftime("%Y-%m-%d")


def _ck(mkt, h):
    return mkt if hz(h) == "day" else f"{mkt}:{h}"


def _col(db, table, cols):
    have = {r["name"] for r in db.q(f"PRAGMA table_info({table})")}
    for name, typ in cols:
        if name not in have:
            db.x(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")


class AIPick:
    def __init__(self, rt):
        self.rt = rt
        rt.db.x("""CREATE TABLE IF NOT EXISTS ai_picks(
            id INTEGER PRIMARY KEY, ts TEXT NOT NULL, mkt TEXT NOT NULL, model TEXT NOT NULL, usd REAL,
            stance TEXT, view TEXT, picks TEXT NOT NULL, text TEXT)""")
        rt.db.x("""CREATE TABLE IF NOT EXISTS ai_guards(
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, mkt TEXT NOT NULL, engine TEXT NOT NULL,
            sym TEXT NOT NULL, name TEXT, exch TEXT, qty INTEGER NOT NULL, entry REAL NOT NULL,
            target_pct REAL NOT NULL, stop_pct REAL NOT NULL, trail_pct REAL NOT NULL DEFAULT 0,
            exit_by TEXT, status TEXT NOT NULL DEFAULT 'watching', base REAL, high REAL, last REAL,
            exit_reason TEXT, exit_price REAL, closed_at TEXT, pick_id INTEGER, msg TEXT)""")
        _col(rt.db, "ai_picks", (("horizon", "TEXT"), ("theme", "TEXT"), ("lv", "INTEGER")))
        _col(rt.db, "ai_guards", (("horizon", "TEXT"), ("exit_date", "TEXT")))
        self._last = 0.0
        self._cands = {}
        self._sess = {}
        self._idx = {}

    # ---------- 추천 자료 ----------
    def context(self, d):
        h = hz(d.get("horizon"))
        if h != "day":
            return self.context_swing(d, h)
        rt, inv = self.rt, self.rt.invest
        mkt = "us" if d.get("mkt") == "us" else "kr"
        if inv.kr() is None:
            raise ValueError("실시간 순위가 필요해서 키움 계좌 연결(설정 → 계좌 연결)이 있어야 추천할 수 있어요")
        ss = session(mkt)
        kinds = ("value", "up", "volume") if mkt == "kr" else ("up", "volume")
        cand, seen = [], set()
        rank_dump = {}
        for k in kinds:
            try:
                r = inv.ranking(mkt, k)
            except Exception as e:
                rank_dump[k] = f"조회 실패: {e}"
                continue
            rank_dump[k] = [{x: it.get(x) for x in ("rank", "sym", "name", "price", "change_pct", "value", "volume")}
                            for it in r["items"][:20]]
            for it in r["items"][:20]:
                if it["sym"] in seen or not it.get("price"):
                    continue
                cp = it.get("change_pct") or 0
                if mkt == "kr" and (cp >= 25 or it["price"] < 1000):
                    continue
                if mkt == "us" and it["price"] < 2:
                    continue
                seen.add(it["sym"])
                cand.append(it)
        # 거래대금·등락 고르게: 앞쪽 12개만 깊게 본다
        deep = []
        for it in cand[:12]:
            row = {"sym": it["sym"], "name": it["name"], "현재가": it["price"], "등락률%": it.get("change_pct")}
            try:
                row["오늘_5분봉"] = bar_features(inv.candles(mkt, it["sym"], "m5").get("bars"))
            except Exception as e:
                row["오늘_5분봉"] = f"없음({e})"
            if mkt == "kr" and len(deep) < 8:
                try:
                    b = inv.orderbook(mkt, it["sym"])
                    row["호가_매수잔량/매도잔량"] = round(b["tot_bid"] / b["tot_ask"], 2) if b.get("tot_ask") else None
                except Exception:
                    pass
            sc = self.learn_score(_feat_of(row, ss), "day", mkt)
            if sc is not None:
                row["과거성적_가산점"] = sc
            deep.append(row)
        if not deep:
            raise ValueError("지금은 순위 자료가 비어 있어요(장 시작 전일 수 있어요)")
        self._cands[mkt] = deep
        self._sess[mkt] = ss
        try:
            idx = rt.market.kr_indices() if mkt == "kr" else rt.market.us_indices()
            idx = [{k: i.get(k) for k in ("name", "price", "change_pct")} for i in idx]
        except Exception:
            idx = []
        cash, acct = self._cash(mkt)
        data = {"시장": "국내(KRX)" if mkt == "kr" else "미국", "기준시각(한국)": ss["kst"], "현지시각": ss["local"],
                "정규장_진행중": ss["open"], "정규장_남은분": ss["left_min"], "정규장_마감(한국시간)": ss["close_kst"],
                "지수": idx, "주문가능금액": cash, "통화": "원" if mkt == "kr" else "달러", "계좌(참고)": acct,
                "후보_상세(오늘 흐름)": deep, "실시간 순위": rank_dump}
        ask = (f"지금({ss['kst']} 한국시간) {'국내' if mkt == 'kr' else '미국'} 시장에서 바로 매수해 오늘 안에 수익으로 끝낼 "
               "확률이 가장 높은 종목을 골라 줘. 후보_상세를 중심으로 판단하고, 필요하면 오늘 뉴스·공시를 확인해.")
        try:
            lp = self.learn_prompt(mkt)
        except Exception:
            lp = None
        if lp:
            data["내 과거 추천 성적(자체 추적·학습 자료)"] = lp
            ask += (" 그리고 '내 과거 추천 성적'을 반드시 반영해: 성적이 나빴던 조건의 종목은 피하거나 비중을 줄이고, 잘 통한 조건을 우선하고, "
                    "win_prob는 과거 실제 익절률에 맞춰 보정하고, 목표·손절 폭은 실제 최대상승·최대하락 중간값을 참고해. 정해 둔 규칙이 있으면 따르되, 오늘 자료와 어긋나면 이유를 말해.")
        if not ss["open"]:
            ask += " (지금은 정규장이 아니야. 다음 정규장 시작 직후 기준으로 판단하고, 그 점을 첫 줄에 밝혀.)"
        return f"AI 종목 추천({'국내' if mkt == 'kr' else '미국'})", ask, data

    def _cash(self, mkt):
        """주문 가능 금액과, 그 값이 어디서 나왔는지 AI가 오해하지 않게 붙이는 계좌 요약."""
        try:
            snap = self.rt.account.snapshot() if self.rt.account.configured() else None
        except Exception:
            snap = None
        if not snap or snap.get("total") is None:
            return None, None
        us = snap.get("us") or {}
        cash = snap.get("orderable") if mkt == "kr" else us.get("usd")
        cd = snap.get("cash_detail") or {}
        acct = {"총자산(원)": snap.get("total"), "주식 평가(원)": snap.get("eval"), "원화 예수금": snap.get("cash"),
                "원화 주문가능": snap.get("orderable"), "주문·미수에 묶인 증거금(원)": cd.get("margin"),
                "달러 예수금": us.get("usd"), "달러 주문가능": us.get("usd_orderable"),
                "설명": "주문가능금액은 그 시장 통화로 지금 바로 살 수 있는 돈이야. 총자산보다 훨씬 작으면 돈이 주식·다른 통화·미체결 주문에 들어가 있는 것이니, 살 수 없다고 단정하지 말고 비중만 그 금액에 맞춰."}
        return cash, {k: v for k, v in acct.items() if v is not None}

    # ---------- 중기(1주·1달) 추천 자료 ----------
    def index_state(self, mkt, sym=""):
        """지수의 일봉 추세(시장 국면)와 지금 값. 국내는 종목이 코스닥이면 코스닥 지수."""
        ysym, name = ("^GSPC", "S&P 500") if mkt == "us" else \
            (("^KQ11", "코스닥") if sym and self.rt.market.kr_market(sym) == "코스닥" else ("^KS11", "코스피"))
        try:
            bars = self.rt.invest.candles("us", ysym, "d").get("bars") or []
            f = daily_features(bars) or {}
            return {"sym": ysym, "name": name, "price": bars[-1]["c"] if bars else None, "5일%": f.get("5일%"),
                    "20일%": f.get("20일%"), "20일선대비%": f.get("20일선대비%")}
        except Exception:
            return {"sym": ysym, "name": name, "price": None}

    def context_swing(self, d, h):
        rt, inv = self.rt, self.rt.invest
        mkt = "us" if d.get("mkt") == "us" else "kr"
        H = HORIZONS[h]
        if inv.kr() is None:
            raise ValueError("실시간 순위와 일봉이 필요해서 키움 계좌 연결(설정 → 계좌 연결)이 있어야 추천할 수 있어요")
        ss = session(mkt)
        theme = str(d.get("theme") or "").strip()[:60]
        take = {"value": 7, "up": 7, "volume": 4}
        src_name = {"value": "거래대금 상위", "up": "상승률 상위", "volume": "거래량 상위"}
        cand, seen, rank_dump = [], set(), {}
        for k in (("value", "up", "volume") if mkt == "kr" else ("up", "volume")):
            try:
                r = inv.ranking(mkt, k)
            except Exception as e:
                rank_dump[k] = f"조회 실패: {e}"
                continue
            rank_dump[k] = [{x: it.get(x) for x in ("rank", "sym", "name", "price", "change_pct", "value")} for it in r["items"][:15]]
            n = 0
            for it in r["items"][:25]:
                if n >= take[k]:
                    break
                if it["sym"] in seen or not it.get("price"):
                    continue
                if (mkt == "kr" and (it["price"] < 1000 or (it.get("change_pct") or 0) >= 29.5)) or (mkt == "us" and it["price"] < 2):
                    continue
                seen.add(it["sym"])
                cand.append(dict(it, src=src_name[k]))
                n += 1
        try:  # 내 관심종목도 후보에
            for sym in (rt.market.watch().get(mkt) or [])[:5]:
                if sym not in seen:
                    q = self.quote(mkt, sym)
                    if q.get("price"):
                        seen.add(sym)
                        cand.append({"sym": sym, "name": q.get("name") or sym, "price": q["price"], "change_pct": q.get("change_pct"), "src": "관심종목"})
        except Exception:
            pass
        ix = self.index_state(mkt)
        self._idx[mkt] = ix
        deep = []
        for it in cand[:20]:
            row = {"sym": it["sym"], "name": it["name"], "현재가": it["price"], "등락률%": it.get("change_pct"), "출처": it["src"]}
            try:
                row["일봉"] = daily_features(inv.candles(mkt, it["sym"], "d").get("bars"))
            except Exception as e:
                row["일봉"] = f"없음({e})"
            sc = self.learn_score(_feat_swing(row, {}, ss, ix, True), h, mkt)
            if sc is not None:
                row["과거성적_가산점"] = sc
            deep.append(row)
        if not deep:
            raise ValueError("지금은 순위 자료가 비어 있어요. 잠시 뒤 다시 받아 보세요")
        self._cands[_ck(mkt, h)] = deep
        self._sess[mkt] = ss
        try:
            idx = rt.market.kr_indices() if mkt == "kr" else rt.market.us_indices()
            idx = [{k: i.get(k) for k in ("name", "price", "change_pct")} for i in idx]
        except Exception:
            idx = []
        cash, acct = self._cash(mkt)
        where = "국내" if mkt == "kr" else "미국"
        data = {"시장": "국내(KRX)" if mkt == "kr" else "미국", "기준시각(한국)": ss["kst"], "현지시각": ss["local"],
                "보유 기간": f"약 {H['days']}거래일({H['label']})", "정규장_진행중": ss["open"], "지수": idx, "지수_추세(일봉)": ix,
                "주문가능금액": cash, "통화": "원" if mkt == "kr" else "달러", "계좌(참고)": acct,
                "후보_상세(일봉 흐름)": deep, "실시간 순위": rank_dump}
        ask = (f"지금({ss['kst']} 한국시간) {where} 시장에서 매수해 앞으로 약 {H['days']}거래일({H['label']}) 들고 갈 종목을 골라 줘. "
               f"먼저 앞으로 {H['label']} 안에 주가를 움직일 재료(정책·예산·실적·수주·일정이 정해진 이벤트·수급)를 찾고, 그 수혜 종목을 골라. "
               "후보_상세는 지금 돈이 몰리는 종목일 뿐이니, 재료가 더 좋은 종목이 있으면 후보 밖에서 골라도 돼. "
               "이미 며칠 급등해 재료가 가격에 반영된 종목은 감점해.")
        if theme:
            data["사용자가 궁금해하는 테마"] = theme
            ask += (f" 사용자가 '{theme}' 테마를 궁금해해. 이 테마의 재료가 지금도 유효한지(이미 반영됐는지, 번복·부인 위험은 없는지) 먼저 판단하고, "
                    "관련 종목을 찾아 평가해. 살 만하면 picks에 넣고, 아니면 판단 글에 이유를 분명히 적어.")
        try:
            lp = self.learn_prompt(mkt, h)
        except Exception:
            lp = None
        if lp:
            data["내 과거 추천 성적(자체 추적·학습 자료)"] = lp
            ask += (" 그리고 '내 과거 추천 성적'을 반드시 반영해: 성적이 나빴던 조건·재료 유형은 피하거나 비중을 줄이고, 잘 통한 조건을 우선하고, "
                    "win_prob는 과거 실제 익절률에 맞추고, 목표·손절·보유일은 '자동 조정값'을 기준으로 잡아. 정해 둔 규칙이 있으면 따르되, 지금 자료와 어긋나면 이유를 말해.")
        return f"AI {H['label']} 추천({where})", ask, data

    def estimate(self, mkt, h="day"):
        """모델별 예상 비용: 지난 실제 사용 평균이 있으면 그걸, 없으면 토큰 추정."""
        from .ai import MODELS, SEARCH_USD, FX_DEFAULT
        fx = FX_DEFAULT
        try:
            fx = float(self.rt.market.yh_quote("USDKRW=X")["price"]) or FX_DEFAULT
        except Exception:
            pass
        out = []
        web = self.rt.ai.conf().get("web_search", True)
        for mid, (label, pi, po) in MODELS.items():
            m = self.rt.ai.meter("pick" if hz(h) == "day" else "pickw", mid, web)  # 앱 공통 비용 계산과 같은 값
            out.append({"id": mid, "label": label, "usd_lo": m["usd_lo"], "usd_hi": m["usd_hi"],
                        "krw_lo": int(m["usd_lo"] * fx), "krw_hi": int(m["usd_hi"] * fx),
                        "avg_krw": int(m["avg_usd"] * fx) if m["avg_usd"] is not None else None, "n": m["n"]})
        c = self.rt.ai.conf()
        return {"models": out, "fx": fx, "default": c.get("model") or "claude-sonnet-5-5",
                "configured": self.rt.ai.configured(), "web_search": c.get("web_search", True),
                "session": session(mkt), "kiwoom": self.rt.invest.kr() is not None, "horizon": hz(h),
                "horizons": [{"id": k, "label": v["label"], "days": v["days"]} for k, v in HORIZONS.items()]}

    # ---------- 결과 정리 ----------
    def _resolve(self, mkt, sym, name):
        """AI가 준 종목을 실제 종목으로 맞춘다. 코드가 틀렸으면 이름으로 다시 찾는다. 반환: (종목코드, 시세)"""
        sym = str(sym or "").strip().upper()
        if mkt == "kr":
            sym = re.sub(r"\D", "", sym)[-6:]
        q = self.quote(mkt, sym) if sym else {}
        if mkt == "kr" and name and (not q.get("price") or len(sym) != 6):
            try:
                hits = self.rt.market.kr_search(str(name).strip())
                hit = next((x for x in hits if x["name"] == str(name).strip()), hits[0] if hits else None)
                if hit:
                    q2 = self.quote(mkt, hit["sym"])
                    if q2.get("price"):
                        return hit["sym"], q2
            except Exception:
                pass
        return sym, q

    def parse(self, text, mkt, model, usd, cut=False, horizon="day", theme=""):
        h = hz(horizon)
        H = HORIZONS[h]
        swing = h != "day"
        m = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or re.findall(r"(\{\s*\"stance\".*\})", text, re.S)
        j, note = None, ""
        if m:
            try:
                j = json.loads(m[-1])
            except ValueError:
                j = None
        if j is None:
            why = "AI 답이 길어 중간에 끊겼어요" if cut else "AI 답이 비어 있었어요" if not text.strip() else "AI 답에서 추천표를 읽지 못했어요"
            j, note = {"stance": "관망", "market_view": "", "picks": []}, f"{why}. 앱이 {'일봉' if swing else '실시간'} 지표로 골랐어요"
            self.rt.db.log("system", "info", f"AI 추천 원문(끝 400자): {text[-400:]}", datetime.now().isoformat(timespec="seconds"))
        ss = self._sess.get(mkt) or session(mkt)
        cands = self._cands.get(_ck(mkt, h)) or []
        end_d = swing_end(mkt, kst_now().replace(tzinfo=None), H["days"])[1].isoformat() if swing else ""
        picks = []
        for p in (j.get("picks") or [])[:3]:
            try:
                sym, q = self._resolve(mkt, p.get("sym"), p.get("name")) if swing else (None, None)
                if not swing:
                    sym = str(p.get("sym") or "").strip().upper()
                    if mkt == "kr":
                        sym = re.sub(r"\D", "", sym)[-6:]
                if not sym:
                    continue
                t, s_ = abs(float(p.get("target_pct") or 0)), abs(float(p.get("stop_pct") or 0))
                if not t or not s_:
                    continue
                if q is None:
                    q = self.quote(mkt, sym)
                if swing and not q.get("price"):
                    continue  # 실제로 없는 종목(코드·이름 모두 못 찾음)
                cand = next((c for c in cands if c["sym"] == sym), None)
                wp = int(float(p.get("win_prob") or 0))
                try:
                    cal, cal_n = self.calibrate(mkt, wp, h)
                except Exception:
                    cal, cal_n = None, 0
                x = {"sym": sym, "name": (q.get("name") if swing else None) or p.get("name") or q.get("name") or sym, "price": q.get("price"),
                     "change_pct": q.get("change_pct"), "exch": q.get("exch"), "cal_prob": cal, "cal_n": cal_n,
                     "score": int(float(p.get("score") or 0)), "win_prob": wp,
                     "entry_type": "market" if p.get("entry_type") == "market" else "limit",
                     "entry_price": float(p.get("entry_price") or q.get("price") or 0),
                     "target_pct": round(min(t, H["tp_max"]), 2), "stop_pct": round(min(s_, H["sl_max"]), 2),
                     "trail_pct": round(abs(float(p.get("trail_pct") or 0)), 2),
                     "weight_pct": float(p.get("weight_pct") or 10),
                     "reasons": [str(r) for r in (p.get("reasons") or [])][:4],
                     "risks": [str(r) for r in (p.get("risks") or [])][:3]}
                if swing:
                    row = cand
                    if row is None:  # 후보 밖(뉴스에서 찾은 종목): 일봉을 받아 특징을 붙인다
                        row = {"sym": sym, "등락률%": q.get("change_pct")}
                        try:
                            row["일봉"] = daily_features(self.rt.invest.candles(mkt, sym, "d").get("bars"))
                        except Exception:
                            row["일봉"] = None
                    ix = self.index_state(mkt, sym) if mkt == "kr" else (self._idx.get(mkt) or self.index_state(mkt))
                    cat = str(p.get("catalyst_type") or "").strip()
                    x.update(horizon=h, hold_days=int(min(max(float(p.get("hold_days") or H["days"]), 1), H["days"])), end_date=end_d,
                             theme=str(p.get("theme") or "")[:20], catalyst=str(p.get("catalyst") or "")[:120],
                             catalyst_type=cat if cat in CATS else "기술적", catalyst_date=str(p.get("catalyst_date") or "")[:30],
                             in_cand=cand is not None, idx0=ix.get("price"), idx_sym=ix.get("sym"), daily=row.get("일봉") if isinstance(row.get("일봉"), dict) else None)
                    x["feat"] = _feat_swing(row, x, ss, ix, cand is not None)
                else:
                    x.update(exit_by=str(p.get("exit_by") or "")[:5], feat=_feat_of(cand or {}, ss))
                picks.append(x)
            except Exception:
                continue
        if not picks:  # 관망이어도 최소 1종목: 지표로 가장 나은 후보를 고른다
            fb = self.fallback(mkt, h)
            if fb:
                picks.append(fb)
                note = note or f"AI가 종목을 비워 와서 앱이 {'일봉' if swing else '실시간'} 지표로 1종목을 골랐어요"
            else:
                note = note or ("조건에 맞는 후보가 없었어요" if swing else "조건에 맞는 후보가 없었어요(등락 −3%~+15% 사이, 오늘 분봉 자료가 있는 종목)")
        body = text.split("```json")[0].strip()
        ls = self.lessons(h) or {}
        lv = int(ls.get("ver") or 0) if ls.get("text") else 0  # 어떤 규칙 판을 읽고 낸 추천인지(판별 성적 비교용)
        pid = self.rt.db.x("INSERT INTO ai_picks(ts,mkt,model,usd,stance,view,picks,text,horizon,theme,lv) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (datetime.now().isoformat(timespec="seconds"), mkt, model, usd, j.get("stance"),
                            j.get("market_view"), json.dumps(picks, ensure_ascii=False), body, h, str(theme or "")[:60], lv))
        return {"id": pid, "stance": j.get("stance"), "view": j.get("market_view"), "picks": picks, "mkt": mkt, "note": note,
                "horizon": h, "label": H["label"], "theme": str(theme or "")[:60]}

    def fallback(self, mkt, h="day"):
        h = hz(h)
        if h != "day":
            return self.fallback_swing(mkt, h)
        best, bs = None, -1e9
        ss = self._sess.get(mkt) or session(mkt)
        for c in self._cands.get(mkt) or []:
            f = c.get("오늘_5분봉")
            if not isinstance(f, dict):
                f = {}
            cp = c.get("등락률%") or 0
            if cp < -5 or cp > 20:
                continue
            sc = (f.get("VWAP대비%") or 0) * 2 + (f.get("최근30분%") or 0) * 1.5 + (f.get("고점대비%") or 0) \
                + min(2.0, f.get("최근30분_거래량배수") or 0) + (c.get("호가_매수잔량/매도잔량") or 1) - 1 \
                - abs(cp - 4) * 0.15  # 너무 급등·급락한 종목보다 +4% 안팎을 선호
            sc += (self.learn_score(_feat_of(c, ss), "day", mkt) or 0) * 0.5  # 과거에 잘 통한 조건이면 가산
            if sc > bs:
                best, bs = c, sc
        if not best:
            return None
        q = self.quote(mkt, best["sym"])
        price = q.get("price") or best.get("현재가")
        return {"sym": best["sym"], "name": best.get("name") or best["sym"], "price": price, "change_pct": q.get("change_pct", best.get("등락률%")),
                "exch": q.get("exch"), "score": 40, "win_prob": 45, "entry_type": "limit", "entry_price": price,
                "target_pct": 1.8, "stop_pct": 1.0, "trail_pct": 0.8, "exit_by": "15:00" if mkt == "kr" else "",
                "weight_pct": 5, "reasons": ["AI는 관망을 권했지만, 실시간 지표(VWAP 위치·최근 30분 흐름·거래량·호가)로 가장 나은 후보를 골랐어요",
                                             "비중 5%, 목표·손절을 좁게 잡은 보수적 진입이에요"],
                "risks": ["시장 분위기가 약해 승률이 낮아요"], "auto": True,
                "feat": _feat_of(best, ss), "cal_prob": None, "cal_n": 0}

    def fallback_swing(self, mkt, h):
        """AI가 종목을 못 줬을 때: 일봉 지표로 1종목. 추세 위 + 거래량이 붙고 + 아직 덜 오른 종목."""
        H = HORIZONS[h]
        ss = self._sess.get(mkt) or session(mkt)
        ix = self._idx.get(mkt) or {}
        best, bs = None, -1e9
        for c in self._cands.get(_ck(mkt, h)) or []:
            f = c.get("일봉")
            if not isinstance(f, dict):
                continue
            r5, cp = f.get("5일%") or 0, c.get("등락률%") or 0
            if r5 > 30 or cp > 20 or cp < -6:
                continue
            sc = (1.5 if (f.get("20일선대비%") or 0) > 0 else -1.5) + max(-5, min(12, r5)) * 0.15 + min(2.0, f.get("5일거래량배수") or 0) \
                + (1 if (f.get("60일고점대비%") or -99) > -8 else 0) - abs(cp - 3) * 0.1
            sc += (self.learn_score(_feat_swing(c, {}, ss, ix, True), h, mkt) or 0) * 0.5
            if sc > bs:
                best, bs = c, sc
        if not best:
            return None
        f = best["일봉"]
        atr = f.get("하루변동폭%") or 3
        k = 1 if h == "swing" else 1.8
        tp, sl = round(min(max(2.5 * atr * k, 6 * k), H["tp_max"] / 2), 1), round(min(max(1.4 * atr * k, 3.5 * k), H["sl_max"] * 0.7), 1)
        try:
            tn = self.tune(h, mkt)
            if tn.get("tp") and tn.get("sl"):
                tp, sl = tn["tp"], tn["sl"]
        except Exception:
            pass
        q = self.quote(mkt, best["sym"])
        price = q.get("price") or best.get("현재가")
        x = {"sym": best["sym"], "name": best.get("name") or best["sym"], "price": price, "change_pct": q.get("change_pct", best.get("등락률%")),
             "exch": q.get("exch"), "score": 40, "win_prob": 42, "entry_type": "limit", "entry_price": price,
             "target_pct": tp, "stop_pct": sl, "trail_pct": 0, "weight_pct": 5, "auto": True, "cal_prob": None, "cal_n": 0,
             "horizon": h, "hold_days": H["days"], "end_date": swing_end(mkt, kst_now().replace(tzinfo=None), H["days"])[1].isoformat(),
             "theme": "", "catalyst": "재료 확인 없이 일봉 흐름만으로 고른 후보예요", "catalyst_type": "기술적", "catalyst_date": "",
             "in_cand": True, "idx0": ix.get("price"), "idx_sym": ix.get("sym"), "daily": f,
             "reasons": ["AI가 종목을 주지 않아, 일봉 지표(20일선 위·거래량 증가·덜 오른 정도)로 가장 나은 후보를 골랐어요",
                         "비중 5%, 하루 변동폭에 맞춘 목표·손절이에요"],
             "risks": ["뉴스·재료를 확인하지 않은 후보예요"]}
        x["feat"] = _feat_swing(best, x, ss, ix, True)
        return x

    def today(self):
        d = datetime.now().strftime("%Y-%m-%d")
        w = self.rt.db.one("SELECT COUNT(*) n FROM ai_guards WHERE status='watching'")["n"]
        done = self.rt.db.q("SELECT base, entry, exit_price FROM ai_guards WHERE status='done' AND closed_at LIKE ?", (d + "%",))
        pl = [(r["exit_price"] / (r["base"] or r["entry"]) - 1) * 100 for r in done if r["exit_price"] and (r["base"] or r["entry"])]
        n = self.rt.db.one("SELECT COUNT(*) n FROM ai_picks WHERE ts LIKE ?", (d + "%",))["n"]
        return {"watching": w, "done_today": len(done), "avg_pct": round(sum(pl) / len(pl), 2) if pl else 0, "picks_today": n}

    def history(self):
        rows = self.rt.db.q("SELECT id,ts,mkt,model,usd,stance,view,picks,horizon,theme FROM ai_picks ORDER BY id DESC LIMIT 30")
        for r in rows:
            r["picks"] = json.loads(r["picks"] or "[]")
            r["horizon"] = hz(r.get("horizon"))
        return rows

    def quote(self, mkt, sym):
        m = self.rt.market
        try:
            return dict(m.kr_quote(sym) if mkt == "kr" else m.us_live(sym))
        except Exception:
            return {}

    # ---------- 자동 익절·손절 ----------
    def add_guard(self, d):
        mkt = "us" if d.get("mkt") == "us" else "kr"
        eng = d.get("engine") or "scalp"
        if self.rt.brokers.get(eng) is None:
            raise ValueError("계좌를 확인해 주세요")
        sym = str(d.get("sym") or "").strip().upper()
        qty = int(float(d.get("qty") or 0))
        entry = float(str(d.get("entry") or 0).replace(",", ""))
        tp, sl = abs(float(d.get("target_pct") or 0)), abs(float(d.get("stop_pct") or 0))
        if not sym or qty <= 0 or entry <= 0:
            raise ValueError("종목·수량·매수가를 확인해 주세요")
        if not (0.2 <= tp <= 80) or not (0.2 <= sl <= 30):
            raise ValueError("익절은 0.2~80%, 손절은 0.2~30% 사이로 넣어 주세요")
        h = hz(d.get("horizon"))
        exit_by = str(d.get("exit_by") or "")[:5]
        if (exit_by and not re.match(r"^\d\d:\d\d$", exit_by)) or h != "day":
            exit_by = ""
        exit_date = ""
        if h != "day":  # 중기: 날마다 정리하지 않고, 보유 기간이 끝나는 날 장 마감 직전에만 정리
            hold = int(min(max(float(d.get("hold_days") or HORIZONS[h]["days"]), 1), HORIZONS[h]["days"]))
            exit_date = swing_end(mkt, kst_now().replace(tzinfo=None), hold)[1].isoformat()
        gid = self.rt.db.x(
            "INSERT INTO ai_guards(created_at,mkt,engine,sym,name,exch,qty,entry,target_pct,stop_pct,trail_pct,exit_by,"
            "base,high,pick_id,msg,horizon,exit_date) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), mkt, eng, sym, d.get("name") or sym, d.get("exch") or "",
             qty, entry, tp, sl, abs(float(d.get("trail_pct") or 0)), exit_by, entry, entry, d.get("pick_id"),
             "체결을 기다리는 중", h, exit_date))
        return self.rt.db.one("SELECT * FROM ai_guards WHERE id=?", (gid,))

    def cancel_guard(self, gid):
        self.rt.db.x("UPDATE ai_guards SET status='cancelled', closed_at=?, msg='감시를 껐어요' WHERE id=? AND status='watching'",
                     (datetime.now().isoformat(timespec="seconds"), gid))
        return {"ok": True}

    def guards(self, live=True):
        rows = self.rt.db.q("SELECT * FROM ai_guards ORDER BY CASE status WHEN 'watching' THEN 0 ELSE 1 END, id DESC LIMIT 30")
        for g in rows:
            b = g["base"] or g["entry"]
            g["tp_price"] = round(b * (1 + g["target_pct"] / 100), 2)
            g["sl_price"] = round(b * (1 - g["stop_pct"] / 100), 2)
            if live and g["status"] == "watching":
                p = self.quote(g["mkt"], g["sym"]).get("price")
                if p:
                    g["last"] = p
            px = g["exit_price"] if g["status"] == "done" else g["last"]
            g["pnl_pct"] = round((px / b - 1) * 100, 2) if px and b else None
        return rows

    def _position(self, g):
        b = self.rt.brokers.get(g["engine"])
        if b is None:
            return None
        if g["mkt"] == "us":
            cl = getattr(b, "client", None)
            if cl is None:
                return None
            for h in cl.us_holdings():
                if h["code"].upper() == g["sym"]:
                    return h
            return {"qty": 0}
        for h in b.positions():
            if h["code"] == g["sym"]:
                return h
        return {"qty": 0}

    def _sell(self, g, qty, reason, price):
        rt = self.rt
        b = rt.brokers.get(g["engine"])
        now = datetime.now().isoformat(timespec="seconds")
        try:
            if g["mkt"] == "us":
                no, exch = b.client.us_order("sell", g["sym"], qty, None, g["exch"] or None)
                res = {"status": "accepted", "fill_price": price, "broker_no": f"{no}|{exch}", "name": g["name"]}
            else:
                res = b.sell(g["sym"], qty, None)
            rt.db.x("INSERT INTO orders(ts,engine,mode,code,name,side,qty,price,status,fill_price,broker_no,msg) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now, g["engine"], b.mode, g["sym"], res.get("name") or g["name"], "sell", qty, None, res["status"],
                     res.get("fill_price") or price, res.get("broker_no", ""), f"AI {reason}"))
            rt.db.x("UPDATE ai_guards SET status='done', exit_reason=?, exit_price=?, closed_at=?, msg=? WHERE id=?",
                    (reason, res.get("fill_price") or price, now, f"{reason} 주문을 넣었어요", g["id"]))
            pl = (price / (g["base"] or g["entry"]) - 1) * 100
            rt.notify(f"[moa AI] {g['name']} {qty}주 {reason} 매도 주문 · 약 {pl:+.2f}%")
        except Exception as e:
            rt.db.x("UPDATE ai_guards SET msg=? WHERE id=?", (f"{reason} 매도 실패: {e} (다음 확인 때 다시)", g["id"]))
            rt.db.log("system", "error", f"AI 자동 {reason} 실패 {g['sym']}: {e}", now)

    def check(self, force=False):
        """감시 중인 종목을 돌며 익절·손절·추적손절·시간 정리."""
        import time as _t
        if not force and _t.time() - self._last < 5:
            return
        self._last = _t.time()
        rows = self.rt.db.q("SELECT * FROM ai_guards WHERE status='watching'")
        for g in rows:
            ss = session(g["mkt"])
            if not ss["open"]:
                continue
            q = self.quote(g["mkt"], g["sym"])
            price = q.get("price")
            if not price:
                continue
            try:
                pos = self._position(g)
            except Exception:
                pos = None
            if pos is None:
                continue
            have = int(pos.get("sellable") or pos.get("qty") or 0)
            base = g["base"] or g["entry"]
            swing = hz(g.get("horizon")) != "day"
            last_day = bool(g.get("exit_date")) and mkt_date(g["mkt"]) >= g["exit_date"]
            if have <= 0:
                # 아직 체결 전: 정리 시각(중기는 보유 기간)이 지나면 감시만 끝낸다
                if (swing and g.get("exit_date") and mkt_date(g["mkt"]) > g["exit_date"]) or \
                        (not swing and g["exit_by"] and kst_now().strftime("%H:%M") >= g["exit_by"] and g["mkt"] == "kr"):
                    self.rt.db.x("UPDATE ai_guards SET status='expired', closed_at=?, msg='체결되지 않아 감시를 끝냈어요' WHERE id=?",
                                 (datetime.now().isoformat(timespec="seconds"), g["id"]))
                else:
                    self.rt.db.x("UPDATE ai_guards SET last=?, msg='체결을 기다리는 중' WHERE id=?", (price, g["id"]))
                continue
            if pos.get("avg_price") and g["msg"] == "체결을 기다리는 중":
                base = float(pos["avg_price"])  # 실제 평균 단가 기준으로 다시 맞춤
            high = max(g["high"] or base, price)
            qty = min(have, g["qty"])
            self.rt.db.x("UPDATE ai_guards SET base=?, high=?, last=?, msg='감시 중' WHERE id=?", (base, high, price, g["id"]))
            g.update(base=base, high=high)
            chg = (price / base - 1) * 100
            reason = None
            if chg >= g["target_pct"]:
                reason = "익절"
            elif chg <= -g["stop_pct"]:
                reason = "손절"
            elif g["trail_pct"] and high > base * (1 + g["trail_pct"] / 100) and price <= high * (1 - g["trail_pct"] / 100) and price > base:
                reason = "추적 익절"
            else:
                now_hm = kst_now().strftime("%H:%M")
                close_soon = ss["left_min"] <= 8  # 정규장 마감 8분 전이면 정리
                if swing:
                    if last_day and close_soon:
                        reason = "기간 정리"
                elif (g["exit_by"] and g["mkt"] == "kr" and now_hm >= g["exit_by"]) or close_soon:
                    reason = "시간 정리"
            if reason:
                self._sell(g, qty, reason, price)


# ======================================================================
# 추천 성적: 사지 않았어도 추천 시점 이후 실제 가격으로 결과를 매긴다
# ======================================================================
FEE = {"kr": 0.2, "us": 0.25}  # 왕복 비용(%)


def _bar_dt(t, ref):
    """분봉 시각 'MM/DD HH:MM'을 datetime으로(연도는 추천 시각 기준)."""
    try:
        md, hm = t.split(" ")
        m, d = map(int, md.split("/"))
        H, M = map(int, hm.split(":"))
        y = ref.year + (1 if ref.month == 12 and m == 1 else 0)
        return datetime(y, m, d, H, M)
    except Exception:
        return None


def window_end(mkt, ts, exit_by=""):
    """추천을 평가할 마지막 시각(한국 시간, naive). 국내는 그날(또는 다음 거래일) 정리 시각, 미국은 그 정규장 마감."""
    if mkt == "kr":
        cap = hm_add("15:20", kr_shift(ts)[1])
        hm = exit_by if re.match(r"^\d\d:\d\d$", exit_by or "") and exit_by <= cap else cap
        d = ts
        if ts.strftime("%H:%M") >= hm or ts.weekday() >= 5:
            d = ts + timedelta(days=1)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            cap = hm_add("15:20", kr_shift(d)[1])
            hm = exit_by if re.match(r"^\d\d:\d\d$", exit_by or "") and exit_by <= cap else cap
        h, m = map(int, hm.split(":"))
        return d.replace(hour=h, minute=m, second=0, microsecond=0)
    # 미국: 추천 시각을 뉴욕 시간으로 바꿔 그 정규장(또는 다음 정규장) 16:00
    u = ts - timedelta(hours=9)  # KST → UTC(naive)
    off = -4 if _us_dst(u.replace(tzinfo=timezone.utc)) else -5
    et = u + timedelta(hours=off)
    d = et
    if et.hour * 60 + et.minute >= 960 or et.weekday() >= 5:
        d = et + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
    close_et = d.replace(hour=16, minute=0, second=0, microsecond=0)
    cu = close_et - timedelta(hours=off)
    off2 = -4 if _us_dst(cu.replace(tzinfo=timezone.utc)) else -5
    return close_et - timedelta(hours=off2) + timedelta(hours=9)


FILL_BARS = 6  # 지정가는 추천 뒤 30분(5분봉 6개) 안에 닿아야 체결로 본다


def judge(bars, entry, tp, sl, entry_type="market", limit=None, fill_bars=FILL_BARS, gap=False):
    """bars: 추천 이후 시간순 [{o,h,l,c}]. 반환: (결과, 진입가, 청산가, 최대상승%, 최대하락%, 체결여부, 끝난 봉 번호).

    gap=True(일봉): 시가가 이미 손절가 아래·익절가 위에서 시작했으면 그 시가에 팔린 것으로 본다(갭).
    """
    if not bars:
        return None
    px, filled, i0 = entry, True, 0
    if entry_type == "limit" and limit and limit < entry * 0.999:
        filled = False
        for i, b in enumerate(bars[:fill_bars]):
            if b["l"] <= limit:
                filled, px, i0 = True, limit, i
                break
        if not filled:
            return ("미체결", None, None, None, None, False, 0)
    up, dn = px * (1 + tp / 100), px * (1 - sl / 100)
    hi, lo = px, px
    for n, b in enumerate(bars[i0:], i0 + 1):
        if gap and b["o"] <= dn:
            lo = min(lo, b["o"])
            return ("손절", px, b["o"], (hi / px - 1) * 100, (lo / px - 1) * 100, True, n)
        if gap and b["o"] >= up:
            hi = max(hi, b["o"])
            return ("익절", px, b["o"], (hi / px - 1) * 100, (lo / px - 1) * 100, True, n)
        hi, lo = max(hi, b["h"]), min(lo, b["l"])
        hit_dn, hit_up = b["l"] <= dn, b["h"] >= up
        if hit_dn and hit_up:
            # 한 봉 안에서 둘 다 닿았을 때: 봉 모양으로 순서를 짐작한다(음봉은 고가가 먼저 → 익절, 양봉은 저가가 먼저 → 손절)
            first_up = b["c"] < b["o"]
            return ("익절" if first_up else "손절", px, up if first_up else dn, (hi / px - 1) * 100, (lo / px - 1) * 100, True, n)
        if hit_dn:
            return ("손절", px, dn, (hi / px - 1) * 100, (lo / px - 1) * 100, True, n)
        if hit_up:
            return ("익절", px, up, (hi / px - 1) * 100, (lo / px - 1) * 100, True, n)
    last = bars[-1]["c"]
    return ("시간 정리", px, last, (hi / px - 1) * 100, (lo / px - 1) * 100, True, len(bars))


def _init_outcomes(db):
    db.x("""CREATE TABLE IF NOT EXISTS ai_outcomes(
        pick_id INTEGER NOT NULL, idx INTEGER NOT NULL, ts TEXT NOT NULL, mkt TEXT NOT NULL, model TEXT NOT NULL,
        sym TEXT NOT NULL, name TEXT, stance TEXT, auto INTEGER DEFAULT 0, score INTEGER, win_prob INTEGER,
        entry_type TEXT, rec_price REAL, entry_price REAL, target_pct REAL, stop_pct REAL, end_ts TEXT,
        status TEXT NOT NULL DEFAULT 'live', result TEXT, filled INTEGER, entry REAL, exit REAL,
        ret_pct REAL, net_pct REAL, mfe REAL, mae REAL, last REAL, src TEXT, updated_at TEXT, cost_usd REAL,
        PRIMARY KEY(pick_id, idx))""")
    _ensure_cols(db)


def _sync_outcomes(self):
    """새 추천을 성적표에 올린다."""
    _ensure_cols(self.rt.db)
    have = {r["pick_id"] for r in self.rt.db.q("SELECT DISTINCT pick_id FROM ai_outcomes")}
    for p in self.rt.db.q("SELECT * FROM ai_picks ORDER BY id"):
        if p["id"] in have:
            continue
        picks = json.loads(p["picks"] or "[]")
        ts = datetime.fromisoformat(p["ts"])
        h = hz(p.get("horizon"))
        for i, x in enumerate(picks):
            if not x.get("price"):
                continue
            end = window_end(p["mkt"], ts, x.get("exit_by") or "") if h == "day" else swing_end(p["mkt"], ts, HORIZONS[h]["days"])[0]
            self.rt.db.x("INSERT OR IGNORE INTO ai_outcomes(pick_id,idx,ts,mkt,model,sym,name,stance,auto,score,win_prob,entry_type,"
                         "rec_price,entry_price,target_pct,stop_pct,end_ts,cost_usd,feat,horizon,hold_days,lv,theme,cat,idx0,idx_sym) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (p["id"], i, p["ts"], p["mkt"], p["model"], x["sym"], x.get("name"), p.get("stance"), int(bool(x.get("auto"))),
                          x.get("score"), x.get("win_prob"), x.get("entry_type"), x["price"], x.get("entry_price") or x["price"],
                          x.get("target_pct"), x.get("stop_pct"), end.isoformat(timespec="minutes"),
                          (p.get("usd") or 0) / max(1, len(picks)), json.dumps(x.get("feat") or {}, ensure_ascii=False),
                          h, x.get("hold_days"), p.get("lv") or 0, x.get("theme") or "", x.get("catalyst_type") or "",
                          x.get("idx0"), x.get("idx_sym") or ""))


PATH_DAYS = (1, 3, 5, 10, 20)  # '손절·익절 없이 그냥 들고 있었다면' 며칠 뒤 수익률을 재는 지점(거래일)


def _eval_swing(self, r, now, path_only=False):
    """중기(1주·1달) 추천 채점: 추천 당일 남은 구간(5분봉) + 그 뒤 일봉으로 익절·손절·기간 만료를 가린다."""
    db, inv = self.rt.db, self.rt.invest
    mkt, sym = r["mkt"], r["sym"]
    ts, end = datetime.fromisoformat(r["ts"]), datetime.fromisoformat(r["end_ts"])
    ph, d0 = phase_at(mkt, ts)
    d0s = d0.isoformat()
    end_ds = swing_end(mkt, ts, HORIZONS[hz(r["horizon"])]["days"])[1].isoformat()
    key = (r["pick_id"], r["idx"])
    try:
        day0 = json.loads(r.get("d0") or "null")
    except ValueError:
        day0 = None
    if ph == "open" and not path_only and mkt_date(mkt) == d0s:
        # 추천 뒤 그날 장이 끝날 때까지의 고가·저가·종가. 5분봉은 다음 날이면 앞부분부터 사라지므로,
        # 추천 당일에만 받아 적고(그날 본 가장 넓은 범위를 유지) 날짜가 바뀌면 그대로 굳힌다
        try:
            start = ts.replace(second=0, microsecond=0)
            seg = []
            for b in inv.candles(mkt, sym, "m5").get("bars") or []:
                dt = _bar_dt(b["t"], ts)
                if not dt or phase_at(mkt, dt)[1] != d0 or dt < start - timedelta(minutes=4):
                    continue
                seg.append(dict(b, o=b["c"], h=b["c"], l=b["c"]) if dt < start else b)
            if seg:
                old = day0 or {}
                day0 = {"t": d0s, "o": old.get("o") or seg[0]["o"], "h": max([b["h"] for b in seg] + [old.get("h") or 0]),
                        "l": min([b["l"] for b in seg] + ([old["l"]] if old.get("l") else [])), "c": seg[-1]["c"]}
                db.x("UPDATE ai_outcomes SET d0=? WHERE pick_id=? AND idx=?", (json.dumps(day0), *key))
        except Exception:
            pass
    try:
        dbars = inv.candles(mkt, sym, "d").get("bars") or []
    except Exception:
        dbars = []
    later = [b for b in dbars if (b["t"] >= d0s if ph == "pre" else b["t"] > d0s)]
    bars = []
    if ph == "open":
        if day0:
            bars.append(day0)
        else:  # 당일 분봉을 못 받았으면 그날은 종가만 쓴다(추천 전 고가·저가가 섞이지 않게)
            b0 = next((b for b in dbars if b["t"] == d0s), None)
            if b0:
                bars.append(dict(b0, o=b0["c"], h=b0["c"], l=b0["c"]))
    bars += [b for b in later if b["t"] <= end_ds]
    entry0 = r["rec_price"]
    # '그냥 들고 있었다면' 경로: 끝난 일봉만
    today, closed = mkt_date(mkt), phase_at(mkt, kst_now().replace(tzinfo=None))[0] == "post"
    full = [b for b in later if b["t"] < today or (b["t"] == today and closed)]
    path = {f"d{k}": round((full[k - 1]["c"] / entry0 - 1) * 100, 2) for k in PATH_DAYS if len(full) >= k and entry0}
    pathd = 1 if len(full) >= PATH_DAYS[-1] or now - ts > timedelta(days=45) else None
    if path_only or r["status"] == "done":
        db.x("UPDATE ai_outcomes SET path=?, pathd=? WHERE pick_id=? AND idx=?", (json.dumps(path), pathd, *key))
        return
    done = now >= end
    if not bars:
        if done and now - end > timedelta(days=5):
            db.x("UPDATE ai_outcomes SET status='done', result='자료 없음', pathd=1, updated_at=? WHERE pick_id=? AND idx=?",
                 (now.isoformat(timespec="seconds"), *key))
        return
    lim = 0.35 if mkt == "kr" else 0.6  # 국내는 하루 ±30%가 한계. 그보다 큰 틈은 시세가 아니라 자료 문제다
    prev, broken = entry0, False
    for b in bars:
        if prev and abs(b["o"] / prev - 1) > lim:
            broken = True
            break
        prev = b["c"]
    if broken:
        # 추천가와 일봉의 가격대가 크게 다르면(액면분할·병합, 수정주가 반영 등) 엉뚱한 손익이 나오므로 채점에서 뺀다
        db.x("UPDATE ai_outcomes SET status='done', result='자료 없음', pathd=1, src='가격대 불일치', updated_at=? WHERE pick_id=? AND idx=?",
             (now.isoformat(timespec="seconds"), *key))
        return
    result, entry, exit_, mfe, mae, filled, n = judge(bars, entry0, r["target_pct"], r["stop_pct"], "market", gap=True)
    ret = (exit_ / entry - 1) * 100 if entry and exit_ else None
    net = ret - FEE[mkt] if ret is not None else None
    final = done or result in ("익절", "손절")
    bench = None
    if final and r.get("idx0") and r.get("idx_sym"):
        try:  # 같은 기간 지수 수익률(시장이 올려 준 몫을 빼고 보려고)
            xd = bars[n - 1].get("t") or end_ds
            ib = [b for b in inv.candles("us", r["idx_sym"], "d").get("bars") or [] if b["t"] <= xd]
            if ib:
                bench = round((ib[-1]["c"] / r["idx0"] - 1) * 100, 2)
        except Exception:
            bench = None
    db.x("UPDATE ai_outcomes SET status=?, result=?, filled=1, entry=?, exit=?, ret_pct=?, net_pct=?, mfe=?, mae=?, last=?, src=?, "
         "updated_at=?, gv=?, days_held=?, bench=?, path=?, pathd=? WHERE pick_id=? AND idx=?",
         ("done" if final else "live", result if final else "진행 중", entry, exit_, round(ret, 3), round(net, 3), round(mfe, 3), round(mae, 3),
          bars[-1]["c"], "일봉" + ("" if ph != "open" or day0 else "(당일 종가부터)"), now.isoformat(timespec="seconds"),
          GRADE_V if final else None, n, bench, json.dumps(path), pathd if final else None, *key))


def evaluate(self, force=False):
    """진행 중인 추천을 실제 가격으로 채점. 정리 시각이 지나면 확정."""
    import time as _t
    if not force and _t.time() - getattr(self, "_ev_last", 0) < 240:
        return
    self._ev_last = _t.time()
    _sync_outcomes(self)
    now = datetime.now()
    _ensure_cols(self.rt.db)
    # 중기 추천: 진행 중인 것은 매번, 끝난 것의 '들고 있었다면' 경로는 한 시간에 한 번
    for r in self.rt.db.q("SELECT * FROM ai_outcomes WHERE COALESCE(horizon,'day')<>'day' AND status='live'"):
        try:
            _eval_swing(self, r, now)
        except Exception as e:
            self.rt.db.log("system", "error", f"AI 중기 추천 채점 실패 {r['sym']}: {e}", now.isoformat(timespec="seconds"))
    if force or _t.time() - getattr(self, "_path_last", 0) > 3600:
        self._path_last = _t.time()
        for r in self.rt.db.q("SELECT * FROM ai_outcomes WHERE COALESCE(horizon,'day')<>'day' AND status='done' AND pathd IS NULL "
                              "AND result<>'자료 없음' AND ts >= ?", ((now - timedelta(days=60)).isoformat(),)):
            try:
                _eval_swing(self, r, now, path_only=True)
            except Exception:
                pass
    rows = self.rt.db.q("SELECT * FROM ai_outcomes WHERE COALESCE(horizon,'day')='day' AND (status='live' OR gv IS NULL OR gv < ?) AND ts >= ?",
                        (GRADE_V, (now - timedelta(days=14)).isoformat()))
    for r in rows:
        regrade = r["status"] != "live"
        ts, end = datetime.fromisoformat(r["ts"]), datetime.fromisoformat(r["end_ts"])
        bars, src = [], ""
        try:
            cd = self.rt.invest.candles(r["mkt"], r["sym"], "m5").get("bars") or []
            start = ts.replace(second=0, microsecond=0)
            for b in cd:
                dt = _bar_dt(b["t"], ts)
                if dt and start - timedelta(minutes=4) <= dt < end:
                    if dt < start:
                        # 추천 시각이 낀 봉: 추천 전 움직임(고가·저가)이 섞여 있으니 그 봉의 종가만 쓴다
                        b = dict(b, o=b["c"], h=b["c"], l=b["c"])
                    bars.append(b)
            src = "5분봉"
        except Exception:
            bars = []
        done = now >= end
        if regrade and not bars:  # 다시 채점할 분봉이 이미 지나갔으면 예전 결과를 그대로 둔다
            self.rt.db.x("UPDATE ai_outcomes SET gv=? WHERE pick_id=? AND idx=?", (GRADE_V, r["pick_id"], r["idx"]))
            continue
        if done and not bars:  # 분봉이 지나가 버렸으면 일봉으로(같은 날 고가·저가)
            try:
                day = end.strftime("%Y-%m-%d") if r["mkt"] == "kr" else (end - timedelta(hours=14)).strftime("%Y-%m-%d")
                bars = [b for b in self.rt.invest.candles(r["mkt"], r["sym"], "d").get("bars") or [] if b["t"] == day]
                src = "일봉(순서 추정)"
            except Exception:
                bars = []
        # 주 성적: 추천을 보고 그 순간 가격에 바로 샀다고 본다(지정가를 기다리지 않음)
        res = judge(bars, r["rec_price"], r["target_pct"], r["stop_pct"], "market")
        if not res:
            if done and now - end > timedelta(days=3):
                self.rt.db.x("UPDATE ai_outcomes SET status='done', result='자료 없음', updated_at=? WHERE pick_id=? AND idx=?",
                             (now.isoformat(timespec="seconds"), r["pick_id"], r["idx"]))
            continue
        result, entry, exit_, mfe, mae, filled = res[:6]
        ret = (exit_ / entry - 1) * 100 if entry and exit_ else None
        net = ret - FEE[r["mkt"]] if ret is not None else None
        final = done or result in ("익절", "손절")
        if not final:
            result = "진행 중"
        # 참고: AI가 말한 지정가를 기다렸다면(추천 뒤 30분 안에 닿아야 체결)
        alt_result, alt_net = None, None
        if r["entry_type"] == "limit" and r["entry_price"] and r["entry_price"] < r["rec_price"] * 0.999:
            alt = judge(bars, r["rec_price"], r["target_pct"], r["stop_pct"], "limit", r["entry_price"])
            if alt and alt[0] == "미체결":
                if done or now >= ts + timedelta(minutes=5 * FILL_BARS + 5):
                    alt_result = "미체결"
            elif alt and (done or alt[0] in ("익절", "손절")):
                alt_result, alt_net = alt[0], round((alt[2] / alt[1] - 1) * 100 - FEE[r["mkt"]], 3)
        self.rt.db.x("UPDATE ai_outcomes SET status=?, result=?, filled=?, entry=?, exit=?, ret_pct=?, net_pct=?, mfe=?, mae=?, "
                     "last=?, src=?, updated_at=?, alt_result=?, alt_net=?, gv=? WHERE pick_id=? AND idx=?",
                     ("done" if final else "live", result, int(filled), entry, exit_,
                      None if ret is None else round(ret, 3), None if net is None else round(net, 3),
                      None if mfe is None else round(mfe, 3), None if mae is None else round(mae, 3),
                      bars[-1]["c"] if bars else None, src, now.isoformat(timespec="seconds"), alt_result, alt_net,
                      GRADE_V if final else None, r["pick_id"], r["idx"]))
    try:
        self.auto_learn()
    except Exception:
        pass


DOW = ["월", "화", "수", "목", "금", "토", "일"]
HIST = {"day": ([-3, -2, -1, 0, 1, 2, 3], ["−3%↓", "−3~−2", "−2~−1", "−1~0", "0~1", "1~2", "2~3", "3%↑"]),
        "swing": ([-8, -5, -2, 0, 2, 5, 8], ["−8%↓", "−8~−5", "−5~−2", "−2~0", "0~2", "2~5", "5~8", "8%↑"]),
        "month": ([-15, -10, -5, 0, 5, 10, 15], ["−15%↓", "−15~−10", "−10~−5", "−5~0", "0~5", "5~10", "10~15", "15%↑"])}


def stats(self, days=0, mkt="", model="", horizon="day"):
    """성적 요약(추천 종류별): 전체·시장·엔진·승률 구간·순위·태도·시간대·요일·규칙 판·조건별 + 개별 목록.

    중기는 재료 유형·테마·후보 안팎·걸린 기간·지수 대비·'들고 있었다면' 경로까지."""
    _sync_outcomes(self)
    h = hz(horizon)
    swing = h != "day"
    q, a = "SELECT * FROM ai_outcomes WHERE COALESCE(horizon,'day')=?", [h]
    if days:
        q += " AND ts >= ?"
        a.append((datetime.now() - timedelta(days=int(days))).isoformat())
    if mkt in ("kr", "us"):
        q += " AND mkt=?"
        a.append(mkt)
    if model:
        q += " AND model=?"
        a.append(model)
    rows = self.rt.db.q(q + " ORDER BY ts DESC, idx", a)
    bought = {(g["pick_id"], g["sym"]) for g in self.rt.db.q("SELECT pick_id, sym FROM ai_guards WHERE pick_id IS NOT NULL")}
    for r in rows:
        r["bought"] = (r["pick_id"], r["sym"]) in bought
        try:
            r["f"] = json.loads(r.get("feat") or "{}")
        except ValueError:
            r["f"] = {}
        try:
            r["path"] = json.loads(r.get("path") or "{}")
        except ValueError:
            r["path"] = {}
    avg = lambda xs, d=2: round(sum(xs) / len(xs), d) if xs else None

    def agg(rs):
        fin = [r for r in rs if r["status"] == "done" and r["result"] in ("익절", "손절", "시간 정리")]
        nets = [r["net_pct"] for r in fin if r["net_pct"] is not None]
        wins = [x for x in nets if x > 0]
        tp = sum(1 for r in fin if r["result"] == "익절")
        sl = sum(1 for r in fin if r["result"] == "손절")
        gw, gl = sum(wins), -sum(x for x in nets if x <= 0)
        cost = sum(r["cost_usd"] or 0 for r in rs)
        bn = [r for r in fin if r.get("bench") is not None and r["net_pct"] is not None]
        return {"n": len(rs), "done": len(fin), "live": sum(1 for r in rs if r["status"] == "live"),
                "unfilled": sum(1 for r in rs if r["result"] == "미체결"),
                "tp": tp, "sl": sl, "time": len(fin) - tp - sl,
                "tp_rate": round(tp / len(fin) * 100, 1) if fin else None,
                "win_rate": round(len(wins) / len(nets) * 100, 1) if nets else None,
                "avg": round(sum(nets) / len(nets), 2) if nets else None,
                "sum": round(sum(nets), 2) if nets else 0,
                "best": round(max(nets), 2) if nets else None, "worst": round(min(nets), 2) if nets else None,
                "pf": round(gw / gl, 2) if gl > 0 else (None if not wins else 99),
                "pred": round(sum(r["win_prob"] or 0 for r in fin) / len(fin), 1) if fin else None,
                "mfe": round(sum(r["mfe"] or 0 for r in fin) / len(fin), 2) if fin else None,
                "mae": round(sum(r["mae"] or 0 for r in fin) / len(fin), 2) if fin else None,
                "alt_n": len([r for r in rs if r.get("alt_result")]),
                "alt_unfilled": len([r for r in rs if r.get("alt_result") == "미체결"]),
                "alt_avg": (lambda xs: round(sum(xs) / len(xs), 2) if xs else None)([r["alt_net"] for r in rs if r.get("alt_net") is not None]),
                "hold": avg([r["days_held"] for r in fin if r.get("days_held")], 1),
                "bench": avg([r["bench"] for r in bn]), "alpha": avg([r["net_pct"] - r["bench"] for r in bn]), "bench_n": len(bn),
                "cost_usd": round(cost, 4)}
    ORDER = ["~40%", "40~50%", "50~60%", "60~70%", "70%~", "장 전", "장 초반(~10시)", "장 초반", "장 중반(~14시)", "장 중반", "장 후반", "장 마감 뒤",
             "공격", "보통", "관망", "지표 자동 선정", "샀음", "안 삼", "kr", "us", "월", "화", "수", "목", "금", "토", "일",
             "당일", "1일", "2~3일", "4~5일", "6~10일", "11일 이상", "순위 후보 안", "AI가 뉴스에서 찾음", "규칙 없음"] + list(CATS)
    by = lambda key, src=None: [{"key": k, **agg([r for r in (src or rows) if key(r) == k])}
                                for k in sorted({key(r) for r in (src or rows)} - {None}, key=lambda k: (ORDER.index(k) if k in ORDER else 99, str(k)))]

    def tod(r):
        hh = int(r["ts"][11:13]) if r["mkt"] == "kr" else (int(r["ts"][11:13]) - 14) % 24
        t = hh * 60 + int(r["ts"][14:16])
        if r["mkt"] == "kr":
            return "장 전" if t < 540 else "장 초반(~10시)" if t < 600 else "장 중반(~14시)" if t < 840 else "장 후반" if t < 920 else "장 마감 뒤"
        return "장 전" if t < 570 else "장 초반" if t < 660 else "장 중반" if t < 900 else "장 후반" if t < 960 else "장 마감 뒤"

    def band(r):
        w = r["win_prob"] or 0
        return "~40%" if w < 40 else "40~50%" if w < 50 else "50~60%" if w < 60 else "60~70%" if w < 70 else "70%~"

    def held(r):
        d = r.get("days_held")
        if r["status"] != "done" or not d:
            return None
        return "당일" if d <= 1 and r["f"].get("open") else "1일" if d <= 1 else "2~3일" if d <= 3 else "4~5일" if d <= 5 else "6~10일" if d <= 10 else "11일 이상"
    fin = [r for r in rows if r["status"] == "done" and r["net_pct"] is not None]
    curve, acc = [], 0.0
    for r in sorted(fin, key=lambda x: (x["ts"], x["idx"])):
        acc += r["net_pct"]
        curve.append({"t": r["ts"][:10], "v": round(acc, 2)})
    edges, labels = HIST[h]
    hist = [0] * 8
    for r in fin:
        x = r["net_pct"]
        hist[next((i for i, e in enumerate(edges) if x < e), 7)] += 1
    from .ai import MODELS
    fx = 1400.0
    try:
        fx = float(self.rt.market.yh_quote("USDKRW=X")["price"]) or fx
    except Exception:
        pass
    # 조건별 성적(추천 순간의 특징값으로 묶음)
    feat = {}
    for b in _buckets([r for r in fin if r["result"] in FIN], h, feat_only=True):
        feat.setdefault(b["group"], []).append({k: b[k] for k in ("key", "n", "avg", "tp_rate")})
    out = {"horizon": h, "label": HORIZONS[h]["label"], "days": HORIZONS[h]["days"],
           "all": agg(rows), "by_mkt": by(lambda r: r["mkt"]), "by_model": by(lambda r: r["model"]),
           "by_band": by(band), "by_rank": by(lambda r: f"{r['idx'] + 1}순위"),
           "by_stance": by(lambda r: ("지표 자동 선정" if r["auto"] else (r["stance"] or "-"))),
           "by_tod": by(tod), "by_bought": by(lambda r: "샀음" if r["bought"] else "안 삼"),
           "by_dow": by(lambda r: DOW[datetime.fromisoformat(r["ts"]).weekday()]),
           "by_lv": by(lambda r: f"규칙 {r['lv']}판" if r.get("lv") else "규칙 없음"),
           "by_feat": [{"group": g, "items": xs} for g, xs in feat.items()],
           "curve": curve, "hist": hist, "hist_labels": labels, "rows": rows[:200], "fx": fx,
           "models": {k: v[0].split(" · ")[0] for k, v in MODELS.items()}}
    if swing:
        themes = by(lambda r: r.get("theme") or None)
        themes.sort(key=lambda x: -x["n"])
        pts = []
        for k in PATH_DAYS:
            xs = [r["path"][f"d{k}"] - FEE[r["mkt"]] for r in rows if f"d{k}" in r["path"]]
            if xs:
                pts.append({"k": k, "n": len(xs), "avg": avg(xs), "win": round(sum(1 for x in xs if x > 0) / len(xs) * 100)})
        out.update(by_cat=by(lambda r: r.get("cat") or None), by_theme=themes[:12],
                   by_cand=by(lambda r: None if "cand" not in r["f"] else ("순위 후보 안" if r["f"]["cand"] else "AI가 뉴스에서 찾음")),
                   by_hold=by(held), path=pts)
    return out


AIPick.evaluate = evaluate
AIPick.stats = stats
_orig_init = AIPick.__init__


def _init(self, rt):
    _orig_init(self, rt)
    _init_outcomes(rt.db)


AIPick.__init__ = _init


def detail(self, pid):
    """추천 한 번의 전체 내용 + 종목별 추적 결과."""
    p = self.rt.db.one("SELECT * FROM ai_picks WHERE id=?", (pid,))
    if not p:
        raise ValueError("추천 기록을 찾지 못했어요")
    evaluate(self, force=True)
    p["picks"] = json.loads(p["picks"] or "[]")
    p["horizon"] = hz(p.get("horizon"))
    p["label"] = HORIZONS[p["horizon"]]["label"]
    oc = {r["idx"]: r for r in self.rt.db.q("SELECT * FROM ai_outcomes WHERE pick_id=?", (pid,))}
    guards = {g["sym"]: g for g in self.guards(live=False) if g.get("pick_id") == pid}
    for i, x in enumerate(p["picks"]):
        o = oc.get(i)
        if o:
            try:
                o["path"] = json.loads(o.get("path") or "{}")
            except ValueError:
                o["path"] = {}
        x["outcome"] = o
        x["guard"] = guards.get(x["sym"])
        q = self.quote(p["mkt"], x["sym"])
        x["now"] = q.get("price")
    return p


AIPick.detail = detail


# ======================================================================
# 자체 학습: 쌓인 추천 성적을 다음 추천에 되먹인다(추천 종류별로 따로)
#  1) 통계 되먹임(무료, 매번 자동): 어떤 조건의 추천이 통했는지 요약해 프롬프트에 넣는다
#  2) 승률 보정: AI가 말한 승률을 과거 같은 구간의 실제 익절률로 당겨서 보여 준다
#  3) 자동 조정값: 실제 최대상승·최대하락·보유 경로에서 목표·손절 폭과 보유일을 뽑고, 피할 조건·우선할 조건을 정한다
#  4) 후보 가산점: 과거에 잘 통한 조건의 후보에 점수를 더해 AI에게 알려 주고, 지표 자동 선정에도 쓴다
#  5) AI 복기(유료, 자동/수동): Claude가 성적표를 보고 '추천 규칙'을 써 두고, 이후 추천마다 읽는다. 규칙 판마다 성적을 따로 잰다
# ======================================================================
LEARN_MIN = 8          # 당일 추천: 이만큼 채점이 쌓여야 통계를 프롬프트에 넣는다(중기는 HORIZONS의 min)
LESSON_STEP = 15       # 당일 추천: 새 채점이 이만큼 쌓이면 AI 복기를 다시 한다(중기는 HORIZONS의 step)
FIN = ("익절", "손절", "시간 정리")


def _feat_of(c, ss):
    f = c.get("오늘_5분봉") if isinstance(c.get("오늘_5분봉"), dict) else {}
    return {"chg": c.get("등락률%"), "vwap": f.get("VWAP대비%"), "hi": f.get("고점대비%"), "m30": f.get("최근30분%"),
            "volx": f.get("최근30분_거래량배수"), "ob": c.get("호가_매수잔량/매도잔량"),
            "left": ss.get("left_min"), "open": bool(ss.get("open"))}


def _feat_swing(c, x, ss, ix, in_cand):
    """중기 추천 순간의 특징값(나중에 '어떤 조건이 통했나'를 가르는 재료)."""
    f = c.get("일봉") if isinstance(c.get("일봉"), dict) else {}
    return {"chg": c.get("등락률%"), "r5": f.get("5일%"), "r20": f.get("20일%"), "ma20": f.get("20일선대비%"), "ma60": f.get("60일선대비%"),
            "hi60": f.get("60일고점대비%"), "volx5": f.get("5일거래량배수"), "atr": f.get("하루변동폭%"), "streak": f.get("연속상승일"),
            "idx20": (ix or {}).get("20일선대비%"), "idx5": (ix or {}).get("5일%"), "cand": bool(in_cand),
            "cat": x.get("catalyst_type"), "open": bool(ss.get("open"))}


GRADE_V = 3  # 채점 방식 버전(바뀌면 최근 추천을 다시 채점)


def _ensure_cols(db):
    _col(db, "ai_outcomes", (("feat", "TEXT"), ("alt_result", "TEXT"), ("alt_net", "REAL"), ("gv", "INTEGER"),
                             ("horizon", "TEXT"), ("hold_days", "INTEGER"), ("path", "TEXT"), ("pathd", "INTEGER"), ("bench", "REAL"),
                             ("days_held", "INTEGER"), ("lv", "INTEGER"), ("theme", "TEXT"), ("cat", "TEXT"), ("d0", "TEXT"),
                             ("idx0", "REAL"), ("idx_sym", "TEXT")))


def _done_rows(self, mkt=None, h="day"):
    _ensure_cols(self.rt.db)
    rows = self.rt.db.q("SELECT * FROM ai_outcomes WHERE status='done' AND net_pct IS NOT NULL AND COALESCE(horizon,'day')=? ORDER BY ts", (hz(h),))
    rows = [r for r in rows if r["result"] in FIN]
    for r in rows:
        try:
            r["f"] = json.loads(r.get("feat") or "{}")
        except ValueError:
            r["f"] = {}
        try:
            r["path"] = json.loads(r.get("path") or "{}")
        except ValueError:
            r["path"] = {}
    return [r for r in rows if not mkt or r["mkt"] == mkt]


def _bucket(rows, label, fn):
    out = []
    groups = {}
    for r in rows:
        k = fn(r)
        if k is not None:
            groups.setdefault(k, []).append(r)
    for k, rs in groups.items():
        if len(rs) >= 3:
            nets = [x["net_pct"] for x in rs]
            out.append({"group": label, "key": k, "n": len(rs), "avg": round(sum(nets) / len(nets), 2),
                        "tp_rate": round(sum(1 for x in rs if x["result"] == "익절") / len(rs) * 100)})
    return out


def _fv(key):
    return lambda r: r["f"].get(key)


def _split(key, cuts, names):
    """특징값을 구간 이름으로. cuts=[a,b] names=[<a, a~b, >=b]"""
    def fn(r):
        v = r["f"].get(key)
        if v is None:
            return None
        for c, n in zip(cuts, names):
            if v < c:
                return n
        return names[-1]
    return fn


# (묶음 이름, 나누는 함수, 추천 순간의 특징인지) — 특징 묶음은 후보 가산점·피할 조건에도 쓴다
def _defs(h):
    if hz(h) == "day":
        return [
            ("VWAP 위치", _split("vwap", [0], ["VWAP 아래", "VWAP 위"]), True),
            ("당일 등락률", _split("chg", [3, 8], ["3% 미만", "3~8%", "8% 이상"]), True),
            ("최근 30분 흐름", lambda r: None if r["f"].get("m30") is None else ("오르는 중" if r["f"]["m30"] > 0 else "내리는 중"), True),
            ("최근 거래량", _split("volx", [1.5], ["1.5배 미만", "1.5배 이상"]), True),
            ("고점 대비", lambda r: None if r["f"].get("hi") is None else ("고점 근처(−1% 안)" if r["f"]["hi"] > -1 else "고점에서 밀림"), True),
            ("호가 잔량", lambda r: None if not r["f"].get("ob") else ("매수 우위" if r["f"]["ob"] >= 1 else "매도 우위"), True),
            ("추천 시점", lambda r: None if "open" not in r["f"] else ("정규장 중" if r["f"]["open"] else "정규장 밖"), True),
            ("남은 장 시간", lambda r: None if not r["f"].get("open") or r["f"].get("left") is None else ("2시간 이상" if r["f"]["left"] >= 120 else "2시간 미만"), True),
            ("목표 크기", lambda r: None if not r.get("target_pct") else "2% 이하" if r["target_pct"] <= 2 else "2~3%" if r["target_pct"] <= 3 else "3% 초과", False),
            ("AI 태도", lambda r: None if "stance" not in r else "지표 자동 선정" if r.get("auto") else (r.get("stance") or "-"), False),
            ("순위", lambda r: None if "idx" not in r else f"{r['idx'] + 1}순위", False),
            ("엔진", lambda r: None if not r.get("model") else r["model"].replace("claude-", ""), False),
            ("AI 예측 승률", lambda r: None if r.get("win_prob") is None else "50% 미만" if r["win_prob"] < 50 else "50~60%" if r["win_prob"] < 60 else "60% 이상", False),
        ]
    return [
        ("재료 유형", lambda r: r["f"].get("cat") or None, True),
        ("종목 출처", lambda r: None if "cand" not in r["f"] else ("순위 후보 안" if r["f"]["cand"] else "AI가 뉴스에서 찾음"), True),
        ("20일선 위치", _split("ma20", [0], ["20일선 아래", "20일선 위"]), True),
        ("60일선 위치", _split("ma60", [0], ["60일선 아래", "60일선 위"]), True),
        ("최근 5일 등락", _split("r5", [0, 10, 25], ["내림", "0~10% 오름", "10~25% 오름", "25% 넘게 급등"]), True),
        ("최근 20일 등락", _split("r20", [0, 20], ["내림", "0~20% 오름", "20% 넘게 오름"]), True),
        ("60일 고점 대비", _split("hi60", [-15, -5], ["고점에서 15% 넘게 밀림", "고점 −15~−5%", "고점 근처(−5% 안)"]), True),
        ("5일 거래량", _split("volx5", [1.5, 3], ["평소 수준(1.5배 미만)", "1.5~3배", "3배 이상 급증"]), True),
        ("하루 변동폭", _split("atr", [3, 6], ["작음(3% 미만)", "보통(3~6%)", "큼(6% 이상)"]), True),
        ("추천 당일 등락", _split("chg", [0, 5, 15], ["내림", "0~5%", "5~15%", "15% 이상"]), True),
        ("시장 국면(지수 20일선)", _split("idx20", [0], ["지수가 20일선 아래", "지수가 20일선 위"]), True),
        ("목표 크기", lambda r: None if not r.get("target_pct") else "8% 이하" if r["target_pct"] <= 8 else "8~15%" if r["target_pct"] <= 15 else "15% 초과", False),
        ("손절 크기", lambda r: None if not r.get("stop_pct") else "4% 이하" if r["stop_pct"] <= 4 else "4~7%" if r["stop_pct"] <= 7 else "7% 초과", False),
        ("AI 태도", lambda r: None if "stance" not in r else "지표 자동 선정" if r.get("auto") else (r.get("stance") or "-"), False),
        ("순위", lambda r: None if "idx" not in r else f"{r['idx'] + 1}순위", False),
        ("엔진", lambda r: None if not r.get("model") else r["model"].replace("claude-", ""), False),
        ("AI 예측 승률", lambda r: None if r.get("win_prob") is None else "45% 미만" if r["win_prob"] < 45 else "45~55%" if r["win_prob"] < 55 else "55% 이상", False),
    ]


def _buckets(rows, h, feat_only=False):
    out = []
    for label, fn, is_feat in _defs(h):
        if feat_only and not is_feat:
            continue
        out += _bucket(rows, label, fn)
    return out


def learn_stats(self, mkt=None, h="day"):
    h = hz(h)
    H = HORIZONS[h]
    rows = _done_rows(self, mkt, h)
    if mkt and len(rows) < H["min"]:  # 그 시장 표본이 적으면 전체로
        rows = _done_rows(self, None, h)
        mkt = None
    n = len(rows)
    if not n:
        return {"n": 0, "scope": "전체", "buckets": [], "insights": [], "overall": None}
    nets = [r["net_pct"] for r in rows]
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
    B = _buckets(rows, h)
    tp = sum(1 for r in rows if r["result"] == "익절")
    overall = {"n": n, "avg": round(sum(nets) / n, 2), "tp_rate": round(tp / n * 100), "win_rate": round(sum(1 for x in nets if x > 0) / n * 100),
               "pred": round(sum(r["win_prob"] or 0 for r in rows) / n), "mfe_med": round(med([r["mfe"] or 0 for r in rows]), 2),
               "mae_med": round(med([r["mae"] or 0 for r in rows]), 2),
               "time_share": round(sum(1 for r in rows if r["result"] == "시간 정리") / n * 100)}
    if h != "day":
        bn = [r for r in rows if r.get("bench") is not None]
        if bn:
            overall["alpha"] = round(sum(r["net_pct"] - r["bench"] for r in bn) / len(bn), 2)
    ins = []
    if n >= H["min"]:
        gap = overall["pred"] - overall["tp_rate"]
        if abs(gap) >= 5:
            ins.append(f"AI 예측 승률이 실제 익절률보다 평균 {abs(gap)}%p {'높아요(과신)' if gap > 0 else '낮아요(보수적)'} → 승률을 그만큼 {'낮춰' if gap > 0 else '높여'} 말하게 해요")
        if overall["time_share"] >= 40:
            how = "시간 정리" if h == "day" else "기간 만료"
            ins.append(f"추천의 {overall['time_share']}%가 익절·손절에 닿지 못하고 {how}로 끝나요 → 목표·손절 폭을 실제 움직임(중간값 +{overall['mfe_med']}% / {overall['mae_med']}%)에 맞추게 해요")
        if overall.get("alpha") is not None and abs(overall["alpha"]) >= 1:
            ins.append(f"같은 기간 지수보다 평균 {overall['alpha']:+.2f}%p {'더 벌었어요' if overall['alpha'] > 0 else '덜 벌었어요(시장만 못해요)'}")
        by = {}
        for b in B:
            by.setdefault(b["group"], []).append(b)
        for g, bs in by.items():
            if len(bs) >= 2 and g not in ("순위", "엔진", "AI 예측 승률"):
                hi, lo = max(bs, key=lambda x: x["avg"]), min(bs, key=lambda x: x["avg"])
                if hi["avg"] - lo["avg"] >= H["gap"]:
                    ins.append(f"{g}: '{hi['key']}' {hi['n']}건 평균 {hi['avg']:+.2f}% vs '{lo['key']}' {lo['n']}건 평균 {lo['avg']:+.2f}% → '{hi['key']}' 쪽을 더 고르게 해요")
    return {"n": n, "scope": {"kr": "국내", "us": "미국"}.get(mkt, "전체"), "overall": overall, "buckets": B, "insights": ins[:8]}


def tune(self, h="day", mkt=None):
    """실제 결과에서 뽑은 자동 조정값: 목표·손절 폭, 보유일, 피할 조건·우선할 조건."""
    h = hz(h)
    H = HORIZONS[h]
    rows = _done_rows(self, mkt, h)
    if mkt and len(rows) < H["min"]:
        rows = _done_rows(self, None, h)
    n = len(rows)
    if n < H["min"]:
        return {"n": n, "ready": False}
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(len(xs) * p))]
    mfe, mae = [r["mfe"] or 0 for r in rows], [abs(r["mae"] or 0) for r in rows]
    # 목표: 절반쯤은 닿았던 상승폭. 손절: 이긴 추천이 견딘 하락폭의 대부분(80%)을 덮는 값
    win_mae = [abs(r["mae"] or 0) for r in rows if r["net_pct"] > 0] or mae
    floor_tp, floor_sl = (1.0, 0.6) if h == "day" else (4.0, 2.5) if h == "swing" else (8.0, 5.0)
    tp = round(min(max(q(mfe, 0.5), floor_tp), H["tp_max"]), 1)
    sl = round(min(max(q(win_mae, 0.8), floor_sl), H["sl_max"]), 1)
    out = {"n": n, "ready": True, "tp": tp, "sl": sl, "now_tp": round(sum(r["target_pct"] or 0 for r in rows) / n, 1),
           "now_sl": round(sum(r["stop_pct"] or 0 for r in rows) / n, 1), "avoid": [], "prefer": [], "hold": None, "hold_avg": None}
    if h != "day":  # 며칠 들고 있었을 때가 가장 좋았나
        best = None
        for k in PATH_DAYS:
            if k > H["days"]:
                continue
            xs = [r["path"][f"d{k}"] - FEE[r["mkt"]] for r in rows if f"d{k}" in r["path"]]
            if len(xs) >= max(3, H["min"] // 2):
                a = sum(xs) / len(xs)
                if best is None or a > best[1]:
                    best = (k, a)
        if best:
            out["hold"], out["hold_avg"] = best[0], round(best[1], 2)
    base = sum(r["net_pct"] for r in rows) / n
    bs = [b for b in _buckets(rows, h, feat_only=True) if b["n"] >= 4]
    txt = lambda b: f"{b['group']} = {b['key']} ({b['n']}건 평균 {b['avg']:+.2f}%)"
    # 전체 평균과 차이가 큰 것부터 5개씩
    out["avoid"] = [txt(b) for b in sorted(bs, key=lambda b: b["avg"]) if b["avg"] <= min(-H["gap"], base - H["gap"])][:5]
    out["prefer"] = [txt(b) for b in sorted(bs, key=lambda b: -b["avg"]) if b["avg"] >= max(H["gap"], base + H["gap"])][:5]
    return out


def learn_score(self, feat, h="day", mkt=None):
    """후보 하나가 과거에 잘 통한 조건에 얼마나 맞는지(같은 조건 묶음의 평균 수익을 전체 평균과 견줘 더한 값)."""
    h = hz(h)
    rows = _done_rows(self, None, h)
    if len(rows) < HORIZONS[h]["min"]:
        return None
    key = (h, len(rows))
    cache = getattr(self, "_ls_cache", {})
    if cache.get("key") != key:
        base = sum(r["net_pct"] for r in rows) / len(rows)
        cache = {"key": key, "map": {(b["group"], b["key"]): b["avg"] - base for b in _buckets(rows, h, feat_only=True)}}
        self._ls_cache = cache
    r, sc = {"f": feat or {}}, 0.0
    for label, fn, is_feat in _defs(h):
        if not is_feat:
            continue
        k = fn(r)
        if k is not None:
            sc += cache["map"].get((label, k), 0)
    lim = 3 if h == "day" else 10
    return round(max(-lim, min(lim, sc)), 2)


def calibrate(self, mkt, p_ai, h="day"):
    """AI 승률을 과거 같은 구간(±10%p)의 실제 익절률 쪽으로 당긴다(표본이 적으면 조금만)."""
    rows = [r for r in _done_rows(self, None, h) if abs((r["win_prob"] or 0) - p_ai) <= 10]
    same = [r for r in rows if r["mkt"] == mkt]
    if len(same) >= 6:
        rows = same
    n = len(rows)
    if n < 4:
        return None, n
    tp = sum(1 for r in rows if r["result"] == "익절")
    k = 8  # 가상의 표본 8건만큼은 AI 말을 믿어 준다
    return round((tp + k * p_ai / 100) / (n + k) * 100), n


def _lkey(h):
    return "ai_lessons" if hz(h) == "day" else f"ai_lessons_{hz(h)}"


def lessons(self, h="day"):
    ls = self.rt.db.get_setting(_lkey(h)) or {}
    if ls.get("text") and not ls.get("ver"):  # 판 번호를 세기 전에 만든 규칙
        ls["ver"] = 1
    return ls


def learn_view(self, h="day"):
    h = hz(h)
    H = HORIZONS[h]
    st = learn_stats(self, None, h)
    ls = lessons(self, h)
    n = st["n"]
    stage = "자료 모으는 중" if n < H["min"] else "통계 반영 중" if not ls.get("text") else "통계 + AI 복기 규칙 반영 중"
    bands = []
    done = _done_rows(self, None, h)
    for lo, hi in ((0, 40), (40, 50), (50, 60), (60, 70), (70, 101)):
        rs = [r for r in done if lo <= (r["win_prob"] or 0) < hi]
        if rs:
            bands.append({"band": f"{lo}~{min(hi, 100)}%", "n": len(rs), "actual": round(sum(1 for r in rs if r["result"] == "익절") / len(rs) * 100),
                          "pred": round(sum(r["win_prob"] or 0 for r in rs) / len(rs))})
    auto = self.rt.db.get_setting("ai_learn_auto")
    # 규칙 판별 성적: 규칙을 고친 뒤 실제로 나아졌는지
    vers = {}
    for r in done:
        vers.setdefault(int(r.get("lv") or 0), []).append(r["net_pct"])
    by_ver = [{"ver": v, "n": len(xs), "avg": round(sum(xs) / len(xs), 2), "win": round(sum(1 for x in xs if x > 0) / len(xs) * 100)}
              for v, xs in sorted(vers.items())]
    live = self.rt.db.one("SELECT COUNT(*) n FROM ai_outcomes WHERE status='live' AND COALESCE(horizon,'day')=?", (h,))["n"]
    return {"horizon": h, "label": H["label"], "n": n, "min": H["min"], "live": live, "stage": stage, "overall": st["overall"], "insights": st["insights"],
            "buckets": st["buckets"], "bands": bands, "lessons": ls, "auto": True if auto is None else bool(auto),
            "next_in": max(0, H["step"] - (n - int(ls.get("n") or 0))), "step": H["step"], "tune": tune(self, h), "by_ver": by_ver}


def learn_prompt(self, mkt, h="day"):
    """다음 추천 프롬프트에 넣을 자체 학습 자료."""
    h = hz(h)
    st = learn_stats(self, mkt, h)
    if st["n"] < HORIZONS[h]["min"]:
        return None
    out = {"표본": f"{st['scope']} {HORIZONS[h]['label']} 추천 {st['n']}건(사지 않은 것 포함, 추천 시점 가격 기준 가상 매매, 비용 차감)",
           "전체": st["overall"], "조건별 성적(건수 3 이상)": [f"{b['group']}={b['key']}: {b['n']}건, 평균 {b['avg']:+.2f}%, 익절률 {b['tp_rate']}%" for b in st["buckets"]],
           "통계가 말하는 점": st["insights"]}
    tn = tune(self, h, mkt)
    if tn.get("ready"):
        adj = {"권장 목표폭%": tn["tp"], "권장 손절폭%": tn["sl"], "지금까지 평균 목표/손절%": f"{tn['now_tp']} / {tn['now_sl']}"}
        if tn.get("hold"):
            adj["가장 성적이 좋았던 보유 거래일"] = f"{tn['hold']}일(평균 {tn['hold_avg']:+.2f}%)"
        if tn["avoid"]:
            adj["피할 조건"] = tn["avoid"]
        if tn["prefer"]:
            adj["우선할 조건"] = tn["prefer"]
        out["자동 조정값(실제 결과에서 계산)"] = adj
    ls = lessons(self, h)
    if ls.get("text"):
        out["지난 복기에서 정한 규칙"] = ls["text"]
    return out


def learn_context(self, h="day"):
    """AI 복기용 자료: 성적 요약 + 추천 하나하나(특징 포함)."""
    h = hz(h)
    H = HORIZONS[h]
    rows = _done_rows(self, None, h)
    if len(rows) < H["min"]:
        raise ValueError(f"{H['label']} 추천 채점이 {H['min']}건 이상 쌓여야 복기할 수 있어요(지금 {len(rows)}건)")
    st = learn_stats(self, None, h)
    slim = []
    for r in rows[-80:]:
        x = {"시각": r["ts"][5:16], "시장": r["mkt"], "엔진": r["model"].replace("claude-", ""), "종목": r["name"], "태도": r["stance"],
             "예측승률": r["win_prob"], "목표%": r["target_pct"], "손절%": r["stop_pct"], "결과": r["result"], "순수익%": r["net_pct"],
             "최대상승%": r["mfe"], "최대하락%": r["mae"], "특징": r["f"]}
        if h != "day":
            x.update({"테마": r.get("theme"), "재료": r.get("cat"), "걸린 거래일": r.get("days_held"), "지수 수익%": r.get("bench"),
                      "들고 있었다면(거래일별 %)": r["path"], "규칙 판": r.get("lv") or 0})
        slim.append(x)
    what = "당일 매매 추천" if h == "day" else f"{H['label']}(약 {H['days']}거래일) 보유 추천"
    ask = (f"아래는 이 앱의 AI 종목 추천({what})을 실제 가격으로 추적한 성적표야. 다음 추천부터 그대로 따를 '추천 규칙'을 만들어 줘.\n"
           "- 표본이 적은 패턴은 단정하지 말고 '잠정'이라고 표시해.\n"
           "- 형식: 첫 줄에 한 줄 총평. 그다음 '## 규칙' 아래 '- '로 8개 이하. 각 규칙은 '언제 → 어떻게'(고르기/피하기, 목표·손절 폭, "
           + ("보유 기간, 재료 유형, " if h != "day" else "") + "승률 말하는 법, 비중)로 구체적으로.\n"
           "- 이전 규칙이 있으면 '규칙 판'별 성적을 보고, 효과가 없던 규칙은 고치거나 빼.\n"
           "- 마지막에 '## 더 지켜볼 것' 아래 2~3개.\n"
           "- 숫자는 자료에 있는 것만. 웹 검색은 하지 마.")
    return f"AI {H['label']} 추천 복기", ask, {"요약": st["overall"], "조건별": st["buckets"], "통계가 말하는 점": st["insights"],
                                           "자동 조정값": tune(self, h), "이전 규칙": lessons(self, h).get("text"), "추천별 기록(최근 80건)": slim}


def save_lessons(self, text, model, usd, h="day"):
    h = hz(h)
    rows = _done_rows(self, None, h)
    old = lessons(self, h)
    hist = list(old.get("hist") or [])
    if old.get("text"):
        hist.append({"ver": old.get("ver") or 1, "at": old.get("at"), "n": old.get("n"), "text": old["text"][:1500]})
    self.rt.db.set_setting(_lkey(h), {"text": text.strip()[:4000], "at": datetime.now().isoformat(timespec="minutes"),
                                      "n": len(rows), "model": model, "usd": usd, "ver": int(old.get("ver") or 0) + 1, "hist": hist[-5:]})


def auto_learn(self):
    """새 채점이 충분히 쌓였으면 뒤에서 AI 복기를 돌린다(추천 종류마다 하루 한 번까지, 설정에서 끌 수 있음)."""
    auto = self.rt.db.get_setting("ai_learn_auto")
    if auto is not None and not auto:
        return
    if not self.rt.ai.configured() or getattr(self, "_learning", False):
        return
    for h, H in HORIZONS.items():
        ls = lessons(self, h)
        n = len(_done_rows(self, None, h))
        if n < H["min"] or n - int(ls.get("n") or 0) < H["step"]:
            continue
        if (ls.get("at") or "")[:10] == datetime.now().strftime("%Y-%m-%d"):
            continue
        self._learning = True

        def run(h=h, H=H):
            try:
                for ev in self.rt.ai.stream("learn", {"horizon": h}):
                    if ev.get("t") == "error":
                        self.rt.db.log("system", "error", f"AI 자동 복기 실패: {ev.get('msg')}", datetime.now().isoformat(timespec="seconds"))
                self.rt.notify(f"[moa AI] {H['label']} 추천 성적을 복기해서 추천 규칙을 새로 정했어요")
            finally:
                self._learning = False
        import threading
        threading.Thread(target=run, daemon=True).start()
        return


for _f in (learn_stats, calibrate, lessons, learn_view, learn_prompt, learn_context, save_lessons, auto_learn, tune, learn_score):
    setattr(AIPick, _f.__name__, _f)
