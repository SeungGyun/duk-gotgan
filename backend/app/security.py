"""네 자리 비밀번호를 다루는 곳.

**왜 네 자리인가.** 집 안 공유기 안에서만 쓰고, 폰으로도 자주 들어옵니다.
거기서 긴 비밀번호는 매번 입력하는 비용만 크고 얻는 것이 적습니다.

**네 자리는 만 가지뿐입니다.** 그대로 두면 자동으로 찍어서 뚫립니다. 그래서
해싱을 세게 거는 것이 아니라 **틀린 횟수를 세는 쪽**으로 막습니다 —
`api/auth.py` 의 `check_pin` 이 다섯 번 틀리면 잠급니다. 짧은 비밀번호를
쓸 수 있게 만드는 것은 해시가 아니라 그 잠금입니다.

그래도 해시는 씁니다. DB 를 통째로 들여다본 사람이 만 가지를 문질러 보면
어차피 찾아내지만, **다른 서비스에서 쓰던 네 자리와 같은지**까지 바로
알려 줄 이유는 없습니다.

라이브러리를 새로 붙이지 않습니다 — `hashlib.scrypt` 가 표준 라이브러리에
있고, 이 용도에는 그것으로 충분합니다.
"""

import hashlib
import hmac
import secrets

# scrypt 매개변수. n=16384 이면 한 번에 50ms 안팎이라, 로그인 한 번의
# 체감에는 걸리지 않으면서 무작정 문지르는 쪽에는 만 번의 벽이 됩니다.
_N = 16384
_R = 8
_P = 1
_DKLEN = 32

PIN_LENGTH = 4


def is_valid_pin(pin: str) -> bool:
    """네 자리 숫자인가. 화면과 API 가 같은 기준을 봐야 합니다."""
    return len(pin) == PIN_LENGTH and pin.isdigit()


def hash_pin(pin: str) -> str:
    """`scrypt$n$r$p$소금$해시` 형태로 돌려줍니다."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pin.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_pin(pin: str, stored: str | None) -> bool:
    """맞으면 True.

    `stored` 가 비어 있으면 **비밀번호를 안 건 사람**입니다. 그건 여기서
    판단하지 않습니다 — 부르는 쪽이 "비밀번호가 없으니 그냥 들여보낸다"를
    결정해야, 이 함수가 실수로 빈 값을 통과시키는 일이 없습니다.
    """
    if not stored:
        return False
    try:
        scheme, n, r, p, salt_hex, want = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(
            pin.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(want) // 2,
        )
    except (ValueError, TypeError):
        # 형태가 깨진 해시는 "틀림" 으로 봅니다. 예외를 밖으로 내보내면
        # 로그인 화면이 500 으로 죽어서 원인이 더 안 보입니다.
        return False
    # 자리마다 비교를 멈추지 않습니다 — 걸리는 시간으로 앞자리를 알아내는
    # 수법을 막습니다.
    return hmac.compare_digest(dk.hex(), want)


def new_token() -> str:
    """쿠키에 들어갈 세션 값. 128비트라 찍어서 맞힐 수 없습니다."""
    return secrets.token_hex(16)


__all__ = ["PIN_LENGTH", "is_valid_pin", "hash_pin", "verify_pin", "new_token"]
