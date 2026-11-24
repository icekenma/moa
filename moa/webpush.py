"""앱 알림(웹 푸시): ntfy 같은 다른 앱 없이, 홈 화면에 추가한 우석World 앱으로 바로 알림을 보낸다.

표준(RFC 8030 전달 · RFC 8291 aes128gcm 암호화 · RFC 8292 VAPID)을 cryptography만으로 구현한다.
- 서버 열쇠(VAPID)는 처음 쓸 때 만들어 서버 DB에만 둔다(저장소·설정 내보내기에 나가지 않음).
- 휴대폰이 알림을 허락하면 받는 주소(endpoint)와 열쇠를 DB(push_subs)에 남기고, 알릴 때 기기마다 암호화해 보낸다.
- 받는 주소가 사라졌다고(404·410) 하면 그 기기는 지운다.
"""
import base64
import hashlib
import hmac
import json
import os
import struct
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

import requests

SUB = "mailto:icekenma@users.noreply.github.com"   # 푸시 서비스가 문제 생기면 연락할 곳(공개 주소)


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def ub64(s):
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hkdf(salt, ikm, info, n):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:n]


def encrypt(payload, p256dh, auth, _priv=None, _salt=None):
    """RFC 8291: 받는 기기 열쇠(p256dh, auth)로 payload(바이트)를 암호화한 본문(aes128gcm)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    ua_pub = ub64(p256dh) if isinstance(p256dh, str) else p256dh
    secret = ub64(auth) if isinstance(auth, str) else auth
    priv = _priv or ec.generate_private_key(ec.SECP256R1())
    as_pub = priv.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    shared = priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    ikm = _hkdf(secret, shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    salt = _salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!L", 4096) + bytes([len(as_pub)]) + as_pub + ct


def decrypt(body, ua_priv, auth):
    """시험용: 기기 쪽에서 푸는 방법(RFC 8291 그대로)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    salt, idlen = body[:16], body[20]
    as_pub, ct = body[21:21 + idlen], body[21 + idlen:]
    ua_pub = ua_priv.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    shared = ua_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = _hkdf(auth, shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    pt = AESGCM(cek).decrypt(nonce, ct, None)
    return pt.rstrip(b"\x00")[:-1]


class WebPush:
    def __init__(self, rt):
        self.rt = rt
        rt.db.x("""CREATE TABLE IF NOT EXISTS push_subs(id INTEGER PRIMARY KEY, endpoint TEXT NOT NULL UNIQUE, p256dh TEXT NOT NULL,
                auth TEXT NOT NULL, ua TEXT, created TEXT NOT NULL, last_ok TEXT, fails INTEGER NOT NULL DEFAULT 0)""")
        self._lock = threading.Lock()
        self.post = requests.post        # 시험에서 바꿔 끼운다

    @staticmethod
    def available():
        import importlib.util
        return importlib.util.find_spec("cryptography") is not None

    # ----- 서버 열쇠(VAPID)
    def _keys(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        k = self.rt.db.get_setting("push_vapid")
        if k and k.get("priv"):
            priv = serialization.load_pem_private_key(k["priv"].encode(), None)
            return priv, k["pub"]
        with self._lock:
            priv = ec.generate_private_key(ec.SECP256R1())
            pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
            pub = b64u(priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
            self.rt.db.set_setting("push_vapid", {"priv": pem, "pub": pub, "at": datetime.now().isoformat(timespec="seconds")})
            return priv, pub

    def public_key(self):
        return self._keys()[1] if self.available() else None

    def _vapid(self, endpoint):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        priv, pub = self._keys()
        u = urlparse(endpoint)
        head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        claim = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": SUB}, separators=(",", ":")).encode())
        r, s = decode_dss_signature(priv.sign(f"{head}.{claim}".encode(), ec.ECDSA(hashes.SHA256())))
        sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"vapid t={head}.{claim}.{sig}, k={pub}"

    # ----- 기기
    def subs(self):
        return self.rt.db.q("SELECT id, endpoint, ua, created, last_ok, fails FROM push_subs ORDER BY id")

    def count(self):
        try:
            return self.rt.db.one("SELECT COUNT(*) n FROM push_subs")["n"]
        except Exception:
            return 0

    def add(self, sub, ua=""):
        sub = sub or {}
        ep, keys = str(sub.get("endpoint") or ""), sub.get("keys") or {}
        if not ep.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
            raise ValueError("알림 받을 주소가 올바르지 않아요")
        if len(ub64(keys["p256dh"])) != 65 or len(ub64(keys["auth"])) < 16:
            raise ValueError("알림 열쇠가 올바르지 않아요")
        self.rt.db.x("""INSERT INTO push_subs(endpoint,p256dh,auth,ua,created) VALUES(?,?,?,?,?)
                        ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth, ua=excluded.ua, fails=0""",
                     (ep, keys["p256dh"], keys["auth"], str(ua)[:160], datetime.now().isoformat(timespec="seconds")))
        return self.info(ep)

    def remove(self, endpoint):
        self.rt.db.x("DELETE FROM push_subs WHERE endpoint=?", (str(endpoint or ""),))
        return self.info()

    def info(self, endpoint=None):
        def dev(ua):
            ua = ua or ""
            return "아이폰" if "iPhone" in ua else "아이패드" if "iPad" in ua else "안드로이드" if "Android" in ua else "맥" if "Mac" in ua else "윈도우 PC" if "Windows" in ua else "기기"
        rows = self.subs()
        return {"ok": self.available(), "key": self.public_key(), "n": len(rows),
                "devices": [{"name": dev(r["ua"]), "since": r["created"][:10], "last_ok": r["last_ok"], "me": r["endpoint"] == endpoint} for r in rows]}

    # ----- 보내기
    def send(self, title, body, url="", tag="", urgent=False, wait=False, nid=None):
        """모든 기기로 보낸다(기본은 따로 도는 스레드에서). 보낸 기기 수를 돌려준다(wait일 때)."""
        rows = self.rt.db.q("SELECT * FROM push_subs")
        if not rows or not self.available():
            return 0
        data = json.dumps({"title": title, "body": body, "url": url or "./", "tag": tag, "nid": nid}, ensure_ascii=False).encode()[:3000]

        def go():
            ok = 0
            for r in rows:
                try:
                    res = self.post(r["endpoint"], data=encrypt(data, r["p256dh"], r["auth"]), timeout=8, headers={
                        "Authorization": self._vapid(r["endpoint"]), "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                        "TTL": "86400", "Urgency": "high" if urgent else "normal", **({"Topic": tag[:32]} if tag else {})})
                    code = res.status_code
                except Exception:
                    code = 0
                if 200 <= code < 300:
                    ok += 1
                    self.rt.db.x("UPDATE push_subs SET last_ok=?, fails=0 WHERE id=?", (datetime.now().isoformat(timespec="seconds"), r["id"]))
                elif code in (404, 410):
                    self.rt.db.x("DELETE FROM push_subs WHERE id=?", (r["id"],))          # 기기가 알림을 끄거나 앱을 지움
                else:
                    self.rt.db.x("UPDATE push_subs SET fails=fails+1 WHERE id=?", (r["id"],))
                    self.rt.db.x("DELETE FROM push_subs WHERE id=? AND fails>=30", (r["id"],))
            return ok
        if wait:
            return go()
        threading.Thread(target=go, name="moa-webpush", daemon=True).start()
        return len(rows)
