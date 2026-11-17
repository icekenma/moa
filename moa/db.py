"""SQLite 저장소. 스레드 하나(스케줄러)와 웹 요청이 함께 쓰므로 잠금으로 직렬화한다."""
import json
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS categories(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('in','out')),
  budget INTEGER NOT NULL DEFAULT 0, sort INTEGER NOT NULL DEFAULT 0,
  UNIQUE(name, kind));
CREATE TABLE IF NOT EXISTS transactions(
  id INTEGER PRIMARY KEY, date TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('in','out')),
  amount INTEGER NOT NULL CHECK(amount > 0),
  category_id INTEGER REFERENCES categories(id),
  memo TEXT NOT NULL DEFAULT '', method TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_tx_date ON transactions(date);
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL,
  balance INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
  linked TEXT, sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS networth_snapshots(
  month TEXT PRIMARY KEY, assets INTEGER NOT NULL, debts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS paper_accounts(
  engine TEXT PRIMARY KEY, cash INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS paper_positions(
  engine TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
  qty INTEGER NOT NULL, avg_price REAL NOT NULL,
  PRIMARY KEY(engine, code));
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, engine TEXT NOT NULL,
  mode TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
  side TEXT NOT NULL, qty INTEGER NOT NULL, price REAL,
  status TEXT NOT NULL, fill_price REAL, broker_no TEXT,
  msg TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_orders_ts ON orders(ts);
CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY, engine TEXT NOT NULL, mode TEXT NOT NULL,
  strategy TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
  qty INTEGER NOT NULL, session TEXT NOT NULL,
  entry_ts TEXT NOT NULL, entry_price REAL NOT NULL,
  stop REAL NOT NULL, stop0 REAL NOT NULL, target REAL NOT NULL,
  entry_reason TEXT NOT NULL DEFAULT '',
  exit_ts TEXT, exit_price REAL, reason TEXT, pnl INTEGER, fees INTEGER);
CREATE INDEX IF NOT EXISTS ix_trades_session ON trades(session);
CREATE TABLE IF NOT EXISTS dca_plans(
  id INTEGER PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL,
  amount INTEGER NOT NULL CHECK(amount > 0),
  day INTEGER NOT NULL CHECK(day BETWEEN 1 AND 28),
  target_weight REAL NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dca_runs(
  id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL,
  due_date TEXT NOT NULL, run_ts TEXT NOT NULL,
  qty INTEGER NOT NULL DEFAULT 0, price REAL, amount INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL, msg TEXT NOT NULL DEFAULT '',
  UNIQUE(plan_id, due_date));
CREATE TABLE IF NOT EXISTS engine_log(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, engine TEXT NOT NULL,
  level TEXT NOT NULL, msg TEXT NOT NULL);
"""

DEFAULT_CATEGORIES = [
    ("식비", "out", 0), ("외식", "out", 0), ("교통", "out", 0),
    ("주거·통신", "out", 0), ("생활용품", "out", 0), ("의료", "out", 0),
    ("취미", "out", 0), ("경조사", "out", 0), ("기타지출", "out", 0),
    ("급여", "in", 0), ("부수입", "in", 0), ("이자·배당", "in", 0), ("기타수입", "in", 0),
]


class DB:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        with self.lock:
            self.conn.executescript(SCHEMA)
            if not self.one("SELECT 1 AS x FROM categories LIMIT 1"):
                for i, (name, kind, budget) in enumerate(DEFAULT_CATEGORIES):
                    self.x("INSERT INTO categories(name,kind,budget,sort) VALUES(?,?,?,?)",
                           (name, kind, budget, i))
            self.conn.commit()
            self._migrate()
            self._clear_sample_budgets()

    def _migrate(self):
        """기존 DB에 새 열을 더한다(데이터는 그대로)."""
        cols = {r["name"] for r in self.q("PRAGMA table_info(accounts)")}
        for name in ("bank", "acct_no"):
            if name not in cols:
                self.x(f"ALTER TABLE accounts ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")

    # v0.4: 처음 버전에 예시로 넣었던 예산(합계 167만 원)이 그대로면 0으로 지운다. 사용자가 바꾼 값은 두지 않는다.
    SAMPLE_BUDGETS = {"식비": 450000, "외식": 200000, "교통": 150000, "주거·통신": 520000,
                      "생활용품": 100000, "의료": 50000, "취미": 200000}

    def _clear_sample_budgets(self):
        if self.get_setting("sample_budgets_cleared"):
            return
        rows = {r["name"]: r["budget"] for r in self.q("SELECT name, budget FROM categories WHERE kind='out'")}
        if all(rows.get(k) == v for k, v in self.SAMPLE_BUDGETS.items()):
            self.x("UPDATE categories SET budget=0 WHERE kind='out'")
        self.set_setting("sample_budgets_cleared", True)

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        with self.lock:
            r = self.conn.execute(sql, args).fetchone()
            return dict(r) if r else None

    def x(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    def get_setting(self, key, default=None):
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(r["value"]) if r else default

    def set_setting(self, key, value):
        self.x("INSERT INTO settings(key,value) VALUES(?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value, ensure_ascii=False)))

    def log(self, engine, level, msg, ts):
        self.x("INSERT INTO engine_log(ts,engine,level,msg) VALUES(?,?,?,?)",
               (ts, engine, level, msg))
