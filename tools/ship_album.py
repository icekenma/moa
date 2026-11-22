"""사진편집 프로그램 수정분을 잠가서 저장소에 올릴 묶음으로 만든다.

    python tools/ship_album.py <프로그램 폴더 또는 zip> "바뀐 내용 한 줄"

- 프로그램 폴더(PhotoGraderPro_web)의 프로그램 파일만 묶는다(.py .html .js .bat .ps1 .txt .md .css .svg .webmanifest).
  사진·DB·설정·기록·캐시는 넣지 않는다.
- ship/album.pub.json(노트북의 잠그는 열쇠)으로 잠가 ship/album.bin, ship/album.json을 쓴다. 그 뒤 커밋·푸시하면 노트북이 5분 안에 받아 적용한다.
"""
import hashlib
import io
import json
import os
import sys
import zipfile
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from moa import shipbox  # noqa: E402

ALLOWED = {".py", ".html", ".js", ".bat", ".ps1", ".txt", ".md", ".css", ".svg", ".webmanifest"}
SKIP = {"settings.json", "update_cfg.json", "update_status.json", "update_manifest.json", "ai_settings.json", "ai_usage.json", "album_check_result.txt"}


def pack(src):
    buf = io.BytesIO()
    names = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.isdir(src):
            for n in sorted(os.listdir(src)):
                p = os.path.join(src, n)
                if os.path.isfile(p) and os.path.splitext(n)[1].lower() in ALLOWED and n not in SKIP and not n.startswith("."):
                    z.write(p, "PhotoGraderPro_web/" + n)
                    names.append(n)
        else:
            with zipfile.ZipFile(src) as zin:
                for i in zin.infolist():
                    n = os.path.basename(i.filename)
                    if not i.is_dir() and os.path.splitext(n)[1].lower() in ALLOWED and n not in SKIP and not n.startswith("."):
                        z.writestr("PhotoGraderPro_web/" + n, zin.read(i))
                        names.append(n)
    if "aws_album.py" not in names or "album_ui.html" not in names:
        raise SystemExit("프로그램 파일(aws_album.py, album_ui.html)이 보이지 않아요")
    return buf.getvalue(), names


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    with open(os.path.join(BASE, "ship", "album.pub.json"), encoding="utf-8") as f:
        pub = json.load(f)
    data, names = pack(sys.argv[1])
    out = os.path.join(BASE, "ship")
    with open(os.path.join(out, "album.bin"), "wb") as f:
        f.write(shipbox.seal(pub, data))
    man = {"id": hashlib.sha256(data).hexdigest(), "key": pub["id"], "note": sys.argv[2][:200], "at": datetime.now().isoformat(timespec="seconds"), "files": len(names)}
    with open(os.path.join(out, "album.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(f"묶음 {len(data):,}바이트 · 파일 {len(names)}개 · {man['id'][:12]} · 열쇠 {pub['id']}")


if __name__ == "__main__":
    main()
