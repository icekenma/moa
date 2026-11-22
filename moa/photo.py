"""사진편집(우석World): 올린 사진 보관, 보정값 저장, 미리보기·내보내기.

보정 계산은 PhotoGrader Pro 원본 엔진이 한다(photo_engine이 다리 노릇). 원본 엔진은 판매용이라 저장소에 넣지 않고
노트북의 data/photo_engine 폴더에만 둔다. numpy·OpenCV 같은 무거운 꾸러미도 기본 설치에 넣지 않고,
앱에서 '필요한 프로그램 설치'를 누르면 이 서버가 직접 설치한다(없어도 우석World의 다른 앱은 그대로 돈다).
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

PKGS = [("numpy", "numpy", True), ("cv2", "opencv-python-headless", True), ("PIL", "pillow", True), ("rawpy", "rawpy", False),
        ("exifread", "exifread", False), ("imageio_ffmpeg", "imageio-ffmpeg", False)]   # (불러올 이름, 설치 이름, 꼭 필요한가). 뒤의 셋: RAW 열기, RAW 촬영 정보, 슬라이드쇼 영상
BASE, FAST, TINY, THUMB = 1440, 720, 220, 360      # 긴 변 픽셀: 미리보기·끄는 동안·프리셋 맛보기·보관함
GEOM = ("rot", "flip", "angle", "crop")
OWN = GEOM + ("skin", "bg_blur", "bg_remove")       # 사진마다 다른 값: 프리셋에 넣지 않는다
ID = re.compile(r"[0-9a-f]{12}")
ENGINE_FILES = ("grade_engine.py", "style_match.py")


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
        root = data_dir or tempfile.mkdtemp(prefix="moa-photo-")
        self.dir = os.path.join(root, "photo")
        # PhotoGrader Pro 원본 엔진을 두는 곳. 판매용 코드라 저장소에는 없고 노트북의 이 폴더에만 있다.
        self.engine_dir = os.environ.get("MOA_PHOTO_ENGINE") or os.path.join(root, "photo_engine")
        os.makedirs(self.dir, exist_ok=True)
        self.model = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "u2netp.onnx")
        self.lock = threading.RLock()          # 보정은 한 번에 하나씩(메모리가 한꺼번에 치솟지 않게)
        self._mem = OrderedDict()
        self._setup = {"running": False, "log": [], "error": ""}
        db.x("""CREATE TABLE IF NOT EXISTS ph_photo(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, ext TEXT NOT NULL DEFAULT 'jpg', w INTEGER NOT NULL, h INTEGER NOT NULL,
            raw INTEGER NOT NULL DEFAULT 0, params TEXT NOT NULL DEFAULT '{}', created TEXT NOT NULL, edited TEXT NOT NULL DEFAULT '')""")
        if not any(r["name"] == "info" for r in db.q("PRAGMA table_info(ph_photo)")):
            db.x("ALTER TABLE ph_photo ADD COLUMN info TEXT NOT NULL DEFAULT '{}'")     # 촬영 정보(바디·렌즈·노출)

    # ---------- 보정 엔진(설치 여부) ----------
    def has_engine(self):
        return all(os.path.isfile(os.path.join(self.engine_dir, n)) for n in ENGINE_FILES)

    def status(self):
        missing = [pip for mod, pip, need in PKGS if need and not _has(mod)]
        from .slideshow import ffmpeg_exe
        eng = self.has_engine()
        return {"ready": not missing and eng, "missing": missing, "engine": eng, "raw": _has("rawpy"), "ai": not missing and os.path.exists(self.model),
                "video": bool(ffmpeg_exe()), "setup": dict(self._setup, log=self._setup["log"][-6:])}

    def engine(self):
        try:
            from . import photo_engine
        except ImportError:
            raise PhotoError("필요한 프로그램이 아직 설치되지 않았어요. 사진편집 첫 화면에서 '필요한 프로그램 설치'를 눌러 주세요")
        try:
            photo_engine.load(self.engine_dir)
        except ValueError as e:
            raise PhotoError(str(e))
        return photo_engine

    def install_engine(self, data):
        """앱에서 올린 엔진 zip을 엔진 폴더에 푼다(정해진 두 파일만, 내용이 맞을 때만)."""
        try:
            from . import photo_engine
        except ImportError:
            raise PhotoError("'필요한 프로그램 설치'를 먼저 눌러 주세요")
        try:
            photo_engine.install(self.engine_dir, data or b"")
        except ValueError as e:
            raise PhotoError(str(e))
        self._mem.clear()
        return self.status()

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

    def _out(self, r):
        return {"id": r["id"], "name": r["name"], "w": r["w"], "h": r["h"], "raw": bool(r["raw"]), "params": json.loads(r["params"] or "{}"),
                "edited": bool(r["edited"]), "ver": r["edited"] or r["created"], "created": r["created"]}

    def _orig(self, pid):
        """올린 원본 파일. v5.2에서 올린 사진은 줄여 둔 작업본만 있다."""
        ext = re.sub(r"[^a-z0-9]", "", str(self._row(pid)["ext"]).lower()) or "jpg"
        for path in (self._path(pid, "orig." + ext), self._path(pid, "work.jpg")):
            if os.path.exists(path):
                return path
        raise PhotoError("사진 파일이 없어요. 다시 올려 주세요")

    def _full(self, pid):
        """원본 크기 사진, EXIF, 촬영 정보. 크니까 기억해 두지 않는다."""
        E = self.engine()
        try:
            return E.read_file(self._orig(pid))
        except ValueError as e:
            raise PhotoError(str(e))

    def _img(self, pid, kind):
        """kind: base(미리보기) · fast(끄는 동안) · tiny(프리셋 맛보기). float32 RGB."""
        E = self.engine()
        key = (pid, kind)
        if key in self._mem:
            self._mem.move_to_end(key)
            return self._mem[key]
        if kind in ("fast", "tiny"):
            a = E.fit(self._img(pid, "base"), FAST if kind == "fast" else TINY)
        else:
            path = self._path(pid, "base.jpg")
            if not os.path.exists(path):
                raise PhotoError("사진 파일이 없어요. 다시 올려 주세요")
            try:
                a = E.read_file(path)[0]
            except ValueError as e:
                raise PhotoError(str(e))
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
        """원본 파일을 그대로 두고(내보낼 때 원본 화질로 보정), 화면용 작은 사진만 따로 만든다."""
        E = self.engine()
        if not data:
            raise PhotoError("빈 파일이에요")
        name = os.path.basename(str(name or "사진")).strip()[:80] or "사진"
        ext = re.sub(r"[^a-z0-9]", "", name.rsplit(".", 1)[-1].lower()) if "." in name else "jpg"
        if ext not in E.all_exts():
            raise PhotoError("열 수 없는 사진이에요 (JPG·PNG·TIF·RAW만 돼요)")
        pid = uuid.uuid4().hex[:12]
        orig = self._path(pid, "orig." + ext)
        with open(orig, "wb") as f:
            f.write(data)
        try:
            img, _, info = E.read_file(orig)
        except ValueError as e:
            os.remove(orig)
            raise PhotoError(str(e))
        h, w = img.shape[:2]
        base = E.fit(img, BASE)
        del img
        with open(self._path(pid, "base.jpg"), "wb") as f:
            f.write(E.jpeg(base, 93))
        with open(self._path(pid, "thumb.jpg"), "wb") as f:
            f.write(E.jpeg(E.fit(base, THUMB), 82))
        self.db.x("INSERT INTO ph_photo(id,name,ext,w,h,raw,params,created,info) VALUES(?,?,?,?,?,?,?,?,?)",
                  (pid, name, ext, w, h, int(ext in E.raw_exts()), "{}", datetime.now().isoformat(timespec="seconds"),
                   json.dumps(info, ensure_ascii=False, default=str)))
        return self._out(self._row(pid))

    def list(self):
        return [self._out(r) for r in self.db.q("SELECT * FROM ph_photo ORDER BY created DESC, id")]

    def user_presets(self):
        d = self.db.get_setting("photo_presets")
        return d if isinstance(d, dict) else {}

    def overview(self):
        st = self.status()
        out = {"status": st, "photos": self.list(), "presets": [], "ranges": {}, "hsl": [], "version": ""}
        if st["ready"]:
            try:
                E = self.engine()
            except PhotoError as e:              # 엔진 파일은 있는데 불러오지 못할 때(필요한 프로그램이 더 빠졌을 때)
                st["ready"], st["setup"]["error"] = False, str(e)
                return out
            out.update(presets=[{"id": "b:" + n, "name": n, "params": p, "mine": False} for n, p in E.presets()]
                       + [{"id": "u:" + n, "name": n, "params": p, "mine": True} for n, p in self.user_presets().items()],
                       ranges={k: list(v) for k, v in E.RANGES.items()}, hsl=[[k, n, h] for k, n, h in E.HSL_COLORS], version=str(E.G.APP_VERSION))
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
        r = self._row(pid)
        for kind in ("orig." + (re.sub(r"[^a-z0-9]", "", str(r["ext"]).lower()) or "jpg"), "work.jpg", "base.jpg", "thumb.jpg", "mask.png"):
            try:
                os.remove(self._path(pid, kind))
            except OSError:
                pass
        for key in [k for k in self._mem if k[0] == pid]:
            self._mem.pop(key, None)
        self.db.x("DELETE FROM ph_photo WHERE id=?", (pid,))

    # ---------- 프리셋(내가 저장한 것) ----------
    def save_preset(self, name, params, old=None):
        """현재 보정값을 이름 붙여 저장한다(자르기·돌리기·AI 값은 빼고). old를 주면 이름 바꾸기."""
        E = self.engine()
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:30]
        if not name:
            raise PhotoError("프리셋 이름을 적어 주세요")
        if name in dict(E.presets()):
            raise PhotoError("기본 프리셋과 같은 이름은 쓸 수 없어요")
        d = self.user_presets()
        if old is not None:
            if old not in d:
                raise PhotoError("없는 프리셋이에요")
            if name != old and name in d:
                raise PhotoError("같은 이름의 프리셋이 이미 있어요")
            d = {(name if k == old else k): v for k, v in d.items()}
        else:
            if len(d) >= 60 and name not in d:
                raise PhotoError("프리셋은 60개까지 저장할 수 있어요")
            q, base = E.clean(params), E.clean({})
            d[name] = {k: v for k, v in q.items() if v != base[k] and k not in OWN}
        self.db.set_setting("photo_presets", d)
        return self.overview()["presets"]

    def delete_preset(self, name):
        d = self.user_presets()
        if name not in d:
            raise PhotoError("없는 프리셋이에요")
        d.pop(name)
        self.db.set_setting("photo_presets", d)
        return self.overview()["presets"]

    # ---------- 보정 ----------
    def _render(self, pid, params, kind, crop=True, orig=False, fast=True, src=None):
        """fast: 화면용(원본 프로그램의 미리보기와 같은 계산). 내보낼 때만 fast=False."""
        E = self.engine()
        p = E.clean(params)
        with self.lock:
            img = src if src is not None else self._img(pid, kind)
            return E.render(img, p, fast=fast, crop=crop, plain=orig, mask_fn=(lambda: self._mask(pid)) if (p["bg_blur"] or p["bg_remove"]) else None), p

    def render(self, pid, params, size="base", crop=True, orig=False):
        """화면에 보일 사진(JPG)과 밝기 분포."""
        self._row(pid)
        kind = size if size in ("fast", "tiny") else "base"
        (a, _), _ = self._render(pid, params, kind, crop=crop, orig=orig)
        E = self.engine()
        return E.jpeg(a, 80 if kind == "fast" else 87), (E.histogram(a) if kind != "tiny" and not orig else b"")

    def save(self, pid, params):
        """보정값을 저장하고 보관함의 작은 사진도 보정한 모습으로 바꾼다."""
        E = self.engine()
        self._row(pid)
        p, base = E.clean(params), E.clean({})
        slim = {k: v for k, v in p.items() if v != base[k]}
        self.db.x("UPDATE ph_photo SET params=?, edited=? WHERE id=?",
                  (json.dumps(slim, ensure_ascii=False), datetime.now().isoformat(timespec="seconds") if slim else "", pid))
        (a, _), _ = self._render(pid, p, "fast")
        with open(self._path(pid, "thumb.jpg"), "wb") as f:
            f.write(E.jpeg(E.fit(a, THUMB), 82))
        return self._out(self._row(pid))

    def _shape(self, pid, params):
        """돌리고 자른 뒤의 미리보기 사진(보정 전). 자동 보정·스포이드·추천이 이 사진을 본다."""
        (a, _), _ = self._render(pid, params, "base", orig=True)
        return a

    def auto(self, pid, params=None):
        self._row(pid)
        E = self.engine()
        return E.from_engine(E.G.auto_adjust(self._shape(pid, params)))

    def wb_pick(self, pid, params, u, v):
        """스포이드: 누른 곳이 무채색이 되게 하는 색 균형."""
        self._row(pid)
        E = self.engine()
        try:
            u, v = min(1.0, max(0.0, float(u))), min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            raise PhotoError("사진 위를 눌러 주세요")
        a = self._shape(pid, params)
        h, w = a.shape[:2]
        x, y = int(u * (w - 1)), int(v * (h - 1))
        px = a[max(0, y - 2):y + 3, max(0, x - 2):x + 3].reshape(-1, 3).mean(axis=0)
        return [round(float(g), 4) for g in E.G.picker_wb_gains(px)]

    def suggest(self, pid):
        """이 사진에 어울리는 기본 프리셋(최대 3개)과 이유."""
        self._row(pid)
        E = self.engine()
        try:
            return [{"id": "b:" + it["name"], "why": it["why"]} for it in E.S.suggest_presets(self._img(pid, "base"))]
        except Exception:
            return []

    def match(self, pid, data):
        """참고 사진의 색감·톤을 따라가는 보정값을 제안한다."""
        self._row(pid)
        E = self.engine()
        try:
            ref = E.decode_small(data or b"")
        except Exception:
            raise PhotoError("참고 사진을 열지 못했어요 (JPG·PNG만 돼요)")
        with self.lock:
            r = E.S.fit_style(ref, [self._img(pid, "base")])
        return {"params": E.from_engine(r["params"]), "before": int(r["before"]), "after": int(r["after"]), "traits": list(r["traits"]), "name": str(r["name"])}

    def export(self, pid, params=None, opt=None):
        """원본 크기로 보정한 파일. opt: fmt(JPG·PNG) quality resize size_cap wm_text wm_pos wm_op shot_info.
        배경 제거를 켰으면 투명 PNG."""
        E = self.engine()
        r = self._row(pid)
        o = dict(opt or {})
        p = params if params is not None else json.loads(r["params"] or "{}")
        img, exif, info = self._full(pid)
        if not info:
            try:
                info = json.loads(r["info"] or "{}")
            except ValueError:
                info = {}
        (a, mask), q = self._render(pid, p, "work", fast=False, src=img)
        del img
        stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", r["name"]) or "photo"
        try:
            o["quality"] = min(100, max(70, int(o.get("quality") or 95)))
            o["resize"] = min(10000, max(0, int(o.get("resize") or 0)))
            o["wm_op"] = min(100, max(10, int(o.get("wm_op") or 60)))
        except (TypeError, ValueError):
            raise PhotoError("내보내기 설정의 숫자를 확인해 주세요")
        if q["bg_remove"] and mask is not None:
            side = o["resize"]
            if side and side < max(a.shape[:2]):
                import cv2
                a = E.fit(a, max(320, side))
                mask = cv2.resize(mask, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
            return E.png_alpha(a, mask), "image/png", f"{stem}_edited.png"
        png = o.get("fmt") == "PNG"
        fd, tmp = tempfile.mkstemp(suffix=".png" if png else ".jpg", dir=self.dir)
        os.close(fd)
        try:
            E.write_file(a, tmp, exif=exif, info=info, opt=o)
            with open(tmp, "rb") as f:
                data = f.read()
        except PhotoError:
            raise
        except Exception as e:
            raise PhotoError(f"사진을 저장하지 못했어요 ({type(e).__name__})")
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return data, "image/png" if png else "image/jpeg", f"{stem}_edited.{'png' if png else 'jpg'}"

    def export_zip(self, ids, opt=None):
        buf, seen = io.BytesIO(), set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for pid in (ids or [])[:200]:
                data, _, name = self.export(pid, None, opt)
                base, n = name, 2
                while name in seen:
                    name = re.sub(r"(\.\w+)$", rf"_{n}\1", base)
                    n += 1
                seen.add(name)
                z.writestr(name, data)
        if not seen:
            raise PhotoError("내보낼 사진을 골라 주세요")
        return buf.getvalue()
