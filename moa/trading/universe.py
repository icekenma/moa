"""살 수 있는 종목 고르기(자동매매): 따져 볼 가치가 없거나 위험 신호가 붙은 종목을 추천 전에 뺀다.

여기서 빼는 것은 '종목이 좋으냐 나쁘냐'가 아니라 '규칙대로 사고팔기에 맞지 않는 종류'다.
- 거래소가 경고를 붙인 종목: 관리종목, 투자경고·위험, 단기과열(30분 단일가라 단타가 안 된다), 정리매매, 거래정지
- 값이 본주·지수와 다르게 움직이는 것: 스팩, 우선주, 레버리지·인버스 상품, ETN
"""
import re

# 키움 종목정보(ka10099)의 주문 경고 값: 0 없음, 1 ETF 투자주의, 2 정리매매, 3 단기과열, 4 투자위험, 5 투자경고
WARN = {"2": "정리매매", "3": "단기과열", "4": "투자위험", "5": "투자경고"}
BAD_WORDS = ("관리종목", "투자주의환기", "거래정지", "정리매매", "투자경고", "투자위험", "단기과열", "불성실공시")
KR_LEVER = re.compile(r"레버리지|인버스|곱버스|2X|3X|ETN", re.I)      # 금·지수 선물을 그대로 따르는 보통 ETF는 빼지 않는다
US_LEVER = re.compile(r"\b(2X|3X|1\.5X|ULTRA(PRO)?|LEVERAGED?|INVERSE|BULL|BEAR|SHORT)\b", re.I)
KR_PREF = re.compile(r"우[A-C]?$|우\(전환\)$|\d우B?$")


def junk_reason(mkt, sym, name="", info=None):
    """뺄 이유(짧은 글). 문제없으면 빈 글. info: 종목 목록에 적힌 {warn, audit, state}(없어도 된다)."""
    sym, name, info = str(sym or "").upper(), str(name or ""), info or {}
    if mkt == "kr":
        w = WARN.get(str(info.get("warn") or "").strip())
        if w:
            return w
        tag = f"{info.get('audit') or ''}|{info.get('state') or ''}"
        for b in BAD_WORDS:
            if b in tag:
                return b
        if "스팩" in name or re.search(r"기업인수목적", name):
            return "스팩"
        if KR_LEVER.search(name):
            return "레버리지·인버스·ETN"
        if (re.fullmatch(r"\d{6}", sym) and sym[-1] != "0" and info.get("market") != "ETF") or KR_PREF.search(name) or re.fullmatch(r"\d{5}[A-Z]", sym):
            return "우선주"
        return ""
    if US_LEVER.search(name):
        return "레버리지·인버스"
    return ""
