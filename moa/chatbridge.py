"""대화창으로 AI 복기 주고받기(v5.64): 자동 개선이 AI 복기를 해야 할 때 API를 부르지 않고 '질의'를 만들어 두면,
사용자가 그 글을 Claude 대화창(구독)에 붙여 넣고 받은 답을 앱에 올린다. 앱은 API 답과 똑같이 읽는다(비용 0).

- 사용자 요청(2026-11-27): '자동 개선을 켜면 지금 API로 하는데, 대화창에서 입력·출력으로 처리할 수도 있게'.
- 방식은 설정 self_improve.via: "api"(기본, 예전 그대로) | "chat"(이 모듈). chat이면 AI 스위치가 꺼져 있어도 질의를 만든다(API를 부르지 않으니).
- 질의 종류: auto(자동매매·실험 기본 플랜 복기 → 개선안 → selfimprove.take로 실험에서 시험), learn(추천 성적 복기 → 추천 규칙 새 판).
- 질의 글 = API로 보내던 것과 같은 지시(SYSTEM + ask)와 앱 데이터(JSON). 같은 대상의 질의는 하나만 열어 둔다(새로 만들지 않고 기다린다).
- 열린 질의는 EXPIRE_D일이 지나면 닫는다(오래된 자료로 판단하지 않게).
"""
import json
from datetime import datetime, timedelta

KINDS = {"auto": "자동매매 복기", "learn": "추천 규칙 복기"}
EXPIRE_D = 4
MODEL = "chat"          # 대화창으로 받은 답(at_proposals.stats.model, lessons.model)
DATA_MAX = 60000



def _fence_json(text):
    """``` 없이 붙여 넣은 답에서 changes가 든 JSON 객체를 찾아 ```json 블록으로 감싼다. 못 찾으면 그대로."""
    dec = json.JSONDecoder()
    for i in [k for k, ch in enumerate(text) if ch == "{"]:
        try:
            obj, end = dec.raw_decode(text[i:])
        except ValueError:
            continue
        if isinstance(obj, dict) and "changes" in obj:
            head = text[:i].rstrip()
            return (head + "\n\n" if head else "") + "```json\n" + text[i:i + end] + "\n```\n" + text[i + end:].lstrip()
    return text

class ChatBridge:
    def __init__(self, rt):
        self.rt = rt
        rt.db.x("""CREATE TABLE IF NOT EXISTS chat_asks(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, target TEXT NOT NULL,
                d TEXT, title TEXT, prompt TEXT, status TEXT NOT NULL DEFAULT 'open', created TEXT NOT NULL, answered TEXT, result TEXT)""")

    @staticmethod
    def _now():
        return datetime.now().isoformat(timespec="seconds")

    def via(self):
        return "chat" if (self.rt.db.get_setting("self_improve") or {}).get("via") == "chat" else "api"

    def on(self):
        si = getattr(self.rt, "selfimp", None)
        return self.via() == "chat" and (si is None or si.on())

    @staticmethod
    def _target(kind, d):
        return f"run:{int(d.get('run_id') or 0)}" if kind == "auto" else f"h:{d.get('horizon') or 'day'}"

    def open_for(self, kind, d):
        return self.rt.db.one("SELECT * FROM chat_asks WHERE kind=? AND target=? AND status='open'", (kind, self._target(kind, d)))

    def prompt(self, kind, d, title, ask, data, aid):
        from .ai import SYSTEM
        body = json.dumps(data, ensure_ascii=False, default=str)
        cut = len(body) > DATA_MAX
        return (f"# 우석World 질의 Q{aid} · {title}\n"
                f"(만든 때 {self._now()[:16].replace('T', ' ')} · 종류: {KINDS[kind]})\n\n"
                "아래 '역할'과 '요청'대로 답해 줘. 답 전체를 그대로 앱의 '답 올리기'에 붙여 넣을 거야.\n\n"
                f"## 역할\n{SYSTEM.strip()}\n\n## 요청\n{ask.strip()}\n\n"
                f"<앱 데이터>\n{body[:DATA_MAX]}\n</앱 데이터>" + ("\n(자료가 길어 뒷부분을 잘랐어)" if cut else ""))

    def create(self, kind, d, why=""):
        """질의를 만든다. 같은 대상의 열린 질의가 있으면 그것을 돌려준다. 자료가 모자라면 ValueError."""
        if kind not in KINDS:
            raise ValueError("모르는 질의 종류예요")
        self.expire()
        old = self.open_for(kind, d)
        if old:
            return self._row(old)
        from .ai import AIError
        try:
            title, ask, data = self.rt.ai.context(kind, d)
        except AIError as e:
            raise ValueError(str(e))
        aid = self.rt.db.x("INSERT INTO chat_asks(kind,target,d,title,status,created) VALUES(?,?,?,?,?,?)",
                           (kind, self._target(kind, d), json.dumps(d, ensure_ascii=False), title, "open", self._now()))
        self.rt.db.x("UPDATE chat_asks SET prompt=? WHERE id=?", (self.prompt(kind, d, title, ask, data, aid), aid))
        si = getattr(self.rt, "selfimp", None)
        if si is not None:
            si.note("review", None, d.get("horizon") if kind == "learn" else None, f"대화창 질의 Q{aid} 만듦 · {title}", why or "질의 글을 Claude 대화창에 붙여 넣고 받은 답을 올려 주세요")
        self.rt.notify(f"{title} 질의가 생겼어요. 자동 개선 화면에서 질의를 복사해 Claude 대화창에 붙여 넣고, 받은 답을 올려 주세요", "info",
                       "대화창 질의", key=f"chatask:{aid}", link="#invest/si")
        return self._row(self.rt.db.one("SELECT * FROM chat_asks WHERE id=?", (aid,)))

    def expire(self):
        cut = (datetime.now() - timedelta(days=EXPIRE_D)).isoformat(timespec="seconds")
        self.rt.db.x("UPDATE chat_asks SET status='expired' WHERE status='open' AND created<?", (cut,))

    def cancel(self, aid):
        self.rt.db.x("UPDATE chat_asks SET status='cancelled' WHERE id=? AND status='open'", (int(aid),))
        return self.list()

    def answer(self, aid, text):
        """받은 답을 API 답과 같은 길로 읽는다. 돌려주는 것: 처리 결과 한 줄."""
        row = self.rt.db.one("SELECT * FROM chat_asks WHERE id=?", (int(aid),))
        if not row:
            raise ValueError("질의를 찾지 못했어요")
        if row["status"] != "open":
            raise ValueError("이미 닫힌 질의예요(답을 올렸거나 기한이 지났어요). 새 질의를 만들어 주세요")
        text = str(text or "").strip()
        if len(text) < 20:
            raise ValueError("답이 너무 짧아요. Claude가 준 답 전체를 붙여 넣어 주세요")
        d = json.loads(row["d"] or "{}")
        if row["kind"] == "auto":
            if "```" not in text:
                text = _fence_json(text)          # 코드 블록 안만 복사해 붙여 넣은 경우(``` 없이 JSON만) — 감싸서 같은 길로
            if "```" not in text:
                raise ValueError("답 끝에 ```json 코드 블록(summary·changes)이 있어야 해요. 그 부분까지 함께 붙여 넣어 주세요")
            pr = self.rt.autotrade.save_review(text, dict(d, auto=True), MODEL, 0.0)
            ch = pr.get("changes") or []
            res = (f"개선안 {len(ch)}개를 받았어요: " + ", ".join(f"{c['label']} {c['from']}→{c['to']}" for c in ch)) if ch else "바꿀 값이 없다는 답이에요(지금 값 유지)"
            st = self.rt.db.one("SELECT status FROM at_proposals WHERE id=?", (pr["id"],))
            if ch and st and st["status"] == "testing":
                res += " · 실험 'AI 개선안' 플랜에서 먼저 시험해요"
        else:
            h = d.get("horizon") or "day"
            self.rt.aipick.save_lessons(text, MODEL, 0.0, h)
            ver = self.rt.aipick.lessons(h).get("ver")
            res = f"추천 규칙 {ver}판으로 저장했어요"
        self.rt.db.x("UPDATE chat_asks SET status='answered', answered=?, result=? WHERE id=?", (self._now(), res, row["id"]))
        si = getattr(self.rt, "selfimp", None)
        if si is not None:
            si.note("review", None, None, f"대화창 답 Q{row['id']} 받음 · {row['title']}", res)
        return {"result": res, "asks": self.list()}

    def _row(self, r, full=True):
        out = {k: r[k] for k in ("id", "kind", "target", "title", "status", "created", "answered", "result")}
        out["kind_label"] = KINDS.get(r["kind"], r["kind"])
        if full:
            out["prompt"] = r["prompt"]
            out["chars"] = len(r["prompt"] or "")
        return out

    def list(self):
        self.expire()
        rows = self.rt.db.q("SELECT * FROM chat_asks ORDER BY id DESC LIMIT 12")
        return {"via": self.via(), "open": [self._row(r) for r in rows if r["status"] == "open"],
                "done": [self._row(r, False) for r in rows if r["status"] != "open"][:6]}

    def targets(self):
        """지금 질의를 만들 수 있는 대상(화면의 '지금 질의 만들기')."""
        out = []
        si = getattr(self.rt, "selfimp", None)
        if si is not None:
            from .autotrade import STYLES
            for b, _ in si._labs():
                out.append({"kind": "auto", "run_id": b["id"], "label": f"{'국내' if b['mkt'] == 'kr' else '미국'} {STYLES[b['style']]['label']} 실험 기본 플랜 복기"})
        from .aipick import HORIZONS
        for h, H in HORIZONS.items():
            out.append({"kind": "learn", "horizon": h, "label": f"{H['label']} 추천 규칙 복기"})
        return out
