"""사진 슬라이드쇼 영상(우석World 사진편집).

고른 사진(보정값 입힌 모습)을 천천히 움직이며 이어 붙여 MP4로 만든다. 배경음악은 Openverse(공개 이용 허락 음원 검색)에서
사진 분위기에 맞는 곡을 그때그때 골라 내려받는다. 저작권 문제가 없도록 CC0·퍼블릭 도메인·CC BY 곡만 쓰고, 출처를 함께 돌려준다.
영상 만들기에는 ffmpeg가 필요하다(imageio-ffmpeg 꾸러미에 들어 있다).
"""
import json
import os
import random
import re
import shutil
import subprocess
import threading
import uuid

API = "https://api.openverse.org/v1/audio/"
UA = "WooseokWorld-slideshow/1.0 (personal photo slideshow; github.com/icekenma/moa)"
LICENSES = "cc0,pdm,by"                      # 누구나 써도 되는 곡만(BY는 출처 표시 조건)
MOODS = {                                    # 분위기 → 검색어 후보(하나를 무작위로)
    "calm": ("잔잔한", ["calm piano", "relaxing ambient", "soft acoustic guitar", "peaceful"]),
    "bright": ("밝은", ["happy acoustic", "uplifting ukulele", "cheerful", "sunny"]),
    "emotional": ("감성", ["emotional piano", "cinematic strings", "nostalgic", "sentimental"]),
    "upbeat": ("신나는", ["upbeat pop", "energetic", "funk groove", "dance"]),
    "night": ("도시의 밤", ["lofi chill", "jazz night", "chillhop", "smooth jazz"]),
}
SIZES = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1080, 1080), "4:5": (1080, 1350)}
FPS, XFADE = 30, 0.7


class SlideError(ValueError):
    pass


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def auto_mood(stats, count):
    """사진들의 평균 밝기·채도·따뜻함으로 분위기를 고른다. stats: [(밝기, 채도, 따뜻함)]"""
    if not stats:
        return "calm"
    L = sum(s[0] for s in stats) / len(stats)
    S = sum(s[1] for s in stats) / len(stats)
    Wm = sum(s[2] for s in stats) / len(stats)
    if L < 0.30:
        return "night"
    if S > 0.30 and L > 0.5:
        return "upbeat" if count >= 15 else "bright"
    if Wm > 0.05 and S < 0.22:
        return "emotional"
    return "bright" if L > 0.6 else "calm"


class Music:
    """Openverse에서 곡을 찾아 내려받는다. 받은 곡은 폴더에 남겨, 인터넷이 안 될 때 다시 쓴다."""

    def __init__(self, folder, http_get=None, rng=None):
        self.dir = folder
        os.makedirs(folder, exist_ok=True)
        self._get = http_get
        self.rng = rng or random.Random()

    def get(self, url, **kw):
        if self._get:
            return self._get(url, **kw)
        import requests
        return requests.get(url, headers={"User-Agent": UA}, **kw)

    @staticmethod
    def _seconds(v):
        try:
            v = float(v or 0)
        except (TypeError, ValueError):
            return 0.0
        return v / 1000.0 if v > 3000 else v          # Openverse는 밀리초로 준다(초로 주는 경우도 받아 준다)

    def search(self, mood):
        q = self.rng.choice(MOODS[mood][1])
        for page in (self.rng.randint(1, 3), 1):
            r = self.get(API, params={"q": q, "category": "music", "license": LICENSES, "page_size": 20, "page": page}, timeout=15)
            if getattr(r, "status_code", 200) != 200:
                continue
            out = []
            for x in (r.json() or {}).get("results") or []:
                sec = self._seconds(x.get("duration"))
                ext = str(x.get("filetype") or "mp3").lower()
                if x.get("url") and not x.get("mature") and 40 <= sec <= 600 and ext in ("mp3", "ogg", "oga", "m4a", "wav", "flac"):
                    out.append({"id": re.sub(r"[^A-Za-z0-9-]", "", str(x.get("id") or uuid.uuid4().hex))[:40], "title": str(x.get("title") or "제목 없음")[:120],
                                "creator": str(x.get("creator") or "")[:80], "license": str(x.get("license") or "").lower(), "version": str(x.get("license_version") or ""),
                                "page": str(x.get("foreign_landing_url") or ""), "url": x["url"], "ext": "ogg" if ext == "oga" else ext, "sec": round(sec), "query": q})
            if out:
                return out
        return []

    def credit(self, m):
        lic = {"cc0": "CC0", "pdm": "퍼블릭 도메인", "by": "CC BY"}.get(m.get("license"), (m.get("license") or "").upper())
        if m.get("license") == "by" and m.get("version"):
            lic += " " + m["version"]
        return f"음악: {m.get('title')}" + (f" — {m['creator']}" if m.get("creator") else "") + f" ({lic})"

    def pick(self, mood):
        """(파일 경로, 곡 정보) 또는 (None, 이유)."""
        try:
            cands = self.search(mood)
            self.rng.shuffle(cands)
            for m in cands[:4]:
                path = os.path.join(self.dir, f"{m['id']}.{m['ext']}")
                if not os.path.exists(path):
                    r = self.get(m["url"], timeout=40)
                    data = getattr(r, "content", b"")
                    if getattr(r, "status_code", 200) != 200 or not 8_000 < len(data) < 25_000_000:
                        continue
                    with open(path, "wb") as f:
                        f.write(data)
                    with open(os.path.join(self.dir, f"{m['id']}.json"), "w", encoding="utf-8") as f:
                        json.dump(dict(m, mood=mood), f, ensure_ascii=False)
                m["mood"], m["credit"] = mood, self.credit(m)
                return path, m
            why = "어울리는 곡을 찾지 못했어요"
        except Exception as e:                           # 인터넷이 안 되거나 주소가 막혔을 때
            why = f"음악 서비스에 연결하지 못했어요 ({type(e).__name__})"
        saved = self.saved(mood) or self.saved()
        if saved:
            path, m = self.rng.choice(saved)
            m["credit"] = self.credit(m)
            m["note"] = why + " — 전에 받아 둔 곡을 썼어요"
            return path, m
        return None, {"note": why + " — 소리 없이 만들었어요"}

    def saved(self, mood=None):
        out = []
        for name in os.listdir(self.dir):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.dir, name), encoding="utf-8") as f:
                    m = json.load(f)
            except (OSError, ValueError):
                continue
            path = os.path.join(self.dir, f"{m.get('id')}.{m.get('ext')}")
            if os.path.exists(path) and (mood is None or m.get("mood") == mood):
                out.append((path, m))
        return out


class Slideshow:
    def __init__(self, photo, http_get=None):
        self.photo = photo
        self.dir = os.path.join(photo.dir, "slideshow")
        os.makedirs(self.dir, exist_ok=True)
        self.music = Music(os.path.join(photo.dir, "music"), http_get)
        self.jobs = {}

    def ready(self):
        return bool(ffmpeg_exe())

    def moods(self):
        return [["auto", "자동 추천"]] + [[k, v[0]] for k, v in MOODS.items()] + [["none", "음악 없음"]]

    # ---------- 한 장을 영상 크기의 '무대'로: 비율이 다르면 흐린 배경 위에 사진을 온전히 올린다 ----------
    def _stage(self, pid, W, H):
        import cv2
        import numpy as np
        (a, _), _ = self.photo._render(pid, json.loads(self.photo._row(pid)["params"] or "{}"), "base")
        img = (a * 255 + 0.5).astype(np.uint8)
        SW, SH = int(W * 1.14) // 2 * 2, int(H * 1.14) // 2 * 2
        h, w = img.shape[:2]

        def cover(src, tw, th):
            k = max(tw / src.shape[1], th / src.shape[0])
            r = cv2.resize(src, (max(tw, int(round(src.shape[1] * k))), max(th, int(round(src.shape[0] * k)))), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
            y0, x0 = (r.shape[0] - th) // 2, (r.shape[1] - tw) // 2
            return r[y0:y0 + th, x0:x0 + tw]

        if abs((w / h) / (SW / SH) - 1) < 0.14:
            return np.ascontiguousarray(cover(img, SW, SH))
        bg = cv2.GaussianBlur(cover(cv2.resize(img, (max(2, w // 8), max(2, h // 8)), interpolation=cv2.INTER_AREA), SW, SH), (0, 0), max(SW, SH) * 0.02)
        bg = (bg.astype(np.float32) * 0.5).astype(np.uint8)
        k = min(SW * 0.9 / w, SH * 0.9 / h)
        fg = cv2.resize(img, (max(2, int(w * k)), max(2, int(h * k))), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
        y0, x0 = (SH - fg.shape[0]) // 2, (SW - fg.shape[1]) // 2
        bg[y0:y0 + fg.shape[0], x0:x0 + fg.shape[1]] = fg
        return bg

    @staticmethod
    def _frame(stage, i, t, W, H):
        """천천히 다가가거나 멀어지며 살짝 옆으로 흐른다(사진마다 방향을 바꾼다)."""
        import cv2
        import numpy as np
        SH, SW = stage.shape[:2]
        e = t * t * (3 - 2 * t)
        z0, z1 = (1.0, 1.10) if i % 2 == 0 else (1.10, 1.0)
        z = (z0 + (z1 - z0) * e) * (W / SW) * 1.14
        dx = ((-1) ** (i // 2)) * (e - 0.5) * W * 0.035
        dy = ((-1) ** ((i + 1) // 2)) * (e - 0.5) * H * 0.02
        m = np.float32([[z, 0, W / 2 - z * SW / 2 + dx], [0, z, H / 2 - z * SH / 2 + dy]])
        return cv2.warpAffine(stage, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    def _stats(self, ids):
        out = []
        for pid in ids[:24]:
            try:
                a = self.photo._img(pid, "tiny")
                out.append((float((a @ self.photo.engine().LUMA).mean()), float((a.max(axis=2) - a.min(axis=2)).mean()), float(a[..., 0].mean() - a[..., 2].mean())))
            except Exception:
                pass
        return out

    # ---------- 만들기 ----------
    def start(self, ids, opts=None, run_async=True):
        self.photo.engine()
        if not self.ready():
            raise SlideError("영상 만들기 도구(ffmpeg)가 없어요. 사진편집 첫 화면에서 '영상 도구 설치'를 눌러 주세요")
        ids = [i for i in (ids or []) if isinstance(i, str)][:120]
        if len(ids) < 2:
            raise SlideError("사진을 2장 이상 골라 주세요")
        for pid in ids:
            self.photo._row(pid)
        if any(j["state"] == "running" for j in self.jobs.values()):
            raise SlideError("지금 만들고 있는 영상이 있어요. 끝난 뒤에 다시 눌러 주세요")
        o = opts or {}
        try:
            sec = min(8.0, max(1.5, float(o.get("sec") or 3.5)))
        except (TypeError, ValueError):
            sec = 3.5
        ratio = o.get("ratio") if o.get("ratio") in SIZES else "16:9"
        W, H = SIZES[ratio]
        if o.get("quality") == "fhd":
            W, H = int(W * 1.5) // 2 * 2, int(H * 1.5) // 2 * 2
        mood = o.get("music") if o.get("music") in MOODS or o.get("music") in ("auto", "none") else "auto"
        jid = uuid.uuid4().hex[:12]
        job = self.jobs[jid] = {"id": jid, "state": "running", "progress": 0.0, "step": "준비 중", "count": len(ids), "music": None, "error": "", "sec": 0,
                                "size": [W, H], "mood": mood}
        args = (job, ids, sec, W, H, mood)
        if run_async:
            threading.Thread(target=self._run, args=args, daemon=True).start()
        else:
            self._run(*args)
        return self.status(jid)

    def _run(self, job, ids, sec, W, H, mood):
        try:
            self._build(job, ids, sec, W, H, mood)
            job.update(state="done", progress=1.0, step="완성")
        except Exception as e:
            job.update(state="error", error=str(e) if isinstance(e, ValueError) else f"영상을 만들지 못했어요 ({type(e).__name__}: {str(e)[:80]})")
        self._tidy(keep=job["id"])

    def _build(self, job, ids, sec, W, H, mood):
        n, xf = int(round(sec * FPS)), int(round(XFADE * FPS))
        stride = n - xf
        total = stride * (len(ids) - 1) + n
        dur = total / FPS
        job["sec"] = round(dur, 1)
        path = None
        if mood != "none":
            job["step"] = "어울리는 음악 찾는 중"
            if mood == "auto":
                mood = auto_mood(self._stats(ids), len(ids))
            path, info = self.music.pick(mood)
            info["mood_label"] = MOODS[mood][0]
            job["music"] = {k: info.get(k) for k in ("title", "creator", "license", "page", "credit", "note", "mood_label", "sec")}
        out = os.path.join(self.dir, f"{job['id']}.mp4")
        ok = self._encode(job, ids, n, xf, stride, total, W, H, out, path, dur)
        if not ok and path:                             # 음악 파일이 깨졌으면 소리 없이 다시
            job["music"] = {"note": "받은 음악 파일을 쓸 수 없어 소리 없이 만들었어요"}
            ok = self._encode(job, ids, n, xf, stride, total, W, H, out, None, dur)
        if not ok:
            raise SlideError("영상을 저장하지 못했어요")

    def _encode(self, job, ids, n, xf, stride, total, W, H, out, music, dur):
        import cv2
        cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
        if music:
            cmd += ["-stream_loop", "-1", "-i", music, "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "160k",
                    "-af", f"afade=t=in:d=1.2,afade=t=out:st={max(0.0, dur - 2.6):.2f}:d=2.6,volume=0.85"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-t", f"{dur:.3f}", out]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        stages = {}
        fade = int(0.5 * FPS)
        try:
            job["step"] = "영상 만드는 중"
            for f in range(total):
                i = min(len(ids) - 1, f // stride)
                if f - i * stride >= n:                 # 마지막 장의 끝
                    i = len(ids) - 1
                for k in [k for k in stages if k < i - 1]:
                    del stages[k]
                with self.photo.lock:
                    if i not in stages:
                        stages[i] = self._stage(ids[i], W, H)
                    fr = self._frame(stages[i], i, (f - i * stride) / max(1, n - 1), W, H)
                    into = f - i * stride                # 앞 장과 겹치는 구간: 서서히 바꾼다
                    if i > 0 and into < xf:
                        if i - 1 not in stages:
                            stages[i - 1] = self._stage(ids[i - 1], W, H)
                        prev = self._frame(stages[i - 1], i - 1, (f - (i - 1) * stride) / max(1, n - 1), W, H)
                        a = (into + 1) / (xf + 1)
                        fr = cv2.addWeighted(fr, a, prev, 1 - a, 0)
                g = min(1.0, (f + 1) / fade, (total - f) / fade)   # 처음과 끝은 검정에서·검정으로
                if g < 1:
                    fr = cv2.convertScaleAbs(fr, alpha=g)
                proc.stdin.write(fr.tobytes())
                if f % 15 == 0:
                    job["progress"] = round(0.05 + 0.93 * f / total, 3)
            proc.stdin.close()
            err = proc.stderr.read().decode("utf-8", "replace")
            return proc.wait(timeout=600) == 0 and os.path.exists(out) and os.path.getsize(out) > 1000 or (job.update(log=err[-300:]) or False)
        except (BrokenPipeError, OSError):
            job["log"] = proc.stderr.read().decode("utf-8", "replace")[-300:] if proc.stderr else ""
            proc.kill()
            return False

    def _tidy(self, keep):
        """영상은 최근 3개만 둔다."""
        done = [j for j in self.jobs.values() if j["state"] != "running" and j["id"] != keep]
        for j in done[:-2]:
            self.jobs.pop(j["id"], None)
        alive = {j["id"] for j in self.jobs.values()}
        for name in os.listdir(self.dir):
            if name.endswith(".mp4") and name[:-4] not in alive:
                try:
                    os.remove(os.path.join(self.dir, name))
                except OSError:
                    pass

    def status(self, jid):
        j = self.jobs.get(str(jid))
        if not j:
            raise SlideError("없는 영상이에요. 다시 만들어 주세요")
        return {k: v for k, v in j.items() if k != "log"}

    def file(self, jid):
        j = self.status(jid)
        path = os.path.join(self.dir, f"{j['id']}.mp4")
        if j["state"] != "done" or not os.path.exists(path):
            raise SlideError("아직 영상이 없어요")
        with open(path, "rb") as f:
            return f.read()
