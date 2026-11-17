"""설정 기본값과 병합. 사용자가 바꾼 값만 DB에 저장되고 나머지는 기본값을 따른다."""
import copy

DEFAULTS = {
    # 시세 출처: demo(가상 시세, 시간 압축) | kiwoom(키움 REST)
    "data_source": "demo",
    "demo_speed": 2.0,              # 가상 시세에서 1분이 흐르는 실제 초
    # 엔진별 주문 방식: paper(앱 내 모의) | kiwoom(키움으로 주문 전송)
    "trade_mode": {"scalp": "paper", "dca": "paper"},
    # 엔진별 키움 키. mock=True면 키움 모의투자 서버로 보낸다.
    "kiwoom": {
        "scalp": {"appkey": "", "secretkey": "", "mock": True},
        "dca": {"appkey": "", "secretkey": "", "mock": True},
    },
    # 키움 실전 서버로 주문을 보내려면 이 값이 True여야 한다(화면에서 따로 켬).
    "real_orders_allowed": {"scalp": False, "dca": False},
    "paper_start_cash": {"scalp": 10_000_000, "dca": 10_000_000},
    "costs": {"commission": 0.00015, "tax": 0.0020, "slippage_ticks": 1},
    "watchlist": ["005930", "000660", "035420", "005380", "035720", "068270"],
    "strategy": {
        "name": "orb_vwap",
        "orb_vwap": {"range_min": 15, "vol_mult": 1.5, "atr_n": 14, "stop_atr": 1.5,
                     "target_r": 2.0, "max_day_pct": 8.0, "min_range_pct": 0.4},
        "vwap_pullback": {"min_day_pct": 2.0, "band_pct": 0.3, "lookback": 5,
                          "min_bars": 30, "target_r": 2.0, "max_day_pct": 12.0},
    },
    "exit": {"breakeven_r": 1.0, "max_hold_min": 90},
    "risk": {
        "capital": 10_000_000, "risk_pct": 0.5, "max_position_krw": 3_000_000,
        "max_positions": 3, "daily_loss_krw": 100_000, "max_trades": 8,
        "loss_streak": 3, "cooldown_min": 30, "min_edge_mult": 3.0,
        "entry_start": "09:15", "entry_end": "14:30", "force_exit": "15:15",
    },
    "scalp_state": "stopped",       # running | paused | stopped
    "dca_auto": True,
    "dca_mode": "fixed",            # fixed | rebalance
    "dca_window": {"start": "09:10", "end": "15:00"},
    "holidays": ["2026-12-25", "2026-12-31"],
    "networth_goal": 0,
    "ntfy_topic": "",
    # 휴대폰에서 GitHub Pages 화면으로 접속할 때 허용할 출처와, 이 서버의 바깥 주소
    "allowed_origins": ["https://icekenma.github.io"],
    "public_url": "",
    "auto_update": True,
    # 관심종목(투자 → 시세)
    "watch": {"kr": ["005930", "000660", "035420", "005380", "069500", "360750"],
              "us": ["AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ"]},
    # 문자·알림톡 자동 기록: 카드 승인은 바로 넣고, 입금·출금은 확인 후 넣는 것이 기본
    "inbox_auto": {"card": True, "income": False, "outflow": False},
    # 내 계좌 보기(조회 전용): 주문은 절대 나가지 않는다
    "kiwoom_view": {"appkey": "", "secretkey": "", "mock": False},            # GitHub에 새 버전이 올라오면 노트북 서버가 알아서 받아 다시 시작
}

SECRET_PATHS = [("kiwoom", "scalp", "secretkey"), ("kiwoom", "dca", "secretkey"), ("kiwoom_view", "secretkey")]
MASK = "********"


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Settings:
    def __init__(self, db):
        self.db = db

    def all(self):
        return deep_merge(DEFAULTS, self.db.get_setting("user", {}))

    def get(self, key):
        return self.all()[key]

    def update(self, patch):
        """patch를 기존 사용자 설정에 병합한다. 가려진 비밀키는 바꾸지 않는다."""
        patch = copy.deepcopy(patch)
        for path in SECRET_PATHS:
            node = patch
            for p in path[:-1]:
                node = node.get(p) if isinstance(node, dict) else None
                if node is None:
                    break
            if isinstance(node, dict) and node.get(path[-1]) == MASK:
                del node[path[-1]]
        user = deep_merge(self.db.get_setting("user", {}), patch)
        self.db.set_setting("user", user)
        return self.all()

    def public(self):
        s = self.all()
        for path in SECRET_PATHS:
            node = s
            for p in path[:-1]:
                node = node[p]
            if node.get(path[-1]):
                node[path[-1]] = MASK
        return s
