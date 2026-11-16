"""moa 실행: python run.py  →  http://127.0.0.1:8765"""
import argparse
from urllib.parse import quote
import os
import webbrowser

from moa.app import access_token, create_app
from moa.db import DB
from moa.runtime import Runtime

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1", help="다른 기기에서 접속하려면 0.0.0.0 (주의: 인증 없음)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    os.makedirs(os.path.join(BASE, "data"), exist_ok=True)
    db = DB(os.path.join(BASE, "data", "moa.db"))
    rt = Runtime(db)
    app = create_app(rt)
    url = f"http://127.0.0.1:{a.port}"
    print(f"moa 실행 중: {url}  (끄려면 이 창을 닫으세요)")
    print(f"휴대폰 접속 토큰: {access_token(db)}")
    pub = rt.settings.get("public_url")
    if pub:
        print(f"휴대폰 연결 주소: https://icekenma.github.io/moa/#connect?server={quote(pub, safe='')}&token={access_token(db)}")
    if not a.no_browser:
        webbrowser.open(url)
    app.run(host=a.host, port=a.port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
