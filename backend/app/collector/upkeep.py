"""낡아서 서는 것을 막습니다 — 지금은 `yt-dlp` 하나.

**왜 자동인가.** 유튜브는 스트리밍 URL 서명 방식을 자주 바꾸고, 그때마다
옛 yt-dlp 는 이렇게 죽습니다.

    [youtube] R-1-bY69cBs: Downloading android vr player API JSON   ← 멀쩡
    [info] Downloading 1 format(s): 251                              ← 멀쩡
    ERROR: unable to download video data: HTTP Error 403: Forbidden  ← 여기서만

**메타데이터는 다 읽히고 실제 내려받기만 막힙니다.** 그래서 IP 차단처럼
보입니다. 2026-08-20 에 이걸 IP 문제로 보고 VPN 을 바꿔 가며 쫓다가, 같은
IP 에서 버전만 올려 갈렸습니다 — 6주 낡은 2026.7.4 는 403, 2026.8.19 는
15MB 를 3초에 받았습니다. 그동안 자막 129편이 죽고 오디오 문이 몇 시간씩
냉각에 들어가 있었습니다.

사람이 알아채기 어려운 종류입니다. 증상이 "차단"의 얼굴을 하고 있고,
멀쩡한 IP 를 의심하게 만드니까요.

**올리기만 하고 끝내지 않습니다.** 검증 없는 자동 업그레이드는 "낡아서
서는 것"을 "새 버전이 깨져서 서는 것"으로 바꿀 뿐입니다. 올린 뒤 **실제로
오디오 한 조각을 받아 보고**, 안 되면 되돌립니다. 확인은 우리가 겪은 그
지점에서 합니다 — 메타데이터가 아니라 **바이트를 받아 봅니다.**
"""

import json
import logging
import subprocess
import urllib.request
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import state
from app.db.models import Video
from config.time import now_kst

logger = logging.getLogger(__name__)

PACKAGE = "yt-dlp"

# 마지막으로 확인한 때. 하루 한 번이면 충분합니다 — 유튜브가 깨지는 주기는
# 주 단위이고, PyPI 를 자주 두드릴 이유가 없습니다.
CHECKED_KEY = "upkeep.ytdlp.checked_at"
# 마지막 결과. 화면이 읽어서 "언제 무엇을 했는지" 를 보여 줍니다.
RESULT_KEY = "upkeep.ytdlp.note"
# PyPI 가 말한 최신 버전. **화면이 이걸 봅니다** — 자막이 막혔을 때
# "낡아서인가, 정말 차단인가" 를 가르는 값인데, 화면이 5초마다 PyPI 를
# 두드릴 수는 없습니다. 하루 한 번 확인할 때 적어 둡니다.
LATEST_KEY = "upkeep.ytdlp.latest"

CHECK_EVERY_HOURS = 24

# 검증에 받아 볼 조각. **1MB 를 넘겨야 합니다.**
#
# 처음엔 64KB 로 두었는데, 깨진 버전(2026.7.4)에서도 통과했습니다 — 가짜
# 안전망이었습니다. 실측해 보니 403 은 첫 바이트가 아니라 **1MB 언저리**
# 에서 옵니다.
#
#      64KB 요청 → 64KB 받음
#    1464KB 요청 → HTTP Error 403: Forbidden
#
# `cookies.py` 에 이미 적혀 있던 값이기도 합니다 — "오디오는 1MB 쯤에서
# 403 이 나는데, 그게 로그인 없는 요청에 걸리는 상한입니다".
#
# 2MB 면 그 문턱을 확실히 넘고, 한 번에 1초쯤입니다. 하루 한 번 무는
# 값으로는 쌉니다.
PROBE_BYTES = 2 * 1024 * 1024


def due(db: Session, now=None) -> bool:
    last = state.get_time(db, CHECKED_KEY)
    now = now or now_kst()
    return last is None or now - last >= timedelta(hours=CHECK_EVERY_HOURS)


def installed() -> str | None:
    try:
        import yt_dlp

        return yt_dlp.version.__version__
    except Exception:  # noqa: BLE001 — 없으면 확인할 것도 없습니다
        return None


def latest(timeout: int = 10) -> str | None:
    """PyPI 가 말하는 최신 버전. 못 물어보면 None — **조용히 넘어갑니다.**

    네트워크가 잠깐 안 되는 것으로 파이프라인이 시끄러워지면 안 됩니다.
    """
    try:
        with urllib.request.urlopen(
            f"https://pypi.org/pypi/{PACKAGE}/json", timeout=timeout
        ) as r:
            return json.load(r)["info"]["version"]
    except Exception as e:  # noqa: BLE001
        logger.debug("[upkeep] 최신 버전을 못 물어봤습니다 — %s", e)
        return None


def _same(a: str, b: str) -> bool:
    """두 버전이 같은가. **문자열로 견주면 안 됩니다.**

    설치된 것은 `2026.08.19`, PyPI 는 `2026.8.19` 라고 말합니다 — 같은
    버전인데 글자가 다릅니다(앞의 0). 그대로 두면 **매일** 올릴 것이
    있다고 판단해서, 아무 일도 안 하면서 "올렸습니다" 를 하루 한 번씩
    찍습니다. 그런 알림은 곧 안 읽힙니다.
    """

    def parts(v: str) -> tuple:
        out = []
        for chunk in v.split("."):
            out.append(int(chunk) if chunk.isdigit() else chunk)
        return tuple(out)

    return parts(a) == parts(b)


def _pip(*args: str) -> bool:
    """`uv pip` 으로 갈아 끼웁니다. 워커의 PATH 에 uv 가 있습니다(ops/*.plist)."""
    try:
        r = subprocess.run(
            ["uv", "pip", *args], capture_output=True, text=True, timeout=180
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("[upkeep] uv 를 부르지 못했습니다 — %s", e)
        return False
    if r.returncode != 0:
        logger.warning("[upkeep] uv pip %s 실패 — %s", " ".join(args), r.stderr[-300:])
        return False
    return True


def probe(db: Session) -> str | None:
    """**바이트를 받아 봅니다.** 되면 None, 안 되면 사유.

    `--simulate` 로는 안 됩니다 — 우리가 겪은 고장에서 메타데이터는 멀쩡히
    읽혔습니다. 실제로 막히는 자리는 미디어 URL 이라 거기까지 가 봐야 합니다.

    **줄에 있는 영상으로 확인합니다.** 특정 영상 id 를 코드에 박아 두면 그
    영상이 내려간 날 검증이 통째로 거짓말을 합니다.
    """
    vid = db.scalar(
        select(Video.id)
        .where(Video.state.in_(("TRANSCRIPT_PENDING", "TRANSCRIBED", "PUBLISHED")))
        .order_by(Video.discovered_at.desc())
        .limit(1)
    )
    if vid is None:
        return None  # 확인할 거리가 없으면 통과로 봅니다

    import yt_dlp

    from app.collector import cookies

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "format": "bestaudio",
        "skip_download": True,
        "socket_timeout": 30,
        **cookies.ytdlp_opts(),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
            url = info.get("url") or (info.get("formats") or [{}])[-1].get("url")
            if not url:
                return "미디어 주소를 못 얻었습니다"
            # yt-dlp 의 세션으로 받습니다. 맨몸 요청은 User-Agent 가 달라
            # 엉뚱한 이유로 막힙니다.
            from yt_dlp.networking import Request

            data = y.urlopen(
                Request(url, headers={"Range": f"bytes=0-{PROBE_BYTES - 1}"})
            ).read(PROBE_BYTES)
    except Exception as e:  # noqa: BLE001 — yt-dlp 는 예외를 세분화하지 않습니다
        return " ".join(str(e).split())[:160]
    return None if data else "받은 바이트가 없습니다"


def refresh(db: Session) -> str | None:
    """낡았으면 올리고, 올린 것이 실제로 되는지 보고, 안 되면 되돌립니다.

    한 일이 있으면 그 문장을, 없으면 None 을 돌려줍니다.
    """
    state.set_time(db, CHECKED_KEY, now_kst())

    have, want = installed(), latest()
    if want:
        # 같든 다르든 적어 둡니다. 화면이 "낡았는가" 를 물을 때 쓰는 값이라,
        # 올릴 일이 없는 날에도 최신이 무엇인지는 알고 있어야 합니다.
        state.set_str(db, LATEST_KEY, want)
    if have is None or want is None or _same(have, want):
        return None

    logger.info("[upkeep] %s %s → %s 올립니다", PACKAGE, have, want)
    if not _pip("install", "-U", PACKAGE):
        return None

    why = probe(db)
    if why is None:
        note = f"{PACKAGE} {have} → {want} 올렸습니다"
        logger.info("[upkeep] %s · 오디오 확인 통과", note)
        state.set_str(db, RESULT_KEY, f"{now_kst():%m-%d %H:%M} · {note}")
        return note

    # **되돌립니다.** 낡아서 서는 것을 새 버전이 깨져서 서는 것으로 바꾸면
    # 아무것도 나아지지 않습니다.
    logger.warning("[upkeep] %s %s 가 오디오를 못 받습니다 (%s) — %s 로 되돌립니다",
                   PACKAGE, want, why, have)
    _pip("install", f"{PACKAGE}=={have}")
    note = f"{PACKAGE} {want} 가 오디오를 못 받아 {have} 로 되돌렸습니다 — {why}"
    state.set_str(db, RESULT_KEY, f"{now_kst():%m-%d %H:%M} · {note}")
    return note


def stale(db: Session) -> bool | None:
    """yt-dlp 가 낡았는가. **모르면 None** — 아직 한 번도 확인 못 했다는 뜻입니다.

    화면이 자막 차단의 원인을 좁힐 때 씁니다. 낡았으면 그게 먼저이고,
    최신인데도 막히면 그때가 진짜 IP 문제라 쿠키를 볼 자리입니다.

    **모를 때 "최신입니다" 라고 하지 않습니다.** 확인한 적 없는 것을
    확인했다고 말하면, 그 말을 믿고 엉뚱한 데를 뒤지게 됩니다.
    """
    have, want = installed(), state.get_str(db, LATEST_KEY)
    if have is None or not want:
        return None
    return not _same(have, want)


def last_note(db: Session) -> str | None:
    """마지막으로 한 일. 화면이 읽습니다."""
    return state.get_str(db, RESULT_KEY)
