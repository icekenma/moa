"""파이어(FIRE, 일찍 은퇴) 계획(v5.61): 공무원 재직자가 몇 살에 그만둬도 평생(기본 95세) 돈이 버티는지 셈한다.

- 개인 자료(나이·연봉·집·대출·연금 예상액)는 서버 DB 설정 'fire_profile'에만 둔다. 저장소에 넣지 않는다.
- 모든 금액은 '지금 돈의 가치'(실질)로 셈한다. 연금공단 예상연금액(현재가치)과 같은 기준이고, 공무원·국민연금은 물가만큼 오른다.
  주택연금(정액형)과 대출 상환액은 이름값이 고정이라 물가만큼 해마다 실질 가치가 줄어든다.
- 제도 숫자는 아래 상수에 출처와 함께 적는다. 바뀌면 여기만 고친다. 확인이 덜 된 값은 화면에 '확인 필요'로 적는다.
- 어림값이다: 세금(퇴직소득세·연금소득세), 기준소득월액 평균, 건강보험 피부양자 판정은 단순하게 본다. 화면에 함께 적는다.
"""
import math
from datetime import date

# 2026 일반직 봉급표 월 봉급(원). 출처: 인사혁신처 2026 공무원 봉급표(gonginfo.kr·itool.co.kr 옮겨 적은 표, 두 곳 같음)
PAY = {
    5: [2896400, 3013400, 3135000, 3261300, 3390900, 3523000, 3657300, 3793200, 3929600, 4066800, 4195300, 4319000, 4436400, 4545900, 4649400,
        4746800, 4838400, 4924800, 5006300, 5082700, 5154500, 5222000, 5285600, 5345200, 5401500, 5454500, 5498400, 5540700, 5579500, 5617200],
    6: [2389500, 2500700, 2615200, 2732400, 2853100, 2977100, 3101500, 3226200, 3351300, 3468700, 3580000, 3689600, 3792700, 3890000, 3983600,
        4071300, 4154900, 4234100, 4309100, 4379600, 4447600, 4511500, 4571300, 4628500, 4682800, 4734100, 4782800, 4823700, 4861900, 4899800,
        4934800, 4967800],
}
PAY_YEAR = 2026
# 퇴직수당 = 재직연수 × 기준소득월액 × 비율(공무원연금법 제62조·시행령 제58조). 재직기간 33년까지
RETIRE_RATE = ((5, 6.5), (10, 22.75), (15, 29.25), (20, 32.5), (99, 39.0))
RETIRE_YEARS_MAX = 33
PENSION_YEARS_MAX = 36      # 퇴직연금 재직기간 상한
PENSION_MIN_YEARS = 10      # 공무원 재직 10년 이상이면 퇴직연금, 미만이면 국민연금과 이어 받는 연계퇴직연금(국민연금 개시 나이부터)
PENSION_AGE = 65            # 국민(연계)연금 개시 나이, 2033년 이후 퇴직자의 공무원연금 개시 나이
START_AGE = ((2021, 60), (2023, 61), (2026, 62), (2029, 63), (2032, 64))   # 퇴직 연도별 공무원연금 개시 나이(그 뒤 65세)


def start_age(retire_year, years):
    """공무원연금을 받기 시작하는 나이. 재직 10년 미만이면 연계퇴직연금이라 국민연금과 같이 65세."""
    if years is not None and years < PENSION_MIN_YEARS:
        return PENSION_AGE
    return next((a for y, a in START_AGE if retire_year <= y), PENSION_AGE)


def accrual_rate(year):
    """그해 재직 1년에 쌓이는 지급률(%): 2026년 1.736%에서 해마다 0.004%p씩 내려 2035년부터 1.7%."""
    return round(max(1.7, 1.736 - 0.004 * max(0, year - 2026)), 3)
EARLY_CUT = 5.0             # 조기퇴직연금: 1년 앞당길 때마다 5% 깎임, 최대 5년(25%)
EARLY_MAX = 5
HONOR_MIN_YEARS = 20        # 명예퇴직: 20년 이상 재직, 정년 1년 이상 남음
HONOR_PAY_RATE = 0.68       # 명예퇴직수당의 월봉급액 = 봉급의 68%(확인 필요: 인사과·「국가공무원 명예퇴직수당 등 지급 규정」)
RETIRE_AGE = 60             # 공무원 정년
# 주택연금 월지급금(종신·정액형, 일반주택, 천원). 한국주택금융공사 2026-03-01 기준 표를 옮긴 것(확인 필요)
HOUSE_PENSION = {55: {9: 1404, 10: 1560}, 60: {9: 1896, 10: 2107}, 65: {9: 2276, 10: 2529}, 70: {9: 2770, 10: 3078}}
# 건강보험 피부양자: 공적연금 등 소득 연 2,000만원 이하, 재산과표 5.4억 이하(5.4~9억이면 소득 1,000만원 이하). 출처: 2026 피부양자 기준 정리(확인 필요)
DEP_INCOME = 20_000_000

SCEN = (("bad", "나쁨", 1.5), ("mid", "보통", 3.5), ("good", "좋음", 5.0))   # 물가를 뺀 투자 수익률(%/년)

FIELDS = {   # 키: (이름, 기본값, 단위, 설명)
    "birth": ("생년월일", "", "date", "국민·공무원연금 개시일과 나이 계산에 써요"),
    "service_start": ("공무원 임용일", "", "date", "재직기간 → 퇴직수당·명예퇴직·연금 크기"),
    "salary": ("작년 세전 연봉", 0, "원", "기준소득월액과 퇴직수당 계산에 써요"),
    "grade_now": ("지금 직급", 6, "급", ""),
    "step_now": ("지금 호봉", 0, "호봉", "승진 뒤 연봉이 얼마나 오르는지 봉급표로 셈해요(모르면 비워 두기)"),
    "promo_date": ("승진 날짜", "", "date", ""),
    "promo_grade": ("승진 직급", 5, "급", ""),
    "promo_step": ("승진 뒤 호봉", 0, "호봉", ""),
    "take_home": ("세후 월 실수령(평균)", 0, "원", "비우면 세전의 76%로 어림해요(기여금 9%·세금·건보 뺀 값)"),
    "spend": ("한 달 생활비(대출 상환 빼고)", 0, "원", "비우면 가계부 최근 12달 평균 지출"),
    "fin_assets": ("금융자산(집 빼고)", 0, "원", "비우면 자산 화면의 현금·예금·주식·연금 합계"),
    "save_extra": ("월 추가 저축(실수령에서 생활비·대출 빼고 남는 것 말고 따로)", 0, "원", "보통은 0"),
    "home_value": ("집 시세", 0, "원", ""),
    "mortgage": ("주택담보대출 잔액", 0, "원", ""),
    "mortgage_rate": ("대출 금리", 0, "%", "모르면 비워 두기(4%로 어림)"),
    "mortgage_years": ("대출 남은 기간", 0, "년", "모르면 비워 두기(25년으로 어림)"),
    "pension_gov": ("공무원(연계퇴직)연금 예상액(월) — 지금 그만두면", 0, "원", "연금공단 예상연금조회 값. 앞으로 일하는 만큼 해마다 '그해 기준소득월액 × 지급률(약 1.7%)'씩 늘려 셈해요"),
    "pension_asof": ("예상액 조회일", "", "date", "비우면 저장한 날"),
    "pension_nps": ("국민(연계노령)연금 예상액(월, 현재가치)", 0, "원", ""),
    "pension_start": ("연금 개시일", "", "date", ""),
    "pension_early": ("공무원연금 앞당겨 받기(년)", 0, "년", "0~5년, 1년마다 5% 깎임"),
    "honor_on": ("명예퇴직수당 셈에 넣기(1 넣음, 0 뺌)", 1, "", "예산·심사로 못 받을 수 있어 보수적으로 보려면 0"),
    "health_ins": ("은퇴 뒤 건강보험료(월, 지역가입자)", 250000, "원", "어림값: 건보공단 '지역보험료 모의계산'으로 확인"),
    "house_pension_age": ("주택연금 시작 나이(0이면 안 씀)", 0, "세", "55세부터. 대출을 다 갚아야(또는 일시인출로 갚아야) 가입"),
    "inflation": ("물가 상승률", 2.5, "%", "고정 금액(주택연금·대출 상환)의 실질 가치를 줄이는 데만 써요"),
    "life": ("계획 끝 나이", 95, "세", ""),
}


def _d(v):
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _years(a, b):
    """a부터 b까지 햇수(소수)."""
    return (b - a).days / 365.2425 if a and b else 0.0


def pay(grade, step):
    t = PAY.get(int(grade or 0))
    if not t or not step:
        return None
    return t[max(1, min(int(step), len(t))) - 1]


def retire_rate(years):
    for lim, r in RETIRE_RATE:
        if years < lim:
            return r if years >= 1 else 0.0
    return RETIRE_RATE[-1][1]


def retire_allowance(years, base_month):
    """퇴직수당(원, 세전). base_month: 기준소득월액."""
    y = min(max(years, 0.0), RETIRE_YEARS_MAX)
    return round(y * base_month * retire_rate(y) / 100) if y >= 1 else 0


def honor_allowance(years, months_left, pay_month):
    """명예퇴직수당(원, 어림). 재직 20년 이상, 정년까지 12달 이상 남았을 때."""
    if years < HONOR_MIN_YEARS or months_left < 12 or not pay_month:
        return 0
    m = min(months_left, 120)
    base = pay_month * HONOR_PAY_RATE
    return round(base * 0.5 * m) if m <= 60 else round(base * 0.5 * (60 + (m - 60) / 2))


def house_pension(age, price):
    """주택연금 월지급금(원, 가입 때 이름값). 표의 나이·가격 사이는 곧게 이어 어림한다."""
    if not price or age < 55:
        return 0
    ages = sorted(HOUSE_PENSION)
    a = min(max(age, ages[0]), ages[-1])
    lo = max(x for x in ages if x <= a)
    hi = min(x for x in ages if x >= a)

    def at(k):
        row = HOUSE_PENSION[k]
        e = price / 1e8
        if e <= 9:
            return row[9] * e / 9
        return row[9] + (row[10] - row[9]) * (e - 9)       # 10억 넘는 값은 같은 기울기로 늘려 본다(확인 필요)
    v = at(lo) if lo == hi else at(lo) + (at(hi) - at(lo)) * (a - lo) / (hi - lo)
    return round(v * 1000)


def annuity(balance, rate_pct, years):
    """원리금균등 월 상환액."""
    if balance <= 0 or years <= 0:
        return 0.0
    r = rate_pct / 100 / 12
    n = years * 12
    return balance / n if r == 0 else balance * r / (1 - (1 + r) ** -n)


class Plan:
    """채워진 값(P)으로 한 해씩 셈한다. 나이는 그해 생일 기준."""

    def __init__(self, P, today=None):
        self.P, self.today = P, today or date.today()
        self.birth = _d(P.get("birth"))
        self.svc = _d(P.get("service_start"))
        self.age0 = _years(self.birth, self.today) if self.birth else None
        self.y0 = self.today.year
        self.infl = float(P.get("inflation") or 0) / 100
        gross = float(P.get("salary") or 0)
        promo = _d(P.get("promo_date"))
        a, b = pay(P.get("grade_now"), P.get("step_now")), pay(P.get("promo_grade"), P.get("promo_step"))
        self.bump = (b / a) if a and b else 1.0
        self.promo_year = promo.year if promo else None
        self.gross = gross
        self.th_rate = (float(P["take_home"]) * 12 / gross) if P.get("take_home") and gross else 0.76
        rate = float(P.get("mortgage_rate") or 4.0)
        yrs = float(P.get("mortgage_years") or 25)
        self.mort_pay = annuity(float(P.get("mortgage") or 0), rate, yrs) * 12
        self.mort_end = self.y0 + math.ceil(yrs) if P.get("mortgage") else self.y0
        self.mort_rate, self.mort_yrs = rate, yrs

    def svc_years(self, when):
        return max(0.0, _years(self.svc, when)) if self.svc else None

    def gross_in(self, year):
        """그해 세전 연봉(실질). 승진은 봉급표 비율, 그 뒤 해마다 호봉 1칸(봉급표 비율)."""
        g = self.gross
        P = self.P
        if self.promo_year and year >= self.promo_year:
            g *= self.bump
            s0 = int(P.get("promo_step") or 0)
            if s0:
                b0, b1 = pay(P.get("promo_grade"), s0), pay(P.get("promo_grade"), s0 + (year - self.promo_year))
                if b0 and b1:
                    g *= b1 / b0
        return g

    def at_retire(self, R):
        """R세에 그만둘 때의 퇴직수당·명예퇴직수당·공무원연금(월)."""
        when = date(self.birth.year + R, self.birth.month, min(self.birth.day, 28))
        yrs = self.svc_years(when)
        g = self.gross_in(when.year)
        out = {"age": R, "date": when.isoformat(), "years": round(yrs, 1) if yrs is not None else None}
        gov, gain = self.gov_at(when)
        if yrs is None:
            return dict(out, allowance=0, honor=0, gov=gov, gain=gain, start=start_age(when.year, None), ok=True)
        allow = retire_allowance(yrs, g / 12)
        months_left = (RETIRE_AGE - R) * 12
        P = self.P
        step = None
        if P.get("promo_step") and self.promo_year:
            step = int(P["promo_step"]) + max(0, when.year - self.promo_year)
        honor = honor_allowance(yrs, months_left, pay(P.get("promo_grade") if self.promo_year and when.year >= self.promo_year else P.get("grade_now"),
                                                     step or P.get("step_now")))
        return dict(out, allowance=allow, honor=honor, gov=gov, gain=gain, start=start_age(when.year, yrs), ok=yrs >= PENSION_MIN_YEARS)

    def gov_at(self, when):
        """그만두는 날의 공무원연금(월, 실질). 조회 때 '지금 그만두면' 받을 값에, 그 뒤 일한 해마다 '그해 기준소득월액 × 지급률'을 더한다.
        기준소득월액은 세전 연봉 ÷ 12로 어림(소득 재분배·상한은 빼고). 재직 36년을 넘는 기간은 더하지 않는다."""
        P = self.P
        base = float(P.get("pension_gov") or 0)
        asof = _d(P.get("pension_asof")) or self.today
        if not base or when <= asof:
            return round(base), 0
        cap = None
        if self.svc:
            cap = date(self.svc.year + PENSION_YEARS_MAX, self.svc.month, min(self.svc.day, 28))
        add, cur = 0.0, asof
        while cur < when:
            nxt = min(when, date(cur.year + 1, 1, 1))
            end = min(nxt, cap) if cap else nxt
            if end > cur:
                add += self.gross_in(cur.year) / 12 * accrual_rate(cur.year) / 100 * _years(cur, end)
            cur = nxt
        return round(base + add), round(self.gross_in(asof.year) / 12 * accrual_rate(asof.year) / 100)

    def run(self, R, ret_pct):
        """R세에 그만두고 수익률 ret_pct(실질)로 굴릴 때, 해마다 금융자산. 바닥나는 나이(없으면 None)."""
        P = self.P
        r = ret_pct / 100
        assets = float(P.get("fin_assets") or 0)
        spend = float(P.get("spend") or 0) * 12
        hins = float(P.get("health_ins") or 0) * 12
        nps = float(P.get("pension_nps") or 0) * 12
        info = self.at_retire(R)
        early = max(0, min(EARLY_MAX, int(P.get("pension_early") or 0))) if info["ok"] else 0   # 연계퇴직연금은 앞당겨 받지 않는 것으로 본다
        gov = info["gov"] * 12 * (1 - EARLY_CUT * early / 100)
        gov_age = info["start"] - early
        hp_age = int(P.get("house_pension_age") or 0)
        hp = house_pension(hp_age, float(P.get("home_value") or 0)) * 12 if hp_age else 0
        life = int(P.get("life") or 95)
        a0 = int(self.age0)
        rows, broke, need = [], None, 0.0
        for age in range(a0, life + 1):
            year = self.birth.year + age
            t = year - self.y0
            deflate = (1 + self.infl) ** -max(0, t)
            mort = self.mort_pay * deflate if year < self.mort_end else 0.0
            if age < R:
                inflow = self.gross_in(year) * self.th_rate + float(P.get("save_extra") or 0) * 12
                out = spend + mort
            else:
                inflow = (gov if age >= gov_age else 0) + (nps if age >= PENSION_AGE else 0) + (hp * (1 + self.infl) ** -(age - hp_age) if hp_age and age >= hp_age else 0)
                out = spend + mort + hins
                if age == R:
                    inflow += info["allowance"] + (info["honor"] if float(P.get("honor_on", 1) or 0) else 0)
            if age == a0:                                    # 올해는 남은 달만
                k = 1 - (self.today.timetuple().tm_yday / 365)
                inflow, out = inflow * k, out * k
            assets = assets * (1 + r) + (inflow - out) * (1 + r / 2)
            if age >= R:
                need += max(0.0, out - inflow)
            rows.append({"age": age, "year": year, "assets": round(assets), "in": round(inflow), "out": round(out)})
            if assets < 0 and broke is None:
                broke = age
        return {"rows": rows, "broke": broke, "info": info, "gov_age": gov_age, "gov_month": round(gov / 12), "hp_month": round(hp / 12)}


def _solve(P, today, R, key, hi, ret=3.5):
    """R세 은퇴가 95세까지 버티게 하는 key 값(생활비면 최댓값, 추가 자산이면 최솟값)을 반으로 나눠 찾는다."""
    def ok(v):
        Q = dict(P)
        if key == "spend":
            Q["spend"] = v
        else:
            Q["fin_assets"] = float(P.get("fin_assets") or 0) + v
        return Plan(Q, today).run(R, ret)["broke"] is None
    lo_, hi_ = 0.0, hi
    if key == "spend":
        if not ok(0):
            return 0
        for _ in range(24):
            m = (lo_ + hi_) / 2
            lo_, hi_ = (m, hi_) if ok(m) else (lo_, m)
        return round(lo_ / 10000) * 10000
    if ok(0):
        return 0
    if not ok(hi):
        return None
    for _ in range(24):
        m = (lo_ + hi_) / 2
        lo_, hi_ = (lo_, m) if ok(m) else (m, hi_)
    return round(hi_ / 1e6) * 1e6


def missing(P):
    need = [("birth", "생년월일(연금 예상액의 개시일이 65세 생일이에요)"), ("service_start", "공무원 임용일(재직기간)"),
            ("spend", "한 달 생활비(가계부가 비어 있으면)"), ("fin_assets", "금융자산(집 빼고)"), ("take_home", "세후 월 실수령"),
            ("mortgage_rate", "대출 금리"), ("mortgage_years", "대출 남은 기간"), ("step_now", "지금 호봉(승진 뒤 연봉 셈)")]
    return [t for k, t in need if not P.get(k)]


class Fire:
    def __init__(self, rt):
        self.rt = rt

    def profile(self):
        p = {k: v[1] for k, v in FIELDS.items()}
        p.update(self.rt.db.get_setting("fire_profile") or {})
        return p

    def save(self, d):
        cur = self.profile()
        for k, (_, dv, unit, _) in FIELDS.items():
            if k not in (d or {}):
                continue
            v = d[k]
            if unit == "date":
                cur[k] = v if _d(v) else ""
            else:
                try:
                    cur[k] = float(str(v).replace(",", "")) if str(v).strip() != "" else (dv if isinstance(dv, (int, float)) and dv else 0)
                except ValueError:
                    raise ValueError(f"{FIELDS[k][0]} 값이 숫자가 아니에요")
                if cur[k] < 0:
                    raise ValueError(f"{FIELDS[k][0]}은(는) 0 이상이어야 해요")
        if cur.get("pension_gov") and not cur.get("pension_asof"):
            cur["pension_asof"] = date.today().isoformat()        # '지금 그만두면' 값은 조회한 날 기준이다
        self.rt.db.set_setting("fire_profile", cur)
        return self.view()

    def _app_data(self):
        """가계부·자산 화면에서 읽는 값(비어 있는 칸을 채울 때만 쓴다)."""
        out = {}
        try:
            tr = [m for m in self.rt.ledger.trend(12) if m["expense"]]
            if tr:
                out["spend"] = round(sum(m["expense"] for m in tr) / len(tr))
                out["spend_n"] = len(tr)
        except Exception:
            pass
        try:
            ov = self.rt.assets.overview()
            fin = sum(g["total"] for g in ov["groups"] if g["type"] not in ("property", "debt"))
            if fin:
                out["fin_assets"] = round(fin)
            prop = sum(g["total"] for g in ov["groups"] if g["type"] == "property")
            if prop:
                out["home_value"] = round(prop)
            debt = sum(g["total"] for g in ov["groups"] if g["type"] == "debt")
            if debt:
                out["mortgage"] = round(debt)
        except Exception:
            pass
        return out

    def view(self, today=None):
        raw = self.profile()
        app = self._app_data()
        P = dict(raw)
        src = {}
        for k in ("spend", "fin_assets", "home_value", "mortgage"):
            if not P.get(k) and app.get(k):
                P[k], src[k] = app[k], "app"
        out = {"profile": raw, "used": P, "src": src, "app": app, "fields": [{"key": k, "label": v[0], "unit": v[2], "help": v[3]} for k, v in FIELDS.items()],
               "missing": missing(raw), "facts": facts()}
        if not _d(P.get("birth")) or not P.get("salary"):
            return dict(out, ready=False)
        pl = Plan(P, today)
        a0 = int(pl.age0)
        ages = list(range(max(a0 + 1, 45), RETIRE_AGE + 1))
        table, best, curves = [], {}, {}
        for R in ages:
            row = {"age": R}
            curves[R] = {}
            for key, _, ret in SCEN:
                res = pl.run(R, ret)
                curves[R][key] = [x["assets"] for x in res["rows"]]
                row[key] = {"broke": res["broke"], "end": res["rows"][-1]["assets"], "at": next((x["assets"] for x in res["rows"] if x["age"] == R), None)}
                if res["broke"] is None and key not in best:
                    best[key] = R
            info = pl.at_retire(R)
            early = max(0, min(EARLY_MAX, int(P.get("pension_early") or 0))) if info["ok"] else 0
            row.update(allowance=info["allowance"], honor=info["honor"], gov=info["gov"], years=info["years"], ok=info["ok"], gov_age=info["start"] - early)
            table.append(row)
        pick = best.get("mid") or RETIRE_AGE
        res = pl.run(pick, 3.5)
        curve_ages = [x["age"] for x in res["rows"]]
        goals = []
        for R in (50, 52, 55, 57, 60):
            if R <= a0:
                continue
            goals.append({"age": R, "spend_max": _solve(P, today, R, "spend", 15_000_000), "add_need": _solve(P, today, R, "add", 3e9)})
        home = float(P.get("home_value") or 0)
        debt = float(P.get("mortgage") or 0)
        fin = float(P.get("fin_assets") or 0)
        gap_years = max(0, res["gov_age"] - pick)
        bridge = sum(max(0, r["out"] - r["in"]) for r in res["rows"] if pick <= r["age"] < res["gov_age"])
        pension_year = (res["gov_month"] + float(P.get("pension_nps") or 0)) * 12
        return dict(out, ready=True, age=round(pl.age0, 1), best=best, pick=pick, scen=[{"key": k, "label": lb, "ret": r} for k, lb, r in SCEN],
                    table=table, curves=curves, curve_ages=curve_ages, goals=goals, at_pick=res["info"], gov_age=res["gov_age"], gov_month=res["gov_month"], hp_month=res["hp_month"],
                    gov_now=round(float(P.get("pension_gov") or 0)), gov_gain=res["info"].get("gain") or pl.gov_at(date(pl.y0 + 1, 12, 31))[1],
                    net={"home": home, "debt": debt, "home_eq": home - debt, "fin": fin, "total": home - debt + fin},
                    mortgage={"pay_month": round(pl.mort_pay / 12), "rate": pl.mort_rate, "years": pl.mort_yrs, "end": pl.mort_end,
                              "guess": not (raw.get("mortgage_rate") and raw.get("mortgage_years"))},
                    salary={"bump": round(pl.bump, 4), "take_home_rate": round(pl.th_rate, 3), "guess_th": not raw.get("take_home"),
                            "y_next": round(pl.gross_in(pl.y0 + 1))},
                    bridge={"years": gap_years, "need": round(bridge)},
                    dependent={"pension_year": round(pension_year), "ok_income": pension_year <= DEP_INCOME},
                    rule4={"spend_year": round(float(P.get("spend") or 0) * 12), "x25": round(float(P.get("spend") or 0) * 12 * 25)})

    def brief(self):
        try:
            v = self.view()
        except Exception:
            return None
        if not v.get("ready"):
            return {"ready": False}
        return {"ready": True, "best": v["best"].get("mid"), "bad": v["best"].get("bad"), "age": v["age"]}


def facts():
    """화면 '제도' 탭: 셈에 쓴 제도 숫자와 출처."""
    return [
        {"t": "공무원연금 지급 시작", "d": f"재직 {PENSION_MIN_YEARS}년 이상이면 퇴직연금: 퇴직 연도에 따라 2027~2029년 63세, 2030~2032년 64세, 2033년 이후 {PENSION_AGE}세부터. 최대 {EARLY_MAX}년 앞당길 수 있고 1년마다 {EARLY_CUT:g}% 깎여요(최대 25%). 10년 미만이면 국민연금과 이어 받는 연계퇴직연금이라 국민연금처럼 {PENSION_AGE}세부터예요.", "src": "공무원연금법 정리 · 연금공단 예상연금조회 화면"},
        {"t": "연금은 일할수록 늘어요", "d": f"공단 예상액은 '지금 그만두면' 받을 값이에요. 앱은 그 뒤 일하는 해마다 '그해 기준소득월액(세전 연봉÷12) × 지급률'만큼 더해요. 지급률은 2026년 1.736%에서 해마다 0.004%p씩 내려 2035년부터 1.7%. 재직 {PENSION_YEARS_MAX}년을 넘는 기간은 더하지 않아요. 2016년 뒤 기간의 소득 재분배(전체 공무원 평균과 섞는 것)와 기준소득 상한은 빼서 어림값이에요.", "src": "공무원연금법 정리"},
        {"t": "퇴직수당", "d": "재직연수 × 기준소득월액 × 비율: 1~5년 6.5%, 5~10년 22.75%, 10~15년 29.25%, 15~20년 32.5%, 20년 이상 39%(재직 33년까지). 세금 전 금액이에요.", "src": "공무원연금법 제62조·시행령 제58조"},
        {"t": "명예퇴직수당", "d": f"재직 {HONOR_MIN_YEARS}년 이상, 정년까지 1년 이상 남았을 때. 월봉급액(봉급의 {HONOR_PAY_RATE:.0%}) × ½ × 정년 잔여월수(5년까지), 5~10년은 [60 + (잔여월수−60)÷2]개월분. 10년 넘게 남아도 10년분. 예산·심사로 못 받을 수 있어요. 확인 필요: 인사과.", "src": "국가공무원 명예퇴직수당 등 지급 규정 정리"},
        {"t": "봉급표", "d": "2026년 일반직 봉급표(본봉, 수당 제외). 승진 뒤 연봉은 '승진 뒤 봉급 ÷ 지금 봉급'만큼 오른다고 보고, 그 뒤 해마다 호봉 1칸씩 올려요. 봉급 인상률은 물가와 같다고 봐요(실질 0%).", "src": "인사혁신처 2026 봉급표"},
        {"t": "건강보험 피부양자", "d": "공적연금은 세전 100%가 소득으로 잡혀요. 연 2,000만원을 넘으면 피부양자가 안 돼요. 재산과표 5.4억 이하면 소득만 보고, 5.4~9억이면 소득 1,000만원 이하여야 해요. 확인 필요: 건보공단.", "src": "2026 피부양자 기준 정리"},
        {"t": "주택연금", "d": "55세부터, 종신·정액형 월지급금(2026-03-01 기준 표): 9억 집이면 55세 약 140만원, 60세 약 190만원, 65세 약 228만원, 70세 약 277만원. 정액이라 물가만큼 실질 가치가 줄어요. 대출이 남아 있으면 먼저 갚아야 해요. 확인 필요: 한국주택금융공사 계산기.", "src": "한국주택금융공사"},
        {"t": "이 계획의 가정", "d": "모든 금액은 지금 돈 가치. 투자 수익률은 물가를 뺀 값(나쁨 1.5% · 보통 3.5% · 좋음 5%). 세금(퇴직소득세·연금소득세)과 큰 지출(자녀·병원·집 수리)은 따로 넣지 않으면 빠져 있어요. 어림값이에요.", "src": ""},
    ]
