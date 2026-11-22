"""사진 보정 엔진(우석World 사진편집). numpy + OpenCV만 쓴다.

grade(img, p): 0~1 float32 RGB 사진에 보정값 p를 차례로 입힌다.
순서: 화이트밸런스·노출(선형 빛) → 밝은 곳·어두운 곳 → 흰색·검정 → 대비 → 안개 제거 → 또렷함
      → 생동감·채도 → 색별(HSL) → 흑백 → 색조 → 바랜 느낌 → 노이즈 → 피부 → 배경 → 선명 → 가장자리 → 입자
이 모듈은 numpy·OpenCV가 있어야 불러올 수 있어서, 쓰는 쪽(photo.py)에서 필요할 때만 불러온다.
"""
import os

import cv2
import numpy as np

# 보정값: (기본, 최소, 최대). 화면의 슬라이더도 이 범위를 그대로 쓴다.
RANGES = {
    "exposure": (0.0, -2.0, 2.0), "contrast": (0, -100, 100), "highlights": (0, -100, 100), "shadows": (0, -100, 100),
    "whites": (0, -100, 100), "blacks": (0, -100, 100),
    "temp": (0, -100, 100), "tint": (0, -100, 100), "vibrance": (0, -100, 100), "saturation": (0, -100, 100),
    "clarity": (0, -100, 100), "dehaze": (0, 0, 100), "sharpen": (0, 0, 100), "sharpen_radius": (1.0, 0.5, 3.0), "sharpen_thresh": (0, 0, 20),
    "nr_luma": (0, 0, 100), "nr_color": (0, 0, 100),
    "vignette": (0, -100, 100), "grain": (0, 0, 100), "fade": (0, 0, 100),
    "split_sh_hue": (220, 0, 360), "split_sh_sat": (0, 0, 100), "split_hl_hue": (40, 0, 360), "split_hl_sat": (0, 0, 100),
    "angle": (0.0, -15.0, 15.0), "skin": (0, 0, 100), "bg_blur": (0, 0, 100),
}
FLAGS = ("bw", "flip", "nr_quality", "bg_remove")
HSL_COLORS = [("red", "빨강", 0), ("orange", "주황", 30), ("yellow", "노랑", 60), ("green", "초록", 120),
              ("aqua", "청록", 180), ("blue", "파랑", 225), ("purple", "보라", 270), ("magenta", "자홍", 315)]
LUMA = np.array([0.299, 0.587, 0.114], np.float32)


def clean(p):
    """화면에서 온 보정값을 범위 안으로 다듬는다. 모르는 값은 버린다."""
    p = p if isinstance(p, dict) else {}
    out = {}
    for k, (d, lo, hi) in RANGES.items():
        try:
            v = float(p.get(k, d))
        except (TypeError, ValueError):
            v = d
        out[k] = min(hi, max(lo, v))
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
    q = clean(p)
    return q == clean({})


def _luma(a):
    return a @ LUMA


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


def _max3(a):
    return np.maximum(np.maximum(a[..., 0], a[..., 1]), a[..., 2])


def _min3(a):
    return np.minimum(np.minimum(a[..., 0], a[..., 1]), a[..., 2])


def _f32(a):
    return a if a.dtype == np.float32 else a.astype(np.float32)


def _hue_rgb(h):
    c = np.float32([[[h % 360, 1.0, 1.0]]])
    return cv2.cvtColor(c, cv2.COLOR_HSV2RGB)[0, 0]


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
def grade(img, p, fast=False, mask=None):
    """img: float32 RGB 0~1(자르기까지 끝난 것). mask: 사람 영역(배경 흐림·제거용, 같은 크기)."""
    a = img.astype(np.float32, copy=True)
    h, w = a.shape[:2]
    size = float(max(h, w))

    # 1) 색온도·색조·노출: 빛의 양에 비례하도록 선형으로 바꿔서 곱한다
    if p["temp"] or p["tint"] or p["exposure"]:
        t, ti = p["temp"] / 100.0, p["tint"] / 100.0
        g = np.float32([1 + 0.32 * t + 0.10 * ti, 1 - 0.22 * ti, 1 - 0.32 * t + 0.10 * ti])
        g /= float(g @ LUMA)
        # 선형 빛에 g를 곱하는 것은 감마 값에 g^(1/2.2)를 곱하는 것과 같다
        a *= np.power(g * np.float32(2.0 ** p["exposure"]), np.float32(1 / 2.2))
        hi = _max3(a)[..., None]                            # 넘친 곳은 색 비율을 지키며 1로 맞춘다(한 색으로 뭉개지지 않게)
        a = np.clip(np.where(hi > 1, a / np.maximum(hi, 1e-6), a), 0, 1)

    # 2) 밝은 곳·어두운 곳: 주변 밝기를 보고 그 영역만 밝기 비율로 움직인다(색은 그대로)
    if p["highlights"] or p["shadows"]:
        L = _luma(a)
        Lb = _blur(L, size * 0.012)
        sh, hl = p["shadows"] / 100.0, p["highlights"] / 100.0
        L2 = L.copy()
        if sh:
            ms = np.clip(1 - Lb / 0.55, 0, 1) ** 1.5
            L2 += sh * 0.38 * ms * (np.where(sh > 0, 1 - L, L))
        if hl:
            mh = np.clip((Lb - 0.45) / 0.55, 0, 1) ** 1.5
            L2 += hl * 0.38 * mh * (np.where(hl > 0, 1 - L, L))
        a *= (np.clip(L2, 0, 1) / np.maximum(L, 1e-4))[..., None]
        a = np.clip(a, 0, 1)

    # 3) 흰색·검정: 양 끝점을 옮긴다
    if p["whites"] or p["blacks"]:
        wp = 1 - p["whites"] / 100.0 * 0.25
        bp = -p["blacks"] / 100.0 * 0.12
        a = np.clip((a - bp) / max(0.2, wp - bp), 0, 1)

    # 4) 대비: 가운데 밝기를 축으로 S자(올릴 때)·직선(내릴 때)
    if p["contrast"]:
        c = p["contrast"] / 100.0
        if c > 0:
            k = 1 + c * 3.0
            a = 0.5 + np.tanh((a - 0.5) * np.float32(k)) * np.float32(1 / (2 * float(np.tanh(k / 2))))
        else:
            a = 0.5 + (a - 0.5) * (1 + c * 0.6)
        a = np.clip(a, 0, 1)

    a = _f32(a)

    # 5) 안개 제거: 뿌연 정도(어두운 채널)를 어림해 걷어낸다
    if p["dehaze"]:
        s = p["dehaze"] / 100.0
        dark = cv2.erode(_min3(a), np.ones((max(3, int(size * 0.012)) | 1,) * 2, np.uint8))
        dark = _blur(dark, size * 0.02)
        A = float(max(0.5, np.percentile(a[::4, ::4], 99.5)))
        t = np.clip(1 - 0.85 * s * dark / A, 0.3, 1)[..., None]
        a = np.clip((a - A) / t + A, 0, 1)

    # 6) 또렷함: 중간 밝기의 굵은 윤곽 대비
    if p["clarity"]:
        L = _luma(a)
        d = L - _blur(L, size * 0.02)
        m = 1 - np.abs(2 * L - 1) ** 2
        a = np.clip(a + (p["clarity"] / 100.0 * 0.9 * d * m)[..., None], 0, 1)

    # 7) 생동감(덜 진한 색 위주)·채도
    if p["vibrance"] or p["saturation"]:
        L = _luma(a)[..., None]
        inv = 1 - (_max3(a) - _min3(a))[..., None]
        f = 1 + p["saturation"] / 100.0 + p["vibrance"] / 100.0 * (1.0 if p["vibrance"] > 0 else 0.8) * inv * inv
        a = np.clip(L + (a - L) * np.maximum(f, 0), 0, 1)

    a = _f32(a)

    # 8) 색별 보정(HSL)
    if p["hsl"]:
        hsv = cv2.cvtColor(a, cv2.COLOR_RGB2HSV)
        H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        dh, fs, fv = np.zeros_like(H), np.ones_like(H), np.ones_like(H)
        for key, _, center in HSL_COLORS:
            if key not in p["hsl"]:
                continue
            hs, ss, ls = [v / 100.0 for v in p["hsl"][key]]
            dist = np.abs((H - center + 180) % 360 - 180)
            wgt = np.clip(1 - dist / 38.0, 0, 1)
            wgt = wgt * wgt * (3 - 2 * wgt)
            dh += hs * 30 * wgt
            fs += ss * wgt
            fv += ls * 0.5 * wgt * np.clip(S * 2, 0, 1)
        hsv[..., 0] = (H + dh) % 360
        hsv[..., 1] = np.clip(S * np.maximum(fs, 0), 0, 1)
        hsv[..., 2] = np.clip(V * np.maximum(fv, 0), 0, 1)
        a = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)

    # 9) 흑백
    if p["bw"]:
        a = np.repeat(_luma(a)[..., None], 3, axis=2)

    # 10) 색조(어두운 곳·밝은 곳에 색 얹기)
    if p["split_sh_sat"] or p["split_hl_sat"]:
        L = _luma(a)[..., None]
        if p["split_sh_sat"]:
            a += (_hue_rgb(p["split_sh_hue"]) - 0.5) * (p["split_sh_sat"] / 100.0 * 0.28) * (1 - L) ** 2
        if p["split_hl_sat"]:
            a += (_hue_rgb(p["split_hl_hue"]) - 0.5) * (p["split_hl_sat"] / 100.0 * 0.28) * L ** 2
        a = np.clip(a, 0, 1)

    # 11) 바랜 느낌
    if p["fade"]:
        f = p["fade"] / 100.0
        a = a * (1 - 0.22 * f) + 0.12 * f

    a = _f32(a)

    # 12) 노이즈 제거: 색 얼룩은 색 성분만 흐리고, 밝기 노이즈는 윤곽을 지키며 깎는다
    if p["nr_color"] or p["nr_luma"]:
        ycc = cv2.cvtColor(a, cv2.COLOR_RGB2YCrCb)
        if p["nr_color"]:
            s = 1.0 + p["nr_color"] / 100.0 * size * 0.006
            ycc[..., 1], ycc[..., 2] = _blur(ycc[..., 1], s), _blur(ycc[..., 2], s)
        if p["nr_luma"]:
            n = p["nr_luma"] / 100.0
            y = ycc[..., 0]
            if p["nr_quality"] and not fast:
                y8 = (np.clip(y, 0, 1) * 255).astype(np.uint8)
                dn = cv2.fastNlMeansDenoising(y8, None, 3 + 14 * n, 7, 21).astype(np.float32) / 255.0
            else:
                dn = cv2.bilateralFilter(y, 5 if fast else 7, 0.02 + 0.10 * n, 2 + 4 * n)
            ycc[..., 0] = y * (1 - min(1.0, 0.35 + n)) + dn * min(1.0, 0.35 + n)
        a = np.clip(cv2.cvtColor(ycc, cv2.COLOR_YCrCb2RGB), 0, 1)

    # 13) 피부 보정: 피부 영역만 곱게, 눈·머리카락 같은 윤곽은 남긴다
    if p["skin"]:
        s = p["skin"] / 100.0
        f = max(1, int(round(size / 480.0)))                # 고운 결만 고르면 되니 줄여서 처리한다
        small = cv2.resize(a, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
        small = cv2.bilateralFilter(small, 9, 0.07 + 0.07 * s, 3 + 4 * s)
        sm = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
        m = (skin_mask(a) * (0.85 * s))[..., None]
        a = a * (1 - m) + sm * m

    # 14) 배경 흐림·제거
    if mask is not None and (p["bg_blur"] or p["bg_remove"]):
        m = mask[..., None]
        if p["bg_remove"]:
            a = a * m + (1 - m)
        else:
            bg = _blur(a, 1 + p["bg_blur"] / 100.0 * size * 0.022)
            a = a * m + bg * (1 - m)

    a = _f32(a)

    # 15) 선명하게: 밝기에만, 문턱값보다 작은 차이(노이즈)는 건드리지 않는다
    if p["sharpen"]:
        L = _luma(a)
        d = L - _blur(L, p["sharpen_radius"])
        th = p["sharpen_thresh"] / 255.0
        if th:
            d = np.sign(d) * np.maximum(np.abs(d) - th, 0)
        a = np.clip(a + (p["sharpen"] / 100.0 * 1.6 * d)[..., None], 0, 1)

    # 16) 가장자리 어둡게·밝게
    if p["vignette"]:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r2 = ((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2
        v = np.clip(r2 / 2, 0, 1) ** 1.3
        a = np.clip(a * (1 + p["vignette"] / 100.0 * 0.75 * v)[..., None], 0, 1)

    # 17) 필름 입자(같은 사진은 늘 같은 무늬)
    if p["grain"]:
        n = _GRAIN.get((h, w))
        if n is None:
            _GRAIN.clear()
            n = np.random.default_rng(7).standard_normal((h, w), dtype=np.float32)
            n = _GRAIN[(h, w)] = _blur(n, max(0.6, size / 2200.0))
        a = np.clip(a + (n * (p["grain"] / 100.0 * 0.10))[..., None] * (1 - np.abs(2 * _luma(a) - 1) * 0.5)[..., None], 0, 1)

    return _f32(np.clip(a, 0, 1))


def render(img, p, fast=False, crop=True, mask_fn=None):
    """돌리기·자르기 → 보정. mask_fn(): 원본 방향 그대로의 사람 영역(없으면 None)."""
    g = geometry(img, p, crop=crop)
    mask = None
    if (p["bg_blur"] or p["bg_remove"]) and mask_fn:
        m = mask_fn()
        if m is not None:
            if m.shape[:2] != img.shape[:2]:
                m = cv2.resize(m, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)
            mask = geometry(m, p, crop=crop)
    return grade(g, p, fast=fast, mask=mask), mask


# ---------- 자동 보정(사진 분석) ----------
def auto(img):
    """사진의 밝기·색 분포를 보고 무난한 보정값을 제안한다. 과하지 않게 한도를 둔다."""
    k = 640.0 / max(img.shape[:2])
    s = cv2.resize(img, (max(1, int(img.shape[1] * k)), max(1, int(img.shape[0] * k))), interpolation=cv2.INTER_AREA) if k < 1 else img
    L = _luma(s)
    p1, p50, p99 = [float(v) for v in np.percentile(L, [1, 50, 99])]
    out = {}
    ev = np.log2(max(1e-4, 0.44 ** 2.2) / max(1e-4, p50 ** 2.2)) * 0.45
    out["exposure"] = round(float(np.clip(ev, -0.8, 1.2)) / 0.05) * 0.05
    gain = 2 ** (out["exposure"] / 2.2)
    if p1 * gain > 0.05:
        out["blacks"] = -int(min(45, (p1 * gain - 0.02) * 380))
    if p99 * gain < 0.93:
        out["whites"] = int(min(45, (0.97 - p99 * gain) * 260))
    if float((L * gain > 0.94).mean()) > 0.04:
        out["highlights"] = -35
    if float((L * gain < 0.12).mean()) > 0.22:
        out["shadows"] = 30
    if float(L.std()) < 0.17:
        out["contrast"] = 18 if float(L.std()) < 0.12 else 10
    mid = (L > 0.2) & (L < 0.85)
    if mid.sum() > 500:                                    # 회색 세계 가정, 절반만 따라간다
        r, g, b = [float(s[..., i][mid].mean()) for i in range(3)]
        out["temp"] = int(np.clip((b - r) / max(1e-3, g) * 60, -18, 18))
        out["tint"] = int(np.clip(((r + b) / 2 - g) / max(1e-3, g) * -70, -12, 12))
    sat = float((s.max(axis=2) - s.min(axis=2)).mean())
    out["vibrance"] = 22 if sat < 0.16 else 12 if sat < 0.26 else 4
    out["clarity"] = 8
    out["sharpen"] = 25
    return {k2: v for k2, v in out.items() if v}


PRESETS = [
    ("none", "원본", {}),
    ("crisp", "선명하게", {"contrast": 14, "clarity": 18, "vibrance": 16, "sharpen": 35, "blacks": -8}),
    ("warm", "따뜻한 필름", {"temp": 16, "tint": 4, "contrast": 8, "fade": 22, "vibrance": 10, "grain": 18, "split_hl_hue": 42, "split_hl_sat": 22, "split_sh_hue": 210, "split_sh_sat": 12}),
    ("cool", "시원한 도시", {"temp": -14, "contrast": 16, "clarity": 12, "vibrance": 8, "saturation": -8, "split_sh_hue": 205, "split_sh_sat": 24, "highlights": -15}),
    ("portrait", "인물", {"exposure": 0.15, "contrast": 4, "highlights": -18, "shadows": 14, "vibrance": 10, "skin": 45, "sharpen": 18, "vignette": -12}),
    ("landscape", "풍경", {"contrast": 12, "highlights": -30, "shadows": 22, "dehaze": 18, "clarity": 16, "vibrance": 26, "sharpen": 30,
                         "hsl": {"blue": [0, 14, -8], "green": [0, 12, 0]}}),
    ("food", "음식", {"exposure": 0.2, "temp": 8, "contrast": 10, "vibrance": 24, "clarity": 10, "sharpen": 28, "hsl": {"orange": [0, 10, 4], "red": [0, 8, 0]}}),
    ("night", "야경", {"exposure": 0.25, "highlights": -40, "shadows": 30, "blacks": -10, "clarity": 14, "vibrance": 14, "nr_luma": 35, "nr_color": 45, "sharpen": 15}),
    ("bw", "흑백", {"bw": True, "contrast": 22, "clarity": 16, "blacks": -10, "grain": 22}),
    ("vintage", "빈티지", {"fade": 45, "contrast": -6, "saturation": -18, "temp": 10, "grain": 35, "vignette": -30, "split_sh_hue": 190, "split_sh_sat": 18, "split_hl_hue": 48, "split_hl_sat": 26}),
]


# ---------- 읽기·쓰기 ----------
RAW_EXT = ("cr2", "cr3", "nef", "arw", "srw", "orf", "rw2", "dng", "raf", "pef")


def decode(data, ext=""):
    """사진 파일 내용 → float 아닌 uint8 RGB. RAW는 rawpy가 있을 때만."""
    if ext.lower() in RAW_EXT:
        try:
            import io

            import rawpy
        except ImportError:
            raise ValueError("RAW 사진은 노트북에 rawpy를 설치해야 열 수 있어요")
        with rawpy.imread(io.BytesIO(data)) as raw:
            return np.ascontiguousarray(raw.postprocess(use_camera_wb=True, no_auto_bright=False, output_bps=8))
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("열 수 없는 사진이에요 (JPG·PNG·WebP·RAW만 돼요)")
    return cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)


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
