"""Claude API 연계: 투자 화면에서 바로 묻는 AI 비서.

- 종목 분석: 시세·차트·내 보유 + 웹 검색(최근 뉴스)으로 한눈에 정리
- 포트폴리오 점검: 국내·미국 보유, 현금, 적립 계획을 보고 쏠림·위험 점검
- 단타 복기: 오늘 거래와 엔진 기록으로 잘된 점·문제점·규칙 제안
- 조건 훑기 해석: 결과를 과최적화 관점에서 풀어 설명
- 자유 질문: 내 계좌·관심종목을 알고 있는 상태로 대화

원칙
- AI는 주문하지 않는다(주문 도구를 주지 않음). 매수·매도 지시 대신 판단 근거와 시나리오를 준다.
- 키는 노트북에만 저장하고 화면에는 가린다. 월 예산을 넘으면 호출하지 않는다.
- 답변은 글자가 나오는 대로 휴대폰에 흘려보낸다(스트리밍).
"""
import json
from datetime import datetime, timezone

import requests

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MODELS = {  # 화면에 보여 줄 모델과 백만 토큰당 가격(USD, 입력/출력)
    "claude-fable-5-2": ("Fable 5.2 · 최상위 추론", 8.0, 40.0),
    "claude-sonnet-5-5": ("Sonnet 5.5 · 빠르고 똑똑함(추천)", 2.0, 10.0),
    "claude-opus-5-5": ("Opus 5.5 · 깊게 분석", 4.0, 20.0),
    "claude-haiku-4-5": ("Haiku 4.5 · 가장 빠르고 저렴", 1.0, 5.0),
}
SEARCH_USD = 0.01  # 웹 검색 1회
LITE_MODEL = "claude-haiku-4-5"  # 자동매매의 '빠른 재추천'에 쓰는 가장 싼 엔진(검색 없이, 짧은 답만 받는다)
REVIEW_MODEL = "claude-opus-5-5"  # 쌓인 기록을 복기하는 엔진(가끔만 돌아서 좋은 엔진을 써도 비용이 작다). 설정 → AI의 '복기 엔진'
REVIEW_KINDS = ("auto", "learn")  # 자동매매 복기 · 추천 성적 복기
FX_DEFAULT = 1400.0

SYSTEM = """너는 한국 개인 투자자의 앱(moa) 안에 있는 투자 도우미야.
- 한국어로, 두괄식·개조식으로 짧게 답해. 첫 줄에 핵심 한 줄. 소제목은 '## ', 항목은 '- '로.
- 매수·매도를 지시하지 마. 대신 판단 근거, 상승·하락 시나리오, 확인할 지표·일정, 위험을 알려 줘.
- 숫자는 주어진 데이터에 있는 것만 쓰고, 모르면 모른다고 해. 데이터 시각을 밝혀.
- 웹 검색을 했다면 근거가 된 기사·공시를 언급해(출처는 앱이 따로 붙여 보여 줌).
- 확실하지 않은 전망을 단정하지 말고, 과거 성과가 미래를 보장하지 않는다는 점을 필요할 때만 한 줄로.
- 투자 결정과 책임은 사용자에게 있다는 걸 전제로, 실용적으로 도와."""


class AIError(RuntimeError):
    pass


class Blocks:
    """스트리밍으로 받은 답의 내용 블록을 다시 조립한다. pause_turn 뒤 '받은 그대로' 돌려보내 이어받는 데 쓴다."""

    def __init__(self):
        self.items, self.json = {}, {}

    def feed(self, ev):
        typ, i = ev.get("type"), ev.get("index")
        if typ == "content_block_start":
            self.items[i] = dict(ev.get("content_block") or {})
            self.json[i] = ""
        elif typ == "content_block_delta" and i in self.items:
            b, dl = self.items[i], ev.get("delta") or {}
            t = dl.get("type")
            if t == "text_delta":
                b["text"] = (b.get("text") or "") + dl.get("text", "")
            elif t == "input_json_delta":
                self.json[i] += dl.get("partial_json", "")
            elif t == "citations_delta":
                b.setdefault("citations", [])
                if not isinstance(b["citations"], list):
                    b["citations"] = []
                b["citations"].append(dl.get("citation"))
            elif t == "thinking_delta":
                b["thinking"] = (b.get("thinking") or "") + dl.get("thinking", "")
            elif t == "signature_delta":
                b["signature"] = dl.get("signature")
        elif typ == "content_block_stop" and i in self.items and self.json.get(i):
            try:
                self.items[i]["input"] = json.loads(self.json[i])
            except ValueError:
                self.items[i]["broken"] = True

    def content(self):
        out = []
        for i in sorted(self.items):
            b = dict(self.items[i])
            if b.pop("broken", None):
                continue
            if b.get("type") == "text":
                if not (b.get("text") or "").strip():
                    continue
                if not b.get("citations"):
                    b.pop("citations", None)
            out.append(b)
        if out and out[-1].get("type") == "text":       # 마지막 글 블록은 공백으로 끝나면 안 된다
            out[-1]["text"] = out[-1]["text"].rstrip()
        return out


def _model_price(model):
    for k, (_, i, o) in MODELS.items():
        if model.startswith(k):
            return i, o
    return 2.0, 10.0


class AI:
    def __init__(self, rt):
        self.rt = rt
        rt.db.x("""CREATE TABLE IF NOT EXISTS ai_usage(
            id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, model TEXT NOT NULL,
            in_tok INTEGER NOT NULL, out_tok INTEGER NOT NULL, searches INTEGER NOT NULL, usd REAL NOT NULL)""")
        self.session = requests.Session()

    # ---------- 설정·사용량 ----------
    def conf(self):
        return self.rt.settings.get("ai")

    def configured(self):
        return bool((self.conf().get("api_key") or "").strip())

    def month_usd(self):
        r = self.rt.db.one("SELECT COALESCE(SUM(usd),0) AS s, COUNT(*) AS n FROM ai_usage WHERE ts >= ?",
                           (datetime.now().strftime("%Y-%m-01"),))
        return float(r["s"]), int(r["n"])

    # ---------- 남은 크레딧 ----------
    # 콘솔 잔액을 직접 알려 주는 공개 API가 없어서: 사용자가 콘솔에서 본 잔액(기준)을 넣으면
    # 그 뒤 moa가 쓴 금액(실시간)과, Admin 키가 있으면 콘솔 전체 사용액(약 5분 지연)을 빼서 보여 준다.
    def _admin_cost_since_day(self, day_iso):
        key = (self.conf().get("admin_key") or "").strip()
        if not key:
            return None
        cache = getattr(self, "_admin_cache", None)
        if cache and cache[0] == day_iso and (datetime.now() - cache[1]).total_seconds() < 60:
            return cache[2]
        try:
            r = self.session.get("https://api.anthropic.com/v1/organizations/cost_report",
                                 params={"starting_at": day_iso + "T00:00:00Z", "limit": 31},
                                 headers={"x-api-key": key, "anthropic-version": API_VERSION}, timeout=10)
            if r.status_code != 200:
                self._admin_err = f"Admin API 오류({r.status_code})"
                return None
            total = 0.0
            for b in r.json().get("data") or []:
                for x in b.get("results") or []:
                    total += float(x.get("amount") or 0) / 100  # 센트 단위 문자열
            self._admin_err = None
            self._admin_cache = (day_iso, datetime.now(), total)
            return total
        except Exception as e:
            self._admin_err = f"Admin API 연결 실패: {e}"
            return None

    def set_credit(self, usd):
        try:
            usd = round(float(usd), 2)
        except (TypeError, ValueError):
            raise AIError("잔액은 숫자(달러)로 넣어 주세요")
        if usd < 0:
            raise AIError("잔액을 확인해 주세요")
        now = datetime.now(timezone.utc)
        base = self._admin_cost_since_day(now.strftime("%Y-%m-%d")) or 0.0
        self.rt.settings.update({"ai": {"credit": {"usd": usd, "at": datetime.now().isoformat(timespec="seconds"),
                                                   "utc_day": now.strftime("%Y-%m-%d"), "base_admin": base}}})
        self.rt.db.set_setting("ai_credit_low", False)
        return self.credit()

    def credit(self):
        cr = self.conf().get("credit") or {}
        if cr.get("usd") is None:
            return {"set": False}
        moa = float(self.rt.db.one("SELECT COALESCE(SUM(usd),0) AS s FROM ai_usage WHERE ts >= ?", (cr["at"],))["s"])
        admin = None
        if cr.get("utc_day"):
            tot = self._admin_cost_since_day(cr["utc_day"])
            if tot is not None:
                admin = max(0.0, tot - float(cr.get("base_admin") or 0))
        spent = max(moa, admin or 0.0)  # 콘솔 집계가 늦게 잡히는 동안은 moa 실시간 사용액으로
        left = max(0.0, float(cr["usd"]) - spent)
        return {"set": True, "start_usd": cr["usd"], "since": cr["at"], "spent_moa": round(moa, 4),
                "spent_admin": None if admin is None else round(admin, 4), "spent": round(spent, 4),
                "left": round(left, 4), "left_pct": round(left / cr["usd"] * 100, 1) if cr["usd"] else 0,
                "admin_error": getattr(self, "_admin_err", None) if self.conf().get("admin_key") else None,
                "exhausted": bool(self.rt.db.get_setting("ai_credit_low")),
                "source": "콘솔 전체(Admin API)" if admin is not None and admin >= moa else "moa 사용분"}

    # 화면 공통 비용 표시: 예상 비용 + 현재 잔액
    KIND_TOK = {"stock": (9000, 1500), "portfolio": (7000, 1600), "review": (9000, 1500), "sweep": (7000, 1500),
                "chat": (6000, 1200), "pick": (16000, 2200), "pickw": (18000, 2600), "pickq": (4500, 650), "learn": (14000, 1500), "divplan": (15000, 2600), "dca": (5000, 1500), "auto": (12000, 1800), "test": (30, 10),
                "brief": (3000, 900), "hold": (2500, 600), "counter": (2500, 600)}

    def fx(self):
        try:
            return float(self.rt.market.yh_quote("USDKRW=X")["price"]) or FX_DEFAULT
        except Exception:
            return FX_DEFAULT

    def review_model(self):
        m = self.conf().get("review_model")
        return m if m in MODELS else REVIEW_MODEL

    def meter(self, kind="chat", model=None, web=None):
        c = self.conf()
        model = model if model in MODELS else self.review_model() if kind in REVIEW_KINDS else (c.get("model") or "claude-sonnet-5-5")
        web = c.get("web_search", True) if web is None else web
        if kind in ("sweep", "test", "learn", "dca", "auto", "pickq"):
            web = False
        pi, po = _model_price(model)
        tin, tout = self.KIND_TOK.get(kind, (6000, 1200))
        lo = (tin * pi + tout * po) / 1e6
        ns = 6 if kind == "pickw" else 3  # 웹 검색 최대 횟수
        hi = ((tin + (10000 * ns if web else 0)) * pi + tout * 1.8 * po) / 1e6 + (ns * SEARCH_USD if web else 0)
        rows = self.rt.db.q("SELECT usd FROM ai_usage WHERE kind=? AND model=? ORDER BY id DESC LIMIT 10", (kind, model))
        avg = sum(r["usd"] for r in rows) / len(rows) if rows else None
        fx = self.fx()
        cr = self.credit()
        return {"kind": kind, "model": model, "model_name": MODELS.get(model, (model,))[0].split(" · ")[0], "web": bool(web),
                "usd_lo": round(lo, 4), "usd_hi": round(hi, 4), "avg_usd": None if avg is None else round(avg, 4), "n": len(rows),
                "fx": fx, "configured": self.configured(), "credit": cr,
                "month_usd": round(self.month_usd()[0], 4), "budget_usd": c.get("budget_usd")}

    def status(self):
        c = self.conf()
        used, n = self.month_usd()
        return {"configured": self.configured(), "model": c.get("model"), "review_model": self.review_model(), "budget_usd": c.get("budget_usd"),
                "web_search": c.get("web_search", True), "month_usd": round(used, 4), "month_calls": n,
                "models": {k: v[0] for k, v in MODELS.items()}, "credit": self.credit(),
                "recent": self.rt.db.q("SELECT ts, kind, model, in_tok, out_tok, searches, usd FROM ai_usage "
                                       "ORDER BY id DESC LIMIT 10")}

    def _record(self, kind, model, usage):
        i, o = _model_price(model)
        usd = (usage["in"] * i + usage["out"] * o) / 1e6 + usage["searches"] * SEARCH_USD
        self.rt.db.x("INSERT INTO ai_usage(ts,kind,model,in_tok,out_tok,searches,usd) VALUES(?,?,?,?,?,?,?)",
                     (datetime.now().isoformat(timespec="seconds"), kind, model, usage["in"], usage["out"],
                      usage["searches"], usd))
        return usd

    # ---------- 화면별 자료 모으기 ----------
    def context(self, kind, d):
        rt, m = self.rt, self.rt.market
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        if kind == "stock":
            mk = "us" if d.get("mkt") == "us" else "kr"
            sym = str(d.get("sym") or "").upper()
            if not sym:
                raise AIError("종목을 골라 주세요")
            q = dict(m.kr_quote(sym) if mk == "kr" else m.us_live(sym))
            q.pop("spark", None)
            try:
                ch = m.kr_chart(sym, "3m") if mk == "kr" else m.yh_chart(sym, "3m")
                closes = [(p["t"], round(p["c"], 2)) for p in ch.get("points", [])][-60:]
            except Exception:
                closes = []
            hold = m.holding(sym) if mk == "kr" else m.us_holding(sym)
            name = q.get("name") or sym
            ask = (f"{name}({sym}, {'국내' if mk == 'kr' else '미국'})를 분석해 줘. 최근 1~2주 뉴스·공시를 웹에서 찾아 "
                   "주가 움직임과 연결하고, 지금 체크할 점을 정리해 줘."
                   + (" 내 보유분 기준으로도 봐 줘." if hold else ""))
            data = {"기준시각": now, "현재 시세": q, "최근 3개월 종가(날짜, 종가)": closes, "내 보유": hold}
            return f"{name} 분석", ask, data
        if kind == "portfolio":
            snap = rt.account.snapshot() if rt.account.configured() else None
            dca = []
            try:
                dca = [{k: p.get(k) for k in ("code", "name", "amount", "day", "target_weight", "enabled")}
                       for p in rt.dca.plans()]
            except Exception:
                pass
            if not snap and not dca:
                raise AIError("키움 계좌를 연결하거나 적립 계획을 만들어야 점검할 수 있어요")
            ask = ("내 포트폴리오를 점검해 줘. 종목·업종 쏠림, 국내/미국·원화/달러 비중, 현금 비중, 적립 계획과의 "
                   "어긋남, 눈여겨볼 위험과 개선 아이디어를 정리해 줘. 필요하면 보유 종목의 최근 큰 뉴스만 찾아 봐.")
            hold = []
            for h in (snap or {}).get("holdings") or []:
                hold.append({k: h.get(k) for k in ("code", "name", "market", "qty", "avg_price", "price", "value",
                                                     "pnl", "pnl_pct")})
            data = {"기준시각": now, "총자산": (snap or {}).get("total"), "예수금": (snap or {}).get("cash"),
                    "미국·달러": (snap or {}).get("us"), "보유 종목(평가액 원화)": hold, "적립 계획(월)": dca,
                    "순자산 목표": rt.settings.get("networth_goal")}
            return "포트폴리오 점검", ask, data
        if kind == "review":
            day = d.get("day") or datetime.now().strftime("%Y-%m-%d")
            trades = rt.db.q("SELECT code,name,qty,entry_ts,entry_price,stop0,target,entry_reason,exit_ts,exit_price,"
                             "reason,pnl,fees FROM trades WHERE engine='scalp' AND session=? ORDER BY id", (day,))
            log = rt.db.q("SELECT ts, level, msg FROM engine_log WHERE engine='scalp' AND ts LIKE ? ORDER BY id DESC "
                          "LIMIT 60", (day + "%",))
            s = rt.settings.all()
            if not trades and not log:
                raise AIError(f"{day}에는 단타 거래나 기록이 없어요")
            ask = (f"{day} 단타 매매를 복기해 줘. 잘된 점, 손실 원인(진입 시점·손절폭·시간대·종목 선택), "
                   "규칙에서 바꿔 볼 후보(어느 값을 어느 방향으로, 왜)를 짧게. 표본이 적으면 성급히 바꾸지 말라고 말해 줘.")
            data = {"날짜": day, "거래": trades, "엔진 기록(최근순)": log, "전략": s["strategy"], "청산": s["exit"],
                    "위험 관리": s["risk"], "비용": s["costs"]}
            return f"{day} 단타 복기", ask, data
        if kind == "sweep":
            st = rt.sweep.status()
            if st.get("status") != "done":
                raise AIError("먼저 조건 훑기를 끝내 주세요")
            slim = {k: st.get(k) for k in ("strategy", "source", "meta", "current", "checked", "tested_ok",
                                            "positive_is", "grid")}
            slim["top"] = st.get("top", [])[:10]
            ask = ("조건 훑기 결과를 해석해 줘. 학습/검증 성적 차이로 본 과최적화 가능성, 믿을 만한 조건과 그 이유, "
                   "지금 내 설정과 비교, 다음에 해 볼 검증(기간·종목·조건 범위)을 알려 줘. 웹 검색은 하지 마.")
            return "조건 훑기 해석", ask, {"결과": slim}
        if kind == "learn":
            try:
                return self.rt.aipick.learn_context(d.get("horizon") or "day")
            except ValueError as e:
                raise AIError(str(e))
        if kind == "pick":
            try:
                return self.rt.aipick.context(d)
            except ValueError as e:
                raise AIError(str(e))
        if kind == "divplan":
            try:
                return self.rt.dividend.ai_context(d)
            except ValueError as e:
                raise AIError(str(e))
        if kind == "dca":
            try:
                return self.rt.dividend.dca_ai_context()
            except ValueError as e:
                raise AIError(str(e))
        if kind == "brief":
            return self.rt.prep.brief_context(d)
        if kind == "hold":
            return self.rt.autotrade.hold_context(d)
        if kind == "counter":
            return self.rt.autotrade.counter_context(d)
        if kind == "auto":
            try:
                return self.rt.autotrade.review_context(d)
            except ValueError as e:
                raise AIError(str(e))
        if kind == "chat":
            q = (d.get("question") or "").strip()
            if not q:
                raise AIError("질문을 넣어 주세요")
            snap = rt.account.snapshot() if rt.account.configured() else None
            hold = [{k: h.get(k) for k in ("name", "code", "market", "qty", "value", "pnl_pct")}
                    for h in (snap or {}).get("holdings") or []]
            data = {"기준시각": now, "내 총자산": (snap or {}).get("total"), "보유 종목": hold,
                    "관심종목": rt.settings.get("watch")}
            return "AI에게 묻기", q[:2000], data
        raise AIError("알 수 없는 요청이에요")

    # ---------- Claude 호출(스트리밍) ----------
    def stream(self, kind, d):
        """화면에 줄 이벤트를 하나씩 낸다: {"t":"status"|"text"|"source"|"done"|"error", ...}"""
        if not self.configured():
            yield {"t": "error", "msg": "설정 → AI에서 Claude API 키를 넣어 주세요"}
            return
        c = self.conf()
        used, _ = self.month_usd()
        if c.get("budget_usd") and used >= float(c["budget_usd"]):
            yield {"t": "error", "msg": f"이번 달 AI 예산(${c['budget_usd']})을 다 썼어요. 설정 → AI에서 늘릴 수 있어요"}
            return
        try:
            title, ask, data = self.context(kind, d)
        except AIError as e:
            yield {"t": "error", "msg": str(e)}
            return
        except Exception as e:
            yield {"t": "error", "msg": f"자료를 모으지 못했어요: {e}"}
            return
        yield {"t": "status", "msg": "자료를 정리해서 Claude에게 보내는 중"}
        user = f"{ask}\n\n<앱 데이터>\n{json.dumps(data, ensure_ascii=False, default=str)[:60000]}\n</앱 데이터>"
        messages = [{"role": m["role"], "content": str(m["content"])[:4000]}
                    for m in (d.get("history") or [])[-6:] if m.get("role") in ("user", "assistant")]
        messages.append({"role": "user", "content": user})
        model = c.get("model") or "claude-sonnet-5-5"
        if kind in REVIEW_KINDS:  # 복기는 따로 정한 엔진(기본 Opus)
            model = self.review_model()
        if d.get("model") in MODELS:  # 화면에서 고른 엔진
            model = d["model"]
        pick = kind == "pick"
        from .aipick import hz, system_prompt
        hzn = hz(d.get("horizon"))
        lite = pick and bool(d.get("lite"))      # 자동매매의 빠른 재추천: 가장 싼 엔진, 검색 없이, 표(JSON)만 받는다
        if lite:
            model = LITE_MODEL
        div = kind == "divplan"
        if div:
            from .dividend import DIV_SYSTEM
        body = {"model": model, "max_tokens": 1500 if lite else 9000 if pick else 8000 if div else 4000 if kind == "auto" else 2500,
                "system": system_prompt(hzn, lite) if pick else DIV_SYSTEM if div else SYSTEM, "messages": messages, "stream": True}
        if kind == "brief":
            from .prep import BRIEF_SYSTEM
            body.update(system=BRIEF_SYSTEM, max_tokens=1800)
        elif kind == "hold":
            from .autotrade import HOLD_SYSTEM
            body.update(system=HOLD_SYSTEM, max_tokens=1500)
        elif kind == "counter":
            from .autotrade import COUNTER_SYSTEM
            body.update(system=COUNTER_SYSTEM, max_tokens=1500)
        web = d["web_search"] if isinstance(d.get("web_search"), bool) else c.get("web_search", True)
        if web and not lite and kind not in ("sweep", "learn", "dca", "auto"):    # 적립 점검은 앱 자료만으로 충분해 검색을 쓰지 않는다
            body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": (3 if hzn == "day" else 6) if pick else 4,
                              "user_location": {"type": "approximate", "country": "KR", "timezone": "Asia/Seoul"}}]
        usage = {"in": 0, "out": 0, "searches": 0}
        sources, seen = [], set()
        buf = []
        rk = "pickq" if lite else "pickw" if pick and hzn != "day" else kind  # 중기 추천은 검색이 많아 비용을 따로 센다
        state = {"blocks": []}

        def run(b):
            """요청 한 번. 여러 번 부르면 사용량은 더해진다. 받은 내용 블록은 이어받기(pause_turn)용으로 모아 둔다."""
            base = {k: usage.get(k, 0) for k in ("in", "out", "searches")}
            u = {"in": 0, "out": 0, "searches": 0}
            blocks = Blocks()
            state["blocks"] = blocks
            usage.pop("stop", None)
            for ev in self._post(b):
                blocks.feed(ev)
                for x in self._handle(ev, u, sources, seen):
                    if x["t"] == "text":
                        buf.append(x["text"])
                    yield x
                for k in base:
                    usage[k] = base[k] + u[k]
                if u.get("stop"):
                    usage["stop"] = u["stop"]

        def text():
            return "".join(buf)

        try:
            yield from run(body)
            # 웹 검색이 길어지면 서버가 중간에 멈추고(pause_turn) 돌려준다. 받은 내용을 그대로 다시 보내면 이어서 답한다.
            for _ in range(3):
                if usage.get("stop") != "pause_turn":
                    break
                content = state["blocks"].content()
                if not content:
                    break
                yield {"t": "status", "msg": "검색이 길어져 이어서 받는 중"}
                body["messages"] = body["messages"] + [{"role": "assistant", "content": content}]
                try:
                    yield from run(body)
                except AIError:
                    usage["stop"] = "pause_turn"          # 이어받기 실패: 아래에서 검색 없이 한 번 더 정리한다
                    break
        except GeneratorExit:  # 사용자가 멈춤: 그때까지 쓴 비용은 기록
            self._record_partial(rk, model, usage)
            raise
        except AIError as e:
            if "tools" in body and "web_search" in str(e).lower():
                # 조직에서 웹 검색을 안 켰으면 검색 없이 다시
                body.pop("tools")
                yield {"t": "status", "msg": "웹 검색을 쓸 수 없어 앱 데이터만으로 답할게요"}
                try:
                    yield from run(body)
                except AIError as e2:
                    self._record_partial(rk, model, usage)
                    yield {"t": "error", "msg": str(e2)}
                    return
            else:
                self._record_partial(rk, model, usage)
                yield {"t": "error", "msg": str(e)}
                return

        def again(why, mdl=None):
            """답이 비었거나 끝까지 오지 않았을 때: 검색 없이, 지금까지 쓴 글을 참고로 처음부터 다시 받는다(한 번만)."""
            prev = text().strip()
            msgs = list(messages)
            if prev:
                msgs += [{"role": "assistant", "content": prev[:6000]},
                         {"role": "user", "content": "답이 끝까지 오지 않았어. 위에서 찾은 내용을 살려서, 정해진 출력 형식 그대로 처음부터 끝까지 다시 써 줘. 웹 검색은 하지 마."}]
            b2 = {k: v for k, v in body.items() if k != "tools"}
            b2.update(messages=msgs, model=mdl or model)
            yield {"t": "status", "msg": why}
            yield {"t": "reset"}
            del buf[:]
            yield from run(b2)

        plan, plan_err = None, None
        need = None
        if not text().strip():
            need = "AI가 이 요청에 답하지 않아 다른 엔진으로 다시 받는 중" if usage.get("stop") == "refusal" else "답이 비어 있어 검색 없이 다시 받는 중"
        elif div:
            try:
                self.rt.dividend.read_plan(text())
            except Exception as e:
                plan_err = e
                need = "구성 표가 끝까지 오지 않아 검색 없이 한 번 더 정리하는 중"
        if need:
            fb = "claude-sonnet-5-5" if usage.get("stop") == "refusal" and model != "claude-sonnet-5-5" else None
            stop0 = usage.get("stop")
            try:
                yield from again(need, fb)
                plan_err, model = None, fb or model
            except GeneratorExit:
                self._record_partial(rk, model, usage)
                raise
            except AIError as e:
                if not div:
                    self._record_partial(rk, model, usage)
                    yield {"t": "error", "msg": str(e)}
                    return
                plan_err = plan_err or ValueError(str(e))
            if not text().strip() and not div and not pick:
                self._record_partial(rk, model, usage)
                yield {"t": "error", "msg": "AI가 답을 주지 않았어요" + (f" (사유: {stop0})" if stop0 and stop0 != "end_turn" else "") + ". 다른 엔진으로 바꾸거나 잠시 뒤 다시 받아 주세요"}
                return
        usd = self._record(rk, model, usage)
        fx = FX_DEFAULT
        try:
            fx = float(self.rt.market.yh_quote("USDKRW=X")["price"]) or FX_DEFAULT
        except Exception:
            pass
        if kind == "learn" and "".join(buf).strip():
            self.rt.aipick.save_lessons("".join(buf), model, usd, hzn)
        if kind == "auto" and "".join(buf).strip():
            try:
                pr = self.rt.autotrade.save_review("".join(buf), d, model, usd)
                yield {"t": "proposal", "id": pr["id"], "changes": len(pr["changes"])}
            except Exception as e:
                yield {"t": "status", "msg": f"개선안 정리 실패: {e}"}
        if pick:
            try:
                res = self.rt.aipick.parse("".join(buf), "us" if d.get("mkt") == "us" else "kr", model, usd,
                                           cut=usage.get("stop") in ("max_tokens", "pause_turn"), horizon=hzn, theme=d.get("theme") or "", lite=lite)
            except Exception as e:
                res = None
                yield {"t": "status", "msg": f"추천 결과 정리 실패: {e}"}
            yield {"t": "picks", **(res or {"picks": [], "stance": None, "view": "", "note": "AI 답이 비어 있어요. 다시 받아 보세요"})}
        if div:
            if text().strip():
                try:
                    plan = self.rt.dividend.parse_plan(text(), d, model, usd)
                except Exception as e:
                    plan_err = e
            elif plan_err is None:
                plan_err = ValueError("AI가 답을 주지 않았어요. 다른 엔진으로 바꾸거나 잠시 뒤 다시 받아 주세요")
            if plan is not None:
                yield {"t": "plan", **plan}
            else:
                why = str(plan_err) if isinstance(plan_err, ValueError) else f"구성 정리 실패 ({type(plan_err).__name__})"
                stop = usage.get("stop")
                if stop and stop not in ("end_turn", "stop_sequence"):
                    why += {"max_tokens": " (답이 길어 중간에 잘렸어요)", "pause_turn": " (검색이 길어져 답이 끊겼어요)",
                            "refusal": " (엔진이 답을 거절했어요. 다른 엔진으로 받아 보세요)"}.get(stop, f" (사유: {stop})")
                yield {"t": "plan", "error": why}
        cr = self.credit()
        yield {"t": "done", "fx": fx, "credit": cr, "title": title, "usd": round(usd, 4), "krw": int(round(usd * fx)), "model": model,
               "searches": usage["searches"], "in": usage["in"], "out": usage["out"],
               "left": cr.get("left") if cr.get("set") else None}

    def _record_partial(self, kind, model, usage):
        """도중에 실패했어도 그때까지 쓴 만큼은 청구되므로, 잔액 표시가 어긋나지 않게 기록해 둔다."""
        try:
            if usage.get("in") or usage.get("out") or usage.get("searches"):
                self._record(kind, model, usage)
        except Exception:
            pass

    def _handle(self, ev, usage, sources, seen):
        typ = ev.get("type")
        if typ == "message_start":
            u = (ev.get("message") or {}).get("usage") or {}
            usage["in"] = max(usage["in"], int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
                              + int(u.get("cache_creation_input_tokens") or 0))
            usage["out"] = max(usage["out"], int(u.get("output_tokens") or 0))
        elif typ == "content_block_start":
            b = ev.get("content_block") or {}
            if b.get("type") == "server_tool_use" and b.get("name") == "web_search":
                yield {"t": "status", "msg": "웹에서 찾는 중"}
            elif b.get("type") == "web_search_tool_result":
                res = b.get("content") if isinstance(b.get("content"), list) else []
                for r in res:
                    url = r.get("url")
                    if url and url not in seen and len(sources) < 12:
                        seen.add(url)
                        src = {"url": url, "title": r.get("title") or url, "age": r.get("page_age")}
                        sources.append(src)
                        yield {"t": "source", **src}
            elif b.get("type") == "text" and b.get("text"):
                yield {"t": "text", "text": b["text"]}
        elif typ == "content_block_delta":
            dl = ev.get("delta") or {}
            if dl.get("type") == "text_delta":
                yield {"t": "text", "text": dl.get("text", "")}
            elif dl.get("type") == "input_json_delta":
                pass
        elif typ == "message_delta":
            if (ev.get("delta") or {}).get("stop_reason"):
                usage["stop"] = ev["delta"]["stop_reason"]
            u = ev.get("usage") or {}
            usage["out"] = max(usage["out"], int(u.get("output_tokens") or 0))
            if u.get("input_tokens"):
                usage["in"] = max(usage["in"], int(u["input_tokens"]))
            st = u.get("server_tool_use") or {}
            usage["searches"] = max(usage["searches"], int(st.get("web_search_requests") or 0))
        elif typ == "error":
            err = ev.get("error") or {}
            raise AIError(f"Claude 오류: {err.get('message') or err}")

    def _post(self, body):
        key = self.conf().get("api_key", "").strip()
        try:
            r = self.session.post(API_URL, json=body, stream=True, timeout=(10, 180),
                                  headers={"x-api-key": key, "anthropic-version": API_VERSION,
                                           "content-type": "application/json"})
        except requests.RequestException as e:
            raise AIError(f"Claude에 연결하지 못했어요: {e}")
        if r.status_code != 200:
            try:
                msg = (r.json().get("error") or {}).get("message") or r.text[:200]
            except ValueError:
                msg = r.text[:200]
            if "credit balance" in str(msg).lower():
                self.rt.db.set_setting("ai_credit_low", True)
                raise AIError("API 크레딧이 바닥났어요. 콘솔에서 충전하거나 Max 월 크레딧을 받은 뒤, 설정 → AI에서 잔액을 다시 맞춰 주세요")
            hint = {401: "API 키를 확인해 주세요", 403: "이 키로는 쓸 수 없는 기능이에요", 429: "요청이 많아요. 잠시 뒤 다시",
                    529: "Claude가 바빠요. 잠시 뒤 다시"}.get(r.status_code, "")
            raise AIError(f"Claude 오류({r.status_code}): {msg} {hint}".strip())
        r.encoding = "utf-8"
        try:
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                try:
                    yield json.loads(line[5:].strip())
                except ValueError:
                    continue
        except requests.RequestException as e:  # 답을 받는 도중 끊김(시간 초과·연결 끊김)
            raise AIError(f"Claude의 답을 받는 도중 연결이 끊겼어요. 다시 받아 주세요 ({type(e).__name__})")
        finally:
            try:
                r.close()
            except Exception:
                pass

    def test(self):
        """키 확인: 아주 짧은 요청 한 번."""
        if not self.configured():
            raise AIError("API 키를 넣어 주세요")
        model = self.conf().get("model") or "claude-sonnet-5-5"
        body = {"model": model, "max_tokens": 20, "stream": True,
                "messages": [{"role": "user", "content": "'연결됐어요'라고만 답해."}]}
        usage, text = {"in": 0, "out": 0, "searches": 0}, []
        for ev in self._post(body):
            for x in self._handle(ev, usage, [], set()):
                if x["t"] == "text":
                    text.append(x["text"])
        usd = self._record("test", model, usage)
        return {"reply": "".join(text).strip(), "usd": round(usd, 5), "model": model}
