"""moa 실행: python run.py  →  http://127.0.0.1:8780

종료 코드 3은 "업데이트했으니 다시 띄워 달라"는 뜻이다(run.bat이 처리).
"""
import argparse
from urllib.parse import quote
import os
import webbrowser

from moa.app import access_token, create_app
from moa.db import DB
from moa.runtime import Runtime
from moa.updater import Updater

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1", help="기본은 이 PC에서만. 휴대폰은 tailscale serve로 연결")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    os.makedirs(os.path.join(BASE, "data"), exist_ok=True)
    db = DB(os.path.join(BASE, "data", "moa.db"))
    rt = Runtime(db)
    updater = Updater(rt, BASE)
    app = create_app(rt, updater)
    updater.start()
    url = f"http://127.0.0.1:{a.port}"
    print(f"moa 실행 중: {url}  (끄려면 이 창을 닫으세요)")
    print(f"휴대폰 접속 토큰: {access_token(db)}")
    if updater.available:
        print(f"버전 {updater.state['current']} · GitHub 자동 업데이트 {'켜짐' if rt.settings.get('auto_update') else '꺼짐'}")
    else:
        print("자동 업데이트 꺼짐: Git 또는 GitHub 연결이 없어요")
    pub = rt.settings.get("public_url")
    if pub:
        print(f"휴대폰 연결 주소: https://icekenma.github.io/moa/#connect?server={quote(pub, safe='')}&token={access_token(db)}")
    if not a.no_browser:
        webbrowser.open(url)
    app.run(host=a.host, port=a.port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
