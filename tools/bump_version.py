"""버전 올리기: GitHub에 올리기 전에 실행.
- VERSION        : 사람이 보는 버전(v0.1, v0.2 ...)
- docs/version.json, app.js UI_VERSION, sw.js CACHE : 화면 새 버전 감지용 빌드 번호
사용: python tools/bump_version.py 0.2 "바뀐 내용 한 줄"   (버전을 빼면 그대로 두고 빌드만 올림)
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
args = sys.argv[1:]
vf = os.path.join(ROOT, "VERSION")
if args and re.fullmatch(r"\d+(\.\d+)+", args[0]):
    open(vf, "w", encoding="utf-8").write(args.pop(0) + "\n")
version = open(vf, encoding="utf-8").read().strip()
note = args[0] if args else ""
build = datetime.now(timezone(timedelta(hours=9))).strftime("%y%m%d.%H%M")


def sub(path, pat, rep):
    s = open(path, encoding="utf-8").read()
    s, n = re.subn(pat, rep, s)
    assert n == 1, path
    open(path, "w", encoding="utf-8").write(s)


sub(os.path.join(DOCS, "app.js"), r'const UI_VERSION = "[^"]*";', f'const UI_VERSION = "{build}";')
sub(os.path.join(DOCS, "sw.js"), r'const CACHE = "[^"]*";', f'const CACHE = "moa-{build}";')
sub(os.path.join(DOCS, "index.html"), r'app\.css\?v=[^"]*"', f'app.css?v={build}"')
sub(os.path.join(DOCS, "index.html"), r'app\.js\?v=[^"]*"', f'app.js?v={build}"')
sub(os.path.join(DOCS, "index.html"), r'boot\.js\?v=[^"]*"', f'boot.js?v={build}"')
with open(os.path.join(DOCS, "version.json"), "w", encoding="utf-8") as f:
    json.dump({"version": version, "build": build, "note": note}, f, ensure_ascii=False)
print(f"v{version} ({build})")
