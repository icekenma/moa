"""배당 투자: 종목별 배당 자료, 내 보유 종목의 배당 달력, 시뮬레이션, AI 포트폴리오.

- 배당 자료는 야후 파이낸스의 배당 내역(배당락일·주당 금액)에서 가져와 하루에 한 번 갱신한다.
  배당률·성장률·지급월은 모두 '최근 실제 지급'을 기준으로 계산한 값이고, 앞으로를 보장하지 않는다.
- 지급월은 배당락일에서 추정한다(미국 ETF는 며칠 뒤, 미국 주식은 3주쯤 뒤, 국내 결산배당은 이듬해 4월).
- 시뮬레이션은 순수 함수(simulate)라 같은 입력이면 같은 결과가 나온다.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

TAX_KR, TAX_US = 15.4, 15.0   # 원천징수 세율(%) 기본값: 국내 배당소득세(지방세 포함), 미국 상장 종목 현지 원천징수
FIN_INCOME_LINE = 20_000_000  # 금융소득종합과세 기준(연, 세전)
FADE_YEARS = 5                # 과거 배당 성장률을 그대로 쓰는 햇수(그 뒤로는 낮춘다)

CATS = {
    "us_etf": "미국 배당 ETF", "us_cc": "미국 커버드콜·고분배", "us_stock": "미국 배당 성장주", "us_month": "미국 월배당 리츠·BDC",
    "us_bond": "미국 단기채(현금성)", "kr_etf": "국내 상장 배당 ETF", "kr_cc": "국내 상장 커버드콜", "kr_stock": "국내 고배당주", "kr_reit": "국내 리츠·인프라",
}
# 주가 상승 가정을 얼마나 적용할지(1 = 그대로). 채권·커버드콜은 주가가 거의 오르지 않는다고 본다
PRICE_FACTOR = {"us_bond": 0.0, "us_cc": 0.3, "kr_cc": 0.3, "us_month": 0.6, "kr_reit": 0.4}

# 기본 후보 목록. 숫자(배당률 등)는 넣지 않는다 — 전부 실제 자료에서 계산한다.
UNIVERSE = [
    ("us", "SCHD", "Schwab 미국 배당주 ETF", "us_etf", "배당을 꾸준히 늘려 온 우량주 100개. 배당 성장의 대표"),
    ("us", "VYM", "Vanguard 고배당 ETF", "us_etf", "배당률이 높은 대형주 400여 개에 넓게 분산"),
    ("us", "DGRO", "iShares 배당성장 ETF", "us_etf", "배당을 5년 이상 늘린 기업. 배당률은 낮고 성장이 빠른 편"),
    ("us", "VIG", "Vanguard 배당성장 ETF", "us_etf", "10년 이상 배당을 늘린 기업. 배당률은 낮지만 안정적"),
    ("us", "HDV", "iShares 핵심 고배당 ETF", "us_etf", "재무가 튼튼한 고배당주 75개. 에너지·헬스케어 비중이 큼"),
    ("us", "SPYD", "SPDR S&P500 고배당 ETF", "us_etf", "S&P500 중 배당률 상위 80개. 배당률은 높고 변동이 큼"),
    ("us", "DIVO", "Amplify 배당+커버드콜 ETF", "us_cc", "배당 성장주에 콜옵션을 조금만 얹어 월배당"),
    ("us", "JEPI", "JPMorgan 주식 프리미엄 인컴 ETF", "us_cc", "대형주 + 옵션 프리미엄으로 월배당. 상승장에서는 덜 오름"),
    ("us", "JEPQ", "JPMorgan 나스닥 프리미엄 인컴 ETF", "us_cc", "나스닥 + 옵션 프리미엄 월배당. 분배금이 달마다 크게 달라짐"),
    ("us", "QYLD", "Global X 나스닥100 커버드콜 ETF", "us_cc", "분배율은 매우 높지만 원금이 줄어 온 대표 사례. 주의"),
    ("us", "SGOV", "iShares 0-3개월 국채 ETF", "us_bond", "초단기 미국 국채. 주가 변동이 거의 없는 달러 현금 대용"),
    ("us", "BIL", "SPDR 1-3개월 국채 ETF", "us_bond", "초단기 미국 국채. SGOV와 같은 역할"),
    ("us", "O", "리얼티인컴", "us_month", "상업용 부동산 리츠. 매달 배당하고 오래 늘려 온 기록"),
    ("us", "MAIN", "메인스트리트 캐피털", "us_month", "중소기업 대출 회사(BDC). 매달 배당 + 가끔 특별배당"),
    ("us", "STAG", "STAG 인더스트리얼", "us_month", "물류창고 리츠. 매달 배당"),
    ("us", "KO", "코카콜라", "us_stock", "60년 넘게 배당을 늘린 필수소비재"),
    ("us", "PEP", "펩시코", "us_stock", "음료·스낵. 50년 넘게 배당 증가"),
    ("us", "JNJ", "존슨앤드존슨", "us_stock", "헬스케어. 60년 넘게 배당 증가"),
    ("us", "PG", "프록터앤드갬블", "us_stock", "생활용품. 60년 넘게 배당 증가"),
    ("us", "ABBV", "애브비", "us_stock", "제약. 배당률과 성장률이 모두 높은 편"),
    ("us", "XOM", "엑슨모빌", "us_stock", "에너지. 유가에 따라 이익이 크게 움직임"),
    ("us", "CVX", "셰브론", "us_stock", "에너지. 30년 넘게 배당 증가"),
    ("us", "VZ", "버라이즌", "us_stock", "통신. 배당률은 높고 성장은 느림"),
    ("us", "MO", "알트리아", "us_stock", "담배. 배당률이 매우 높지만 사업이 줄어드는 중"),
    ("us", "MCD", "맥도날드", "us_stock", "외식. 40년 넘게 배당 증가"),
    ("us", "HD", "홈디포", "us_stock", "주택 자재 유통. 배당 성장이 빠른 편"),
    ("kr", "458730", "TIGER 미국배당다우존스", "kr_etf", "SCHD와 같은 지수를 국내 계좌에서. 월배당"),
    ("kr", "446720", "SOL 미국배당다우존스", "kr_etf", "SCHD와 같은 지수. 월배당"),
    ("kr", "402970", "ACE 미국배당다우존스", "kr_etf", "SCHD와 같은 지수. 월배당"),
    ("kr", "161510", "PLUS 고배당주", "kr_etf", "국내 고배당주 30개. 금융·통신 비중이 큼"),
    ("kr", "279530", "KODEX 고배당", "kr_etf", "국내 고배당주에 분산"),
    ("kr", "211560", "TIGER 배당성장", "kr_etf", "국내 배당 성장주"),
    ("kr", "466940", "TIGER 은행고배당플러스TOP10", "kr_etf", "은행·보험 상위 10개. 월배당"),
    ("kr", "329200", "TIGER 리츠부동산인프라", "kr_reit", "국내 리츠·인프라 묶음. 월배당"),
    ("kr", "441640", "KODEX 미국배당커버드콜액티브", "kr_cc", "미국 배당주 + 커버드콜. 월배당"),
    ("kr", "458760", "TIGER 미국배당다우존스타겟커버드콜2호", "kr_cc", "미국배당다우존스 + 일부 커버드콜로 분배율을 높임"),
    ("kr", "088980", "맥쿼리인프라", "kr_reit", "도로·항만 등 인프라 펀드. 반기 배당"),
    ("kr", "395400", "SK리츠", "kr_reit", "오피스·주유소 리츠. 분기 배당"),
    ("kr", "330590", "롯데리츠", "kr_reit", "백화점·마트 리츠. 반기 배당"),
    ("kr", "293940", "신한알파리츠", "kr_reit", "오피스 리츠. 반기 배당"),
    ("kr", "005935", "삼성전자우", "kr_stock", "분기 배당. 보통주보다 배당률이 조금 높음"),
    ("kr", "005387", "현대차2우B", "kr_stock", "우선주. 분기 배당"),
    ("kr", "033780", "KT&G", "kr_stock", "담배·인삼. 반기 배당과 자사주 소각"),
    ("kr", "017670", "SK텔레콤", "kr_stock", "통신. 분기 배당"),
    ("kr", "030200", "KT", "kr_stock", "통신. 분기 배당"),
    ("kr", "105560", "KB금융", "kr_stock", "은행 지주. 분기 배당"),
    ("kr", "055550", "신한지주", "kr_stock", "은행 지주. 분기 배당"),
    ("kr", "086790", "하나금융지주", "kr_stock", "은행 지주. 분기 배당"),
    ("kr", "316140", "우리금융지주", "kr_stock", "은행 지주. 분기 배당"),
    ("kr", "024110", "기업은행", "kr_stock", "국책은행. 결산 배당 위주"),
    ("kr", "000810", "삼성화재", "kr_stock", "손해보험. 결산 배당"),
]

# 성향별 예시 구성(추천이 아니라 출발점). 비중 합 100
PRESETS = [
    {"id": "growth", "name": "배당 성장형", "risk": "보통", "desc": "지금 배당은 적어도 해마다 늘어나는 쪽. 오래 들고 갈수록 유리해요",
     "assets": [("us", "SCHD", 60), ("us", "DGRO", 20), ("us", "VYM", 20)]},
    {"id": "stable", "name": "성장 + 안정형", "risk": "낮음", "desc": "배당 성장 ETF에 단기 국채를 섞어 하락장 충격을 줄여요",
     "assets": [("us", "SCHD", 50), ("us", "SGOV", 30), ("us", "VYM", 20)]},
    {"id": "monthly", "name": "매월 현금흐름형", "risk": "보통", "desc": "매달 들어오는 돈을 크게. 대신 배당 성장과 주가 상승은 작아요",
     "assets": [("us", "JEPI", 30), ("us", "SCHD", 30), ("us", "O", 15), ("us", "JEPQ", 15), ("us", "SGOV", 10)]},
    {"id": "kr", "name": "국내 계좌형", "risk": "보통", "desc": "국내 상장 종목만. 환전 없이 원화로, 연금·ISA 계좌에도 담을 수 있어요",
     "assets": [("kr", "458730", 40), ("kr", "161510", 20), ("kr", "466940", 15), ("kr", "088980", 15), ("kr", "329200", 10)]},
    {"id": "mix", "name": "국내 + 미국 균형형", "risk": "보통", "desc": "달러와 원화, ETF와 개별주를 나눠 담아요",
     "assets": [("us", "SCHD", 35), ("kr", "458730", 20), ("us", "JEPI", 15), ("kr", "105560", 10), ("kr", "033780", 10), ("us", "SGOV", 10)]},
]

DIV_SYSTEM = """너는 한국 개인 투자자의 앱(moa)에 들어 있는 배당 포트폴리오 설계 분석가야.
목표: 사용자의 투자금·목표·성향에 맞춰, 세금을 뺀 뒤 원화로 매달 들어오는 배당이 안정적으로 이어지고 늘어나는 구성을 제안하는 것.

원칙
- 후보 목록의 숫자(배당률·성장률·지급 횟수·지급월)는 실제 최근 지급 자료에서 계산한 값이야. 이 숫자와 검색으로 확인한 사실만 근거로 써. 없는 숫자를 지어내지 마.
- 후보에 없는 종목도 넣을 수 있어. 미국은 티커, 국내는 6자리 코드를 정확히 적어.
- 배당률만 높은 종목을 쫓지 마. 배당이 줄어든 이력, 원금이 깎여 온 커버드콜, 한 업종 쏠림을 반드시 따져.
- 한 종목은 ETF 50%, 개별주 15%를 넘기지 마. 커버드콜·고분배 상품은 다 합쳐 30% 이하(사용자가 '고분배'를 골랐을 때만 40%까지).
- 매달 받는 돈이 고르게 들어오도록 지급월이 겹치지 않게 섞어.
- 세금: 국내 배당 15.4%, 미국 상장 종목 15% 원천징수. 연 금융소득이 2천만 원을 넘으면 종합과세·건강보험료가 달라질 수 있다는 점을 금액으로 판단해 알려 줘.
- 미국 종목은 환율에 따라 원화 배당이 달라져. 환율 위험을 risks에 적어.
- 확정 수익처럼 말하지 마. 과거 지급 기준 추정이야.

출력 형식(반드시 지켜)
1) 먼저 한국어로 8줄 이내: 첫 줄에 결론(무엇을 얼마나), 이어서 '- ' 항목으로 이유.
2) 그 다음 마지막에 아래 JSON 하나를 ```json 코드 블록으로. 다른 코드 블록은 쓰지 마.
{"title":"구성 이름(짧게)","summary":"한 줄 요약",
 "assets":[{"sym":"티커 또는 6자리 코드","mkt":"us|kr","name":"이름","weight":비중 숫자,"role":"핵심|성장|현금흐름|완충","reason":"이 종목을 이 비중으로 넣은 이유 한 줄"}],
 "rebalance":"언제 어떻게 다시 맞출지 한 줄","risks":["위험1","위험2"],"notes":["세금·계좌 등 참고 1","참고 2"]}
assets는 3~8개, weight 합은 100."""


def ysym(mkt, sym, kr_market=None):
    if mkt == "us":
        return sym
    return f"{sym}.{'KQ' if (kr_market or (lambda s: ''))(sym) == '코스닥' else 'KS'}"


def pay_date(mkt, kind, ex):
    """배당락일에서 지급일을 추정한다. kind: ETF / EQUITY."""
    if mkt == "us":
        return ex + timedelta(days=4 if kind == "ETF" else 21)
    if kind == "ETF":
        return ex + timedelta(days=4)
    if ex.month == 12 and ex.day >= 20:        # 국내 결산배당: 12월 말 기준 → 이듬해 4월
        return date(ex.year + 1, 4, 15)
    if ex.month in (2, 3) or (ex.month == 4 and ex.day < 10):  # 배당 기준일을 주총 뒤로 옮긴 회사: 2~3월 기준 → 4월
        return date(ex.year, 4, 20)
    return ex + timedelta(days=45)             # 분기·중간 배당: 기준일 뒤 6~7주


def analyze(divs, price, mkt, kind, today=None):
    """divs: [(date, 주당 금액)] 오름차순. 최근 1년 지급 기준으로 배당률·횟수·지급월·성장률을 낸다."""
    today = today or date.today()
    divs = sorted((d, float(a)) for d, a in divs if a and a > 0)
    out = {"ttm": 0.0, "yield": None, "freq": 0, "months": [0.0] * 12, "growth5": None, "growth3": None, "streak": 0,
           "years": [], "last": None, "count": len(divs)}
    if not divs:
        return out
    year_ago = today - timedelta(days=365)
    recent = [(d, a) for d, a in divs if d > year_ago]
    ttm = sum(a for _, a in recent)
    out["ttm"] = round(ttm, 6)
    out["yield"] = round(ttm / price * 100, 2) if price and ttm else None
    n = len(recent)
    out["freq"] = 12 if n >= 10 else 4 if n >= 3 else 2 if n == 2 else 1 if n == 1 else 0
    if ttm:
        for d, a in recent:
            out["months"][pay_date(mkt, kind, d).month - 1] += a / ttm
        out["months"] = [round(x, 4) for x in out["months"]]
    out["last"] = {"date": divs[-1][0].isoformat(), "amt": divs[-1][1], "pay": pay_date(mkt, kind, divs[-1][0]).isoformat()}
    if n and len(divs) >= 2:  # 다음 배당락 예상: 최근 간격만큼 뒤
        gap = max(25, min(370, round(365 / max(1, out["freq"] or n))))
        nx = divs[-1][0] + timedelta(days=gap)
        while nx < today:
            nx += timedelta(days=gap)
        out["next_ex"] = nx.isoformat()
    by = {}
    for d, a in divs:
        by[d.year] = by.get(d.year, 0) + a
    full = sorted(y for y in by if y < today.year)          # 끝난 해만
    out["years"] = [{"y": y, "amt": round(by[y], 6)} for y in sorted(by)][-11:]

    def cagr(k):
        if len(full) < k + 1:
            return None
        a, b = by.get(full[-1 - k]), by.get(full[-1])
        if not a or not b or full[-1] - full[-1 - k] != k:
            return None
        return round(((b / a) ** (1 / k) - 1) * 100, 2)
    out["growth5"], out["growth3"] = cagr(5), cagr(3)
    streak = 0
    for i in range(len(full) - 1, 0, -1):
        if full[i] - full[i - 1] == 1 and by[full[i]] >= by[full[i - 1]] * 0.999:
            streak += 1
        else:
            break
    out["streak"] = streak
    return out


def default_growth(info):
    """시뮬레이션 기본 배당 성장률(%): 과거 5년(없으면 3년) 성장률을 0~10% 안으로."""
    g = info.get("growth5") if info.get("growth5") is not None else info.get("growth3")
    if g is None:
        return 0.0
    return round(max(0.0, min(10.0, g)), 1)


# ---------------------------------------------------------------- 시뮬레이션
def _run(assets, amount, monthly_add, years, reinvest, price_growth, growth_scale, tax_kr, tax_us, fx_change, cut, start):
    """한 가지 가정으로 달마다 계산. 금액은 모두 원화. 반환: (월별 목록, 연도별 목록)."""
    st = []
    for a in assets:
        w = a["weight"] / 100
        g = (a.get("growth") or 0) / 100 * growth_scale
        pg_a = price_growth / 100 * PRICE_FACTOR.get(a.get("cat"), 1.0)
        # 배당이 주가보다 훨씬 빨리 늘어난다고 계속 가정하면 배당률이 끝없이 올라가 결과가 부풀려진다.
        # 그래서 과거 성장률은 처음 FADE_YEARS년만 쓰고, 그 뒤로는 주가 상승률 + 1%p를 넘지 않게 한다.
        g_long = max(0.0, min(g, pg_a + 0.01))
        fac, f = [], 1.0
        for k in range(years):
            if k > 0:
                f *= 1 + (g if k <= FADE_YEARS else g_long)
            fac.append(f)
        st.append({"a": a, "n": amount * w, "w": w,                      # n: 주식 수(시작 가격을 1로 둔 단위)
                   "y0": (a.get("yield") or 0) / 100, "fac": fac,
                   "pg": pg_a,
                   "tax": (tax_us if a["mkt"] == "us" else tax_kr) / 100, "us": a["mkt"] == "us",
                   "months": a.get("months") if a.get("months") and sum(a["months"]) > 0 else [1 / 12] * 12})
    months, yearly = [], []
    invested = amount
    y, m = start.year, start.month
    for k in range(years):
        yr = {"year": k + 1, "gross": 0.0, "net": 0.0, "tax": 0.0}
        for j in range(12):
            t = k * 12 + j
            fxm = (1 + fx_change / 100) ** (t / 12)                      # 환율 변화(미국 종목의 원화 환산)
            row = {"ym": f"{y}-{m:02d}", "gross": 0.0, "net": 0.0, "by": []}
            buys = []
            for s in st:
                price = (1 + s["pg"]) ** (t / 12) * (fxm if s["us"] else 1)
                dps = s["y0"] * s["fac"][k] * (1 - cut / 100) * (fxm if s["us"] else 1)
                gross = s["n"] * dps * s["months"][m - 1]
                net = gross * (1 - s["tax"])
                if gross > 0:
                    row["by"].append({"sym": s["a"]["sym"], "net": round(net)})
                row["gross"] += gross
                row["net"] += net
                buys.append((s, price))
            # 받은 배당(재투자할 때)과 이달 추가 투자금을 목표 비중대로 다시 산다
            cash = (row["net"] if reinvest else 0) + (monthly_add if t > 0 else 0)
            if t > 0:
                invested += monthly_add
            if cash > 0:
                for s, price in buys:
                    s["n"] += cash * s["w"] / price
            yr["gross"] += row["gross"]
            yr["net"] += row["net"]
            row["gross"], row["net"] = round(row["gross"]), round(row["net"])
            months.append(row)
            m += 1
            if m > 12:
                y, m = y + 1, 1
        t_end = (k + 1) * 12
        fxe = (1 + fx_change / 100) ** (t_end / 12)
        value = sum(s["n"] * (1 + s["pg"]) ** (t_end / 12) * (fxe if s["us"] else 1) for s in st)
        yr.update(invested=round(invested), value=round(value), tax=round(yr["gross"] - yr["net"]), gross=round(yr["gross"]), net=round(yr["net"]),
                  monthly=round(yr["net"] / 12), yoc=round(yr["net"] / invested * 100, 2) if invested else 0)
        yearly.append(yr)
    return months, yearly


def simulate(p, today=None):
    """p: {amount, monthly_add, years, reinvest, price_growth, growth_scale, tax_kr, tax_us, fx_change, goal, assets:[...]}"""
    today = today or date.today()
    assets = [a for a in (p.get("assets") or []) if (a.get("weight") or 0) > 0]
    if not assets:
        raise ValueError("구성에 종목을 하나 이상 넣어 주세요")
    tw = sum(a["weight"] for a in assets)
    assets = [dict(a, weight=a["weight"] / tw * 100) for a in assets]
    amount = float(p.get("amount") or 0)
    if amount <= 0:
        raise ValueError("투자금을 넣어 주세요")
    years = int(min(max(int(p.get("years") or 10), 1), 40))
    add = max(0.0, float(p.get("monthly_add") or 0))
    reinvest = bool(p.get("reinvest", True))
    pg = float(p.get("price_growth") if p.get("price_growth") is not None else 3)
    gs = float(p.get("growth_scale") if p.get("growth_scale") is not None else 1)
    tk = float(p.get("tax_kr") if p.get("tax_kr") is not None else TAX_KR)
    tu = float(p.get("tax_us") if p.get("tax_us") is not None else TAX_US)
    fxc = float(p.get("fx_change") or 0)
    start = date(today.year + (today.month == 12), today.month % 12 + 1, 1)   # 다음 달부터
    args = dict(assets=assets, amount=amount, monthly_add=add, years=years, tax_kr=tk, tax_us=tu, start=start)
    months, yearly = _run(reinvest=reinvest, price_growth=pg, growth_scale=gs, fx_change=fxc, cut=0, **args)
    _, other = _run(reinvest=not reinvest, price_growth=pg, growth_scale=gs, fx_change=fxc, cut=0, **args)
    y1, yn = yearly[0], yearly[-1]
    us_w = sum(a["weight"] for a in assets if a["mkt"] == "us")
    blended = sum(a["weight"] * (a.get("yield") or 0) for a in assets) / 100
    miss = [a["sym"] for a in assets if not a.get("yield")]
    goal = float(p.get("goal") or 0)
    marks = sorted({500_000, 1_000_000, 2_000_000, 3_000_000, 5_000_000} | ({goal} if goal > 0 else set()))
    hit = lambda rows, target: next((r["year"] for r in rows if r["monthly"] >= target), None)
    milestones = [{"target": int(t), "year": hit(yearly, t), "other": hit(other, t), "goal": t == goal} for t in marks]
    # 금융소득 2천만 원(세전)을 넘는 첫 해
    over = next((r["year"] for r in yearly if r["gross"] > FIN_INCOME_LINE), None)
    # 가정을 바꿔 본 범위: 보수 / 기본 / 낙관
    scen = []
    for name, kw in (("보수적", dict(price_growth=0, growth_scale=gs * 0.5, fx_change=fxc - 1)),
                     ("기본", dict(price_growth=pg, growth_scale=gs, fx_change=fxc)),
                     ("낙관적", dict(price_growth=pg + 2, growth_scale=gs * 1.2, fx_change=fxc + 1))):
        _, ys = _run(reinvest=reinvest, cut=0, **kw, **args)
        scen.append({"name": name, "first": ys[0]["monthly"], "last": ys[-1]["monthly"], "value": ys[-1]["value"],
                     "note": f"주가 연 {kw['price_growth']:g}% · 배당 성장 {kw['growth_scale'] / (gs or 1) * 100:.0f}% · 환율 연 {kw['fx_change']:+g}%"})
    # 민감도: 환율(미국 비중만큼 움직임), 배당 삭감
    fx_s = [{"chg": c, "monthly": round(y1["net"] / 12 * (1 + us_share_net(months[:12], assets) * c / 100))} for c in (-20, -10, 0, 10, 20)]
    _, cut_y = _run(reinvest=reinvest, price_growth=pg, growth_scale=gs, fx_change=fxc, cut=25, **args)
    return {
        "start": start.isoformat(), "years": years, "reinvest": reinvest, "assets": assets,
        "months": months[:12], "last_months": months[-12:], "yearly": yearly, "other": other,
        "summary": {"amount": round(amount), "monthly_add": round(add), "yield": round(blended, 2),
                    "yield_net": round(y1["net"] / amount * 100, 2), "first_monthly": y1["monthly"], "first_net": y1["net"], "first_gross": y1["gross"],
                    "last_monthly": yn["monthly"], "last_value": yn["value"], "invested": yn["invested"],
                    "total_net": sum(r["net"] for r in yearly), "total_tax": sum(r["tax"] for r in yearly),
                    "other_last_monthly": other[-1]["monthly"], "other_last_value": other[-1]["value"],
                    "us_weight": round(us_w, 1), "min_month": min(r["net"] for r in months[:12]), "max_month": max(r["net"] for r in months[:12]),
                    "over_line_year": over, "missing": miss},
        "milestones": milestones, "scenarios": scen, "fx": fx_s, "cut": {"pct": 25, "first": cut_y[0]["monthly"], "last": cut_y[-1]["monthly"]},
        "assume": {"price_growth": pg, "growth_scale": gs, "tax_kr": tk, "tax_us": tu, "fx_change": fxc},
    }


def us_share_net(months, assets):
    """첫 12개월 세후 배당 중 미국 종목이 차지하는 몫(환율 민감도 계산용)."""
    us = {a["sym"] for a in assets if a["mkt"] == "us"}
    tot = sum(r["net"] for r in months) or 1
    return sum(b["net"] for r in months for b in r["by"] if b["sym"] in us) / tot


# ---------------------------------------------------------------- 자료·화면용
class Dividend:
    TTL, TTL_ERR = 24 * 3600, 3600

    def __init__(self, rt):
        self.rt = rt
        rt.db.x("CREATE TABLE IF NOT EXISTS div_cache(mkt TEXT NOT NULL, sym TEXT NOT NULL, data TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY(mkt, sym))")
        rt.db.x("""CREATE TABLE IF NOT EXISTS div_plans(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, model TEXT, usd REAL,
            params TEXT, plan TEXT, text TEXT)""")
        self._mem = {}

    # ---------- 종목 하나 ----------
    def _fetch(self, mkt, sym):
        m = self.rt.market
        res = m._yh_chart(ysym(mkt, sym, m.kr_market), "10y", "1mo", events="div")
        meta = res.get("meta") or {}
        off = meta.get("gmtoffset") or 0
        kind = "ETF" if str(meta.get("instrumentType") or "").upper() in ("ETF", "MUTUALFUND") else "EQUITY"
        divs = []
        for v in ((res.get("events") or {}).get("dividends") or {}).values():
            try:
                d = datetime.fromtimestamp(int(v["date"]) + off, timezone.utc).date()
                divs.append((d, float(v["amount"])))
            except (KeyError, TypeError, ValueError):
                continue
        price = meta.get("regularMarketPrice")
        if not price:
            closes = [c for c in (((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []) if c]
            price = closes[-1] if closes else None
        out = analyze(divs, price, mkt, kind)
        out.update(price=price, currency=meta.get("currency") or ("USD" if mkt == "us" else "KRW"), kind=kind,
                   yname=meta.get("shortName") or meta.get("longName") or "")
        return out

    def info(self, mkt, sym, force=False):
        """종목의 배당 자료(하루 한 번 갱신). 실패하면 error를 담아 돌려준다(화면이 멈추지 않게)."""
        import time
        mkt = "us" if mkt == "us" else "kr"
        sym = str(sym).strip().upper() if mkt == "us" else re.sub(r"\D", "", str(sym))[-6:]
        key, now = (mkt, sym), time.time()
        hit = self._mem.get(key)
        if hit is None:
            r = self.rt.db.one("SELECT data, at FROM div_cache WHERE mkt=? AND sym=?", key)
            if r:
                hit = (json.loads(r["data"]), r["at"])
                self._mem[key] = hit
        if hit and not force and now - hit[1] < (self.TTL_ERR if hit[0].get("error") else self.TTL):
            return self._decorate(mkt, sym, hit[0])
        try:
            data = self._fetch(mkt, sym)
        except Exception as e:
            if hit and not hit[0].get("error"):          # 새로 받기 실패: 예전 자료를 계속 쓴다
                self._mem[key] = (hit[0], now - self.TTL + self.TTL_ERR)
                return self._decorate(mkt, sym, dict(hit[0], stale=True))
            data = {"error": f"배당 자료를 받지 못했어요 ({type(e).__name__})", "ttm": 0, "yield": None, "freq": 0, "months": [0] * 12,
                    "growth5": None, "growth3": None, "streak": 0, "years": [], "last": None, "price": None,
                    "currency": "USD" if mkt == "us" else "KRW", "kind": "", "yname": ""}
        data["at"] = datetime.now().isoformat(timespec="minutes")
        self._mem[key] = (data, now)
        self.rt.db.x("INSERT OR REPLACE INTO div_cache(mkt,sym,data,at) VALUES(?,?,?,?)", (mkt, sym, json.dumps(data, ensure_ascii=False), now))
        return self._decorate(mkt, sym, data)

    def _decorate(self, mkt, sym, data):
        u = next((x for x in UNIVERSE if x[0] == mkt and x[1] == sym), None)
        c = next((x for x in self.custom() if x["mkt"] == mkt and x["sym"] == sym), None)
        name = (u[2] if u else None) or (c or {}).get("name") or data.get("yname") or sym
        cat = u[3] if u else (("us_etf" if data.get("kind") == "ETF" else "us_stock") if mkt == "us" else ("kr_etf" if data.get("kind") == "ETF" else "kr_stock"))
        return dict(data, mkt=mkt, sym=sym, name=name, cat=cat, cat_name=CATS.get(cat, ""), note=u[4] if u else "",
                    custom=bool(c and not u), growth=default_growth(data), freq_name={12: "월배당", 4: "분기", 2: "반기", 1: "연 1회"}.get(data.get("freq"), "-"))

    def many(self, pairs, force=False, deadline=25):
        """여러 종목을 한꺼번에(동시에 8개씩). 바깥 서버가 느려도 화면이 오래 멈추지 않게, 정해진 시간 안에 못 받은 종목은
        '받는 중'으로 돌려주고 뒤에서 계속 받아 둔다(다음에 열면 저장본이 나온다)."""
        from concurrent.futures import wait
        pairs = [("us" if m == "us" else "kr", s) for m, s in pairs]
        uniq = list(dict.fromkeys(pairs))
        ex = ThreadPoolExecutor(max_workers=8)
        futs = {p: ex.submit(self.info, p[0], p[1], force) for p in uniq}
        wait(list(futs.values()), timeout=deadline)
        ex.shutdown(wait=False)
        out = {}
        for p, f in futs.items():
            if f.done() and not f.exception():
                out[p] = f.result()
            else:
                out[p] = self._decorate(p[0], str(p[1]).strip().upper(), {
                    "error": "배당 자료를 아직 받는 중이에요. 잠시 뒤 다시 열어 주세요", "pending": True, "ttm": 0, "yield": None, "freq": 0,
                    "months": [0] * 12, "growth5": None, "growth3": None, "streak": 0, "years": [], "last": None, "price": None,
                    "currency": "USD" if p[0] == "us" else "KRW", "kind": "", "yname": ""})
        return [out[p] for p in pairs]

    # ---------- 후보 목록 ----------
    def custom(self):
        return self.rt.db.get_setting("div_custom") or []

    def add_custom(self, mkt, sym, name=""):
        mkt = "us" if mkt == "us" else "kr"
        sym = str(sym).strip().upper() if mkt == "us" else re.sub(r"\D", "", str(sym))[-6:]
        if not sym or (mkt == "kr" and len(sym) != 6):
            raise ValueError("종목 코드를 확인해 주세요")
        cur = [x for x in self.custom() if not (x["mkt"] == mkt and x["sym"] == sym)]
        if any(u[0] == mkt and u[1] == sym for u in UNIVERSE):
            return self.info(mkt, sym)
        if len(cur) >= 40:
            raise ValueError("직접 넣은 종목은 40개까지예요")
        if mkt == "kr" and not name:
            try:
                name = (self.rt.market.kr_quote(sym) or {}).get("name") or ""
            except Exception:
                name = ""
        cur.append({"mkt": mkt, "sym": sym, "name": str(name)[:40]})
        self.rt.db.set_setting("div_custom", cur)
        return self.info(mkt, sym, force=True)

    def remove_custom(self, mkt, sym):
        self.rt.db.set_setting("div_custom", [x for x in self.custom() if not (x["mkt"] == mkt and x["sym"] == sym)])

    def fx(self):
        try:
            return float(self.rt.market.yh_quote("USDKRW=X")["price"]) or 1400.0
        except Exception:
            return 1400.0

    def universe(self, force=False):
        pairs = [(u[0], u[1]) for u in UNIVERSE] + [(c["mkt"], c["sym"]) for c in self.custom()]
        items = self.many(pairs, force)
        by = {(i["mkt"], i["sym"]): i for i in items}
        presets = []
        for p in PRESETS:
            assets = [self._asset(by[(m, s)], w) for m, s, w in p["assets"] if (m, s) in by]
            y = sum(a["weight"] * (a["yield"] or 0) for a in assets) / 100
            presets.append({"id": p["id"], "name": p["name"], "risk": p["risk"], "desc": p["desc"], "assets": assets, "yield": round(y, 2),
                            "missing": sum(1 for a in assets if not a["yield"])})
        return {"items": items, "cats": CATS, "presets": presets, "fx": self.fx(), "tax": {"kr": TAX_KR, "us": TAX_US},
                "goal": self.goal(), "failed": sum(1 for i in items if i.get("error")), "line": FIN_INCOME_LINE}

    @staticmethod
    def _asset(i, weight):
        """시뮬레이션에 넣는 종목 한 줄."""
        return {"mkt": i["mkt"], "sym": i["sym"], "name": i["name"], "cat": i["cat"], "weight": weight, "yield": i.get("yield"),
                "growth": i.get("growth") or 0, "months": i.get("months"), "freq": i.get("freq"), "freq_name": i.get("freq_name"), "error": i.get("error")}

    def goal(self):
        return int(self.rt.db.get_setting("div_goal") or 0)

    def set_goal(self, v):
        self.rt.db.set_setting("div_goal", int(max(0, min(float(v or 0), 1e9))))

    # ---------- 내 보유 종목의 배당 ----------
    def mine(self):
        acc = self.rt.account
        if not acc.configured():
            return {"configured": False, "rows": [], "months": [0] * 12, "annual_net": 0, "annual_gross": 0, "goal": self.goal()}
        snap = acc.snapshot()
        hold = [h for h in (snap.get("holdings") or []) if h.get("qty")]
        fx = (snap.get("us") or {}).get("fx") or self.fx()
        infos = self.many([("us" if h.get("market") == "us" else "kr", h["code"]) for h in hold])
        rows, months, gross_t, net_t, value_t, cost_t = [], [0.0] * 12, 0.0, 0.0, 0.0, 0.0
        for h, i in zip(hold, infos):
            us = h.get("market") == "us"
            k = fx if us else 1
            gross = h["qty"] * (i.get("ttm") or 0) * k
            net = gross * (1 - (TAX_US if us else TAX_KR) / 100)
            cost = h["qty"] * (h.get("avg_price") or 0) * k
            for m in range(12):
                months[m] += net * (i.get("months") or [0] * 12)[m]
            gross_t, net_t, value_t, cost_t = gross_t + gross, net_t + net, value_t + (h.get("value") or 0), cost_t + cost
            rows.append({"mkt": i["mkt"], "sym": i["sym"], "name": h.get("name") or i["name"], "qty": h["qty"], "value": h.get("value") or 0,
                         "gross": round(gross), "net": round(net), "yield": i.get("yield"), "yoc": round(gross / cost * 100, 2) if cost else None,
                         "freq": i.get("freq"), "freq_name": i.get("freq_name"), "months": i.get("months"), "growth": i.get("growth"),
                         "next_ex": i.get("next_ex"), "last": i.get("last"), "error": i.get("error"), "cat": i["cat"]})
        rows.sort(key=lambda r: -r["net"])
        goal = self.goal()
        return {"configured": True, "ok": snap.get("ok", True), "rows": rows, "months": [round(x) for x in months], "annual_gross": round(gross_t),
                "annual_net": round(net_t), "monthly_net": round(net_t / 12), "value": round(value_t), "total": snap.get("total"),
                "yield": round(gross_t / value_t * 100, 2) if value_t else None, "yoc": round(gross_t / cost_t * 100, 2) if cost_t else None,
                "goal": goal, "goal_pct": round(net_t / 12 / goal * 100, 1) if goal else None,
                "need": round((goal * 12 - net_t) / (net_t / value_t)) if goal and net_t > 0 and value_t and goal * 12 > net_t else None,
                "payers": sum(1 for r in rows if r["net"] > 0), "fx": fx, "line": FIN_INCOME_LINE}

    # ---------- 시뮬레이션 ----------
    def run_sim(self, d):
        """화면에서 온 구성(종목·비중)에 실제 배당 자료를 붙여 계산한다. 사용자가 고친 값(yield·growth)이 있으면 그 값을 쓴다."""
        want = [a for a in (d.get("assets") or []) if a.get("sym")][:20]
        infos = self.many([(a.get("mkt"), a["sym"]) for a in want])
        assets = []
        for a, i in zip(want, infos):
            x = self._asset(i, float(a.get("weight") or 0))
            if a.get("yield") not in (None, ""):
                x["yield"], x["edited"] = float(a["yield"]), True
            if a.get("growth") not in (None, ""):
                x["growth"] = float(a["growth"])
            assets.append(x)
        p = dict(d, assets=assets, goal=d.get("goal") if d.get("goal") is not None else self.goal())
        out = simulate(p)
        out["fx_now"] = self.fx()
        return out

    # ---------- AI 포트폴리오 ----------
    def ai_context(self, d):
        amount = float(d.get("amount") or 0)
        if amount <= 0:
            raise ValueError("투자금을 넣어 주세요")
        uni = self.universe()
        rows = []
        for i in uni["items"]:
            if i.get("error") or not i.get("yield"):
                continue
            pays = [m + 1 for m, v in enumerate(i.get("months") or []) if v > 0.01]
            rows.append({"sym": i["sym"], "mkt": i["mkt"], "name": i["name"], "분류": i["cat_name"], "배당률%(세전, 최근 1년)": i["yield"],
                         "지급": i["freq_name"], "지급월(추정)": pays, "5년 배당성장률%": i.get("growth5"), "3년 배당성장률%": i.get("growth3"),
                         "배당 안 줄인 연속 햇수": i.get("streak"), "설명": i.get("note")})
        if len(rows) < 3:
            raise ValueError("배당 자료를 아직 받지 못했어요. 잠시 뒤 다시 해 주세요")
        try:
            mine = self.mine()
            held = [{"종목": r["name"], "평가액": r["value"], "연 배당(세후)": r["net"]} for r in mine["rows"][:12]] if mine.get("configured") else None
        except Exception:
            held = None
        goal = float(d.get("goal") or self.goal() or 0)
        style = {"growth": "배당 성장(지금 배당은 적어도 해마다 늘어나는 쪽)", "income": "매월 현금흐름(지금 받는 돈을 크게)",
                 "balance": "균형(성장과 현금흐름 절반씩)", "high": "고분배(커버드콜 포함, 원금 변동 감수)"}.get(d.get("style"), "균형")
        region = {"us": "미국 상장 종목 위주", "kr": "국내 상장 종목만(환전 없이)", "mix": "국내와 미국을 섞어서"}.get(d.get("region"), "국내와 미국을 섞어서")
        data = {"투자금(원)": round(amount), "매월 추가 투자(원)": round(float(d.get("monthly_add") or 0)), "목표 월 배당(세후, 원)": round(goal) or "정하지 않음",
                "원하는 방향": style, "지역": region, "투자 기간(년)": int(d.get("years") or 10), "배당 재투자": "함" if d.get("reinvest", True) else "안 함(생활비로 씀)",
                "사용자 메모": str(d.get("memo") or "")[:300] or None, "환율(원/달러)": round(uni["fx"]), "세율 가정%": {"국내": TAX_KR, "미국": TAX_US},
                "금융소득종합과세 기준(연, 세전)": FIN_INCOME_LINE, "후보 목록(실제 최근 1년 지급 기준)": rows, "지금 보유 종목의 배당": held}
        ask = (f"투자금 {round(amount):,}원으로 배당 포트폴리오를 짜 줘. 방향: {style}. 지역: {region}. "
               "후보 목록의 실제 숫자를 근거로 종목과 비중을 정하고, 매달 들어오는 돈이 고르게 되도록 지급월을 따져. "
               "세후 원화 기준으로 첫해에 한 달 평균 얼마쯤 들어오는지, 연 금융소득 2천만 원 기준을 넘는지 판단 글에 적어.")
        if goal:
            ask += f" 목표는 세후 월 {round(goal):,}원이야. 이 투자금으로 가능한지, 안 되면 얼마가 더 필요하거나 몇 년 재투자해야 하는지도 말해 줘."
        return "AI 배당 포트폴리오", ask, {k: v for k, v in data.items() if v is not None}

    @staticmethod
    def read_plan(text):
        """AI 답에서 구성 JSON을 읽는다. 코드 블록이 없거나, 쉼표가 남았거나, 끝이 잘렸어도 종목 줄은 살려 낸다."""
        text = text or ""
        cands = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        i = text.find("{", max(0, text.rfind("```json")))
        if i >= 0:
            cands.append(text[i:text.rfind("}") + 1] if text.rfind("}") > i else text[i:])
        k = text.find('"assets"')
        if k >= 0 and text.rfind("{", 0, k) >= 0:
            cands.append(text[text.rfind("{", 0, k):text.rfind("}") + 1])
        for c in reversed(cands):
            for fix in (c, re.sub(r",\s*([}\]])", r"\1", c)):
                try:
                    j = json.loads(fix)
                except ValueError:
                    continue
                if isinstance(j, dict) and isinstance(j.get("assets"), list) and j["assets"]:
                    return j
        # 끝이 잘린 답: 온전한 종목 줄({...sym...weight...})만 모은다
        rows = []
        for m in re.finditer(r"\{[^{}]*\"sym\"[^{}]*\}", text, re.S):
            try:
                a = json.loads(re.sub(r",\s*}", "}", m.group(0)))
            except ValueError:
                continue
            if a.get("sym") and a.get("weight") not in (None, ""):
                rows.append(a)
        if len(rows) >= 2:
            pick = lambda key: (re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % key, text) or [None, ""])[1]
            return {"title": pick("title"), "summary": pick("summary"), "assets": rows, "rebalance": pick("rebalance"), "partial": True}
        raise ValueError("AI 답에서 포트폴리오 표를 읽지 못했어요. 다시 받아 주세요")

    def parse_plan(self, text, d, model, usd):
        j = self.read_plan(text)
        want = []
        for a in j["assets"][:10]:
            sym = str(a.get("sym") or "").strip().upper()
            mkt = "kr" if (a.get("mkt") == "kr" or re.fullmatch(r"\d{6}", sym)) else "us"
            try:
                w = float(str(a.get("weight") or 0).replace("%", "").strip())
            except (TypeError, ValueError):
                w = 0
            if sym and w > 0:
                want.append((mkt, sym, w, str(a.get("role") or "")[:8], str(a.get("reason") or "")[:160], str(a.get("name") or "")[:40]))
        infos = self.many([(x[0], x[1]) for x in want])
        assets = []
        for x, i in zip(want, infos):
            if i.get("error") and not any(u[0] == x[0] and u[1] == x[1] for u in UNIVERSE):
                continue                                   # 자료를 못 찾은 후보 밖 종목(잘못된 티커일 수 있음)은 뺀다
            a = self._asset(i, x[2])
            if i["name"] == i["sym"] and x[5]:
                a["name"] = x[5]
            a.update(role=x[3], reason=x[4])
            assets.append(a)
        if not assets:
            raise ValueError("AI가 고른 종목의 자료를 찾지 못했어요. 다시 받아 주세요")
        tw = sum(a["weight"] for a in assets)
        for a in assets:
            a["weight"] = round(a["weight"] / tw * 100, 1)
        y = sum(a["weight"] * (a["yield"] or 0) for a in assets) / 100
        plan = {"title": str(j.get("title") or "AI 배당 구성")[:30], "summary": str(j.get("summary") or "")[:160], "assets": assets,
                "yield": round(y, 2), "rebalance": str(j.get("rebalance") or "")[:160],
                "risks": [str(r)[:160] for r in (j.get("risks") or [])][:5], "notes": [str(r)[:160] for r in (j.get("notes") or [])][:5]}
        if j.get("partial"):
            plan["notes"] = (plan["notes"] + ["AI 답의 끝부분이 잘려, 읽을 수 있는 종목만으로 비중을 100%에 맞췄어요"])[:5]
        keep = {k: d.get(k) for k in ("amount", "monthly_add", "years", "reinvest", "style", "region", "goal", "memo")}
        pid = self.rt.db.x("INSERT INTO div_plans(ts,model,usd,params,plan,text) VALUES(?,?,?,?,?,?)",
                           (datetime.now().isoformat(timespec="seconds"), model, usd, json.dumps(keep, ensure_ascii=False),
                            json.dumps(plan, ensure_ascii=False), text.split("```json")[0].strip()))
        return dict(plan, id=pid, params=keep)

    def plans(self):
        out = []
        for r in self.rt.db.q("SELECT * FROM div_plans ORDER BY id DESC LIMIT 20"):
            out.append({"id": r["id"], "ts": r["ts"], "model": r["model"], "usd": r["usd"], "text": r["text"],
                        "params": json.loads(r["params"] or "{}"), **json.loads(r["plan"] or "{}")})
        return out

    def delete_plan(self, pid):
        self.rt.db.x("DELETE FROM div_plans WHERE id=?", (pid,))
