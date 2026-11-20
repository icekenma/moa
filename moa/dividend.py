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
# 건강보험료(2026년 기준. 해마다 바뀌므로 어림값으로만 쓴다)
HEALTH_RATE = 7.19            # 건강보험료율(%)
CARE_RATE = 13.14             # 장기요양보험료: 건강보험료에 곱하는 비율(%)
HEALTH_LOCAL_LINE = 10_000_000   # 지역가입자: 이자·배당이 연 1천만 원을 넘으면 '전액'이 보험료 대상(이하면 대상 아님)
HEALTH_WORK_LINE = 20_000_000    # 직장가입자: 월급 외 소득이 연 2천만 원을 넘는 '초과분'만 대상
US_GAIN_FREE, US_GAIN_TAX = 2_500_000, 22.0   # 미국 상장 종목을 팔아 남긴 이익: 연 250만 원 공제 뒤 22%
# 하락장 가정: 주가가 1년에 걸쳐 35% 내리고, 1년 머문 뒤, 3년에 걸쳐 돌아온다. 커버드콜은 반등을 덜 따라가 85%까지만 돌아온다고 본다
SHOCK = {"drop": 35, "down": 12, "flat": 12, "up": 36, "cc_back": 0.85}

CATS = {
    "us_etf": "미국 배당 ETF", "us_cc": "미국 커버드콜·고분배", "us_stock": "미국 배당 성장주", "us_month": "미국 월배당 리츠·BDC",
    "us_bond": "미국 단기채(현금성)", "us_growth": "미국 성장 ETF(배당 적음)", "kr_etf": "국내 상장 배당 ETF", "kr_cc": "국내 상장 커버드콜", "kr_stock": "국내 고배당주", "kr_reit": "국내 리츠·인프라",
}
# 주가 상승 가정을 얼마나 적용할지(1 = 그대로). 채권은 주가가 오르지 않는다고 본다. 커버드콜은 _pg에서 따로 계산한다
PRICE_FACTOR = {"us_bond": 0.0, "us_month": 0.6, "kr_reit": 0.4}
PRICE_ADD = {"us_growth": 3.0}        # 성장 ETF는 배당이 적은 대신 주가가 기본 가정보다 연 3%p 더 오른다고 본다
CASH_CATS, CC_CATS, GROWTH_CATS = ("us_bond",), ("us_cc", "kr_cc"), ("us_growth",)
# 같은 지수를 바탕으로 한 종목(겉은 달라도 함께 오르내린다). 쏠림 점검에 쓴다
BASE = {"나스닥100": ("QQQ", "QQQM", "QLD", "TQQQ", "JEPQ", "QYLD", "QQQI", "GPIQ"),
        "S&P500": ("VOO", "SPY", "IVV", "SPLG", "SPYM", "SSO", "UPRO", "JEPI", "XYLD", "SPYI", "GPIX"),
        "미국배당다우존스": ("SCHD", "458730", "446720", "402970", "458760", "441640")}
BASE_OF = {sym: name for name, syms in BASE.items() for sym in syms}

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
    ("us", "QQQI", "NEOS 나스닥100 고분배 ETF", "us_cc", "나스닥100 + 지수 옵션 프리미엄 월배당. 분배율이 높은 만큼 상승을 덜 따라감"),
    ("us", "QQQ", "Invesco 나스닥100 ETF", "us_growth", "나스닥 대형 기술주 100개. 배당은 거의 없고 주가 성장이 목적. 변동이 큼"),
    ("us", "VOO", "Vanguard S&P500 ETF", "us_growth", "미국 대형주 500개. 시장 전체를 사는 기본형"),
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
    # 모은 돈을 생활비로 쓰는 단계의 대표적인 짜임 다섯 가지('생활비로 살아 보기'에서 버티는지 따져 볼 출발점)
    {"id": "l_three", "group": "live", "name": "배당성장 + 커버드콜 + 성장", "risk": "보통", "desc": "생활비는 커버드콜·배당으로, 나머지는 성장에 두고, 현금을 조금 남겨요",
     "assets": [("us", "JEPQ", 42), ("us", "QQQ", 32), ("us", "SCHD", 20), ("us", "SGOV", 6)]},
    {"id": "l_ccg", "group": "live", "name": "커버드콜 + 성장", "risk": "높음", "desc": "매달 현금흐름과 성장을 함께. 둘 다 같은 지수면 떨어질 때도 함께 떨어져요",
     "assets": [("us", "QQQ", 60), ("us", "JEPQ", 40)]},
    {"id": "l_dcc", "group": "live", "name": "배당성장 + 커버드콜", "risk": "보통", "desc": "사고팔지 않고 배당만으로 지내는 짜임. 성장 몫이 작아 물가에 약해요",
     "assets": [("us", "JEPQ", 60), ("us", "SCHD", 40)]},
    {"id": "l_self", "group": "live", "name": "성장 + 현금(팔아서 쓰기)", "risk": "높음", "desc": "배당 대신 조금씩 팔아 생활비로. 하락장에는 현금 몫을 먼저 써요",
     "assets": [("us", "QQQ", 50), ("us", "VOO", 30), ("us", "SGOV", 20)]},
    {"id": "l_cc", "group": "live", "name": "커버드콜만", "risk": "높음", "desc": "지금 받는 돈은 가장 크지만, 상승을 덜 따라가고 분배금이 줄 수 있어요",
     "assets": [("us", "JEPQ", 60), ("us", "JEPI", 40)]},
]

DIV_SYSTEM = """너는 한국 개인 투자자의 앱(moa)에 들어 있는 배당 포트폴리오 설계 분석가야.
목표: 사용자의 투자금·목표·성향에 맞춰, 세금을 뺀 뒤 원화로 매달 들어오는 배당이 안정적으로 이어지고 늘어나는 구성을 제안하는 것.

원칙
- 후보 목록의 숫자(배당률·성장률·지급 횟수·지급월)는 실제 최근 지급 자료에서 계산한 값이야. 이 숫자와 검색으로 확인한 사실만 근거로 써. 없는 숫자를 지어내지 마.
- 후보에 없는 종목도 넣을 수 있어. 미국은 티커, 국내는 6자리 코드를 정확히 적어.
- 배당률만 높은 종목을 쫓지 마. 배당이 줄어든 이력, 원금이 깎여 온 커버드콜, 한 업종 쏠림을 반드시 따져.
- 한 종목은 ETF 50%, 개별주 15%를 넘기지 마. 커버드콜·고분배 상품은 다 합쳐 30% 이하(사용자가 '고분배'를 골랐을 때만 40%까지).
- 매달 받는 돈이 고르게 들어오도록 지급월이 겹치지 않게 섞어.
- 생활비가 주어지면 그 돈이 세후로 나오게 하는 것이 먼저야. 배당은 필요한 만큼만 만들고, 남는 자금은 성장 자산(role "성장")에 둬. 필요 이상으로 배당을 키우면 세금과 건강보험료만 늘어.
- 같은 지수를 바탕으로 한 종목끼리 몰리면(예: 나스닥100 커버드콜 + 나스닥100 성장 ETF) 오를 때도 내릴 때도 함께 움직여. 이런 쏠림은 risks에 반드시 적어.
- 생활비로 쓰는 구성이면 하락장에 싼값에 팔지 않도록 현금성 자산(role "완충")을 생활비 몇 개월치로 둘지 판단해서 넣어.
- 건강보험: 직장이 없으면(지역가입자) 이자·배당이 연 1천만 원을 넘는 순간 전액이 보험료 대상이고, 직장가입자는 월급 외 소득 2천만 원 초과분이 대상이야(제도는 바뀔 수 있어). 해당하면 notes에 금액으로 적어.
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
    if len(divs) >= 2:      # 직전 지급: 월배당은 앞 3번 평균(달마다 출렁여서), 그 밖에는 바로 앞 지급과 비교한다
        prev = [a for _, a in divs[-4:-1]] if out["freq"] == 12 else [divs[-2][1]]
        out["prev"] = {"date": divs[-2][0].isoformat(), "amt": round(sum(prev) / len(prev), 6), "n": len(prev)}
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
        pg_a = _pg(a, price_growth)
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
                   "pg": pg_a, "cc": a.get("cat") in CC_CATS,
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
                # 커버드콜 분배금은 주가(순자산)에 비례한다: 원금이 줄면 분배금도 같이 준다
                up = (1 + s["pg"]) ** (t / 12) if s["cc"] else s["fac"][k]
                dps = s["y0"] * up * (1 - cut / 100) * (fxm if s["us"] else 1)
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


def yield_band(divs, bars, price, today=None, months=60):
    """지난 5년 월말 주가와 그때까지 1년 배당으로 '그 달의 배당률'을 만들고, 지금 배당률이 그 안에서 어디쯤인지.
    bars: [(date, 종가)] 오름차순. pct = 지금보다 배당률이 낮았던 달의 비율(높을수록 지금 배당률이 높은 편 = 주가가 싼 편)."""
    today = today or date.today()
    divs = sorted((d, float(a)) for d, a in divs if a and a > 0)
    ys = []
    for d, c in bars[-months:]:
        if not c:
            continue
        t = sum(a for x, a in divs if d - timedelta(days=365) < x <= d)
        if t:
            ys.append((d, round(t / c * 100, 2)))
    if len(ys) < 24:
        return None
    t_now = sum(a for x, a in divs if x > today - timedelta(days=365))
    cur = round(t_now / price * 100, 2) if price and t_now else None
    if cur is None:
        return None
    vs = sorted(v for _, v in ys)
    return {"cur": cur, "lo": vs[0], "hi": vs[-1], "med": vs[len(vs) // 2], "n": len(vs),
            "pct": round(sum(1 for v in vs if v < cur) / len(vs) * 100), "series": [v for _, v in ys], "from": ys[0][0].isoformat()[:7]}


def change_kind(new, prev, freq):
    """새 배당이 직전보다 얼마나 바뀌었나. 월배당(커버드콜 등)은 달마다 출렁여 기준을 넓게 본다."""
    if not prev:
        return "first", None
    chg = round((new / prev - 1) * 100, 1)
    th = 15 if freq == 12 else 3
    return ("raise" if chg >= th else "cut" if chg <= -th else "same"), chg


def health_fee(gross_year, kind):
    """배당(세전, 연)에 붙는 건강보험료 어림(연, 장기요양 포함). 소득분만 본다 — 지역가입자의 재산분, 다른 소득은 넣지 않는다."""
    rate = HEALTH_RATE / 100 * (1 + CARE_RATE / 100)
    if kind == "work":
        return max(0.0, gross_year - HEALTH_WORK_LINE) * rate
    if kind == "local":
        return gross_year * rate if gross_year > HEALTH_LOCAL_LINE else 0.0
    return 0.0


def overlap(assets):
    """같은 지수에 몰린 정도. 절반 이상 몰린 지수만 돌려준다."""
    by = {}
    for a in assets:
        b = BASE_OF.get(str(a["sym"]).upper())
        if b:
            x = by.setdefault(b, {"base": b, "weight": 0.0, "syms": [], "kinds": set()})
            x["weight"] += a["weight"]
            x["syms"].append(a["sym"])
            x["kinds"].add("cc" if a.get("cat") in CC_CATS else "growth" if a.get("cat") in GROWTH_CATS else "etc")
    out = []
    for x in by.values():
        if x["weight"] >= 50 and len(x["syms"]) >= 2:
            out.append({"base": x["base"], "weight": round(x["weight"], 1), "syms": x["syms"], "both": {"cc", "growth"} <= x["kinds"]})
    return sorted(out, key=lambda x: -x["weight"])


def _pg(a, price_growth):
    """종목 한 줄의 주가 상승 가정(연, 소수).
    커버드콜·고분배는 '분배금 + 주가'를 합친 수익이 성장 ETF의 주가 상승 가정과 같다고 본다(옵션을 판 대가로 상승을 내주므로
    지수보다 더 벌 수는 없다). 그래서 분배율이 그 수익보다 높으면 그만큼 주가(원금)가 해마다 줄어든다."""
    cat = a.get("cat")
    if cat in CC_CATS:
        return (price_growth + PRICE_ADD["us_growth"] - (a.get("yield") or 0)) / 100
    return (price_growth * PRICE_FACTOR.get(cat, 1.0) + PRICE_ADD.get(cat, 0.0)) / 100


def _shock(t, cat):
    """하락장 가정에서 t개월째 주가가 평소 경로의 몇 배인지."""
    if cat in CASH_CATS:
        return 1.0
    d, a, b, c = SHOCK["drop"] / 100, SHOCK["down"], SHOCK["flat"], SHOCK["up"]
    end = SHOCK["cc_back"] if cat in CC_CATS else 1.0
    if t <= a:
        return 1 - d * t / a
    if t <= a + b:
        return 1 - d
    if t <= a + b + c:
        return (1 - d) + (end - (1 - d)) * (t - a - b) / c
    return end


def _live(assets, amount, years, spend, inflation, other, other_from, health, price_growth, growth_scale, tax_kr, tax_us, fx_change, shock=False):
    """모은 돈을 생활비로 쓰는 경우를 달마다 계산한다.
    배당(세후)으로 먼저 생활비를 대고, 모자라면 현금성 자산 → 나머지 순으로 판다. 남으면 목표 비중대로 다시 산다.
    건강보험료는 지난해 배당 기준으로, 미국 종목을 팔아 남긴 이익의 세금은 이듬해에 낸다. 반환: (연도별 목록, 바닥난 해 또는 None)."""
    st = []
    for a in assets:
        w = a["weight"] / 100
        g = (a.get("growth") or 0) / 100 * growth_scale
        pg_a = _pg(a, price_growth)
        g_long = max(0.0, min(g, pg_a + 0.01))
        fac, f = [], 1.0
        for k in range(years):
            if k > 0:
                f *= 1 + (g if k <= FADE_YEARS else g_long)
            fac.append(f)
        st.append({"cat": a.get("cat"), "n": amount * w, "cost": amount * w, "w": w, "y0": (a.get("yield") or 0) / 100, "fac": fac, "pg": pg_a,
                   "tax": (tax_us if a["mkt"] == "us" else tax_kr) / 100, "us": a["mkt"] == "us", "cash": a.get("cat") in CASH_CATS,
                   "cc": a.get("cat") in CC_CATS})
    yearly, dead = [], None
    gross_prev = sum(s["n"] * s["y0"] for s in st)          # 첫해 건강보험료는 지금 배당 수준으로 어림
    cg_due = 0.0
    for k in range(years):
        fee_y = health_fee(gross_prev, health)
        infl = (1 + inflation / 100) ** k
        need_m = spend * infl
        other_m = other * infl if other > 0 and k + 1 >= other_from else 0.0
        yr = {"year": k + 1, "gross": 0.0, "div": 0.0, "sell": 0.0, "cg": cg_due}
        gain_us = 0.0
        for j in range(12):
            t = k * 12 + j
            fxm = (1 + fx_change / 100) ** (t / 12)
            net = 0.0
            for s in st:
                sm = _shock(t, s["cat"]) if shock else 1.0
                s["p"] = (1 + s["pg"]) ** (t / 12) * (fxm if s["us"] else 1) * sm
                up = (1 + s["pg"]) ** (t / 12) * sm if s["cc"] else s["fac"][k]                 # 커버드콜 분배금은 주가(순자산)를 따라간다
                dps = s["y0"] * up * (fxm if s["us"] else 1)
                gross = s["n"] * dps / 12        # 달마다 고르게 들어온다고 본다(분기 배당의 빈 달은 그 사이 받은 배당으로 지낸다)
                yr["gross"] += gross
                net += gross * (1 - s["tax"])
            yr["div"] += net
            out = max(0.0, need_m - other_m) + fee_y / 12 + (cg_due if j == 0 else 0.0)
            if net >= out:
                extra = net - out
                for s in st:
                    s["n"] += extra * s["w"] / s["p"]
                    s["cost"] += extra * s["w"]
            else:
                short = out - net
                for group in ([s for s in st if s["cash"]], [s for s in st if not s["cash"]]):
                    tot = sum(s["n"] * s["p"] for s in group)
                    if tot <= 0 or short <= 0:
                        continue
                    take = min(short, tot)
                    f = take / tot
                    for s in group:
                        sold = s["n"] * s["p"] * f
                        if s["us"] and not s["cash"]:
                            gain_us += sold - s["cost"] * f
                        s["n"] *= 1 - f
                        s["cost"] *= 1 - f
                    yr["sell"] += take
                    short -= take
                if short > 1:
                    dead = k + 1
            if dead:
                break
        value = sum(s["n"] * s.get("p", 1.0) for s in st)
        cash = sum(s["n"] * s.get("p", 1.0) for s in st if s["cash"])
        needs = max(0.0, need_m - other_m) + fee_y / 12
        yearly.append({"year": k + 1, "need": round(need_m), "other": round(other_m), "fee": round(fee_y / 12), "cg": round(yr["cg"]),
                       "div": round(yr["div"] / 12), "sell": round(yr["sell"] / 12), "gross": round(yr["gross"]),
                       "cover": round(yr["div"] / 12 / needs * 100) if needs > 0 else 999, "value": 0 if dead else round(value), "cash": 0 if dead else round(cash)})
        if dead:
            break
        gross_prev = yr["gross"]
        cg_due = max(0.0, gain_us - US_GAIN_FREE) * US_GAIN_TAX / 100
    return yearly, dead


def live(p, assets, amount, pg, gs, tk, tu, fxc):
    """'생활비로 살아 보기': 이 구성으로 생활비를 쓰며 버티는지, 평소와 하락장 두 경우로 본다."""
    L = p.get("live") or {}
    try:
        spend = float(L.get("spend") or 0)
    except (TypeError, ValueError):
        spend = 0
    if spend <= 0 or amount <= 0:
        return None
    num = lambda k, d, lo, hi: min(max(float(L.get(k) if L.get(k) not in (None, "") else d), lo), hi)
    infl, other, o_from = num("inflation", 2.5, 0, 10), num("other", 0, 0, 1e9), int(num("other_from", 1, 1, 40))
    health = L.get("health") if L.get("health") in ("work", "local") else "local"
    years = int(num("years", 30, 5, 50))               # 생활비로 쓰는 기간은 모으는 기간과 따로 본다(기본 30년)
    kw = dict(assets=assets, amount=amount, years=years, spend=spend, inflation=infl, other=other, other_from=o_from, health=health,
              price_growth=pg, growth_scale=gs, tax_kr=tk, tax_us=tu, fx_change=fxc)
    base, dead = _live(**kw)
    bad, bad_dead = _live(shock=True, **kw)
    y1, b1 = base[0], bad[0]
    w = lambda cats: sum(a["weight"] for a in assets if a.get("cat") in cats)
    cash0 = amount * w(CASH_CATS) / 100
    gap = lambda r: max(0, r["need"] - r["other"] + r["fee"] - r["div"])
    worst_gap = max(gap(r) for r in bad[:3])                               # 하락장 첫 3년 가운데 한 달에 가장 많이 모자란 돈
    sells = any(r["sell"] > 0 for r in base)
    code = "out" if dead else "mix" if sells else "div"
    net_need = max(0.0, y1["need"] - y1["other"]) + y1["fee"]
    tips = []
    for o in overlap(assets):
        tips.append({"kind": "overlap", "text": f"{o['base']} 한 지수에 {o['weight']:g}%가 몰려 있어요({', '.join(o['syms'])}). "
                     + ("커버드콜과 성장 ETF가 같은 지수라 " if o["both"] else "") + "오를 때도 같이 오르지만 떨어질 때도 같이 떨어져요."})
    cc_w = w(CC_CATS)
    if cc_w >= 50:
        tips.append({"kind": "cc", "text": f"커버드콜·고분배가 {cc_w:g}%예요. 상승을 덜 따라가고, 하락 뒤에는 분배금과 원금이 함께 줄 수 있어요. 하락장 결과를 꼭 같이 보세요."})
    if y1["div"] > net_need * 1.3 and net_need > 0:
        tips.append({"kind": "over", "text": f"배당이 생활비보다 한 달 {round((y1['div'] - net_need) / 1e4):,}만 원쯤 많아요. 쓰지 않을 배당은 세금"
                     + ("·건강보험료" if y1["fee"] else "") + "만 늘리니, 그 몫은 성장 자산으로 옮기는 것도 방법이에요."})
    if health == "local" and HEALTH_LOCAL_LINE < y1["gross"] <= HEALTH_LOCAL_LINE * 1.3:
        tips.append({"kind": "health", "text": f"연 배당(세전) {round(y1['gross'] / 1e4):,}만 원이 1천만 원을 조금 넘어, 전액이 건강보험료 대상이 돼요(한 달 약 {y1['fee']:,}원). 1천만 원 아래로 맞추면 이 보험료는 0이에요."})
    elif y1["fee"] > 0:
        tips.append({"kind": "health", "text": f"배당에 붙는 건강보험료가 한 달 약 {y1['fee']:,}원이에요({'지역가입자: 연 1천만 원을 넘어 전액 대상' if health == 'local' else '직장가입자: 2천만 원 초과분'}). 생활비에서 빼고 계산했어요."})
    if worst_gap > 0:
        if cash0 <= 0:
            tips.append({"kind": "cushion", "text": f"현금성 자산이 없어요. 하락장에는 한 달 {round(worst_gap / 1e4):,}만 원쯤을 싼값에 팔아 메워야 해요. 생활비 1~2년치를 단기채 같은 현금성 자산으로 두는 방법이 있어요."})
        elif cash0 / worst_gap < 12:
            tips.append({"kind": "cushion", "text": f"현금성 자산이 하락장에 모자라는 돈의 {cash0 / worst_gap:.0f}개월치예요. 주가가 돌아오기 전에 바닥나면 싼값에 팔아야 해요."})
    return {"spend": round(spend), "inflation": infl, "other": round(other), "other_from": o_from, "health": health,
            "code": code, "dead": dead, "years": years, "yearly": base, "bad": bad, "bad_dead": bad_dead,
            "first": y1, "bad_first": b1, "rate": round(y1["sell"] * 12 / amount * 100, 2),
            "cash": {"value": round(cash0), "months": round(cash0 / worst_gap, 1) if worst_gap > 0 and cash0 > 0 else None, "gap": round(worst_gap)},
            "alone": {"spend": max(0, round(y1["div"] + y1["other"] - y1["fee"])),                 # 배당(과 다른 수입)만으로 낼 수 있는 한 달 생활비
                      "amount": round(amount * net_need / y1["div"]) if y1["div"] > 0 else None},  # 배당만으로 생활비를 대려면 필요한 투자금(같은 구성 기준)
            "last": {"value": base[-1]["value"], "bad_value": bad[-1]["value"]}, "tips": tips,
            "shock": {"drop": SHOCK["drop"], "down": SHOCK["down"] // 12, "flat": SHOCK["flat"] // 12, "up": SHOCK["up"] // 12, "cc_back": round(SHOCK["cc_back"] * 100)},
            "rates": {"health": round(HEALTH_RATE * (1 + CARE_RATE / 100), 2), "local_line": HEALTH_LOCAL_LINE, "work_line": HEALTH_WORK_LINE,
                      "gain_free": US_GAIN_FREE, "gain_tax": US_GAIN_TAX}}


def simulate(p, today=None):
    """p: {amount, monthly_add, years, reinvest, price_growth, growth_scale, tax_kr, tax_us, fx_change, goal, assets:[...]}"""
    today = today or date.today()
    assets = [a for a in (p.get("assets") or []) if (a.get("weight") or 0) > 0]
    if not assets:
        raise ValueError("구성에 종목을 하나 이상 넣어 주세요")
    tw = sum(a["weight"] for a in assets)
    assets = [dict(a, weight=a["weight"] / tw * 100) for a in assets]
    amount = max(0.0, float(p.get("amount") or 0))
    add = max(0.0, float(p.get("monthly_add") or 0))
    if amount <= 0 and add <= 0:                       # 처음 넣는 돈 없이 매달 적립만 하는 경우도 계산한다
        raise ValueError("투자금이나 매달 넣는 돈을 넣어 주세요")
    years = int(min(max(int(p.get("years") or 10), 1), 40))
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
                    "yield_net": round(y1["net"] / amount * 100, 2) if amount > 0 else None, "first_monthly": y1["monthly"], "first_net": y1["net"], "first_gross": y1["gross"],
                    "last_monthly": yn["monthly"], "last_value": yn["value"], "invested": yn["invested"],
                    "total_net": sum(r["net"] for r in yearly), "total_tax": sum(r["tax"] for r in yearly),
                    "other_last_monthly": other[-1]["monthly"], "other_last_value": other[-1]["value"],
                    "us_weight": round(us_w, 1), "min_month": min(r["net"] for r in months[:12]), "max_month": max(r["net"] for r in months[:12]),
                    "over_line_year": over, "missing": miss},
        "milestones": milestones, "scenarios": scen, "fx": fx_s, "cut": {"pct": 25, "first": cut_y[0]["monthly"], "last": cut_y[-1]["monthly"]},
        "assume": {"price_growth": pg, "growth_scale": gs, "tax_kr": tk, "tax_us": tu, "fx_change": fxc, "growth_add": PRICE_ADD["us_growth"]},
        "overlap": overlap(assets),
        "live": live(p, assets, amount, pg, gs, tk, tu, fxc),
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
        rt.db.x("""CREATE TABLE IF NOT EXISTS div_daily(day TEXT NOT NULL, mkt TEXT NOT NULL, sym TEXT NOT NULL, price REAL, ttm REAL, yld REAL,
            pct INTEGER, PRIMARY KEY(day, mkt, sym))""")
        rt.db.x("""CREATE TABLE IF NOT EXISTS div_events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, mkt TEXT NOT NULL, sym TEXT NOT NULL,
            name TEXT, ex TEXT NOT NULL, amt REAL, prev REAL, chg REAL, kind TEXT, held INTEGER NOT NULL DEFAULT 0, UNIQUE(mkt, sym, ex))""")
        self._mem = {}
        self._collecting = False

    # ---------- 매일 모으기 ----------
    def daily_due(self, now=None):
        """하루 한 번(아침 8시 뒤: 미국장까지 끝난 뒤) 후보·직접 넣은 종목·보유 종목의 배당 자료를 새로 받아 기록한다. 뒤에서 돈다."""
        now = now or datetime.now()
        day = now.strftime("%Y-%m-%d")
        if now.strftime("%H:%M") < "08:00" or self._collecting or self.rt.db.get_setting("div_daily_day") == day:
            return False
        self._collecting = True
        import threading

        def go():
            try:
                self.collect(day)
            except Exception as e:
                self.rt.db.log("system", "error", f"배당 자료 매일 모으기 오류: {type(e).__name__}: {e}", datetime.now().isoformat(timespec="seconds"))
            finally:
                self._collecting = False
        threading.Thread(target=go, daemon=True).start()
        return True

    def _held(self):
        try:
            acc = self.rt.account
            if not acc.configured():
                return {}
            snap = acc.snapshot()
            return {("us" if h.get("market") == "us" else "kr", str(h["code"]).strip().upper() if h.get("market") == "us" else str(h["code"])): h.get("name") or ""
                    for h in (snap.get("holdings") or []) if h.get("qty")}
        except Exception:
            return {}

    def collect(self, day=None):
        """모든 대상 종목을 새로 받아(배당률·배당률 위치) 그날 기록을 남기고, 새 배당이 나왔으면 직전과 비교해 변화(인상·삭감)를 적는다.
        보유 종목의 인상·삭감은 알림으로 보낸다."""
        day = day or date.today().isoformat()
        held = self._held()
        pairs = list(dict.fromkeys([(u[0], u[1]) for u in UNIVERSE] + [(c["mkt"], c["sym"]) for c in self.custom()] + list(held)))
        old = {}
        for m, sy in pairs:
            r = self.rt.db.one("SELECT data FROM div_cache WHERE mkt=? AND sym=?", (m, sy))
            if r:
                old[(m, sy)] = json.loads(r["data"])
        items = self.many(pairs, force=True, deadline=180)
        n, ev = 0, []
        for i in items:
            if i.get("error"):
                continue
            key, b = (i["mkt"], i["sym"]), i.get("band") or {}
            self.rt.db.x("INSERT OR REPLACE INTO div_daily(day,mkt,sym,price,ttm,yld,pct) VALUES(?,?,?,?,?,?,?)",
                         (day, i["mkt"], i["sym"], i.get("price"), i.get("ttm"), i.get("yield"), b.get("pct")))
            n += 1
            o, last = old.get(key), i.get("last")
            if not o or o.get("error") or not last or (o.get("last") or {}).get("date", "") >= last["date"]:
                continue                                  # 처음 받는 종목은 비교할 게 없다(알림 폭탄 방지)
            kind, chg = change_kind(last["amt"], (i.get("prev") or {}).get("amt"), i.get("freq"))
            if self.rt.db.one("SELECT 1 FROM div_events WHERE mkt=? AND sym=? AND ex=?", (i["mkt"], i["sym"], last["date"])):
                continue
            self.rt.db.x("INSERT OR IGNORE INTO div_events(ts,mkt,sym,name,ex,amt,prev,chg,kind,held) VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (datetime.now().isoformat(timespec="seconds"), i["mkt"], i["sym"], i["name"], last["date"], last["amt"],
                                (i.get("prev") or {}).get("amt"), chg, kind, 1 if key in held else 0))
            ev.append((i, kind, chg, key in held))
        self.rt.db.set_setting("div_daily_day", day)
        for i, kind, chg, h in ev:
            if h and kind in ("raise", "cut"):
                self.rt.notify(f"{i['name']} 배당 {'인상' if kind == 'raise' else '삭감'} {chg:+.1f}% (배당락 {i['last']['date']})", "real", "보유 종목 배당 변화")
        return {"day": day, "saved": n, "events": len(ev), "total": len(pairs)}

    def events(self, days=90):
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = self.rt.db.q("SELECT ts, mkt, sym, name, ex, amt, prev, chg, kind, held FROM div_events WHERE ex>=? ORDER BY ex DESC, id DESC LIMIT 80", (since,))
        last = self.rt.db.get_setting("div_daily_day")
        nd = self.rt.db.one("SELECT COUNT(DISTINCT day) n FROM div_daily")["n"]
        return {"events": rows, "last_day": last, "days": nd}

    def history(self, mkt, sym, days=400):
        since = (date.today() - timedelta(days=days)).isoformat()
        return self.rt.db.q("SELECT day, price, ttm, yld, pct FROM div_daily WHERE mkt=? AND sym=? AND day>=? ORDER BY day", (mkt, sym, since))

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
        q = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        bars = [(datetime.fromtimestamp(int(t) + off, timezone.utc).date(), c) for t, c in zip(res.get("timestamp") or [], q) if c]
        out["band"] = yield_band(divs, bars, price)
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
            presets.append({"id": p["id"], "group": p.get("group", "grow"), "name": p["name"], "risk": p["risk"], "desc": p["desc"], "assets": assets, "yield": round(y, 2),
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

    # ---------- 적립 플랜과 잇기 ----------
    def dca_outlook(self, years=20):
        """적립 플랜을 지금처럼 이어 갈 때의 배당 전망: 플랜 종목의 실제 배당 자료 + 보유분의 지금 배당 + 해마다 추정."""
        rt = self.rt
        plans = rt.dca.plans()
        on = [p for p in plans if p["enabled"]]
        monthly = sum(p["amount"] for p in on)
        goal = self.goal()
        out = {"n": len(plans), "monthly": monthly, "goal": goal, "rows": [], "sim": None, "tax": TAX_KR, "now_net": 0, "now_monthly": 0, "start": 0}
        if not plans:
            return out
        names, amt = {}, {}
        for p in plans:
            names.setdefault(p["code"], p["name"])
            if p["enabled"]:
                amt[p["code"]] = amt.get(p["code"], 0) + p["amount"]
        codes = list(names)
        infos = dict(zip(codes, self.many([("kr", c) for c in codes], deadline=20)))
        hold = {}
        b = rt.brokers.get("dca")
        if b is not None:
            try:
                for h in b.positions():
                    x = hold.setdefault(h["code"], [0, 0.0])
                    x[0] += h["qty"]
                    x[1] += h["qty"] * h["price"]
            except Exception:
                pass
        for c in codes:
            i, (qty, value) = infos[c], hold.get(c, (0, 0.0))
            net = qty * (i.get("ttm") or 0) * (1 - TAX_KR / 100)
            out["rows"].append({"code": c, "sym": i["sym"], "name": names[c], "amount": amt.get(c, 0),
                                "share": round(amt.get(c, 0) / monthly * 100, 1) if monthly else 0, "yield": i.get("yield"), "freq": i.get("freq"),
                                "freq_name": i.get("freq_name"), "months": i.get("months"), "growth": i.get("growth"), "cat_name": i.get("cat_name"),
                                "error": i.get("error"), "pending": bool(i.get("pending")), "held": round(value), "net": round(net)})
        out["rows"].sort(key=lambda r: -r["amount"])
        out["now_net"] = sum(r["net"] for r in out["rows"])
        out["now_monthly"] = round(out["now_net"] / 12)
        out["start"] = round(sum(hold.get(c, (0, 0.0))[1] for c in amt))
        out["pending"] = any(r["pending"] for r in out["rows"] if r["amount"])
        out["payers"] = sum(1 for r in out["rows"] if r["amount"] and r["yield"])
        if monthly <= 0:
            return out
        assets = [dict(self._asset(infos[c], a), name=names[c]) for c, a in amt.items()]
        sim = simulate({"amount": out["start"], "monthly_add": monthly, "years": years, "reinvest": True, "goal": goal, "assets": assets})
        g = next((m for m in sim["milestones"] if m["goal"]), None)
        out["sim"] = {"years": sim["years"], "yield": sim["summary"]["yield"], "missing": sim["summary"]["missing"],
                      "yearly": [{k: y[k] for k in ("year", "monthly", "value", "invested")} for y in sim["yearly"]],
                      "spend": [y["monthly"] for y in sim["other"]],                       # 배당을 다시 투자하지 않을 때
                      "goal_year": g["year"] if g else None, "goal_year_spend": g["other"] if g else None,
                      "months": [m["net"] for m in sim["last_months"]], "month0": int(sim["last_months"][0]["ym"][5:])}
        out["assets"] = [{"mkt": "kr", "sym": a["sym"], "weight": round(a["weight"], 1), "name": a["name"]} for a in sim["assets"]]
        return out

    def dca_ai_context(self):
        """AI 적립 점검에 줄 자료: 플랜·성과·매수 기록과 배당 전망."""
        rt = self.rt
        st = rt.dca.status()
        if not st["plans"]:
            raise ValueError("적립 플랜을 먼저 만들어 주세요")
        try:
            o = self.dca_outlook()
        except Exception:
            o = {"rows": [], "sim": None}
        dv = {r["code"]: r for r in o.get("rows") or []}
        costs = rt.settings.get("costs")
        plans = []
        for p in st["plans"]:
            r = dv.get(p["code"]) or {}
            try:
                price = float(rt.feed.quote(p["code"])["price"])
            except Exception:
                price = None
            plans.append({"종목": p["name"], "코드": p["code"], "매달(원)": p["amount"], "매수일": p["day"], "켜짐": bool(p["enabled"]),
                          "목표 비중%": p["weight_goal"] or None, "지금 비중%": p["weight_now"], "평가액": p["value"], "매수 원금": p["cost"],
                          "수익률%": p["gain_pct"], "현재가": price,
                          "한 달 금액으로 살 수 있는 주식 수": None if not price else int(p["amount"] // (price * (1 + costs["commission"]))),
                          "분류": r.get("cat_name") or None, "배당률%(세전, 최근 1년)": r.get("yield"), "배당 지급": r.get("freq_name"),
                          "배당 성장률%(시뮬레이션 기본값)": r.get("growth")})
        codes = {p["code"] for p in st["plans"]}
        runs = st["runs"]
        sim = o.get("sim") or {}
        at = lambda n: next((y for y in sim.get("yearly") or [] if y["year"] == n), None)
        data = {"기준시각": datetime.now().isoformat(timespec="minutes"), "계좌": st["mode_label"], "자동 매수": "켜짐" if st["auto"] else "꺼짐",
                "매수 방식": "목표 비중 맞추기" if st["rebalance"] == "rebalance" else "플랜별 고정 금액", "매달 합계(원)": st["monthly"],
                "남은 현금": st["cash"], "매수 원금 합계": st["cost"], "평가액 합계": st["value"], "수익률%": st["gain_pct"], "플랜": plans,
                "플랜에 없는 보유 종목": [{"종목": h.get("name") or h["code"], "평가액": int(h["qty"] * h["price"])} for h in st["holdings"] if h["code"] not in codes][:10] or None,
                "최근 매수 기록": [{"날짜": r["run_ts"][:10], "종목": r.get("name"), "결과": {"done": "매수", "skipped": "건너뜀", "failed": "실패"}.get(r["status"], r["status"]),
                              "수량": r["qty"], "메모": r["msg"] or None} for r in runs[:12]] or None,
                "배당 전망(지금 플랜을 그대로 이어 가고 배당을 다시 투자할 때, 세후 월평균)": {
                    "지금 보유분": o.get("now_monthly"), "5년 뒤": (at(5) or {}).get("monthly"), "10년 뒤": (at(10) or {}).get("monthly"),
                    "20년 뒤": (at(20) or {}).get("monthly"), "플랜 배당률%(세전)": sim.get("yield")} if sim else None,
                "목표 월 배당(세후, 원)": o.get("goal") or None, "배당 목표 도달(추정)": f"{sim['goal_year']}년차" if sim.get("goal_year") else None}
        ask = ("내 적립 플랜을 점검해 줘. 볼 것: ① 종목끼리 겹치는 부분(같은 지수·같은 종목을 담은 ETF)과 쏠림 ② 금액·비중이 목표 비중과 맞는지 "
               "③ 한 달 금액으로 1주를 못 사서 건너뛰게 되는 플랜, 최근 건너뜀·실패 기록 ④ 배당 관점(지금 배당률, 성장, 지급 주기, 목표 월 배당까지의 거리) "
               "⑤ 환노출·보수·세금 계좌처럼 확인할 점. 마지막에 '고칠 만한 것'을 우선순위대로 3개 이내로 적어. "
               "종목을 새로 사라고 지시하지 말고, 선택지와 근거를 알려 줘.")
        return "AI 적립 점검", ask, {k: v for k, v in data.items() if v is not None}

    # ---------- 시뮬레이션 ----------
    def run_sim(self, d):
        """화면에서 온 구성(종목·비중)에 실제 배당 자료를 붙여 계산한다. 사용자가 고친 값(yield·growth)이 있으면 그 값을 쓴다."""
        want = [a for a in (d.get("assets") or []) if a.get("sym")][:20]
        infos = self.many([(a.get("mkt"), a["sym"]) for a in want])
        assets = []
        for a, i in zip(want, infos):
            x = self._asset(i, float(a.get("weight") or 0))
            if a.get("name") and i["name"] in (i["sym"], i.get("yname")):      # 후보 밖 종목(보유·적립 종목)은 화면이 아는 이름을 쓴다
                x["name"] = str(a["name"])[:40]
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
                         "배당 안 줄인 연속 햇수": i.get("streak"), "기초 지수": BASE_OF.get(i["sym"]), "설명": i.get("note")})
        if len(rows) < 3:
            raise ValueError("배당 자료를 아직 받지 못했어요. 잠시 뒤 다시 해 주세요")
        try:
            mine = self.mine()
            held = [{"종목": r["name"], "평가액": r["value"], "연 배당(세후)": r["net"]} for r in mine["rows"][:12]] if mine.get("configured") else None
        except Exception:
            held = None
        try:
            dca = [{"종목": p["name"], "코드": p["code"], "매달(원)": p["amount"]} for p in self.rt.dca.plans() if p["enabled"]] or None
        except Exception:
            dca = None
        goal = float(d.get("goal") or self.goal() or 0)
        style = {"growth": "배당 성장(지금 배당은 적어도 해마다 늘어나는 쪽)", "income": "매월 현금흐름(지금 받는 돈을 크게)",
                 "balance": "균형(성장과 현금흐름 절반씩)", "high": "고분배(커버드콜 포함, 원금 변동 감수)"}.get(d.get("style"), "균형")
        region = {"us": "미국 상장 종목 위주", "kr": "국내 상장 종목만(환전 없이)", "mix": "국내와 미국을 섞어서"}.get(d.get("region"), "국내와 미국을 섞어서")
        data = {"투자금(원)": round(amount), "매월 추가 투자(원)": round(float(d.get("monthly_add") or 0)), "목표 월 배당(세후, 원)": round(goal) or "정하지 않음",
                "원하는 방향": style, "지역": region, "투자 기간(년)": int(d.get("years") or 10), "배당 재투자": "함" if d.get("reinvest", True) else "안 함(생활비로 씀)",
                "사용자 메모": str(d.get("memo") or "")[:300] or None, "환율(원/달러)": round(uni["fx"]), "세율 가정%": {"국내": TAX_KR, "미국": TAX_US},
                "금융소득종합과세 기준(연, 세전)": FIN_INCOME_LINE, "후보 목록(실제 최근 1년 지급 기준)": rows, "지금 보유 종목의 배당": held,
                "지금 매달 적립 중인 플랜(국내 종목만 자동 적립 가능)": dca}
        ask = (f"투자금 {round(amount):,}원으로 배당 포트폴리오를 짜 줘. 방향: {style}. 지역: {region}. "
               "후보 목록의 실제 숫자를 근거로 종목과 비중을 정하고, 매달 들어오는 돈이 고르게 되도록 지급월을 따져. "
               "세후 원화 기준으로 첫해에 한 달 평균 얼마쯤 들어오는지, 연 금융소득 2천만 원 기준을 넘는지 판단 글에 적어.")
        if dca:
            ask += " 이미 매달 적립 중인 플랜이 있으니, 겹치는 종목은 그 사실을 판단 글에 한 줄로 적어."
        if float(d.get("monthly_add") or 0) > 0:
            ask += " 매월 추가 투자금은 앱의 적립 플랜(국내 상장 종목만 자동 매수)으로 넣을 수 있어. 미국 종목 몫은 직접 사야 한다는 점을 notes에 적어."
        L = d.get("live") or {}
        try:
            spend = float(L.get("spend") or 0) if L.get("on", True) else 0
        except (TypeError, ValueError):
            spend = 0
        if spend > 0:
            hk = "직장가입자(월급 외 소득 2천만 원 초과분만 대상)" if L.get("health") == "work" else "지역가입자(이자·배당이 연 1천만 원을 넘으면 전액 대상)"
            data["생활비로 쓰는 계획"] = {"한 달 생활비(원, 지금 물가)": round(spend), "다른 수입(월, 원)": round(float(L.get("other") or 0)) or None,
                                   "다른 수입이 시작되는 해": int(L.get("other_from") or 1) if L.get("other") else None, "건강보험": hk,
                                   "건강보험료율%(장기요양 포함, 어림)": round(HEALTH_RATE * (1 + CARE_RATE / 100), 2)}
            ask += (f" 이 돈으로 한 달 생활비 {round(spend):,}원을 쓰며 지낼 계획이야. 세금과 건강보험료를 뺀 배당이 생활비에 얼마나 닿는지 먼저 따지고, "
                    "배당은 필요한 만큼만 만들고 남는 자금은 성장 자산에, 하락장에 버틸 현금성 자산을 몇 개월치 둘지도 정해. "
                    "같은 지수에 몰리는지, 모자라는 돈을 팔아서 메워야 한다면 한 해에 자산의 몇 %인지도 판단 글에 적어.")
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
