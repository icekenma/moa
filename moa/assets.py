"""자산: 계좌별 잔액, 순자산, 월별 기록. 주식 계좌는 매매 엔진 잔고와 연결할 수 있다."""
from datetime import date

TYPES = {"cash": "현금", "deposit": "예금·적금", "stock": "주식·ETF",
         "pension": "연금", "property": "부동산", "etc": "기타", "debt": "갚을 것"}


class AssetError(ValueError):
    pass


class Assets:
    def __init__(self, db, value_of_engine):
        self.db = db
        self.value_of_engine = value_of_engine  # engine -> 평가금(현금+주식)

    def accounts(self):
        rows = self.db.q("SELECT * FROM accounts ORDER BY sort, id")
        for r in rows:
            r["type_label"] = TYPES.get(r["type"], r["type"])
            if r["linked"] in ("scalp", "dca"):
                try:
                    r["balance"] = int(self.value_of_engine(r["linked"]))
                    r["synced"] = True
                except Exception as e:  # 연결 실패 시 마지막 저장값 사용
                    r["synced"] = False
                    r["sync_error"] = str(e)
        return rows

    def save(self, d):
        name = (d.get("name") or "").strip()
        if not name:
            raise AssetError("계좌 이름을 넣어 주세요")
        if d.get("type") not in TYPES:
            raise AssetError("종류를 골라 주세요")
        try:
            bal = int(str(d.get("balance") or 0).replace(",", ""))
        except ValueError:
            raise AssetError("잔액은 숫자로 넣어 주세요")
        bal = abs(bal)
        linked = d.get("linked") if d.get("linked") in ("scalp", "dca") else None
        note = (d.get("note") or "").strip()[:60]
        if d.get("id"):
            self.db.x("UPDATE accounts SET name=?,type=?,balance=?,note=?,linked=? WHERE id=?",
                      (name, d["type"], bal, note, linked, int(d["id"])))
            return int(d["id"])
        return self.db.x("INSERT INTO accounts(name,type,balance,note,linked,sort) VALUES(?,?,?,?,?,?)",
                         (name, d["type"], bal, note, linked, 100))

    def delete(self, aid):
        self.db.x("DELETE FROM accounts WHERE id=?", (aid,))

    def overview(self, today=None):
        today = today or date.today()
        accts = self.accounts()
        groups = {}
        for a in accts:
            g = groups.setdefault(a["type"], {"type": a["type"], "label": TYPES[a["type"]], "total": 0, "items": []})
            g["total"] += a["balance"]
            g["items"].append(a)
        assets = sum(a["balance"] for a in accts if a["type"] != "debt")
        debts = sum(a["balance"] for a in accts if a["type"] == "debt")
        month = today.strftime("%Y-%m")
        if accts:
            self.db.x("INSERT INTO networth_snapshots(month,assets,debts) VALUES(?,?,?) "
                      "ON CONFLICT(month) DO UPDATE SET assets=excluded.assets, debts=excluded.debts",
                      (month, assets, debts))
        history = self.db.q("SELECT month, assets-debts AS net FROM networth_snapshots ORDER BY month DESC LIMIT 12")
        history.reverse()
        prev = history[-2]["net"] if len(history) >= 2 else None
        order = list(TYPES)
        mix = [{"type": t, "label": TYPES[t], "total": groups[t]["total"]}
               for t in order if t in groups and t != "debt" and groups[t]["total"] > 0]
        return {
            "assets": assets, "debts": debts, "net": assets - debts,
            "change": (assets - debts - prev) if prev is not None else None,
            "groups": [groups[t] for t in order if t in groups],
            "mix": mix, "history": history, "types": TYPES,
        }
