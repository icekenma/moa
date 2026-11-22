"""몬테카를로 위험 계산(표준 라이브러리만).

과거에 실제로 있었던 하루하루(또는 거래 하나하나)를 무작위로 다시 뽑아 '앞으로 있을 법한 길'을 수천 개 만들고,
그 길들의 분포로 위험을 잰다. 블랙록 알라딘이 위험 요인을 흔들어 시나리오를 만드는 것과 같은 생각을, 개인 계좌 크기로 줄인 것.

지키는 원칙
- 같은 자료면 늘 같은 답(씨앗을 자료에서 만든다): 화면을 새로 고칠 때마다 숫자가 흔들리지 않게.
- 종목들은 '같은 날'의 수익률을 함께 뽑는다: 같이 오르내리는 성질(상관)이 그대로 남는다.
- 며칠씩 묶어 뽑는다(block): 흔들림이 큰 날이 몰려 오는 성질이 남는다.
- 평균은 0으로 놓는다: 최근에 올랐다고 앞으로도 오른다고 가정하지 않는다(위험을 작게 보지 않으려고).
어림값이다. 과거에 없던 크기의 충격은 나오지 않으므로, 실제 손실은 더 클 수 있다.
"""
import random

PATHS = 3000


def _q(sorted_xs, q):
    if not sorted_xs:
        return None
    i = q * (len(sorted_xs) - 1)
    lo = int(i)
    hi = min(lo + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (i - lo)


def _seed(*xs):
    s = 0
    for x in xs:
        s = (s * 1000003 + int(round(float(x) * 1e6))) % 2147483647
    return s or 7


def mix(weights, rets, floors=None, demean=False):
    """종목별 일간 수익률 → 묶음(계좌) 하루 수익률 줄. 같은 날끼리 더한다. floors: 종목별 하루 손실의 바닥(손절선, 음수) 또는 None.
    demean: 종목마다 평균을 먼저 뺀다(바닥을 적용하기 전에 빼야 바닥 아래로 내려가지 않는다)."""
    use = [(w, ([v - sum(r) / len(r) for v in r] if demean else r), (floors[i] if floors else None)) for i, (w, r) in enumerate(zip(weights, rets)) if r and w]
    if not use:
        return []
    T = min(len(r) for _, r, _ in use)
    out = []
    for t in range(T):
        d = 0.0
        for w, r, f in use:
            v = r[len(r) - T + t]
            d += w * (max(v, f) if f is not None else v)
        out.append(d)
    return out


def paths(day, days=20, n=PATHS, block=5, demean=True):
    """하루 수익률 줄(day)로 앞으로 days일의 길 n개. 결과: 끝 수익률·중간 최대 낙폭의 분포(%)와 날짜별 띠."""
    if len(day) < 10:
        return None
    m = sum(day) / len(day) if demean else 0.0
    xs = [v - m for v in day]
    T = len(xs)
    rng = random.Random(_seed(len(xs), days, *xs[-8:]))
    ends, mdds = [], []
    marks = sorted({max(1, round(days * k / 4)) for k in (1, 2, 3, 4)})
    at = {d: [] for d in marks}
    for _ in range(n):
        eq, peak, mdd, t = 1.0, 1.0, 0.0, 0
        while t < days:
            s = rng.randrange(T)
            for k in range(min(block, days - t)):
                eq *= 1 + xs[(s + k) % T]
                peak = max(peak, eq)
                mdd = max(mdd, 1 - eq / peak)
                t += 1
                if t in at:
                    at[t].append(eq - 1)
        ends.append(eq - 1)
        mdds.append(mdd)
    ends.sort()
    mdds.sort()
    tail = ends[:max(1, n // 20)]
    P = lambda xs_, q: round(_q(xs_, q) * 100, 2)
    band = []
    for d in marks:
        v = sorted(at[d])
        band.append({"d": d, "p5": P(v, .05), "p25": P(v, .25), "p50": P(v, .5), "p75": P(v, .75), "p95": P(v, .95)})
    return {"n": n, "days": days, "p5": P(ends, .05), "p25": P(ends, .25), "p50": P(ends, .5), "p75": P(ends, .75), "p95": P(ends, .95),
            "var95": round(-_q(ends, .05) * 100, 2), "cvar95": round(-sum(tail) / len(tail) * 100, 2),
            "p_loss5": round(sum(1 for e in ends if e <= -0.05) / n * 100, 1), "p_loss10": round(sum(1 for e in ends if e <= -0.10) / n * 100, 1),
            "mdd50": P(mdds, .5), "mdd95": P(mdds, .95), "band": band}


def one_day_var(weights, rets, floors=None, n=2000):
    """보유 묶음의 '나쁜 날' 하루 손실(95%, 굴리는 돈 대비 %). 자료가 모자라면 None."""
    day = mix(weights, rets, floors, demean=True)
    r = paths(day, days=1, n=n, block=1, demean=False)
    return None if r is None else max(0.0, r["var95"])


def prob_positive(xs, rounds=800):
    """'이 거래들의 평균이 0보다 크다'가 우연이 아닐 가능성(0~1). 같은 수만큼 다시 뽑아 평균을 내 보기를 되풀이한다."""
    if len(xs) < 5:
        return None
    rng = random.Random(_seed(len(xs), sum(xs)))
    n, hit = len(xs), 0
    for _ in range(rounds):
        hit += sum(rng.choice(xs) for _ in range(n)) > 0
    return round(hit / rounds, 3)


def trades_forward(steps, n_next, loss_now=0.0, loss_limit=None, n=PATHS):
    """거래 하나가 굴리는 돈을 얼마나 움직였는지(steps, %)를 다시 뽑아 앞으로 n_next번의 거래를 그려 본다.
    loss_now: 지금까지 잃은 폭(%, 양수), loss_limit: 전체 손실 한도(%). 결과: 한도에 닿을 확률, 끝 손익 분포, 가장 깊은 낙폭."""
    if len(steps) < 10 or n_next < 1:
        return None
    rng = random.Random(_seed(len(steps), n_next, sum(steps), loss_now))
    ends, mdds, ruin = [], [], 0
    for _ in range(n):
        eq, peak, mdd, hit = -loss_now, 0.0, 0.0, False
        for _ in range(n_next):
            eq += rng.choice(steps)
            peak = max(peak, eq)
            mdd = max(mdd, peak - eq)
            if loss_limit is not None and eq <= -loss_limit:
                hit = True
                break
        ruin += hit
        ends.append(eq + loss_now)                    # 지금부터의 변화
        mdds.append(mdd)
    ends.sort()
    mdds.sort()
    return {"n": n, "next": n_next, "p_ruin": round(ruin / n * 100, 1), "p_profit": round(sum(1 for e in ends if e > 0) / n * 100, 1),
            "p5": round(_q(ends, .05), 2), "p50": round(_q(ends, .5), 2), "p95": round(_q(ends, .95), 2), "mdd95": round(_q(mdds, .95), 2)}
