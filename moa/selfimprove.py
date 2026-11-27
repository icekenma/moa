"""자동 개선: 자동매매가 스스로 성적을 보고 판단 기준을 고치고, 고친 것이 실제로 나았는지 확인해 남기거나 되돌리는 순환.

한 바퀴(시장·방식마다 — 국내/미국 × 단타/스윙):
  ① 복기   추천 성적이 쌓이면 AI가 '추천 규칙'을 새로 쓴다(aipick.auto_learn). 새 규칙이 앞 규칙보다 나쁘면 앞 규칙으로 되돌린다.
  ② 개선안 자동매매·실험 기본 플랜의 거래를 분석해 기준값을 바꾸는 안을 낸다(규칙 분석 + AI 복기). 바로 반영하지 않는다.
  ③ 시험   개선안은 실험 묶음의 'AI 개선안' 플랜에 넣어, 다른 플랜과 같은 추천으로 성적을 견준다.
  ④ 승격   어떤 실험 플랜이 같은 기간 기본 플랜보다 충분히(거래 수·평균 수익률·총손익·승률) 나으면 그 값을 기본 플랜과
           같은 시장·방식의 자동매매(모의, 그리고 켜 두면 실전)에 반영한다. 다른 실험 플랜은 새 기본값에서 다시 갈라진다.
  ⑤ 지켜보기 반영한 뒤 거래가 쌓이면 반영 전과 견줘, 나빠졌으면 되돌리고 그 값은 2주 동안 다시 쓰지 않는다.

이 순환이 부르는 AI(자동 복기)는 Sonnet까지만 쓴다(ai.BG_MAX, 2026-11-25 비용 사고 뒤 Fable 고정을 없앰).
절대 스스로 바꾸지 않는 것: 하루·전체 손실 한도, 굴리는 돈, AI 비용 한도, 실전 주문 허용, 가장 기본 원칙(evolution.CORE),
그리고 PLAN_KEYS 범위 밖의 값. 실험의 기준선 플랜('가리지 않고 사기')은 승격 후보가 아니다.
"""
import copy
import json
import threading
import time
from datetime import datetime, timedelta

FABLE = "claude-fable-5-2"
SAFE_KEYS = {"daily_loss_pct", "total_loss_pct"}          # 손실 한도는 사람만 바꾼다
NO_PROMOTE = {"base", "all"}                              # 기본 플랜 자신, 기준선 플랜은 승격 후보가 아니다
MIN_N = {"day": 20, "swing": 10, "month": 8}               # 승격하려면 같은 기간 이 플랜의 끝난 거래가 이만큼
EDGE = 0.25                                               # 거래당 평균 수익률이 기본 플랜보다 이만큼(%p) 높아야
WIN_SLACK = 5                                             # 승률은 기본 플랜보다 이만큼(%p) 넘게 낮으면 안 된다
WATCH_N = {"day": 15, "swing": 8, "month": 6}             # 반영 뒤 이만큼 거래가 쌓이면 반영 전 같은 수와 견준다
COOL_H = 72                                               # 한 번 승격하면 같은 묶음은 이 시간 동안 새로 승격하지 않는다
BLOCK_D = 14                                              # 되돌린 값은 이 날수 동안 다시 반영하지 않는다
REVIEW_EVERY = {"day": 10, "swing": 6, "month": 5}         # 실험 기본 플랜에 끝난 거래가 이만큼 새로 쌓이면 AI 복기(FABLE)
REVIEW_GAP_H = 48                                         # AI 복기 사이 최소 간격
LESSON_N = 15                                             # 추천 규칙 판끼리 견줄 때 각 판의 채점 수
TICK_S = 300                                              # 순환을 5분마다 한 번 본다

KIND = {"lesson": "추천 규칙", "test": "개선안 시험", "adopt": "반영", "rollback": "되돌림", "keep": "확인 완료", "review": "AI 복기",
        "engine": "추천 엔진", "setting": "설정"}


def _f(v):
    """화면용 값: 55.0 → 55, 1.20 → 1.2"""
    if isinstance(v, bool) or v is None:
        return {True: "켬", False: "끔", None: "-"}[v]
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f"{round(v, 2):g}"
    return str(v)


def _line(ch, back=False):
    return ", ".join(f"{x['label']} {_f(x['to'] if back else x['from'])}→{_f(x['from'] if back else x['to'])}" for x in ch)


class SelfImprove:
    def __init__(self, rt):
        self.rt = rt
        db = rt.db
        db.x("""CREATE TABLE IF NOT EXISTS si_log(id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, mkt TEXT, style TEXT,
                title TEXT NOT NULL, detail TEXT, data TEXT)""")
        db.x("""CREATE TABLE IF NOT EXISTS si_changes(id INTEGER PRIMARY KEY, ts TEXT NOT NULL, run_id INTEGER NOT NULL, lab_id INTEGER,
                mkt TEXT, style TEXT, changes TEXT NOT NULL, evidence TEXT, status TEXT NOT NULL DEFAULT 'active', result TEXT, done_ts TEXT)""")
        self._at = 0.0
        self._rv = {}          # 묶음 → AI 복기 스레드

    # ------------------------------------------------------------------ 설정
    def conf(self):
        s = self.rt.db.get_setting("self_improve") or {}
        return {"on": bool(s.get("on", True)), "real": bool(s.get("real", True)), "ui": bool(s.get("ui", True)),
                "via": "chat" if s.get("via") == "chat" else "api"}     # AI 복기를 API로 부를지, 대화창 질의로 주고받을지(chatbridge)

    def on(self):
        return self.conf()["on"]

    def set(self, d):
        c = self.conf()
        ch = []
        name = {"on": "자동 개선", "real": "실전에도 반영", "ui": "투자 화면 다듬기"}
        for k in name:
            if k in (d or {}) and bool(d[k]) != c[k]:
                c[k] = bool(d[k])
                ch.append(f"{name[k]} {'켬' if c[k] else '끔'}")
        if d and d.get("via") in ("api", "chat") and d["via"] != c["via"]:
            c["via"] = d["via"]
            ch.append("AI 복기: " + ("대화창으로 주고받기" if c["via"] == "chat" else "API로 바로"))
        if ch:
            self.rt.db.set_setting("self_improve", c)
            self.note("setting", None, None, " · ".join(ch))
        return self.view()

    @staticmethod
    def _now():
        return datetime.now().isoformat(timespec="seconds")

    def note(self, kind, mkt, style, title, detail="", data=None):
        self.rt.db.x("INSERT INTO si_log(ts,kind,mkt,style,title,detail,data) VALUES(?,?,?,?,?,?,?)",
                     (self._now(), kind, mkt, style, str(title)[:200], str(detail or "")[:2000], json.dumps(data, ensure_ascii=False, default=str) if data else None))

    # ------------------------------------------------------------------ 순환
    def tick(self, force=False):
        """자동매매 틱에서 부른다(5분에 한 번만 실제로 돈다)."""
        if not self.on() or (not force and time.time() - self._at < TICK_S):
            return
        self._at = time.time()
        for fn in (self._lessons, self._watch, self._promote_all, self._reviews):
            try:
                fn()
            except Exception as e:
                self.rt.db.log("system", "error", f"자동 개선 오류({fn.__name__}): {type(e).__name__}: {e}", self._now())

    # ----- 묶음
    def _labs(self):
        """돌고 있는 실험 묶음: [(기본 플랜, 모든 플랜)]"""
        at = self.rt.autotrade
        out = {}
        for r in at._runs("WHERE lab_id IS NOT NULL AND status IN ('running','paused')"):
            out.setdefault(r["lab_id"], []).append(r)
        res = []
        for lab, rs in out.items():
            b = next((x for x in rs if x.get("variant") == "base"), None)
            if b:
                res.append((b, rs))
        return res

    def _own(self, mkt, style):
        """같은 시장·방식의 직접 만든 자동매매(실험 아님)."""
        rs = self.rt.autotrade._runs("WHERE lab_id IS NULL AND status IN ('running','paused') AND mkt=? AND style=?", (mkt, style))
        return [r for r in rs if r["mode"] == "paper" or self.conf()["real"]]

    def _stats(self, rid, since="", until=None, last=None):
        """끝난 거래 성적. since 이후에 산 것만(until 앞까지). last를 주면 until 앞의 마지막 last건."""
        q = "SELECT pnl, pnl_pct FROM at_trades WHERE run_id=? AND status='closed' AND entry_ts>=?"
        a = [rid, since or ""]
        if until:
            q += " AND entry_ts<?"
            a.append(until)
        q += " ORDER BY id" + (" DESC LIMIT ?" if last else "")
        if last:
            a.append(int(last))
        rows = self.rt.db.q(q, tuple(a))
        n = len(rows)
        if not n:
            return {"n": 0, "avg": None, "win": None, "sum": 0.0}
        return {"n": n, "avg": round(sum(float(r["pnl_pct"] or 0) for r in rows) / n, 3),
                "win": round(sum(1 for r in rows if (r["pnl"] or 0) > 0) / n * 100), "sum": round(sum(float(r["pnl"] or 0) for r in rows), 2)}

    def _since(self, run):
        """이 플랜의 값이 마지막으로 바뀐 때(실험 비교는 그 뒤 거래만)."""
        r = self.rt.db.one("SELECT MAX(ts) t FROM at_changes WHERE run_id=?", (run["id"],))
        t = (r or {}).get("t") or run.get("created_at") or ""
        return max(t, str(run["params"].get("si_since") or ""))

    def _diff(self, base_p, p):
        from .autotrade import PLAN_KEYS, _clamp_key
        out = []
        for k in PLAN_KEYS:
            if k in SAFE_KEYS or k not in p:
                continue
            try:
                v = _clamp_key(k, p[k])
            except Exception:
                continue
            if v != base_p.get(k):
                out.append((k, base_p.get(k), v))
        return out

    def _blocked(self, mkt, style, k, v):
        since = (datetime.now() - timedelta(days=BLOCK_D)).isoformat(timespec="seconds")
        for r in self.rt.db.q("SELECT changes FROM si_changes WHERE status='rolled' AND mkt=? AND style=? AND done_ts>=?", (mkt, style, since)):
            for c in json.loads(r["changes"] or "[]"):
                if c["key"] == k and c["to"] == v:
                    return True
        return False

    # ----- ④ 승격
    def _promote_all(self):
        for b, rs in self._labs():
            self._promote(b, rs)

    def candidates(self, b, rs):
        """승격 후보 표(화면에도 쓴다): 플랜마다 기본 플랜과 같은 기간 성적, 승격까지 모자란 것."""
        from .autotrade import VARIANTS
        need = MIN_N.get(b["style"], 10)
        sb = self._since(b)
        out = []
        for r in rs:
            if r.get("variant") in NO_PROMOTE:
                continue
            since = max(sb, self._since(r))
            S, B = self._stats(r["id"], since), self._stats(b["id"], since)
            keys = [(k, f, t) for k, f, t in self._diff(b["params"], r["params"]) if not self._blocked(b["mkt"], b["style"], k, t)]
            diff = round(S["avg"] - B["avg"], 3) if S["avg"] is not None and B["avg"] is not None else None
            why = []
            if not keys:
                why.append("기본 플랜과 바꿀 값이 같음")
            if S["n"] < need:
                why.append(f"거래 {S['n']}/{need}건")
            if B["n"] < need // 2:
                why.append(f"기본 플랜 거래 {B['n']}/{need // 2}건")
            if diff is not None and diff < EDGE:
                why.append(f"평균 차이 {diff:+.2f}%p(기준 +{EDGE:g}%p)")
            if S["n"] and B["n"] and S["sum"] <= B["sum"]:
                why.append("총손익이 기본보다 크지 않음")
            if S["win"] is not None and B["win"] is not None and S["win"] < B["win"] - WIN_SLACK:
                why.append(f"승률 {S['win']}% < 기본 {B['win']}%−{WIN_SLACK}")
            label = VARIANTS.get(r.get("variant") or "", (r["name"],))[0]
            out.append({"id": r["id"], "variant": r.get("variant"), "label": label, "since": since, "me": S, "base": B, "diff": diff,
                        "keys": [{"key": k, "from": f, "to": t} for k, f, t in keys], "ok": not why, "why": why,
                        "si": r.get("variant") == "si", "si_from": r["params"].get("si_from")})
        out.sort(key=lambda c: (not c["ok"], -(c["diff"] if c["diff"] is not None else -99)))
        return out

    def _promote(self, b, rs):
        recent = (datetime.now() - timedelta(hours=COOL_H)).isoformat(timespec="seconds")
        if self.rt.db.one("SELECT 1 x FROM si_changes WHERE run_id=? AND ts>=?", (b["id"], recent)):
            return None
        ok = [c for c in self.candidates(b, rs) if c["ok"]]
        if not ok:
            return None
        return self.adopt(b, rs, ok[0])

    def adopt(self, b, rs, c):
        """승격: 이긴 플랜의 값을 기본 플랜과 같은 시장·방식의 자동매매에 반영하고, 다른 실험 플랜은 새 기본값에서 다시 가른다."""
        at = self.rt.autotrade
        from .autotrade import PLAN_KEYS, STYLES
        ch = [{"key": x["key"], "label": PLAN_KEYS[x["key"]][3], "from": x["from"], "to": x["to"]} for x in c["keys"]]
        ev = {"plan": c["label"], "me": c["me"], "base": c["base"], "diff": c["diff"], "since": c["since"], "si_from": c.get("si_from")}
        why = f"실험 '{c['label']}'이 같은 기간 기본 플랜보다 거래당 {c['diff']:+.2f}%p({c['me']['n']}건 · 승률 {c['me']['win']}%)"
        newp = copy.deepcopy(b["params"])
        for x in ch:
            newp[x["key"]] = x["to"]
        self._write(b, newp, ch, why)
        self.rt.db.x("INSERT INTO si_changes(ts,run_id,lab_id,mkt,style,changes,evidence) VALUES(?,?,?,?,?,?,?)",
                     (self._now(), b["id"], b["lab_id"], b["mkt"], b["style"], json.dumps(ch, ensure_ascii=False, default=str), json.dumps(ev, ensure_ascii=False, default=str)))
        self._rederive(b["lab_id"])
        names = []
        for r in self._own(b["mkt"], b["style"]):
            p = copy.deepcopy(r["params"])
            mine = []
            for x in ch:
                if p.get(x["key"]) != x["to"]:
                    mine.append(dict(x, **{"from": p.get(x["key"])}))
                    p[x["key"]] = x["to"]
            if not mine:
                continue
            self._write(r, p, mine, why)
            self.rt.db.x("INSERT INTO si_changes(ts,run_id,lab_id,mkt,style,changes,evidence) VALUES(?,?,?,?,?,?,?)",
                         (self._now(), r["id"], None, r["mkt"], r["style"], json.dumps(mine, ensure_ascii=False, default=str), json.dumps(ev, ensure_ascii=False, default=str)))
            names.append(("실전 " if r["mode"] == "real" else "") + r["name"])
        line = _line(ch)
        self.note("adopt", b["mkt"], b["style"], f"'{c['label']}' 값을 반영: {line}",
                  why + (f". 함께 바꾼 자동매매: {', '.join(names)}" if names else ""), dict(ev, changes=ch, runs=names))
        try:
            real = any(n.startswith("실전 ") for n in names)
            self.rt.notify(f"{line}\n{why}" + (f"\n함께 바뀜: {', '.join(names)}" if names else ""), "real" if real else "info",
                           f"자동 개선 반영 · {'국내' if b['mkt'] == 'kr' else '미국'} {STYLES[b['style']]['label']}",
                           key=f"si-adopt-{b['lab_id']}", quiet_min=1, link="#invest/si")
        except Exception:
            pass
        at.log(b["id"], "review", f"자동 개선 · {line}", {"si": ev})
        return ch

    def _write(self, run, p, ch, why):
        ver = int(run.get("ver") or 1) + 1
        self.rt.db.x("UPDATE at_runs SET params=?, ver=? WHERE id=?", (json.dumps(p, ensure_ascii=False), ver, run["id"]))
        self.rt.db.x("INSERT INTO at_changes(run_id,ts,ver,source,changes,stats) VALUES(?,?,?,?,?,?)",
                     (run["id"], self._now(), ver, "si", json.dumps([dict(x, why=why) for x in ch], ensure_ascii=False, default=str), None))
        self.rt.autotrade.log(run["id"], "review", "자동 개선 · " + _line(ch), {"si": True})
        run["params"], run["ver"] = p, ver

    def _rederive(self, lab):
        """기본 플랜이 바뀌면 다른 실험 플랜을 새 기본값에서 다시 가른다(차이는 그대로). 'AI 개선안' 플랜은 기본값으로 돌아가 다음 개선안을 기다린다."""
        from .autotrade import PLAN_KEYS, VARIANTS, apply_variant
        at = self.rt.autotrade
        rs = at._runs("WHERE lab_id=?", (lab,))
        b = next((x for x in rs if x.get("variant") == "base"), None)
        if not b:
            return
        for r in rs:
            v = r.get("variant")
            if v == "base" or r["status"] == "ended":
                continue
            d = apply_variant(copy.deepcopy(b["params"]), v) if v in VARIANTS and v != "si" else copy.deepcopy(b["params"])
            p = copy.deepcopy(r["params"])
            ch = []
            for k in PLAN_KEYS:
                if k in d and p.get(k) != d[k]:
                    ch.append({"key": k, "label": PLAN_KEYS[k][3], "from": p.get(k), "to": d[k]})
                    p[k] = d[k]
            if v == "si":
                p.pop("si_since", None)
                p.pop("si_from", None)
            if ch or v == "si":
                self._write(r, p, ch, "기본 플랜이 바뀌어 다시 가름")

    # ----- ⑤ 지켜보기 · 되돌리기
    def _watch(self):
        for c in self.rt.db.q("SELECT * FROM si_changes WHERE status='active' ORDER BY id"):
            self.check(c)

    def check(self, c):
        at = self.rt.autotrade
        try:
            run = at._run(c["run_id"])
        except Exception:
            run = None
        if not run or run["status"] == "ended":
            self.rt.db.x("UPDATE si_changes SET status='closed', done_ts=? WHERE id=?", (self._now(), c["id"]))
            return None
        k = WATCH_N.get(run["style"], 10)
        after = self._stats(run["id"], c["ts"])
        if after["n"] < k:
            return None
        rows = self.rt.db.q("SELECT pnl_pct FROM at_trades WHERE run_id=? AND status='closed' AND entry_ts>=? ORDER BY id LIMIT ?", (run["id"], c["ts"], k))
        a_avg = round(sum(float(r["pnl_pct"] or 0) for r in rows) / len(rows), 3)
        before = self._stats(run["id"], "", c["ts"], last=k)
        ch = json.loads(c["changes"] or "[]")
        res = {"after": {"n": len(rows), "avg": a_avg}, "before": before}
        if before["n"] >= k // 2 and before["avg"] is not None and a_avg < before["avg"] - EDGE:
            p = copy.deepcopy(run["params"])
            back = [dict(x, **{"from": p.get(x["key"]), "to": x["from"]}) for x in ch if p.get(x["key"]) == x["to"]]
            for x in back:
                p[x["key"]] = x["to"]
            if back:
                self._write(run, p, back, f"반영 뒤 {len(rows)}건 평균 {a_avg:+.2f}%가 반영 전 {before['avg']:+.2f}%보다 나빠 되돌림")
                if run.get("lab_id") and run.get("variant") == "base":
                    self._rederive(run["lab_id"])
            self.rt.db.x("UPDATE si_changes SET status='rolled', result=?, done_ts=? WHERE id=?", (json.dumps(res), self._now(), c["id"]))
            try:
                self.rt.notify(f"{run['name']}: {_line(ch, back=True)}\n반영 뒤 {len(rows)}건 평균 {a_avg:+.2f}% < 반영 전 {before['avg']:+.2f}%",
                               "real" if run["mode"] == "real" else "info", "자동 개선 되돌림", key=f"si-roll-{c['id']}", link="#invest/si")
            except Exception:
                pass
            self.note("rollback", run["mkt"], run["style"], f"{run['name']}: " + _line(ch, back=True) + " 되돌림",
                      f"반영 뒤 {len(rows)}건 평균 {a_avg:+.2f}% < 반영 전 {before['avg']:+.2f}%. 이 값은 {BLOCK_D}일 동안 다시 쓰지 않아요", res)
            return "rolled"
        self.rt.db.x("UPDATE si_changes SET status='kept', result=?, done_ts=? WHERE id=?", (json.dumps(res), self._now(), c["id"]))
        self.note("keep", run["mkt"], run["style"], f"{run['name']}: 반영한 값 유지",
                  f"반영 뒤 {len(rows)}건 평균 {a_avg:+.2f}%" + (f" · 반영 전 {before['avg']:+.2f}%" if before["avg"] is not None else ""), res)
        return "kept"

    # ----- ② 개선안 → ③ 시험
    def take(self, run, pr):
        """자동매매에서 나온 개선안을 바로 반영하지 않고, 같은 시장·방식 실험의 'AI 개선안' 플랜에 넣어 시험한다. 처리했으면 True."""
        if not self.on() or not pr or pr.get("status") != "pending":
            return False
        at = self.rt.autotrade
        if run.get("lab_id") and run.get("variant") != "base":       # 실험 플랜마다 나온 개선안은 쓰지 않는다(플랜은 일부러 다른 값이다)
            self.rt.db.x("UPDATE at_proposals SET status='skipped', decided_at=? WHERE id=?", (self._now(), pr["id"]))
            return True
        lab = next(((b, rs) for b, rs in self._labs() if b["mkt"] == run["mkt"] and b["style"] == run["style"]), None)
        si = next((x for x in lab[1] if x.get("variant") == "si"), None) if lab else None
        if not si:
            return False
        b = lab[0]
        from .autotrade import PLAN_KEYS, _clamp_key
        p = copy.deepcopy(b["params"])
        ch = []
        for c in pr.get("changes") or []:
            k = c.get("key")
            if k not in PLAN_KEYS or k in SAFE_KEYS:
                continue
            try:
                v = _clamp_key(k, c.get("to"))
            except Exception:
                continue
            if v != p.get(k) and not self._blocked(b["mkt"], b["style"], k, v):
                ch.append({"key": k, "label": PLAN_KEYS[k][3], "from": p.get(k), "to": v, "why": str(c.get("why") or "")[:200]})
                p[k] = v
        if not ch:
            self.rt.db.x("UPDATE at_proposals SET status='none', decided_at=? WHERE id=?", (self._now(), pr["id"]))
            return True
        src = {"run": run["name"], "proposal": pr["id"], "source": pr.get("source"), "summary": pr.get("summary"), "changes": ch}
        p["si_since"], p["si_from"] = self._now(), src
        cur = {k: si["params"].get(k) for k in PLAN_KEYS}
        self._write(si, p, [{"key": x["key"], "label": x["label"], "from": cur.get(x["key"]), "to": x["to"]} for x in ch], "새 개선안 시험")
        self.rt.db.x("UPDATE at_proposals SET status='testing', decided_at=? WHERE id=?", (self._now(), pr["id"]))
        line = _line(ch)
        self.note("test", b["mkt"], b["style"], f"개선안 시험 시작: {line}",
                  f"'{run['name']}'의 {'AI' if pr.get('source') == 'ai' else '규칙'} 분석에서 나온 안을 실험 'AI 개선안' 플랜에서 먼저 시험해요. "
                  f"같은 추천으로 기본 플랜보다 나으면 반영해요" + (f" · {pr.get('summary')}" if pr.get("summary") else ""), src)
        at.log(run["id"], "review", f"개선안을 바로 반영하지 않고 실험에서 먼저 시험해요: {line}", {"proposal": pr["id"]})
        return True

    def _reviews(self):
        """실험 기본 플랜에 거래가 새로 쌓이면 AI 복기(FABLE)를 부른다 → 개선안 → take()로 시험."""
        at = self.rt.autotrade
        chat = self.conf()["via"] == "chat"              # 대화창 방식: API를 부르지 않고 질의만 만든다(AI 스위치와 상관없이)
        if not chat and not self.rt.ai.configured():
            return
        for b, rs in self._labs():
            th = self._rv.get(b["id"])
            if th and th.is_alive():
                continue
            last = self.rt.db.one("SELECT MAX(ts) t FROM at_proposals WHERE run_id=? AND source='ai'", (b["id"],)) or {}
            t = last.get("t") or ""
            gap = (datetime.now() - timedelta(hours=REVIEW_GAP_H)).isoformat(timespec="seconds")
            if t and t > gap:
                continue
            # 시도 시각도 본다: 복기가 실패하면 개선안이 남지 않아 5분마다 다시 부르던 구멍(2026-11-25 비용 사고)을 막는다
            tries = self.rt.db.get_setting("si_rv_try") or {}
            if (tries.get(str(b["id"])) or "") > gap:
                continue
            if self._stats(b["id"], t)["n"] < REVIEW_EVERY.get(b["style"], 8):
                continue
            if chat:
                if self.rt.chat.open_for("auto", {"run_id": b["id"]}):
                    continue                                  # 이미 열린 질의가 있으면 답을 기다린다
                tries[str(b["id"])] = datetime.now().isoformat(timespec="seconds")
                self.rt.db.set_setting("si_rv_try", tries)
                try:
                    self.rt.chat.create("auto", {"run_id": b["id"], "auto": True}, f"실험 기본 플랜의 새 거래 {self._stats(b['id'], t)['n']}건을 복기할 차례예요")
                except ValueError:
                    pass
                continue
            self.note("review", b["mkt"], b["style"], "AI 복기 시작", f"실험 기본 플랜의 새 거래 {self._stats(b['id'], t)['n']}건을 복기해 개선안을 찾아요")
            tries[str(b["id"])] = datetime.now().isoformat(timespec="seconds")
            self.rt.db.set_setting("si_rv_try", tries)
            th = threading.Thread(target=at._ai_review_bg, args=(b["id"],), name=f"moa-si-review-{b['id']}", daemon=True)
            self._rv[b["id"]] = th
            th.start()

    # ----- ① 추천 규칙(복기) 지켜보기
    def _lessons(self):
        ap = self.rt.aipick
        from . import aipick as AP
        seen = self.rt.db.get_setting("si_lessons") or {}
        for h, H in AP.HORIZONS.items():
            ls = ap.lessons(h)
            ver = int(ls.get("ver") or 0)
            S = seen.get(h) or {}
            if ver and ver > int(S.get("ver") or 0):
                self.note("lesson", None, h, f"{H['label']} 추천 규칙 {ver}판", (ls.get("text") or "")[:1200], {"ver": ver, "model": ls.get("model"), "n": ls.get("n")})
                S = {"ver": ver, "checked": False}
            # 새 판이 앞 판보다 나빴는지: 각 판의 채점이 LESSON_N건 넘게 쌓이면 견준다
            if ver >= 2 and not S.get("checked") and not ls.get("restored_from"):
                vers = {v["ver"]: v for v in ap.learn_view(h).get("by_ver") or []}
                cur, prev = vers.get(ver), vers.get(ver - 1)
                if cur and prev and cur["n"] >= LESSON_N and prev["n"] >= LESSON_N:
                    S["checked"] = True
                    old = next((x for x in reversed(ls.get("hist") or []) if int(x.get("ver") or 0) == ver - 1), None)
                    if cur["avg"] < prev["avg"] - EDGE and old and old.get("text"):
                        hist = list(ls.get("hist") or []) + [{"ver": ver, "at": ls.get("at"), "n": ls.get("n"), "text": (ls.get("text") or "")[:1500]}]
                        self.rt.db.set_setting(AP._lkey(h), {"text": old["text"], "at": datetime.now().isoformat(timespec="minutes"), "n": ls.get("n"),
                                                             "model": ls.get("model"), "usd": 0, "ver": ver + 1, "hist": hist[-5:], "restored_from": ver - 1})
                        self.note("rollback", None, h, f"{H['label']} 추천 규칙 {ver}판 → {ver - 1}판 내용으로 되돌림({ver + 1}판)",
                                  f"{ver}판 채점 {cur['n']}건 평균 {cur['avg']:+.2f}% < {ver - 1}판 {prev['n']}건 평균 {prev['avg']:+.2f}%", {"cur": cur, "prev": prev})
                        S = {"ver": ver + 1, "checked": True}
                    else:
                        self.note("keep", None, h, f"{H['label']} 추천 규칙 {ver}판 유지",
                                  f"{ver}판 평균 {cur['avg']:+.2f}%({cur['n']}건) · {ver - 1}판 {prev['avg']:+.2f}%({prev['n']}건)")
            seen[h] = S
        self.rt.db.set_setting("si_lessons", seen)

    # ------------------------------------------------------------------ 화면
    def view(self):
        from . import aipick as AP
        from .ai import MODELS
        from .autotrade import STYLES
        c = self.conf()
        groups = []
        for b, rs in sorted(self._labs(), key=lambda x: (x[0]["mkt"] != "kr", x[0]["style"] != "day")):
            cands = self.candidates(b, rs)
            si = next((x for x in rs if x.get("variant") == "si"), None)
            act = self.rt.db.q("SELECT * FROM si_changes WHERE status='active' AND mkt=? AND style=? ORDER BY id DESC", (b["mkt"], b["style"]))
            W = WATCH_N.get(b["style"], 10)
            watch = []
            for a in act:
                try:
                    r = self.rt.autotrade._run(a["run_id"])
                except Exception:
                    continue
                watch.append({"run": r["name"], "mode": r["mode"], "ts": a["ts"], "n": self._stats(a["run_id"], a["ts"])["n"], "need": W,
                              "changes": json.loads(a["changes"] or "[]")})
            si_c = next((x for x in cands if x["si"]), None)
            if watch:
                stage, line = 5, f"반영한 값을 지켜보는 중 · {watch[0]['n']}/{W}건"
            elif si_c and si_c["si_from"]:
                stage, line = 3, f"개선안 시험 중 · {si_c['me']['n']}/{MIN_N.get(b['style'], 10)}건"
            elif cands and cands[0]["ok"]:
                last = self.rt.db.one("SELECT MAX(ts) t FROM si_changes WHERE run_id=?", (b["id"],)) or {}
                until = (datetime.fromisoformat(last["t"]) + timedelta(hours=COOL_H)) if last.get("t") else None
                stage, line = 4, (f"'{cands[0]['label']}'이 앞서는 중 · 지난 반영 뒤 {COOL_H // 24}일이 지나는 {until.strftime('%m/%d %H:%M')}에 반영"
                                  if until and until > datetime.now() else f"'{cands[0]['label']}' 반영 조건 충족 · 5분 안에 반영")
            else:
                best = next((x for x in cands if x["diff"] is not None), None)
                stage, line = (2, f"가장 앞선 '{best['label']}' {best['diff']:+.2f}%p · " + " · ".join(best["why"][:2])) if best else (1, "성적 모으는 중")
            last_ai = self.rt.db.one("SELECT ts, summary, status FROM at_proposals WHERE run_id=? AND source='ai' ORDER BY id DESC LIMIT 1", (b["id"],))
            need_rv = REVIEW_EVERY.get(b["style"], 8)
            got = self._stats(b["id"], (last_ai or {}).get("ts") or "")["n"]
            own = self._own(b["mkt"], b["style"])
            groups.append({"lab": b["lab_id"], "mkt": b["mkt"], "style": b["style"], "label": f"{'국내' if b['mkt'] == 'kr' else '미국'} {STYLES[b['style']]['label']}",
                           "stage": stage, "line": line, "plans": len(rs), "cands": cands[:6], "watch": watch,
                           "si": {"from": si["params"].get("si_from"), "since": si["params"].get("si_since")} if si else None,
                           "review": {"last": last_ai, "got": got, "need": need_rv}, "runs": [{"name": r["name"], "mode": r["mode"]} for r in own],
                           "base": {k: b["params"].get(k) for k in ("min_win_prob", "min_score", "min_rr", "per_trade_pct", "max_positions", "chase_pct", "exit_mult")}})
        lessons = []
        for h, H in AP.HORIZONS.items():
            ls = self.rt.aipick.lessons(h)
            if ls.get("text") or ls.get("ver"):
                bv = (self.rt.aipick.learn_view(h).get("by_ver") or [])[-3:]
                lessons.append({"h": h, "label": H["label"], "ver": ls.get("ver"), "at": ls.get("at"), "model": ls.get("model"), "text": (ls.get("text") or "")[:900],
                                "by_ver": bv, "restored_from": ls.get("restored_from")})
        ev = self.rt.db.q("SELECT * FROM si_log ORDER BY id DESC LIMIT 60")
        for e in ev:
            e["data"] = json.loads(e["data"]) if e["data"] else None
            e["kind_label"] = KIND.get(e["kind"], e["kind"])
        m0 = datetime.now().strftime("%Y-%m-01")
        u = self.rt.db.one("SELECT COALESCE(SUM(usd),0) s, COUNT(*) n FROM ai_usage WHERE kind IN ('auto','learn') AND ts>=?", (m0,)) or {}
        cnt = {k: self.rt.db.one("SELECT COUNT(*) n FROM si_log WHERE kind=?", (k,))["n"] for k in ("adopt", "rollback", "keep", "test", "lesson")}
        from .evolution import DEV
        ui_log = [{"ver": v, "day": d, "title": t, "what": w, "why": y} for v, d, a, t, w, y in DEV if a == "ui"][:8]
        from .ai import BG_MAX
        ch = self.rt.chat.list() if getattr(self.rt, "chat", None) else {"open": [], "done": []}
        return {"on": c["on"], "real": c["real"], "ui": c["ui"], "via": c["via"], "chat": dict(ch, targets=self.rt.chat.targets() if getattr(self.rt, "chat", None) else []), "ui_on": c["on"] and c["ui"], "ui_log": ui_log, "engine": {"model": BG_MAX, "name": MODELS[BG_MAX][0].split(" · ")[0]},
                "cost": {"usd": round(float(u.get("s") or 0), 3), "n": int(u.get("n") or 0), "krw": round(float(u.get("s") or 0) * self.rt.ai.fx())},
                "counts": cnt, "groups": groups, "lessons": lessons, "events": ev,
                "rules": {"min_n": MIN_N, "edge": EDGE, "watch_n": WATCH_N, "cool_h": COOL_H, "block_d": BLOCK_D, "review_every": REVIEW_EVERY},
                "guards": ["하루·전체 손실 한도, 굴리는 돈, 하루 AI 비용 한도는 스스로 바꾸지 않아요(사람만 바꿔요)",
                           "실전 주문 허용과 가장 기본 원칙('자산을 불릴 확률이 가장 높은 쪽으로')은 건드리지 않아요",
                           "바꿀 수 있는 값은 정해 둔 범위 안에서만 바꿔요(범위 밖 값은 버려요)",
                           f"새 값은 먼저 실험에서 같은 추천으로 시험하고, 거래가 충분히(단타 {MIN_N['day']}건·스윙 {MIN_N['swing']}건) 쌓여 기본보다 거래당 +{EDGE:g}%p 넘게 나을 때만 반영해요",
                           f"반영한 뒤 단타 {WATCH_N['day']}건·스윙 {WATCH_N['swing']}건을 반영 전과 견줘, 나빠졌으면 되돌리고 그 값은 {BLOCK_D}일 동안 다시 안 써요",
                           f"한 번 반영하면 그 묶음은 {COOL_H // 24}일 동안 새로 반영하지 않아요(한꺼번에 여러 번 바뀌지 않게)",
                           "이 순환의 AI 복기는 Sonnet까지만 써요. 하루 AI 비용 상한(설정 › AI)을 넘으면 그날은 부르지 않아요",
                           "투자 화면 다듬기는 보이는 방식만 바꿔요. 사고파는 판단·주문·금액 계산은 건드리지 않아요"]}
