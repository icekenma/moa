"""사진편집(우석World) 보정 다리: 화면에서 온 보정값을 PhotoGrader Pro 원본 엔진에 넘긴다.

색·톤·디테일 보정은 전부 원본 엔진(grade_engine.py, style_match.py)이 한다. 그 두 파일은 판매용이라
이 저장소에 넣지 않고, 노트북의 data/photo_engine 폴더에만 둔다(load가 거기서 불러온다).
여기 있는 것은 그 앞뒤 일뿐이다: 값 다듬기, 돌리기·수평·자르기, 피부·배경(AI) 덧보정, 파일 읽고 쓰기.
이 모듈은 numpy·OpenCV가 있어야 불러올 수 있어서, 쓰는 쪽(photo.py)에서 필요할 때만 불러온다.
"""
import hashlib
import importlib.util
import io
import os
import sys

import cv2
import numpy as np

# 보정값: (기본, 최소, 최대). 이름과 범위는 원본 화면(ui.html)의 슬라이더와 같다. 끝의 셋은 여기서 덧붙인 것.
RANGES = {
    "exposure": (0.0, -3.0, 3.0), "contrast": (0, -100, 100), "highlights": (0, -100, 100), "shadows": (0, -100, 100),
    "whites": (0, -100, 100), "blacks": (0, -100, 100),
    "temp": (0, -100, 100), "tint": (0, -100, 100), "vibrance": (0, -100, 100), "saturation": (0, -100, 100),
    "texture": (0, -100, 100), "clarity": (0, -100, 100), "sharpen_amt": (0, 0, 150), "sharpen_rad": (1.0, 0.5, 3.0),
    "noise_red": (0, 0, 100), "noise_detail": (50, 0, 100), "noise_chroma": (0, 0, 100), "noise_chroma_rad": (50, 0, 100),
    "fade": (0, 0, 100), "grain": (0, 0, 100), "vignette": (0, -100, 100),
    "split_sh_hue": (210, 0, 360), "split_sh_amt": (0, 0, 100), "split_hi_hue": (45, 0, 360), "split_hi_amt": (0, 0, 100),
    "angle": (0.0, -15.0, 15.0), "skin": (0, 0, 100), "bg_blur": (0, 0, 100),
}
EXTRA = ("angle", "skin", "bg_blur")                      # 원본 엔진에 없는 값(넘기지 않는다)
FLAGS = ("bw", "flip", "noise_hq", "bg_remove")
HSL_COLORS = [("red", "빨강", 0), ("orange", "주황", 30), ("yellow", "노랑", 60), ("green", "초록", 120),
              ("aqua", "아쿠아", 180), ("blue", "파랑", 240), ("purple", "보라", 285), ("magenta", "자홍", 325)]
LUMA = np.array([0.299, 0.587, 0.114], np.float32)
# 앱에서 올리는 엔진 파일은 이 지문과 같을 때만 받는다(아무 코드나 서버에 올려 돌리지 못하게). 파일 내용은 여기 없다.
ENGINE_FILES = {"grade_engine.py": "5639b3ae463ce759fc22fc18d1060292f12229d352cfdecb024fe2e37ad5021f",
                "style_match.py": "ba4e78f2711aa7ba41717f9ac24e649f722a5398ba747ac9991a603277b7992e"}
G = None            # 원본 엔진(grade_engine)
S = None            # 참고 사진 맞추기(style_match)
_DIR = ""


class EngineMissing(ValueError):
    pass


def has_engine(folder):
    return all(os.path.isfile(os.path.join(folder, n)) for n in ENGINE_FILES)


def load(folder):
    """노트북에 둔 원본 엔진을 불러온다. 같은 폴더면 한 번만."""
    global G, S, _DIR
    if G is not None and _DIR == folder:
        return G
    if not has_engine(folder):
        raise EngineMissing("PhotoGrader 엔진 파일이 아직 없어요. 사진편집 첫 화면에서 엔진 파일을 올려 주세요")
    mods = {}
    try:
        for name in ("grade_engine", "style_match"):      # style_match가 grade_engine을 이름으로 찾으므로 먼저 등록해 둔다
            spec = importlib.util.spec_from_file_location(name, os.path.join(folder, name + ".py"))
            mods[name] = sys.modules[name] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mods[name])
    except ImportError as e:
        for name in mods:
            sys.modules.pop(name, None)
        raise EngineMissing(f"엔진에 필요한 프로그램이 빠졌어요 ({e.name or e}). '필요한 프로그램 설치'를 눌러 주세요")
    g, s = mods["grade_engine"], mods["style_match"]
    G, S, _DIR = g, s, folder
    return G


def install(folder, data):
    """올린 zip에서 엔진 파일 둘만 꺼내 둔다. 지문이 다르면 받지 않는다."""
    import zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("zip 파일이 아니에요")
    found = {}
    for info in z.infolist():
        base = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
        if base in ENGINE_FILES and base not in found and info.file_size < 2_000_000:
            raw = z.read(info)
            if hashlib.sha256(raw).hexdigest() == ENGINE_FILES[base]:
                found[base] = raw
    lack = [n for n in ENGINE_FILES if n not in found]
    if lack:
        raise ValueError(f"이 zip에는 맞는 엔진 파일이 없어요 ({', '.join(lack)}). 받은 엔진 zip을 그대로 올려 주세요")
    os.makedirs(folder, exist_ok=True)
    for name, raw in found.items():
        with open(os.path.join(folder, name), "wb") as f:
            f.write(raw)
    global G
    G = None


def clean(p):
    """화면에서 온 보정값을 범위 안으로 다듬는다. 모르는 값은 버린다."""
    p = p if isinstance(p, dict) else {}
    out = {}
    for k, (d, lo, hi) in RANGES.items():
        try:
            v = float(p.get(k, d))
        except (TypeError, ValueError):
            v = d
        out[k] = min(hi, max(lo, v)) if v == v else d
    for k in FLAGS:
        out[k] = bool(p.get(k))
    try:
        out["rot"] = int(p.get("rot") or 0) % 360 // 90 * 90
    except (TypeError, ValueError):
        out["rot"] = 0
    hsl = p.get("hsl") if isinstance(p.get("hsl"), dict) else {}
    out["hsl"] = {}
    for key, _, _ in HSL_COLORS:
        v = hsl.get(key)
        if isinstance(v, (list, tuple)) and len(v) == 3:
            try:
                t = [min(100.0, max(-100.0, float(x))) for x in v]
            except (TypeError, ValueError):
                continue
            if any(t):
                out["hsl"][key] = t
    out["wb_gains"] = None                                # 스포이드·자동 보정이 넣는 색 균형(빨강·초록·파랑 배율)
    g = p.get("wb_gains")
    if isinstance(g, (list, tuple)) and len(g) == 3:
        try:
            out["wb_gains"] = [round(min(2.0, max(0.5, float(x))), 4) for x in g]
        except (TypeError, ValueError):
            pass
    c = p.get("crop")
    out["crop"] = None
    if isinstance(c, (list, tuple)) and len(c) == 4:
        try:
            x, y, w, h = [float(v) for v in c]
            x, y = min(0.98, max(0.0, x)), min(0.98, max(0.0, y))
            w, h = min(1.0 - x, max(0.02, w)), min(1.0 - y, max(0.02, h))
            if w < 0.999 or h < 0.999:
                out["crop"] = [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]
        except (TypeError, ValueError):
            pass
    return out


def is_neutral(p):
    return clean(p) == clean({})


def to_edit(p):
    """다듬은 보정값 → 원본 엔진의 EditParams. 돌리기·자르기는 여기서 먼저 하므로 넘기지 않는다."""
    d = {k: v for k, v in p.items() if k in RANGES and k not in EXTRA}
    d["bw"], d["noise_hq"], d["wb_gains"] = p["bw"], p["noise_hq"], p["wb_gains"]
    for i, name in enumerate(("hsl_hue", "hsl_sat", "hsl_lum")):
        d[name] = [float((p["hsl"].get(key) or (0, 0, 0))[i]) for key, _, _ in HSL_COLORS]
    return G.EditParams.from_dict(d)


def from_engine(d):
    """원본 엔진의 보정값(프리셋·자동·참고 사진 결과) → 화면이 쓰는 모양."""
    out, hsl = {}, {}
    for k, v in (d or {}).items():
        if k in RANGES and k not in EXTRA and isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = v
        elif k in ("bw", "noise_hq") and v:
            out[k] = True
        elif k == "wb_gains" and v is not None:
            out[k] = [float(x) for x in v]
    for i, name in enumerate(("hsl_hue", "hsl_sat", "hsl_lum")):
        for (key, _, _), v in zip(HSL_COLORS, (d or {}).get(name) or []):
            if v:
                hsl.setdefault(key, [0, 0, 0])[i] = float(v)
    if hsl:
        out["hsl"] = hsl
    q = clean(out)
    base = clean({})
    return {k: v for k, v in q.items() if v != base[k]}


def presets():
    return [(name, from_engine(p)) for name, p in G.BUILTIN_PRESETS.items()]


def _blur(x, sigma):
    """가우시안 흐림. 넓게 흐릴 때는 줄여서 흐린 뒤 다시 키운다(결과는 거의 같고 훨씬 빠르다)."""
    sigma = max(0.3, float(sigma))
    if sigma < 3.0:
        return cv2.GaussianBlur(x, (0, 0), sigma)
    f = int(min(8, max(2, sigma // 1.5)))
    h, w = x.shape[:2]
    small = cv2.resize(x, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / f)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _f32(a):
    return a if a.dtype == np.float32 else a.astype(np.float32)


# ---------- 돌리기·뒤집기·수평·자르기 ----------
def geometry(img, p, crop=True):
    a = img
    k = (p["rot"] // 90) % 4
    if k:
        a = np.ascontiguousarray(np.rot90(a, 4 - k))        # 시계 방향
    if p["flip"]:
        a = np.ascontiguousarray(a[:, ::-1])
    ang = p["angle"]
    if abs(ang) > 0.05:
        h, w = a.shape[:2]
        m = cv2.getRotationMatrix2D((w / 2, h / 2), -ang, 1.0)
        a = cv2.warpAffine(a, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        # 빈 모서리가 안 보이게 가장 큰 안쪽 사각형만 남긴다
        t = abs(np.radians(ang))
        s, c = np.sin(t), np.cos(t)
        long_, short = max(w, h), min(w, h)
        if short <= 2 * s * c * long_:
            x = 0.5 * short
            wr, hr = (x / s, x / c) if w >= h else (x / c, x / s)
        else:
            c2 = c * c - s * s
            wr, hr = (w * c - h * s) / c2, (h * c - w * s) / c2
        wr, hr = int(max(8, min(w, wr))), int(max(8, min(h, hr)))
        x0, y0 = (w - wr) // 2, (h - hr) // 2
        a = a[y0:y0 + hr, x0:x0 + wr]
    if crop and p["crop"]:
        h, w = a.shape[:2]
        x, y, cw, ch = p["crop"]
        x0, y0 = int(round(x * w)), int(round(y * h))
        x1, y1 = max(x0 + 4, int(round((x + cw) * w))), max(y0 + 4, int(round((y + ch) * h)))
        a = a[y0:min(h, y1), x0:min(w, x1)]
    return np.ascontiguousarray(a)


# ---------- 얼굴·피부·사람(배경) 찾기 ----------
_FACE = None
_NET = {}
_GRAIN = {}


def faces(img):
    global _FACE
    if _FACE is None:
        path = os.path.join(getattr(cv2, "data", None).haarcascades if getattr(cv2, "data", None) else "", "haarcascade_frontalface_default.xml")
        _FACE = cv2.CascadeClassifier(path) if os.path.exists(path) else False
    if not _FACE:
        return []
    h, w = img.shape[:2]
    k = 480.0 / max(h, w)
    g = cv2.cvtColor((cv2.resize(img, (max(1, int(w * k)), max(1, int(h * k)))) * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    return [(x / k, y / k, fw / k, fh / k) for x, y, fw, fh in _FACE.detectMultiScale(g, 1.15, 5, minSize=(28, 28))]


def skin_mask(img):
    """피부색 영역(0~1). 얼굴을 찾으면 얼굴 둘레로 좁혀서 벽·나무 같은 비슷한 색을 건드리지 않는다."""
    H, W = img.shape[:2]
    k = min(1.0, 480.0 / max(H, W))
    sm = cv2.resize(img, (max(1, int(W * k)), max(1, int(H * k))), interpolation=cv2.INTER_AREA) if k < 1 else img
    ycc = cv2.cvtColor((np.clip(sm, 0, 1) * 255).astype(np.uint8), cv2.COLOR_RGB2YCrCb).astype(np.float32)
    cr, cb = ycc[..., 1], ycc[..., 2]
    m = np.clip(1 - np.abs(cr - 153) / 22, 0, 1) * np.clip(1 - np.abs(cb - 102) / 26, 0, 1)
    m = np.clip(m * 2.2, 0, 1)
    h, w = sm.shape[:2]
    fs = faces(sm)
    if fs:
        area = np.zeros((h, w), np.float32)
        for x, y, fw, fh in fs:
            cv2.ellipse(area, (int(x + fw / 2), int(y + fh * 0.55)), (int(fw * 1.25), int(fh * 1.9)), 0, 0, 360, 1.0, -1)
        m *= _blur(area, max(h, w) * 0.02)
    else:
        m *= 0.6
    m = _blur(m, max(h, w) * 0.006)
    return cv2.resize(m, (W, H), interpolation=cv2.INTER_LINEAR) if k < 1 else m


def subject_mask(img, model_path):
    """사람·주제 영역(0~1). U2-Net(작은 판)으로 찾는다. 모델이나 OpenCV dnn이 없으면 None."""
    if not model_path or not os.path.exists(model_path):
        return None
    try:
        if model_path not in _NET:
            _NET[model_path] = cv2.dnn.readNetFromONNX(model_path)
        net = _NET[model_path]
        a = cv2.resize(img, (320, 320), interpolation=cv2.INTER_AREA).astype(np.float32)
        a = (a - np.float32([0.485, 0.456, 0.406])) / np.float32([0.229, 0.224, 0.225])
        net.setInput(np.ascontiguousarray(a.transpose(2, 0, 1)[None]))
        o = net.forward()[0, 0]
        o = (o - o.min()) / max(1e-6, float(o.max() - o.min()))
        h, w = img.shape[:2]
        m = cv2.resize(o, (w, h), interpolation=cv2.INTER_CUBIC)
        return np.clip((m - 0.25) / 0.5, 0, 1).astype(np.float32)
    except Exception:
        return None


# ---------- 보정 ----------
def extras(a, p, mask=None):
    """원본 엔진 뒤에 덧붙이는 것: 피부 보정, 배경 흐림·제거."""
    h, w = a.shape[:2]
    size = float(max(h, w))
    if p["skin"]:
        s = p["skin"] / 100.0
        f = max(1, int(round(size / 480.0)))                # 고운 결만 고르면 되니 줄여서 처리한다
        small = cv2.resize(a, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
        small = cv2.bilateralFilter(small, 9, 0.07 + 0.07 * s, 3 + 4 * s)
        sm = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
        m = (skin_mask(a) * (0.85 * s))[..., None]
        a = a * (1 - m) + sm * m
    if mask is not None and (p["bg_blur"] or p["bg_remove"]):
        m = mask[..., None]
        if p["bg_remove"]:
            a = a * m + (1 - m)
        else:
            a = a * m + _blur(a, 1 + p["bg_blur"] / 100.0 * size * 0.022) * (1 - m)
    return _f32(a)


def render(img, p, fast=False, crop=True, mask_fn=None, plain=False):
    """돌리기·자르기 → 원본 엔진 보정 → 덧보정. fast: 화면용(원본 엔진의 preview). plain: 보정 없이 모양만."""
    g = _f32(geometry(img, p, crop=crop))
    if plain:
        return g, None
    mask = None
    if (p["bg_blur"] or p["bg_remove"]) and mask_fn:
        m = mask_fn()
        if m is not None:
            if m.shape[:2] != img.shape[:2]:
                m = cv2.resize(m, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)
            mask = geometry(m, p, crop=crop)
    out = np.clip(G.apply_edit(g, to_edit(p), preview=fast, skip_geometry=True), 0, 1)
    return extras(out, p, mask), mask


def histogram(a, bins=64):
    """빨강·초록·파랑 밝기 분포(각 64칸, 0~255로 맞춤)."""
    k = max(1, max(a.shape[:2]) // 360)
    h = G.histogram_rgb(a[::k, ::k], bins)
    return (np.clip(h, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes()


# ---------- 파일 ----------
def read_file(path, half=False):
    """원본 엔진으로 사진을 연다(RAW 현상 포함) → float RGB, EXIF, 촬영 정보. 세워 찍은 사진은 바로 세운다."""
    try:
        img, exif = G.load_image(path, half=half)
    except Exception as e:
        try:
            img, exif = G._load_raw_preview(path, e)     # RAW 현상이 안 되면 안에 든 미리보기로
        except Exception:
            raise ValueError(f"열 수 없는 사진이에요 ({type(e).__name__})")
    try:
        info = G.read_shot_info(path) or {}
    except Exception:
        info = {}
    if exif:
        try:
            from PIL import Image
            ex = Image.Exif()
            ex.load(exif)
            o = ex.get(274, 1)
            op = {2: lambda a: a[:, ::-1], 3: lambda a: a[::-1, ::-1], 4: lambda a: a[::-1], 5: lambda a: a.transpose(1, 0, 2),
                  6: lambda a: np.rot90(a, 3), 7: lambda a: np.rot90(a, 2).transpose(1, 0, 2), 8: lambda a: np.rot90(a)}.get(o)
            if op:
                img = np.ascontiguousarray(op(img))
                ex[274] = 1
                exif = ex.tobytes()
        except Exception:
            pass
    return np.ascontiguousarray(img, dtype=np.float32), exif, info


def write_file(a, path, exif=None, info=None, opt=None):
    """원본 엔진의 내보내기(용량 제한·워터마크·촬영 정보 표기 포함)."""
    o = opt or {}
    if not exif and info:
        exif = G.build_exif(info)
    G.encode_capped(a, path, exif=exif, quality=int(o.get("quality") or 95), fmt="PNG" if o.get("fmt") == "PNG" else "JPG",
                    resize_long=int(o.get("resize") or 0), wm_text=str(o.get("wm_text") or "")[:60], wm_opacity=int(o.get("wm_op") or 60),
                    wm_pos=o.get("wm_pos") or "우측 하단", cap_bytes=20 * 1024 * 1024 if o.get("size_cap") else 0,
                    info_lines=G.shot_lines(info) if (o.get("shot_info") and info) else None)
    del G._DRAW_ERR[:]


def raw_exts():
    return tuple(e.lstrip(".") for e in G.RAW_EXTS)


def all_exts():
    return tuple(e.lstrip(".") for e in G.ALL_EXTS)


def decode_small(data, max_px=1024):
    """참고 사진(파일 내용) → float RGB. 원본의 decode_image와 같은 처리."""
    import base64
    return S.decode_image(base64.b64encode(data).decode(), max_px)


def fit(rgb, long_side):
    h, w = rgb.shape[:2]
    k = long_side / float(max(h, w))
    if k >= 1:
        return rgb
    return cv2.resize(rgb, (max(1, int(round(w * k))), max(1, int(round(h * k)))), interpolation=cv2.INTER_AREA)


def to_float(rgb8):
    return rgb8.astype(np.float32) / 255.0


def jpeg(a, quality=90):
    """float RGB 0~1 → JPEG 바이트."""
    b = cv2.cvtColor((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8), cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".jpg", b, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise ValueError("사진을 저장하지 못했어요")
    return buf.tobytes()


def png_alpha(a, mask):
    """배경 제거용: 사람 영역을 투명도로 넣은 PNG."""
    rgb = (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)
    alpha = (np.clip(mask, 0, 1) * 255 + 0.5).astype(np.uint8)
    ok, buf = cv2.imencode(".png", np.dstack([cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), alpha]))
    if not ok:
        raise ValueError("사진을 저장하지 못했어요")
    return buf.tobytes()
