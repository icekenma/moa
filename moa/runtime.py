"""시세·브로커·엔진을 설정대로 묶고, 백그라운드에서 엔진을 돌린다."""
import threading
import time
from datetime import date, datetime

import requests

from .assets import Assets
from .broker.brokers import KiwoomBroker, PaperBroker
from .broker.demo import DemoFeed
from .broker.kiwoom import KiwoomClient, KiwoomFeed
from .inbox import Inbox
from .ledger import Ledger
from .market import Market
from .myaccount import AccountView
from .settings import Settings
from .trading.dca import DcaEngine
from .trading.scalp import ScalpEngine


class Runtime:
    def __init__(self, db, start_thread=True, data_dir=None):
        self.db = db
        db.x("CREATE TABLE IF NOT EXISTS notify_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, cat TEXT NOT NULL, title TEXT, msg TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0)")
        _log = db.log

        def log_and_tell(engine, level, msg, ts):          # 엔진 오류는 기록과 함께 휴대폰으로도(같은 오류는 1시간에 한 번)
            _log(engine, level, msg, ts)
            if level == "error":
                try:
                    self.notify(str(msg)[:180], "urgent", "우석World · 엔진 오류", key="err:" + str(msg)[:60], quiet_min=60)
                except Exception:
                    pass
        db.log = log_and_tell
        self.settings = Settings(db)
        self.ledger = Ledger(db)
        self.account = AccountView(self.settings, fx=lambda: self.market.yh_quote("USDKRW=X")["price"])
        self.inbox = Inbox(db, self.ledger, self.settings)
        from .fixed import Fixed
        self.fixed = Fixed(self)
        self.inbox.fixed = self.fixed
        self.market = Market(self, data_dir or ".")
        from .invest import Invest
        self.invest = Invest(self)
        from .ai import AI
        self.ai = AI(self)
        from .aipick import AIPick
        self.aipick = AIPick(self)
        from .dividend import Dividend
        self.dividend = Dividend(self)
        from .evolution import Evolution
        self.evolution = Evolution(self)
        from .autotrade import AutoTrade
        self.autotrade = AutoTrade(self)
        from .payoff import Payoff
        self.payoff = Payoff(self)
        from .trading.sweep import SweepJob
        self.sweep = SweepJob(self, data_dir or ".")
        from .backup import Backup
        self.backup = Backup(self, data_dir)
        self.assets = Assets(db, self.value_of, kiwoom_on=lambda: self.account.configured())
        from .health import Health
        self.health = Health(db)
        from .album import Album
        self.album = Album(db)
        self.feed = None
        self.brokers = {}
        self.errors = {}
        self._feed_key = None
        self._lock = threading.RLock()
        self.rebuild()
        self.scalp = ScalpEngine(self)
        if self.settings.get("scalp_state") != "stopped":  # 화면이 없어진 엔진이 몰래 돌지 않게
            self.settings.update({"scalp_state": "stopped"})
        self.dca = DcaEngine(self)
        self._stop = threading.Event()
        self._last_dca = 0.0
        self._thread = None
        if start_thread:
            self._thread = threading.Thread(target=self._loop, name="moa-engine", daemon=True)
            self._thread.start()
            self.market.warm()

    # ---------- 구성 ----------
    def _client(self, engine):
        k = self.settings.get("kiwoom")[engine]
        return KiwoomClient(k["appkey"], k["secretkey"], k["mock"])

    def rebuild(self):
        with self._lock:
            s = self.settings.all()
            self.errors = {}
            key = (s["data_source"], s["demo_speed"], repr(s["kiwoom"]), repr(s["kiwoom_view"]))
            if key != self._feed_key or self.feed is None:
                feed = None
                if s["data_source"] == "kiwoom":
                    for eng in ("scalp", "dca"):  # 주문 계좌 키 → 없으면 내 계좌(조회 전용) 키로 시세만 받는다
                        k = s["kiwoom"][eng]
                        if k.get("appkey") and k.get("secretkey"):
                            feed = KiwoomFeed(self._client(eng))
                            break
                    if feed is None and self.account.configured():
                        feed = KiwoomFeed(self.account.client())
                    if feed is None:
                        self.errors["feed"] = "키움 시세를 쓰려면 설정에서 키움 키를 넣어 주세요. 지금은 가상 시세로 돌아요"
                if feed is None:
                    feed = DemoFeed(s["demo_speed"])
                self.feed = feed
                self._feed_key = key if not self.errors.get("feed") else None
            brokers = {}
            for eng in ("scalp", "dca"):
                try:
                    if s["trade_mode"][eng] == "kiwoom":
                        client = self._client(eng)
                        if not client.mock and not s["real_orders_allowed"][eng]:
                            raise RuntimeError("키움 실전 주문이 꺼져 있어요. 설정에서 실전 주문을 허용해 주세요")
                        brokers[eng] = KiwoomBroker(client, self.feed)
                    else:
                        brokers[eng] = PaperBroker(self.db, eng, self.feed, s["costs"], s["paper_start_cash"][eng])
                except Exception as e:
                    brokers[eng] = None
                    self.errors[eng] = str(e)
            self.brokers = brokers

    def value_of(self, engine):
        if engine == "kiwoom":
            return self.account.total()
        b = self.brokers.get(engine)
        if b is None:
            raise RuntimeError(self.errors.get(engine) or "계좌가 준비되지 않았어요")
        return b.cash() + sum(h["qty"] * h["price"] for h in b.positions())

    # ---------- 휴대폰 알림(ntfy) ----------
    # 갈래(cat): urgent 꼭 알아야 할 것(손실 한도·주문 실패·보안·엔진 오류) / real 실전 매매·실전 플랜 변화 / daily 장 마감 요약
    #           / paper 모의 매매 하나하나(기본 끔: 요약으로 충분) / ledger 가계부 자동 기록 / info 그 밖(가격 알림·배당·고정비·복기)
    NOTIFY_CATS = {"urgent": ("꼭 알아야 할 것", 5, "rotating_light"), "real": ("실전 자동매매", 4, "chart_with_upwards_trend"),
                   "daily": ("장 마감 요약", 3, "bar_chart"), "paper": ("모의 매매 하나하나", 2, "test_tube"),
                   "ledger": ("가계부 자동 기록", 2, "receipt"), "info": ("그 밖의 알림", 3, "bell")}
    NOTIFY_OFF = ("paper",)          # 기본으로 꺼 둔 갈래

    def notify_on(self, cat):
        conf = self.settings.get("notify") or {}
        return bool(conf.get(cat, cat not in self.NOTIFY_OFF)) or cat == "urgent"

    def notify(self, msg, cat="info", title=None, key=None, quiet_min=0, force=False):
        """휴대폰으로 알린다. key를 주면 같은 key는 quiet_min분(0이면 하루) 안에 한 번만 보낸다. 보낸 것·걸러진 것 모두 알림 기록에 남는다."""
        topic = self.settings.get("ntfy_topic")
        now = time.time()
        if key:
            seen = self._notified = getattr(self, "_notified", {})
            last = seen.get(key)
            if last and now - last < (quiet_min or 1440) * 60:
                return False
            seen[key] = now
            if len(seen) > 500:
                for k in sorted(seen, key=seen.get)[:200]:
                    seen.pop(k, None)
        label, prio, tag = self.NOTIFY_CATS.get(cat, self.NOTIFY_CATS["info"])
        on = bool(topic) and (force or self.notify_on(cat))
        try:
            self.db.x("INSERT INTO notify_log(ts,cat,title,msg,sent) VALUES(?,?,?,?,?)",
                      (datetime.now().isoformat(timespec="seconds"), cat, title or label, msg, 1 if on else 0))
            self.db.x("DELETE FROM notify_log WHERE id < (SELECT MAX(id) FROM notify_log) - 300")
        except Exception:
            pass
        if not on:
            return False
        body = {"topic": topic, "message": msg, "title": title or f"우석World · {label}", "priority": prio, "tags": [tag]}
        url = (self.settings.get("public_url") or "").strip()
        if url:
            body["click"] = url

        def send():
            try:
                requests.post("https://ntfy.sh/", json=body, timeout=6)
            except Exception:
                pass
        threading.Thread(target=send, daemon=True).start()
        return True

    def notify_log(self, n=40):
        return self.db.q("SELECT ts, cat, title, msg, sent FROM notify_log ORDER BY id DESC LIMIT ?", (n,))

    # ---------- 반복 ----------
    def _loop(self):
        while not self._stop.is_set():
            try:
                with self._lock:
                    # 단타 엔진은 화면에서 뺐다(AI 탭 자동매매로 대체 예정). 엔진은 멈춘 채로 둔다.
                    try:
                        self.aipick.check()  # AI 추천 종목 자동 익절·손절(5초마다)
                        self.aipick.evaluate()  # AI 추천 성적 채점(4분마다)
                    except Exception as e:
                        self.db.log("system", "error", f"AI 익절·손절 확인 오류: {e}", datetime.now().isoformat(timespec="seconds"))
                    try:
                        self.autotrade.tick()  # AI 자동매매: 보유 종목 감시·새 매수(5초마다)
                    except Exception as e:
                        self.db.log("system", "error", f"AI 자동매매 오류: {e}", datetime.now().isoformat(timespec="seconds"))
                    if time.time() - self._last_dca > 30:
                        self._last_dca = time.time()
                        try:
                            self.invest.check_alerts()  # 가격 알림
                        except Exception as e:
                            self.db.log("system", "error", f"가격 알림 확인 오류: {e}", datetime.now().isoformat(timespec="seconds"))
                        try:
                            self.fixed.run()  # 고정 지출 자동 기록·알림(5분마다)
                        except Exception as e:
                            self.db.log("system", "error", f"고정 지출 처리 오류: {e}", datetime.now().isoformat(timespec="seconds"))
                        try:
                            self.backup.auto_due()  # 하루 한 번 자동 백업
                        except Exception as e:
                            self.db.log("system", "error", f"자동 백업 오류: {e}", datetime.now().isoformat(timespec="seconds"))
                        try:
                            self.dividend.daily_due()  # 하루 한 번 배당 자료 모으기(뒤에서)
                        except Exception as e:
                            self.db.log("system", "error", f"배당 자료 모으기 오류: {e}", datetime.now().isoformat(timespec="seconds"))
                        self.dca.run_due(datetime.now())
                        if datetime.now().strftime("%H:%M") >= "15:40":
                            self.dca.snapshot(date.today())
            except Exception as e:
                try:
                    self.db.log("system", "error", f"엔진 반복 중 오류: {e}", datetime.now().isoformat(timespec="seconds"))
                except Exception:
                    pass
            self._stop.wait(1.0 if self.feed and self.feed.kind == "demo" else 3.0)

    def stop(self):
        self._stop.set()
