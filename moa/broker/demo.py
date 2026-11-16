"""가상 시세. 실제 시장과 무관한 무작위 데이터로, 화면과 엔진 동작을 장 시간이 아니어도 확인하는 용도다.

시간이 압축되어 흐른다: demo_speed초마다 가상 1분이 지나고, 15:30이 지나면 다음 날 09:00으로 넘어간다.
"""
import random
import threading
import time
from datetime import date, datetime, timedelta

NAMES = {
    "005930": ("삼성전자", 77000), "000660": ("SK하이닉스", 198000), "035420": ("NAVER", 205000),
    "005380": ("현대차", 251000), "035720": ("카카오", 41000), "068270": ("셀트리온", 182000),
    "069500": ("KODEX 200", 39400), "360750": ("TIGER 미국S&P500", 22150),
    "458730": ("TIGER 미국배당다우존스", 11785), "133690": ("TIGER 미국나스닥100", 128000),
}
SESSION_MIN = 390  # 09:00 ~ 15:30


def minute_label(i):
    m = 9 * 60 + i
    return f"{m // 60:02d}:{m % 60:02d}"


def demo_day(code, day, base=None):
    """하루치 분봉 390개. 같은 (종목, 날짜)는 항상 같은 결과."""
    rng = random.Random(f"{code}-{day.isoformat()}")
    price = float(base or NAMES.get(code, ("", 50000))[1])
    price *= 1 + rng.gauss(0, 0.012)          # 갭
    trend = rng.choice([0, 0, 0, 1, -1])      # 추세일 비율 40%
    drift = trend * rng.uniform(0.0002, 0.0008)
    vol = rng.uniform(0.0012, 0.0028)
    bars = []
    for i in range(SESSION_MIN):
        # U자형 거래량, 장 초반 변동성 큼
        u = 1.8 if i < 20 else (1.4 if i > 360 else 1.0)
        o = price
        ret = drift * (1 if i < 200 else 0.4) + rng.gauss(0, vol * u)
        if rng.random() < 0.01:
            ret += rng.choice([-1, 1]) * vol * 6  # 가끔 급등락
        c = max(100.0, o * (1 + ret))
        h = max(o, c) * (1 + abs(rng.gauss(0, vol * 0.5)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, vol * 0.5)))
        v = int(rng.uniform(0.5, 1.5) * 20000 * u * (1 + 40 * abs(ret)))
        bars.append({"t": minute_label(i), "o": round(o), "h": round(h), "l": round(lo), "c": round(c), "v": v})
        price = c
    return bars


class DemoFeed:
    kind = "demo"

    def __init__(self, speed=2.0, start_day=None):
        self.speed = max(0.05, float(speed))
        self.t0 = time.time()
        self.day0 = start_day or date.today()
        self._cache = {}
        self._lock = threading.Lock()
        self._closes = {}

    def _clock(self):
        elapsed_min = int((time.time() - self.t0) / self.speed)
        day_idx, minute = divmod(elapsed_min, SESSION_MIN + 30)  # 장 끝나고 30분 뒤 다음 날
        d = self.day0
        for _ in range(day_idx):
            d += timedelta(days=1)
            while d.weekday() >= 5:
                d += timedelta(days=1)
        return d, minute

    def now(self):
        d, minute = self._clock()
        return datetime.combine(d, datetime.min.time()) + timedelta(minutes=9 * 60 + minute)

    def market_open(self):
        _, minute = self._clock()
        return minute < SESSION_MIN

    def _day(self, code, d):
        key = (code, d)
        with self._lock:
            if key not in self._cache:
                prev = self._closes.get(code)
                self._cache[key] = demo_day(code, d, prev)
                self._closes[code] = self._cache[key][-1]["c"]
                if len(self._cache) > 200:
                    self._cache.pop(next(iter(self._cache)))
            return self._cache[key]

    def name(self, code):
        return NAMES.get(code, (code, 0))[0]

    def bars(self, code):
        """완성된 분봉만."""
        d, minute = self._clock()
        return list(self._day(code, d)[:min(minute, SESSION_MIN)])

    def quote(self, code):
        d, minute = self._clock()
        day = self._day(code, d)
        b = day[min(minute, SESSION_MIN - 1)]
        done = day[:min(minute, SESSION_MIN - 1) + 1]
        return {"code": code, "name": self.name(code), "price": b["c"], "open": day[0]["o"],
                "high": max(x["h"] for x in done), "low": min(x["l"] for x in done),
                "volume": sum(x["v"] for x in done)}
