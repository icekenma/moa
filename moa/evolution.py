"""엔진 노트: 투자 엔진이 수익률을 높이려고 걸어온 길(앱 개선 + 실제로 스스로 고친 기록), 지금 로직 전반, 앞으로의 전략을 한곳에.

- 앱 개선 이력(DEV)은 사람이 적어 두는 목록이다. 투자 엔진에 영향을 주는 판을 올릴 때마다 한 줄씩 더한다.
- 나머지(플랜 조정·엔진 교체·복기·실험·성적)는 모두 DB에서 그때그때 계산한다.
"""
import json
from datetime import date, datetime

# (판, 날짜, 분야, 제목, 무엇을 했나, 왜: 수익률·안정성과의 관계)
AREAS = {"ai": "AI 추천", "auto": "자동매매", "learn": "학습", "cost": "비용", "lab": "실험", "safe": "안전장치", "data": "시세·자료", "div": "배당·적립"}
DEV = [
    ("v1.2", "2026-11-17", "data", "투자 시세 시작", "국내(키움)·미국 시세, 지수, 종목 차트", "판단의 재료가 되는 시세부터 갖춤"),
    ("v1.6", "2026-11-17", "data", "키움 호출 한도 대응", "앱키별 토큰·간격 공유, 재시도", "시세가 끊기면 매매 판단도 멈추므로"),
    ("v2.1", "2026-11-17", "data", "미국 주식 주문·잔고", "키움 미국 주문·체결·예수금 연결", "미국장까지 기회를 넓힘"),
    ("v2.4", "2026-11-17", "lab", "단타 조건 대량 훑기", "1분봉을 모아 조건 수천 개를 학습/검증으로 나눠 백테스트", "감이 아니라 숫자로 조건을 고르려고"),
    ("v2.6", "2026-11-17", "ai", "Claude AI 투자 도우미", "종목·포트폴리오 정리, 웹 검색 출처", "뉴스·공시를 사람 대신 읽게"),
    ("v3.0", "2026-11-17", "data", "실제 체결가 반영", "체결 내역의 가중 평균 체결가로 손익 계산", "가짜 손익으로 판단하지 않게"),
    ("v3.5", "2026-11-17", "ai", "AI 종목 추천 + 자동 익절·손절", "추천마다 목표·손절선, 켜 둔 종목만 자동 매도", "들어갈 때와 나올 때를 함께 정함"),
    ("v4.2", "2026-11-17", "learn", "AI 추천 성적표", "모든 추천을 추천 시점 가격으로 가상 매매해 채점", "AI가 실제로 맞히는지 재기 시작"),
    ("v4.8", "2026-11-17", "learn", "AI 자기 학습 고리", "조건별 성적·승률 보정·복기 규칙을 다음 추천에 넣음", "틀린 패턴을 반복하지 않게"),
    ("v4.20", "2026-11-17", "learn", "채점 방식 바로잡기", "추천 전 가격 범위 제외, 체결 가능 시간 제한", "성적이 부풀려지지 않게"),
    ("v4.28", "2026-11-17", "ai", "스윙(1주·1달) 추천", "보유 기간별로 따로 추천·학습", "단타 말고 다른 시간대의 기회"),
    ("v4.32", "2026-11-18", "div", "배당 탭", "배당 달력·시뮬레이션·종목·AI 구성", "매달 들어오는 현금흐름 쪽 전략"),
    ("v4.38", "2026-11-18", "auto", "AI 자동매매", "플랜·모의/실전·매매 기록·개선안 승인", "추천을 규칙대로 실행해 사람 실수를 줄임"),
    ("v4.41", "2026-11-18", "learn", "기회 분석", "건너뛴 종목이 그 뒤 어땠는지 보고 기준을 조정(바닥 있음)", "너무 깐깐해 놓친 수익을 되찾으려고"),
    ("v4.49", "2026-11-19", "cost", "추천 함께 쓰기", "같은 조건의 자동매매는 한 번 받은 추천을 나눠 씀", "AI 비용을 줄여 순수익을 늘림"),
    ("v4.51", "2026-11-19", "cost", "비용을 먼저 따지는 플랜", "약한 날은 AI를 안 부름, 관망 뒤 쉬기, 비용 한도에 맞춰 엔진·횟수 조절", "수수료처럼 AI 비용도 수익을 깎으므로"),
    ("v4.51", "2026-11-19", "lab", "모의 실험 묶음", "같은 추천을 기준이 다른 플랜들이 동시에 굴림", "어떤 기준이 더 버는지 같은 날 비교"),
    ("v4.52", "2026-11-19", "lab", "손 안 대는 자동 테스트", "국내·미국 단타·스윙 실험을 앱이 알아서 돌리고 실전 후보를 추천", "실전 전에 모의로 충분히 검증"),
    ("v4.57", "2026-11-19", "lab", "짧게/길게 끊기 실험", "AI가 낸 익절·손절 폭을 0.5배·1.5배로", "파는 타이밍이 수익에 주는 영향 측정"),
    ("v4.59", "2026-11-19", "lab", "가리지 않고 사기 기준선", "AI가 낸 종목을 거의 다 사 보는 플랜", "앱의 거르기가 실제로 도움이 되는지 비교 기준"),
    ("v4.62", "2026-11-19", "lab", "모의는 1주라도 사 봄", "배정 금액이 모자라도 현금이 되면 1주", "돈 부족으로 시험이 안 되는 일을 없앰"),
    ("v4.64", "2026-11-19", "cost", "빠른 재추천", "정밀 추천 사이에 가장 싼 엔진으로 자주 다시 물음", "적은 비용으로 기회를 더 자주 봄"),
    ("v4.70", "2026-11-19", "learn", "매매 기록(날짜·전략·기준별)", "지난 거래를 여러 각도로 모아 봄", "무엇이 벌고 잃는지 보이게"),
    ("v4.73", "2026-11-19", "learn", "정밀 추천만으로 학습", "빠른 재추천 결과는 학습에서 뺌", "싼 엔진의 잡음이 학습을 흐리지 않게"),
    ("v4.74", "2026-11-19", "cost", "시드에 비례한 AI 한도 + 엔진 올리기", "한도 = 한 달 시드의 1%(하루 최소 $3), 성과가 나쁘면 Sonnet→Opus→Fable", "돈이 클수록 더 좋은 판단에 투자"),
    ("v4.77", "2026-11-19", "auto", "자동매매 흐름도", "점검→추천→거르기→크기→주문→보유→팔기→배우기와 지금 위치", "어디서 막히는지 바로 보이게"),
    ("v4.78", "2026-11-19", "learn", "복기 엔진 Opus", "쌓인 기록을 돌아보는 AI는 따로 좋은 엔진으로", "배우는 질이 가장 중요해서"),
    ("v4.79", "2026-11-20", "cost", "실험 AI 한도 수정", "정밀 추천 한 번에 한도가 차면 그 차례는 빠른 재추천으로", "하루 중간에 추천이 멈추던 문제"),
    ("v4.80", "2026-11-20", "div", "배당 자료 매일 모으기", "배당률 기록, 인상·삭감 감지·알림, 5년 대비 배당률 위치", "싸게 살 때와 삭감 위험을 보려고"),
    ("v4.88", "2026-11-20", "auto", "분할 매도 · 분할 매수", "목표의 절반에서 절반을 먼저 팔고 남긴 몫은 본전 손절 / 절반만 먼저 사고 오르면 나머지. 실험 플랜으로 먼저 비교, 플랜에서 켤 수 있음",
     "이익은 일부 먼저 챙기고 나머지는 더 길게, 바로 밀리는 매수는 절반만 잃게"),
    ("v4.86", "2026-11-20", "lab", "약한 날 쉬기 · 흔들림 맞춤 손절 실험", "지수 -1% 아래면 사지 않는 플랜, 손절을 최근 흔들림(ATR)×배수까지 넓히고 수량을 줄이는 플랜을 실험에 추가",
     "11/19 오른 장 전부 익절, 11/20 내린 장 전부 손절 — 지수 기준과 손절폭이 실제로 손실을 줄이는지 같은 추천으로 비교"),
    ("v4.82", "2026-11-20", "learn", "매매 기록 숫자 바로잡기", "국내(원)·미국(달러)을 섞어 센 이익/손실 비를 원화로, 날짜마다 시장별 손익", "성적을 잘못 읽고 기준을 고치지 않게"),
    ("v4.81", "2026-11-20", "learn", "엔진 노트", "개선 과정·지금 로직·앞으로 전략을 한곳에", "전체를 보고 다음 수를 정하려고"),
]
PRINCIPLES = [
    ("검증 먼저", "새 기준은 모의 실험에서 먼저 굴리고, 한 플랜이 30건·10거래일 이상에서 AI 비용까지 빼고 플러스일 때만 실전 후보로 올려요."),
    ("비용까지 뺀 수익", "AI 비용도 수수료처럼 수익을 깎아요. 모든 성적은 비용을 뺀 값으로 보고, 비용 한도 안에서 엔진·횟수를 맞춰요."),
    ("손실 먼저 막기", "하루 손실 한도, 연속 손절이면 그날 쉬기, 손절선은 사기 전에 정해요. 큰 손실 한 번이 작은 이익 여러 번을 지워요."),
    ("숫자로 고치기", "기준은 실제 결과(건너뛴 종목 포함)가 쌓였을 때만 바꾸고, 실전 변경은 승인 후 반영해요."),
    ("판단의 질에 투자", "성과가 나쁘면 더 좋은 엔진으로 올리고, 복기는 처음부터 좋은 엔진을 써요."),
]


class Evolution:
    def __init__(self, rt):
        self.rt = rt

    # ------------------------------------------------------------------ 한눈에
    def overview(self):
        db, at, fx = self.rt.db, self.rt.autotrade, self.rt.ai.fx()
        firsts = [x for x in (db.one("SELECT MIN(ts) t FROM ai_picks")["t"], db.one("SELECT MIN(created_at) t FROM at_runs")["t"]) if x]
        since = min(firsts + [DEV[0][1]])[:10]                 # 투자 엔진을 만들기 시작한 날부터
        pk = db.one("SELECT COUNT(*) n FROM ai_picks WHERE COALESCE(lite,0)=0")["n"] if self._has("ai_picks", "lite") else db.one("SELECT COUNT(*) n FROM ai_picks")["n"]
        lite = "AND COALESCE(lite,0)=0" if self._has("ai_outcomes", "lite") else ""
        g = db.one(f"SELECT COUNT(*) n, AVG(net_pct) a, COALESCE(SUM(net_pct>0),0) w FROM ai_outcomes WHERE status!='live' AND net_pct IS NOT NULL {lite}")
        # 자동매매: 모의·실전 각각 (원화, AI 비용 뺀 값)
        modes = {}
        cost_by = {}
        for r in db.q("SELECT r.mode, r.mkt, l.day, l.data FROM at_log l JOIN at_runs r ON r.id=l.run_id WHERE l.kind='cycle' AND l.data IS NOT NULL"):
            u = float((json.loads(r["data"]) or {}).get("usd") or 0)
            if u:
                cost_by[(r["mode"], r["day"])] = cost_by.get((r["mode"], r["day"]), 0) + u * fx
        pnl_by = {}
        for r in db.q("SELECT r.mode, t.mkt, t.exit_day, t.pnl, t.pnl_pct FROM at_trades t JOIN at_runs r ON r.id=t.run_id WHERE t.status='closed'"):
            k = fx if r["mkt"] == "us" else 1.0
            m = modes.setdefault(r["mode"], {"n": 0, "w": 0, "pnl": 0.0, "sp": 0.0})
            m["n"] += 1
            m["w"] += 1 if (r["pnl"] or 0) > 0 else 0
            m["pnl"] += (r["pnl"] or 0) * k
            m["sp"] += r["pnl_pct"] or 0
            pnl_by[(r["mode"], r["exit_day"])] = pnl_by.get((r["mode"], r["exit_day"]), 0) + (r["pnl"] or 0) * k
        auto = {}
        for mode in ("paper", "real"):
            m = modes.get(mode, {"n": 0, "w": 0, "pnl": 0.0, "sp": 0.0})
            cost = sum(v for (md, _), v in cost_by.items() if md == mode)
            auto[mode] = {"n": m["n"], "win": round(m["w"] / m["n"] * 100) if m["n"] else None, "avg": round(m["sp"] / m["n"], 2) if m["n"] else None,
                          "pnl": round(m["pnl"]), "cost": round(cost), "net": round(m["pnl"] - cost)}
        # 날짜별 누적(비용 뺀 손익)
        days = sorted({d for _, d in list(pnl_by) + list(cost_by) if d})
        curve = {}
        for mode in ("paper", "real"):
            s, pts = 0.0, []
            for d in days:
                s += pnl_by.get((mode, d), 0) - cost_by.get((mode, d), 0)
                pts.append(round(s))
            curve[mode] = pts
        usd_all = db.one("SELECT COALESCE(SUM(usd),0) s FROM ai_usage")["s"]
        usd_m = self.rt.ai.month_usd()[0]
        ch = {r["source"]: r["n"] for r in db.q("SELECT source, COUNT(*) n FROM at_changes WHERE source NOT IN ('start') GROUP BY source")}
        eng = db.one("SELECT COUNT(*) n FROM at_log WHERE kind='control' AND data LIKE '%\"engine\": {%'")["n"]
        lessons = sum(int((self.rt.aipick.lessons(h) or {}).get("ver") or 0) for h in ("day", "swing", "month"))
        return {"since": since, "days": (date.today() - date.fromisoformat(since)).days + 1,
                "picks": pk, "graded": g["n"], "pick_avg": round(g["a"], 2) if g["a"] is not None else None, "pick_win": round(g["w"] / g["n"] * 100) if g["n"] else None,
                "auto": auto, "curve": {"days": days, **curve}, "usd_all": round(usd_all, 2), "usd_month": round(usd_m, 2), "cost_all": round(usd_all * fx),
                "changes": {"plan": sum(v for k, v in ch.items() if k != "user"), "user": ch.get("user", 0), "engine": eng, "lessons": lessons},
                "dev": len(DEV), "runs": db.one("SELECT COUNT(*) n FROM at_runs WHERE status IN ('running','paused','closing')")["n"], "fx": fx,
                "auto_on": at.auto_on()}

    def _has(self, table, col):
        try:
            return any(r["name"] == col for r in self.rt.db.q(f"PRAGMA table_info({table})"))
        except Exception:
            return False

    # ------------------------------------------------------------------ 걸어온 길
    def timeline(self, limit=160):
        db, out = self.rt.db, []
        for i, (v, d, a, t, what, why) in enumerate(DEV):
            out.append({"day": d, "ts": f"{d}T00:00:{i:03d}", "kind": "dev", "area": a, "area_label": AREAS[a], "title": f"{v} {t}", "what": what, "why": why})
        names = {r["id"]: r for r in db.q("SELECT id, name, mode, mkt, style, lab_id, variant, created_at, ended_at, status FROM at_runs")}
        src = {"ai": "AI 분석", "auto": "자동 분석", "self": "스스로 반영(모의)", "user": "직접 고침", "opp": "기회 분석"}
        for r in db.q("SELECT run_id, ts, ver, source, changes FROM at_changes WHERE source!='start' ORDER BY id DESC LIMIT 120"):
            run = names.get(r["run_id"]) or {}
            cs = json.loads(r["changes"] or "[]")
            out.append({"day": r["ts"][:10], "ts": r["ts"], "kind": "plan", "area": "auto", "area_label": "플랜 조정", "title": f"{run.get('name', '')} 플랜 {r['ver']}판",
                        "what": " · ".join(f"{c.get('label') or c.get('key')} {c.get('from')}→{c.get('to')}" for c in cs)[:240],
                        "why": src.get(r["source"], r["source"]) + (f" · {cs[0].get('why')}" if cs and cs[0].get("why") else ""), "run": r["run_id"]})
        for r in db.q("SELECT run_id, ts, status, source, summary FROM at_proposals WHERE status IN ('pending','rejected') ORDER BY id DESC LIMIT 40"):
            run = names.get(r["run_id"]) or {}
            out.append({"day": r["ts"][:10], "ts": r["ts"], "kind": "proposal", "area": "learn", "area_label": "개선안", "title": f"{run.get('name', '')} 개선안 {'승인 대기' if r['status'] == 'pending' else '반영 안 함'}",
                        "what": r["summary"][:240], "why": src.get(r["source"], r["source"]), "pending": r["status"] == "pending", "run": r["run_id"]})
        for r in db.q("SELECT run_id, ts, msg FROM at_log WHERE kind='control' AND (data LIKE '%\"engine\": {%' OR msg LIKE '%AI 비용 한도%' OR msg LIKE '%비용 목표%') ORDER BY id DESC LIMIT 60"):
            run = names.get(r["run_id"]) or {}
            eng = "엔진" in r["msg"]
            out.append({"day": r["ts"][:10], "ts": r["ts"], "kind": "engine" if eng else "cost", "area": "ai" if eng else "cost", "area_label": "엔진 교체" if eng else "비용 조정",
                        "title": run.get("name", ""), "what": r["msg"][:240], "why": "", "run": r["run_id"]})
        for r in db.q("SELECT run_id, ts, msg FROM at_log WHERE kind='review' AND msg LIKE '%분석 ·%' ORDER BY id DESC LIMIT 40"):
            run = names.get(r["run_id"]) or {}
            out.append({"day": r["ts"][:10], "ts": r["ts"], "kind": "review", "area": "learn", "area_label": "복기", "title": run.get("name", ""), "what": r["msg"][:240], "why": "", "run": r["run_id"]})
        for h, lbl in (("day", "당일"), ("swing", "1주"), ("month", "1달")):
            ls = self.rt.aipick.lessons(h) or {}
            if ls.get("text") and ls.get("at"):
                out.append({"day": str(ls["at"])[:10], "ts": str(ls["at"]), "kind": "lesson", "area": "learn", "area_label": "추천 규칙",
                            "title": f"{lbl} 추천 규칙 {ls.get('ver', 1)}판", "what": str(ls["text"])[:300], "why": f"추천 {ls.get('n') or 0}건의 결과를 복기"})
        for r in names.values():
            if r.get("lab_id") and r.get("variant") == "base":
                out.append({"day": r["created_at"][:10], "ts": r["created_at"], "kind": "lab", "area": "lab", "area_label": "실험 시작",
                            "title": f"{'국내' if r['mkt'] == 'kr' else '미국'} {'단타' if r['style'] == 'day' else '스윙'} 실험 묶음", "what": "같은 추천을 기준이 다른 플랜들로 동시에 굴려 비교", "why": ""})
            elif not r.get("lab_id"):
                out.append({"day": r["created_at"][:10], "ts": r["created_at"], "kind": "run", "area": "auto", "area_label": "실전 시작" if r["mode"] == "real" else "모의 시작",
                            "title": r["name"], "what": "", "why": "", "run": r["id"]})
        out.sort(key=lambda x: x["ts"], reverse=True)
        return out[:limit]

    # ------------------------------------------------------------------ 지금 로직
    def logic(self):
        from . import aipick as AP, autotrade as AT
        from .ai import LITE_MODEL, MODELS
        rt, at = self.rt, self.rt.autotrade
        c = rt.ai.conf()
        name = lambda m: MODELS.get(m, (m,))[0].split(" · ")[0]
        hz = []
        for h, H in AP.HORIZONS.items():
            st = rt.aipick.learn_stats(None, h)
            tn = rt.aipick.tune(h) if st["n"] >= H["min"] else {}
            ls = rt.aipick.lessons(h) or {}
            hz.append({"h": h, "label": H["label"], "n": st["n"], "min": H["min"], "step": H["step"], "overall": st.get("overall"),
                       "insights": (st.get("insights") or [])[:3], "tune": {k: tn.get(k) for k in ("tp", "sl", "avoid", "prefer", "hold")} if tn.get("ready") else None,
                       "lesson": {"ver": ls.get("ver"), "at": str(ls.get("at") or "")[:10], "text": str(ls.get("text") or "")[:400]} if ls.get("text") else None,
                       "next_lesson": max(0, H["step"] - (st["n"] - int(ls.get("n") or 0))) if st["n"] >= H["min"] else H["min"] - st["n"]})
        runs = []
        for r in at._runs("WHERE status IN ('running','paused','closing') AND lab_id IS NULL"):
            p = r["params"]
            runs.append({"id": r["id"], "name": r["name"], "mode": r["mode"], "mkt": r["mkt"], "style": r["style"], "model": name(r["model"]), "ver": r["ver"],
                         "keys": [["최소 승률", f"{p['min_win_prob']:g}%"], ["최소 점수", f"{p['min_score']:g}"], ["손익비", f"{p['min_rr']:g} 이상"],
                                  ["동시 보유", f"{p['max_positions']}종목"], ["1회 비중", f"{p['per_trade_pct']:g}%"], ["하루 손실 한도", f"-{p['daily_loss_pct']:g}%"],
                                  ["정밀 추천", f"하루 {p['cycles_per_day']}번"], ["빠른 재추천", f"{p.get('quick_gap_min') or 0}분마다" if p.get("quick_gap_min") else "꺼짐"],
                                  ["하루 AI 한도", f"${float(p.get('ai_daily_usd') or 1):g}"]]})
        labs = []
        for L in (at.labs().get("labs") or []):
            rs = sorted(L["runs"], key=lambda x: -(x.get("net_ret_pct") or 0))
            labs.append({"mkt": L["mkt"], "style": L["style"], "label": f"{'국내' if L['mkt'] == 'kr' else '미국'} {L.get('style_label', '')}",
                         "plans": [{"label": x["label"], "variant": x["variant"], "desc": AT.VARIANTS.get(x["variant"], ("", ""))[1], "net": x.get("net_ret_pct"), "n": x.get("closed")} for x in rs]})
        S = [
            {"key": "ai", "t": "AI 추천", "items": [
                ["무엇을 묻나", "그 시장 지수·거래대금 상위·급등락·뉴스(웹 검색)를 모아 Claude에게 살 종목과 진입·목표·손절가, 승률·점수를 받아요."],
                ["엔진", f"정밀 추천 기본 {name(c.get('model') or 'claude-sonnet-5-5')}(웹 검색 {'켬' if c.get('web_search', True) else '끔'}) · 빠른 재추천 {name(LITE_MODEL)}(검색 없이 숫자만)"],
                ["보유 기간", " · ".join(f"{H['label']}(익절 최대 {H['tp_max']}%·손절 최대 {H['sl_max']}%)" for H in AP.HORIZONS.values())],
                ["배운 것 넣기", "조건별 성적, 실제 결과로 맞춘 익절·손절 폭, 피할·우선할 조건, 지난 복기 규칙을 매 추천에 함께 보내요."],
                ["승률 보정", "AI가 말한 승률을 같은 구간의 실제 적중률로 바꿔 써요(10건 이상 쌓인 구간)."]]},
            {"key": "auto", "t": "자동매매 한 바퀴", "items": [
                ["① 점검(무료)", "장 시간·기간·매수 시간·자리·하루 손실·연속 손절·현금·차례·AI 비용·지수·관망·키·월 예산 13가지"],
                ["② AI 추천", "정밀 추천(하루 몇 번) 사이사이 빠른 재추천. 이미 들고 있거나 오늘 거래한 종목은 빼고 물어요."],
                ["③ 거르기", "승률·점수·손익비·비용(목표가 비용의 3배)·같은 날 중복·추격(추천가보다 많이 오르면 안 삼)"],
                ["④ 크기", "굴리는 돈 × 1회 비중. 처음 몇 건은 검증 단계로 줄여 사고, 관망이면 절반"],
                ["⑤ 주문", "모의는 앱 안 가상 체결(미끄러짐 반영), 실전은 키움 실주문('실전' 입력으로 만든 것만)"],
                ["⑥⑦ 보유·팔기", "익절·손절·추적 익절(최고점에서 밀리면)·시간 정리(단타는 장 마감 전)·기간 정리(스윙)"],
                ["⑧ 배우기", "거래 30건 또는 5거래일마다 성적 분석 → 개선안(실전은 승인 후), 엔진 점검"]]},
            {"key": "learn", "t": "학습·개선", "items": [
                ["추천 성적표", "모든 정밀 추천을 추천 시점 가격으로 가상 매매해 채점(빠른 재추천은 학습에서 뺌)"],
                ["추천 규칙 복기", f"결과가 쌓이면(당일 {AP.HORIZONS['day']['step']}건·1주 {AP.HORIZONS['swing']['step']}건·1달 {AP.HORIZONS['month']['step']}건마다) {name(rt.ai.review_model())}로 복기해 규칙을 고쳐요."],
                ["자동매매 개선안", "조건별 성적 + 건너뛴 종목의 실제 결과. 기준은 8건 이상 결과가 좋을 때만 한 단계 낮추고, 처음 플랜의 바닥 아래로는 안 내려요."],
                ["엔진 올리기", f"정밀 추천 거래 {AT.ESC_N}건마다 평균 0% 이하 또는 이익 비율 {AT.ESC_WIN}% 미만이면 Haiku→Sonnet→Opus→Fable 한 칸 위로. 더 나빠지면 되돌리고 멈춰요."],
                ["실전 후보", f"모의 실험의 한 플랜이 {AT.READY_N}건·{AT.READY_DAYS}거래일 이상에서 AI 비용까지 빼고 플러스일 때"]]},
            {"key": "cost", "t": "비용 관리", "items": [
                ["직접 만든 자동매매", f"하루 AI 한도 = 쓸 돈의 한 달 {AT.COST_PCT:g}%를 하루로 나눈 값(최소 ${AT.RUN_DAY_MIN_USD:g})"],
                ["모의 자동 테스트", f"하루 ${AT.AUTO_DAY_USD:g}을 단타 35%씩·스윙 15%씩 나눠요. 정밀 추천이 남은 한도를 넘기면 그 차례는 빠른 재추천으로."],
                ["복기", f"{name(rt.ai.review_model())} · 가끔만 돌아 한 번 100원 남짓"],
                ["월 예산", f"${c.get('budget_usd') or 0:g} · 이번 달 ${rt.ai.month_usd()[0]:.2f} 사용 · 넘으면 AI를 부르지 않아요"]]},
            {"key": "safe", "t": "안전장치", "items": [
                ["AI는 주문하지 않아요", "AI 추천의 매수는 사람이 해요. 자동 매도는 켜 둔 익절·손절만."],
                ["실전 자동매매", "'실전'을 직접 입력해 만든 것만 실제 주문. 하루 손실 한도·연속 손절이면 그날 쉼"],
                ["조회 전용 키", "내 계좌 보기 키로는 주문이 나가지 않아요."],
                ["실전 변경은 승인", "개선안·엔진 변경의 실전 반영은 승인이 기본이에요(모의만 스스로 반영 가능)."]]},
            {"key": "div", "t": "배당·적립", "items": [
                ["매일 모으기", "아침 8시 뒤 배당 자료를 새로 받아 배당률을 기록하고, 새 배당은 직전과 비교해 인상·삭감을 알려요."],
                ["배당률 위치", "지금 배당률이 지난 5년 범위에서 어디쯤인지(높으면 주가가 싼 편)"],
                ["적립", "정해 둔 날 정해 둔 금액을 사고, 배당 전망과 이어 봐요."]]},
        ]
        return {"sections": S, "horizons": hz, "runs": runs, "labs": labs, "principles": [{"t": t, "d": d} for t, d in PRINCIPLES]}

    # ------------------------------------------------------------------ 앞으로
    def plan(self):
        from . import autotrade as AT
        rt, at, db = self.rt, self.rt.autotrade, self.rt.db
        steps = []
        for a in at.advice():
            lab = f"{'국내' if a['mkt'] == 'kr' else '미국'} {a['style_label']}"
            top = a.get("top") or []
            n, d = a.get("max_n", 0), a.get("max_days", 0)
            st = "done" if a.get("best") else "doing"
            steps.append({"area": "lab", "t": f"{lab}: 실전 후보 찾기", "d": a["text"], "st": st,
                          "prog": [min(n, AT.READY_N), AT.READY_N], "prog2": [min(d, AT.READY_DAYS), AT.READY_DAYS],
                          "top": [{"label": x["label"], "net": x["net_pct"], "n": x["n"], "win": x["win_rate"]} for x in top]})
        pend = db.q("SELECT p.id, p.run_id, p.summary, r.name FROM at_proposals p JOIN at_runs r ON r.id=p.run_id WHERE p.status='pending' ORDER BY p.id DESC LIMIT 5")
        for p in pend:
            steps.append({"area": "learn", "t": f"{p['name']}: 개선안 승인 대기", "d": p["summary"][:200], "st": "wait", "run": p["run_id"]})
        for r in at._runs("WHERE status='running'"):
            if r.get("lab_id") and r.get("variant") != "base":
                continue
            p = r["params"]
            es = at.engine_stats([r], p.get("engine_since") or r["created_at"])
            steps.append({"area": "ai", "t": f"{r['name'] if not r.get('lab_id') else '실험 묶음'}: 다음 엔진 점검", "st": "doing",
                          "d": f"지금 {AT._short_model(r['model'])} · 정밀 추천 거래 {es['n']}/{AT.ESC_N}건" + (f" · 평균 {es['avg']:+.2f}% · 이익 {es['win']}%" if es["n"] else "")
                          + (" · 더 올리지 않기로 함" if p.get("engine_lock") else ""), "prog": [min(es["n"], AT.ESC_N), AT.ESC_N]})
        from . import aipick as AP
        for h, H in AP.HORIZONS.items():
            st = rt.aipick.learn_stats(None, h)
            ls = rt.aipick.lessons(h) or {}
            if st["n"] < H["min"]:
                steps.append({"area": "learn", "t": f"{H['label']} 추천: 첫 규칙 만들기", "d": f"채점된 추천 {st['n']}/{H['min']}건이 모이면 처음 복기해요.", "st": "doing", "prog": [st["n"], H["min"]]})
            else:
                k = st["n"] - int(ls.get("n") or 0)
                steps.append({"area": "learn", "t": f"{H['label']} 추천: 규칙 다시 고치기", "d": f"지난 복기 뒤 새 결과 {min(k, H['step'])}/{H['step']}건", "st": "doing", "prog": [min(k, H["step"]), H["step"]]})
        ideas = [
            {"t": "흔들림 맞춤 손절을 실전에", "d": "실험의 '흔들림 맞춤 손절'이 기본보다 나으면, 실전 플랜에서도 켤 수 있게(플랜의 '흔들림에 맞춰 손절')", "st": "idea"},
            {"t": "실전은 엔진 올리기도 승인 후", "d": "지금은 실전도 성과가 나쁘면 자동으로 올려요. 비용이 커지는 변경이라 승인 방식으로 바꿀 수 있어요.", "st": "idea"},
            {"t": "실험 1등 기준을 실전 플랜에", "d": "실전 후보가 나오면 그 플랜의 기준(승률·점수·손익비·익절 폭)으로 작게 실전을 시작", "st": "idea"},
            {"t": "배당률 위치로 적립 강약", "d": "배당 자료가 쌓이면 5년 대비 배당률이 높은 달엔 적립을 늘리고 낮은 달엔 줄이는 규칙을 시험", "st": "idea"},
        ]
        goal = None
        try:
            pf = rt.settings.get("payoff") or {}
            if pf.get("amount") and pf.get("deadline"):
                goal = {"name": pf.get("name") or "갚기 목표", "amount": pf["amount"], "deadline": pf["deadline"]}
        except Exception:
            goal = None
        return {"steps": steps, "ideas": ideas, "goal": goal}

    def all(self):
        return {"overview": self.overview(), "timeline": self.timeline(), "logic": self.logic(), "plan": self.plan(), "areas": AREAS,
                "at": datetime.now().isoformat(timespec="minutes")}
