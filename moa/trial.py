"""2주 점검(v5.57): 사용자가 'Max 구독이 2주 남았다. 2주 안에 투자 부분 개선 효과가 없으면 해지'라고 했다(2026-11-25).
그 판단을 숫자로 하려고, 정한 날부터 14일 동안 아래 네 가지를 모은다. 판단(해지 여부)은 사용자가 한다 — 앱은 사실만 보여 준다.

① 앱 판단 > 기준선: 실험 묶음마다 기본 플랜(앱의 기준·자동 개선이 반영되는 플랜)과 '가리지 않고 사기'(AI가 낸 종목을 다 사 보는 기준선)의
   거래당 평균 수익률(수수료 뺀)을 견준다. 둘 다 MIN_N건 이상인 묶음만.
② 수익: 기본 플랜들의 거래를 모두 합친 거래당 평균 수익률이 0보다 큰가(MIN_N건 이상).
③ AI 비용 지킴: 날마다 AI 비용이 하루 상한(설정 › AI) 안이었나.
④ 비용 뺀 손익: 기본 플랜들의 모의 손익(원) − 그 기간 AI 비용(원).
"""
from datetime import date, datetime, timedelta

DAYS = 14
MIN_N = 15


class Trial:
    def __init__(self, rt):
        self.rt = rt

    # ----- 기간
    def conf(self):
        c = self.rt.db.get_setting("trial") or {}
        if not c.get("start"):
            c = {"start": date.today().isoformat(), "days": DAYS, "sent": []}
            self.rt.db.set_setting("trial", c)
        return c

    def restart(self):
        self.rt.db.set_setting("trial", {"start": date.today().isoformat(), "days": DAYS, "sent": []})
        return self.view()

    # ----- 모으기
    def _labs(self, start):
        at = self.rt.autotrade
        from .autotrade import STYLES, _agg
        labs = {}
        for r in at._runs("WHERE lab_id IS NOT NULL AND variant IN ('base','all')"):
            labs.setdefault(r["lab_id"], {})[r["variant"]] = r
        out = []
        for lab, v in sorted(labs.items()):
            b = v.get("base")
            if not b:
                continue
            rows = {k: self.rt.db.q("SELECT * FROM at_trades WHERE run_id=? AND status='closed' AND entry_ts>=? ORDER BY id", (r["id"], start))
                    for k, r in v.items()}
            if not rows.get("base") and b["status"] == "ended":
                continue
            B, A = _agg(rows.get("base") or []), _agg(rows.get("all") or [])
            ok = B["n"] >= MIN_N and A["n"] >= MIN_N
            out.append({"lab": lab, "mkt": b["mkt"], "style": b["style"], "label": f"{'국내' if b['mkt'] == 'kr' else '미국'} {STYLES[b['style']]['label']}",
                        "status": b["status"], "base": B, "all": A, "rows": rows.get("base") or [],
                        "diff": round(B["avg_pct"] - A["avg_pct"], 3) if ok else None})
        return out

    def _ai(self, start, cap):
        days = {}
        for r in self.rt.db.q("SELECT ts, usd FROM ai_usage WHERE ts>=?", (start,)):
            days[r["ts"][:10]] = days.get(r["ts"][:10], 0.0) + float(r["usd"] or 0)
        rows = [{"day": d, "usd": round(u, 3), "over": bool(cap) and u > cap} for d, u in sorted(days.items())]
        return {"days": rows, "usd": round(sum(days.values()), 3), "over": sum(1 for x in rows if x["over"]), "cap": cap}

    def view(self):
        c = self.conf()
        start = c["start"]
        s = date.fromisoformat(start)
        end = s + timedelta(days=int(c.get("days") or DAYS))
        today = date.today()
        day_n = min((today - s).days + 1, int(c.get("days") or DAYS))
        done = today >= end
        ai = self.rt.ai
        fx = ai.fx()
        labs = self._labs(start)
        cost = self._ai(start, ai.day_cap())

        def krw(rows, mkt):
            return sum((r["pnl"] or 0) * (1 if mkt == "kr" else fx) for r in rows)
        base_rows = [r for L in labs for r in L["rows"]]
        from .autotrade import _agg
        pool = _agg(base_rows)
        pnl_krw = round(sum(krw(L["rows"], L["mkt"]) for L in labs))
        cost_krw = round(cost["usd"] * fx)

        cmp_ = [L for L in labs if L["diff"] is not None]
        better = sum(1 for L in cmp_ if L["diff"] > 0)
        checks = [
            {"key": "edge", "title": "앱 판단이 기준선보다 나은가",
             "state": "wait" if not cmp_ else ("pass" if better * 2 > len(cmp_) else "fail"),
             "value": (f"{len(cmp_)}개 묶음 중 {better}개가 나음" if cmp_ else f"묶음마다 두 플랜 모두 {MIN_N}건 이상 쌓이면 견줘요"),
             "help": "기본 플랜(앱의 기준·자동 개선 반영)과 'AI가 낸 종목 다 사기' 기준선의 거래당 평균 수익률(수수료 뺀)"},
            {"key": "profit", "title": "거래당 평균 수익이 플러스인가",
             "state": "wait" if pool["n"] < MIN_N else ("pass" if pool["avg_pct"] > 0 else "fail"),
             "value": (f"{pool['n']}건 평균 {pool['avg_pct']:+.2f}% · 이긴 비율 {pool['win_rate']:.0f}%" if pool["n"] else "아직 거래가 없어요"),
             "help": "기본 플랜 거래를 모두 합쳐서"},
            {"key": "cost", "title": "AI 비용이 하루 상한 안이었나",
             "state": "pass" if not cost["over"] else "fail",
             "value": (f"{len(cost['days'])}일 합계 ${cost['usd']:.2f} · 상한 넘은 날 {cost['over']}일" + (f" (상한 ${cost['cap']:g})" if cost["cap"] else "")),
             "help": "설정 › AI의 하루 상한 기준"},
            {"key": "net", "title": "AI 비용을 빼도 남는가",
             "state": "wait" if pool["n"] < MIN_N else ("pass" if pnl_krw - cost_krw > 0 else "fail"),
             "value": f"모의 손익 {pnl_krw:+,}원 − AI 비용 {cost_krw:,}원 = {pnl_krw - cost_krw:+,}원",
             "help": "모의(가상 돈) 손익이라 실제 돈과는 달라요. 미국은 지금 환율로 원 환산"},
        ]
        n_pass = sum(1 for x in checks if x["state"] == "pass")
        n_fail = sum(1 for x in checks if x["state"] == "fail")
        warn = []
        if not ai.enabled():
            warn.append("AI가 꺼져 있어 새 추천·거래가 쌓이지 않아요. 점검하려면 설정 › AI에서 켜야 해요")
        if not labs:
            warn.append("돌고 있는 모의 자동 테스트가 없어요. 투자 › AI 자동매매에서 켜야 견줄 수 있어요")
        return {"start": start, "end": (end - timedelta(days=1)).isoformat(), "day": day_n, "days": int(c.get("days") or DAYS), "done": done,
                "left": max(0, (end - today).days), "checks": checks, "pass": n_pass, "fail": n_fail, "warn": warn, "min_n": MIN_N,
                "labs": [{k: L[k] for k in ("label", "status", "base", "all", "diff")} for L in labs], "cost": cost,
                "summary": self._summary(done, n_pass, n_fail, checks)}

    @staticmethod
    def _summary(done, n_pass, n_fail, checks):
        waiting = sum(1 for x in checks if x["state"] == "wait")
        if not done:
            return f"진행 중 · 통과 {n_pass} · 미달 {n_fail} · 표본 모자람 {waiting}"
        return f"2주 끝 · 네 가지 중 {n_pass}가지 통과" + (f" · 표본이 모자란 {waiting}가지는 판단 못 함" if waiting else "")

    # ----- 알림: 중간(7일째)과 끝(14일째)에 한 번씩
    def tick(self):
        c = self.conf()
        s = date.fromisoformat(c["start"])
        n = (date.today() - s).days
        sent = list(c.get("sent") or [])
        for mark, label in ((7, "중간"), (int(c.get("days") or DAYS), "끝")):
            if n >= mark and mark not in sent and datetime.now().hour >= 9:
                v = self.view()
                body = " · ".join(f"{x['title']}: {'통과' if x['state'] == 'pass' else '미달' if x['state'] == 'fail' else '판단 전'}" for x in v["checks"])
                self.rt.notify(f"{v['summary']}. {body}", "info", f"2주 점검 {label}", key=f"trial:{c['start']}:{mark}", link="#invest/auto")
                sent.append(mark)
                c["sent"] = sent
                self.rt.db.set_setting("trial", c)
