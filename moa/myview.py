"""내 의견: 사용자가 자동매수에 직접 보태는 세 가지.

① 메모 — "이번 주 관심 테마", "실적 발표 전엔 조심" 같은 글. AI 추천이 매번 함께 읽는다(참고 자료일 뿐, 사실 확인 없이 따르지 않게 일러 둔다).
② 제외 목록 — 절대 사지 않을 종목. 후보에서 빼고, AI가 골라도 '경고·특수 종목' 거르기에서 걸러진다(모의·실전 모두).
③ 추천 평가 — 추천 종목에 동의·반대를 남긴다. 사고파는 데는 쓰지 않고, 내 판단이 실제로 맞았는지를 추천 성적에서 따로 잰다.
"""
import re
from datetime import date, datetime, timedelta

MAX_NOTES, MAX_BLOCK, NOTE_LEN = 8, 60, 120


class MyView:
    def __init__(self, rt):
        self.rt = rt
        rt.db.x("""CREATE TABLE IF NOT EXISTS ai_votes(pick_id INTEGER NOT NULL, sym TEXT NOT NULL, vote INTEGER NOT NULL, ts TEXT NOT NULL,
                   UNIQUE(pick_id, sym))""")

    def _get(self):
        d = self.rt.db.get_setting("my_view", {}) or {}
        return {"notes": list(d.get("notes") or []), "block": list(d.get("block") or []), "seq": int(d.get("seq") or 0)}

    def _put(self, d):
        self.rt.db.set_setting("my_view", d)

    # ----- 메모
    def notes(self, mkt=None, today=None):
        t = (today or date.today()).isoformat()
        return [n for n in self._get()["notes"] if (not n.get("until") or n["until"] >= t) and (mkt is None or n.get("mkt") in ("all", mkt))]

    def add_note(self, d):
        text = re.sub(r"\s+", " ", str(d.get("text") or "")).strip()[:NOTE_LEN]
        if not text:
            raise ValueError("메모를 적어 주세요")
        v = self._get()
        t = date.today().isoformat()
        v["notes"] = [n for n in v["notes"] if not n.get("until") or n["until"] >= t]      # 기한이 지난 것은 이때 치운다
        if len(v["notes"]) >= MAX_NOTES:
            raise ValueError(f"메모는 {MAX_NOTES}개까지예요. 안 쓰는 것을 지워 주세요")
        days = int(d.get("days") or 0)
        v["seq"] += 1
        v["notes"].append({"id": v["seq"], "text": text, "mkt": d.get("mkt") if d.get("mkt") in ("kr", "us") else "all",
                           "until": (date.today() + timedelta(days=min(days, 90))).isoformat() if days > 0 else "", "ts": datetime.now().isoformat(timespec="seconds")})
        self._put(v)
        return self.view()

    def del_note(self, nid):
        v = self._get()
        v["notes"] = [n for n in v["notes"] if n.get("id") != int(nid)]
        self._put(v)
        return self.view()

    # ----- 제외 목록
    def blocked(self, mkt, sym):
        s = str(sym or "").upper()
        return any(b["mkt"] == mkt and b["sym"] == s for b in self._get()["block"])

    def add_block(self, d):
        mkt, q = ("us" if d.get("mkt") == "us" else "kr"), str(d.get("q") or "").strip()
        if not q:
            raise ValueError("종목 이름이나 코드를 넣어 주세요")
        m = self.rt.market
        if mkt == "kr":
            code = re.sub(r"\D", "", q)[-6:] if re.fullmatch(r"[A-Za-z]?\d{6}", q) else ""
            name = (m.kr_info(code) or {}).get("name") if code else ""
            if not name:
                hits = m.kr_search(q) or []
                hit = next((x for x in hits if x.get("name") == q), hits[0] if hits else None)
                if not hit:
                    raise ValueError(f"'{q}' 종목을 찾지 못했어요. 이름을 정확히 넣거나 6자리 코드를 넣어 주세요")
                code, name = hit["sym"], hit.get("name") or hit["sym"]
        else:
            code = q.upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", code):
                raise ValueError("미국 종목은 기호(예: TSLA)로 넣어 주세요")
            name = code
        v = self._get()
        if not any(b["mkt"] == mkt and b["sym"] == code for b in v["block"]):
            if len(v["block"]) >= MAX_BLOCK:
                raise ValueError(f"제외 종목은 {MAX_BLOCK}개까지예요")
            v["block"].append({"mkt": mkt, "sym": code, "name": name, "why": str(d.get("why") or "").strip()[:40], "ts": datetime.now().isoformat(timespec="seconds")})
            self._put(v)
        return self.view()

    def del_block(self, mkt, sym):
        v = self._get()
        v["block"] = [b for b in v["block"] if not (b["mkt"] == mkt and b["sym"] == str(sym).upper())]
        self._put(v)
        return self.view()

    def view(self):
        v = self._get()
        t = date.today().isoformat()
        return {"notes": [dict(n, live=not n.get("until") or n["until"] >= t) for n in v["notes"]], "block": v["block"], "max_notes": MAX_NOTES, "note_len": NOTE_LEN}

    # ----- AI 추천에 메모 싣기
    def into(self, ask, data, mkt):
        ns = self.notes(mkt)
        if ns:
            data["사용자 메모(사용자가 직접 적은 관심·주의 사항)"] = [n["text"] for n in ns]
            ask += (" '사용자 메모'도 읽어. 참고 자료일 뿐이니 사실과 맞는지 스스로 확인하고, 맞지 않거나 근거가 약하면 따르지 말고 이유를 말해."
                    " 메모 때문에 win_prob를 올리지 말고, 메모를 반영해 고른 종목은 reasons에 '사용자 메모 반영'이라고 밝혀.")
        return ask, data

    # ----- 추천 평가
    def vote(self, d):
        pid, sym, v = int(d.get("pick_id") or 0), str(d.get("sym") or "").upper(), int(d.get("vote") or 0)
        if not pid or not sym or not self.rt.db.one("SELECT 1 x FROM ai_picks WHERE id=?", (pid,)):
            raise ValueError("추천을 찾지 못했어요")
        if v == 0:
            self.rt.db.x("DELETE FROM ai_votes WHERE pick_id=? AND sym=?", (pid, sym))
        else:
            self.rt.db.x("INSERT INTO ai_votes(pick_id,sym,vote,ts) VALUES(?,?,?,?) ON CONFLICT(pick_id,sym) DO UPDATE SET vote=excluded.vote, ts=excluded.ts",
                         (pid, sym, 1 if v > 0 else -1, datetime.now().isoformat(timespec="seconds")))
        return {"pick_id": pid, "sym": sym, "vote": 0 if v == 0 else (1 if v > 0 else -1)}

    def votes(self, pick_ids=None):
        rows = self.rt.db.q("SELECT pick_id, sym, vote FROM ai_votes")
        return {(r["pick_id"], r["sym"]): r["vote"] for r in rows if pick_ids is None or r["pick_id"] in pick_ids}
