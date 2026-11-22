"""잠근 묶음(우석World): 공개 저장소로 보내도 받는 컴퓨터만 풀 수 있게 잠그는 도구. 파이썬 기본 기능만 쓴다.

쓰임: 사진편집 프로그램(판매용 코드)의 수정분을 공개 저장소에 올려 노트북으로 보낼 때.
  - 노트북이 열쇠 한 쌍을 만든다. 잠그는 열쇠(공개)는 저장소에 두고, 푸는 열쇠(비밀)는 노트북의 data 폴더에만 둔다.
  - seal(공개 열쇠, 내용): 한 번 쓰고 버릴 값 r을 RSA로 감싸고(RSA-KEM), r에서 뽑은 열쇠로 내용을 가린 뒤(SHAKE-256 흐름) 변조 확인표(HMAC-SHA256)를 붙인다.
  - open_(비밀 열쇠, 묶음): 확인표가 맞을 때만 내용을 돌려준다.
"""
import hashlib
import hmac
import secrets

MAGIC = b"MOASHIP1"
BITS = 3072
_SMALL = [p for p in range(3, 2000, 2) if all(p % q for q in range(3, int(p ** 0.5) + 1, 2))]


def _is_prime(n, rounds=40):
    if n < 4 or n % 2 == 0:
        return n in (2, 3)
    if any(n % p == 0 for p in _SMALL):
        return n in _SMALL
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        x = pow(secrets.randbelow(n - 3) + 2, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits):
    while True:
        p = secrets.randbits(bits) | (3 << (bits - 2)) | 1        # 위 두 비트를 세워 곱이 정확히 2*bits 비트가 되게
        if (p - 1) % 65537 and _is_prime(p):
            return p


def keygen(bits=BITS):
    """{'n','e','d'} (16진수 글자). 몇 초에서 수십 초 걸린다."""
    e = 65537
    while True:
        p, q = _prime(bits // 2), _prime(bits // 2)
        if p != q:
            break
    n = p * q
    d = pow(e, -1, (p - 1) * (q - 1))
    return {"n": format(n, "x"), "e": e, "d": format(d, "x")}


def key_id(key):
    return hashlib.sha256(bytes.fromhex(key["n"] if len(key["n"]) % 2 == 0 else "0" + key["n"])).hexdigest()[:16]


def public(key):
    return {"id": key_id(key), "n": key["n"], "e": int(key["e"])}


def _keys(r, size):
    seed = hashlib.sha256(b"moa-ship|kem|" + r.to_bytes(size, "big")).digest()
    return hashlib.sha256(b"enc|" + seed).digest(), hashlib.sha256(b"mac|" + seed).digest()


def _mask(data, k_enc, nonce):
    if not data:
        return b""
    stream = hashlib.shake_256(k_enc + nonce).digest(len(data))
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(len(data), "big")


def seal(pub, data):
    n, e = int(pub["n"], 16), int(pub["e"])
    size = (n.bit_length() + 7) // 8
    r = secrets.randbelow(n - 3) + 2
    k_enc, k_mac = _keys(r, size)
    nonce = secrets.token_bytes(16)
    head = MAGIC + size.to_bytes(2, "big") + pow(r, e, n).to_bytes(size, "big") + nonce
    body = _mask(bytes(data), k_enc, nonce)
    return head + body + hmac.new(k_mac, head + body, hashlib.sha256).digest()


def open_(key, blob):
    n, d = int(key["n"], 16), int(key["d"], 16)
    size = (n.bit_length() + 7) // 8
    if len(blob) < len(MAGIC) + 2 + size + 16 + 32 or blob[:len(MAGIC)] != MAGIC or int.from_bytes(blob[8:10], "big") != size:
        raise ValueError("묶음 모양이 맞지 않아요(다른 열쇠로 잠갔거나 손상됨)")
    at = 10 + size
    r = pow(int.from_bytes(blob[10:at], "big"), d, n)
    k_enc, k_mac = _keys(r, size)
    head, body, tag = blob[:at + 16], blob[at + 16:-32], blob[-32:]
    if not hmac.compare_digest(tag, hmac.new(k_mac, head + body, hashlib.sha256).digest()):
        raise ValueError("묶음을 풀 수 없어요(다른 열쇠로 잠갔거나 내용이 바뀜)")
    return _mask(body, k_enc, head[-16:])
