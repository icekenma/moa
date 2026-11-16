"""거래 비용: 호가 단위, 슬리피지, 수수료, 거래세."""
import math


def tick_size(price):
    """KRX 주식 호가 단위(2023년 이후 코스피·코스닥 공통)."""
    p = float(price)
    if p < 2000:
        return 1
    if p < 5000:
        return 5
    if p < 20000:
        return 10
    if p < 50000:
        return 50
    if p < 200000:
        return 100
    if p < 500000:
        return 500
    return 1000


def round_to_tick(price, up=False):
    t = tick_size(price)
    f = math.ceil if up else math.floor
    return f(price / t) * t


def buy_fill(price, costs):
    return price + costs["slippage_ticks"] * tick_size(price)


def sell_fill(price, costs):
    return max(1, price - costs["slippage_ticks"] * tick_size(price))


def buy_fee(amount, costs):
    return int(round(amount * costs["commission"]))


def sell_fee(amount, costs):
    return int(round(amount * costs["commission"])) + int(round(amount * costs["tax"]))


def round_trip_pct(price, costs):
    """매수 후 같은 가격에 팔 때 잃는 비율(%). 수수료·세금·양방향 슬리피지 포함."""
    slip = 2 * costs["slippage_ticks"] * tick_size(price) / price
    return (2 * costs["commission"] + costs["tax"] + slip) * 100


def trade_pnl(entry_fill, exit_fill, qty, costs):
    buy_amt = entry_fill * qty
    sell_amt = exit_fill * qty
    fees = buy_fee(buy_amt, costs) + sell_fee(sell_amt, costs)
    return int(round(sell_amt - buy_amt - fees)), fees
