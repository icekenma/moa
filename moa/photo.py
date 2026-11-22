"""사진편집(우석World): 올린 사진 보관, 보정값 저장, 미리보기·내보내기.

보정 계산은 photo_engine(numpy·OpenCV)이 한다. 두 꾸러미는 무거워서 기본 설치에 넣지 않고,
앱에서 '보정 엔진 설치'를 누르면 이 서버가 직접 설치한다(없어도 우석World의 다른 앱은 그대로 돈다).
사진 파일은 저장소가 아니라 data/photo 아래에만 둔다.
"""
import importlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from collections import OrderedDict
from datetime import datetime

PKGS = [("numpy", "numpy", True), ("cv2", "opencv-python-headless", True), ("rawpy", "rawpy", False),
        ("imageio_ffmpeg", "imageio-ffmpeg", False)]   # (불러올 이름, 설치 이름, 꼭 필요한가). 뒤의 둘: RAW 열기, 슬라이드쇼 영상
WORK, BASE, FAST, TINY, THUMB = 4096, 1440, 720, 220, 360      # 긴 변 픽셀: 작업본·미리보기·끄는 동안·프리셋 맛보기·보관함
GEOM = ("rot", "flip", "angle", "crop")
ID = re.compile(r"[0-9a-f]{12}")


class PhotoError(ValueError):
    pass


def _has(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


class Photo:
    def __init__(self, db, data_dir=None):
        self.db = db
        self.dir = os.path.join(data_dir or tempfile.mkdtemp(prefix="moa-photo-"), "photo")
        os.makedirs(self.dir, exist_ok=True)
        self.model = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "u2netp.onnx")
        self.lock = threading.RLock()          # 보정은 한 번에 하나씩(메모리가 한꺼번에 치솟지 않게)
        self._mem = OrderedDict()
        self._setup = {"running": False, "log": [], "error": ""}
        db.x("""CREATE TABLE IF NOT EXISTS ph_photo(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, ext TEXT NOT NULL DEFAULT 'jpg', w INTEGER NOT NULL, h INTEGER NOT NULL,
            raw INTEGER NOT NULL DEFAULT 0, params TEXT NOT NULL DEFAULT '{}', created TEXT NOT NULL, edited TEXT NOT NULL DEFAULT '')""")

    # ---------- 보정 엔진(설치 여부) ----------
    def status(self):
        missing = [pip for mod, pip, need in PKGS if need and not _has(mod)]
        from .slideshow import ffmpeg_exe
        return {"ready": not missing, "missing": missing, "raw": _has("rawpy"), "ai": not missing and os.path.exists(self.model), "video": bool(ffmpeg_exe()),
                "setup": dict(self._setup, log=self._setup["log"][-6:]), "max_side": WORK}

    def engine(self):
        try:
            from . import photo_engine
            return photo_engine
        except ImportError:
            raise PhotoError("보정 엔진이 아직 설치되지 않았어요. 사진편집 첫 화면에서 '보정 엔진 설치'를 눌러 주세요")

    def setup(self, run=None):
        """빠진 꾸러미를 하나씩 설치한다(하나가 안 돼도 나머지는 계속)."""
        with self.lock:
            if self._setup["running"]:
                return self.status()
            todo = [(pip, need) for mod, pip, need in PKGS if not _has(mod)]
            self._setup = {"running": bool(todo), "log": [], "error": ""}
        if not todo:
            return self.status()
        run = run or (lambda pip: subprocess.run([sys.executable, "-m", "pip", "install", "-q", pip], capture_output=True, text=True, timeout=900))

        def work():
            for pip, need in todo:
                self._setup["log"].append(f"{pip} 설치 중")
                try:
                    r = run(pip)
                    ok = r.returncode == 0
                except Exception as e:      # 인터넷이 안 되거나 pip이 없을 때
                    ok, r = False, None
                    self._setup["log"].append(str(e)[:120])
                self._setup["log"].append(f"{pip} {'설치 완료' if ok else '설치 실패'}")
                if not ok and need:
                    self._setup["error"] = f"{pip} 설치에 실패했어요. 노트북의 인터넷 연결을 확인하고 다시 눌러 주세요"
            importlib.invalidate_caches()
            self._setup["running"] = False

        threading.Thread(target=work, daemon=True).start()
        return self.status()

    # ---------- 파일·기억 ----------
    def _path(self, pid, kind):
        if not ID.fullmatch(str(pid)):
            raise PhotoError("없는 사진이에요")
        return os.path.join(self.dir, f"{pid}.{kind}")

    def _row(self, pid):
        r = self.db.one("SELECT * FROM ph_photo WHERE id=?", (str(pid),))
        if not r:
            raise PhotoError("없는 사진이에요")
        return r

    def _out(self, r, E=None):
        p = json.loads(r["params"] or "{}")
        return {"id": r["id"], "name": r["name"], "w": r["w"], "h": r["h"], "raw": bool(r["raw"]), "params": p,
                "edited": bool(r["edited"]), "ver": r["edited"] or r["created"], "created": r["created"]}

    def _img(self, pid, kind):
        """kind: work(내보내기) · base(미리보기) · fast(끄는 동안) · tiny(프리셋 맛보기). float32 RGB."""
        E = self.engine()
        key = (pid, kind)
        if key in self._mem:
            self._mem.move_to_end(key)
            return self._mem[key]
        if kind in ("fast", "tiny"):
            src = self._img(pid, "base")
            a = E.to_float(E.fit((src * 255 + 0.5).astype("uint8"), FAST if kind == "fast" else TINY))
        else:
            path = self._path(pid, "work.jpg" if kind == "work" else "base.jpg")
            if not os.path.exists(path):
                raise PhotoError("사진 파일이 없어요. 다시 올려 주세요")
            with open(path, "rb") as f:
                a = E.to_float(E.decode(f.read()))
        if kind != "work":                      # 큰 작업본은 기억해 두지 않는다
            self._mem[key] = a
            while len(self._mem) > 6:
                self._mem.popitem(last=False)
        return a

    def _mask(self, pid):
        """사람(주제) 영역. 한 번 찾으면 파일로 남겨 다시 쓴다."""
        E = self.engine()
        import cv2
        import numpy as np
        key = (pid, "mask")
        if key in self._mem:
            return self._mem[key]
        path = self._path(pid, "mask.png")
        m = None
        if os.path.exists(path):
            g = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)
            m = g.astype(np.float32) / 255.0 if g is not None else None
        if m is None:
            m = E.subject_mask(self._img(pid, "base"), self.model)
            if m is None:
                raise PhotoError("이 노트북에서는 AI 배경 기능을 쓸 수 없어요 (모델을 불러오지 못했어요)")
            ok, buf = cv2.imencode(".png", (m * 255 + 0.5).astype(np.uint8))
            if ok:
                buf.tofile(path)
        self._mem[key] = m
        return m

    # ---------- 올리기·목록 ----------
    def add(self, name, data):
        E = self.engine()
        if not data:
            raise PhotoError("빈 파일이에요")
        name = os.path.basename(str(name or "사진")).strip()[:80] or "사진"
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else "jpg"
        try:
            rgb = E.decode(data, ext)
        except ValueError as e:
            raise PhotoError(str(e))
        pid = uuid.uuid4().hex[:12]
        work = E.fit(rgb, WORK)
        h, w = work.shape[:2]
        with open(self._path(pid, "work.jpg"), "wb") as f:
            f.write(E.jpeg(E.to_float(work), 95))
        with open(self._path(pid, "base.jpg"), "wb") as f:
            f.write(E.jpeg(E.to_float(E.fit(work, BASE)), 92))
        with open(self._path(pid, "thumb.jpg"), "wb") as f:
            f.write(E.jpeg(E.to_float(E.fit(work, THUMB)), 82))
        self.db.x("INSERT INTO ph_photo(id,name,ext,w,h,raw,params,created) VALUES(?,?,?,?,?,?,?,?)",
                  (pid, name, ext, w, h, int(ext in E.RAW_EXT), "{}", datetime.now().isoformat(timespec="seconds")))
        return self._out(self._row(pid))

    def list(self):
        return [self._out(r) for r in self.db.q("SELECT * FROM ph_photo ORDER BY created DESC, id")]

    def overview(self):
        st = self.status()
        out = {"status": st, "photos": self.list(), "presets": [], "ranges": {}, "hsl": []}
        if st["ready"]:
            E = self.engine()
            out.update(presets=[{"id": i, "name": n, "params": p} for i, n, p in E.PRESETS],
                       ranges={k: list(v) for k, v in E.RANGES.items()}, hsl=[[k, n, h] for k, n, h in E.HSL_COLORS])
        return out

    def brief(self):
        rows = self.db.q("SELECT edited FROM ph_photo")
        return {"count": len(rows), "edited": sum(1 for r in rows if r["edited"])}

    def thumb(self, pid):
        path = self._path(pid, "thumb.jpg")
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def delete(self, pid):
        self._row(pid)
        for kind in ("work.jpg", "base.jpg", "thumb.jpg", "mask.png"):
            try:
                os.remove(self._path(pid, kind))
            except OSError:
                pass
        for key in [k for k in self._mem if k[0] == pid]:
            self._mem.pop(key, None)
        self.db.x("DELETE FROM ph_photo WHERE id=?", (pid,))

    # ---------- 보정 ----------
    def _render(self, pid, params, kind, crop=True, orig=False, fast=False):
        E = self.engine()
        p = E.clean(params)
        if orig:                                 # 비교용 원본: 돌리기·자르기만 같게
            p = dict(E.clean({k: p[k] for k in GEOM}))
        if p["nr_quality"] and kind != "work":
            fast = True                          # 고품질 노이즈 제거는 느려서 내보낼 때만 쓴다
        with self.lock:
            img = self._img(pid, kind)
            return E.render(img, p, fast=fast, crop=crop, mask_fn=(lambda: self._mask(pid)) if (p["bg_blur"] or p["bg_remove"]) else None), p

    def render(self, pid, params, size="base", crop=True, orig=False):
        self._row(pid)
        kind = size if size in ("fast", "tiny") else "base"
        (a, _), _ = self._render(pid, params, kind, crop=crop, orig=orig, fast=kind != "base")
        return self.engine().jpeg(a, 80 if kind == "fast" else 86)

    def save(self, pid, params):
        """보정값을 저장하고 보관함의 작은 사진도 보정한 모습으로 바꾼다."""
        E = self.engine()
        self._row(pid)
        p = E.clean(params)
        neutral = E.is_neutral(p)
        slim = {k: v for k, v in p.items() if v != E.clean({}).get(k)}
        self.db.x("UPDATE ph_photo SET params=?, edited=? WHERE id=?",
                  (json.dumps(slim, ensure_ascii=False), "" if neutral else datetime.now().isoformat(timespec="seconds"), pid))
        (a, _), _ = self._render(pid, p, "fast", fast=True)
        with open(self._path(pid, "thumb.jpg"), "wb") as f:
            f.write(E.jpeg(E.to_float(E.fit((a * 255 + 0.5).astype("uint8"), THUMB)), 82))
        return self._out(self._row(pid))

    def auto(self, pid):
        self._row(pid)
        with self.lock:
            return self.engine().auto(self._img(pid, "base"))

    def export(self, pid, params=None, long_side=0, quality=92):
        """보정한 사진 파일. 배경 제거를 켰으면 투명 PNG, 아니면 JPG."""
        E = self.engine()
        r = self._row(pid)
        p = params if params is not None else json.loads(r["params"] or "{}")
        (a, mask), q = self._render(pid, p, "work")
        side = int(long_side or 0)
        if side and side < max(a.shape[:2]):
            a = E.to_float(E.fit((a * 255 + 0.5).astype("uint8"), max(320, side)))
            if mask is not None:
                import cv2
                mask = cv2.resize(mask, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
        stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", r["name"]) or "photo"
        if q["bg_remove"] and mask is not None:
            return E.png_alpha(a, mask), "image/png", f"{stem}_edit.png"
        return E.jpeg(a, min(100, max(60, int(quality or 92)))), "image/jpeg", f"{stem}_edit.jpg"

    def export_zip(self, ids, long_side=0, quality=92):
        buf, seen = io.BytesIO(), set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for pid in (ids or [])[:200]:
                data, _, name = self.export(pid, None, long_side, quality)
                base, n = name, 2
                while name in seen:
                    name = re.sub(r"(\.\w+)$", rf"_{n}\1", base)
                    n += 1
                seen.add(name)
                z.writestr(name, data)
        if not seen:
            raise PhotoError("내보낼 사진을 골라 주세요")
        return buf.getvalue()
