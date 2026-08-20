"""유튜브 로그인 쿠키 — 세 경로가 같은 것을 씁니다.

**왜 필요한가.** 유튜브는 로그인 없는 요청을 IP 단위로 조입니다. 하루치를
모으고 나면 자막 엔드포인트는 429, 오디오는 **1MB쯤 받고 403** 이 됩니다
(실측: 메타데이터는 멀쩡하고, 256KB 범위 요청도 206 인데, 누적 1MB 를
넘기는 순간 끊깁니다). 클라이언트를 바꾸거나 URL 을 새로 받아 이어받아도
안 됩니다 — 요청 모양의 문제가 아니라 **누가 요청하느냐**의 문제입니다.

로그인 세션을 붙이면 그 상한이 계정 기준으로 올라갑니다. 그래서 쿠키가
차단 대책의 마지막 칸입니다. 앞의 칸은 요청 간격(DELAY_RANGE)과 냉각이고,
그걸로 안 되면 이쪽입니다.

⚠️ **쓰던 계정이 묶일 수 있습니다.** 대량 수집은 유튜브 약관이 곱게 보는
일이 아니고, 로그인 쿠키를 붙이는 순간 그 요청들이 **사람의 계정에 붙습니다.**
가족 계정 말고 이 용도로 만든 계정을 쓰는 편이 낫습니다.

**설정은 두 가지 중 하나입니다** (`config/settings.py`):

  - `youtube_cookies_file` — 브라우저에서 뽑아 둔 Netscape 형식 파일.
    확실하고, 브라우저를 켜 둘 필요도 없습니다.
  - `youtube_cookies_browser` — `chrome` · `safari` · `chrome:Profile 1`.
    편하지만 브라우저가 쿠키를 실제로 들고 있어야 합니다. 크롬은 실행
    중이면 DB 를 잠그기도 하고, 맥에서는 키체인 접근이 막히면 조용히
    빈 꾸러미가 나옵니다 — 그래서 아래에서 **비어 있으면 경고**합니다.

둘 다 비어 있으면 지금까지와 똑같이 **쿠키 없이** 돕니다. 이 파일이 하는
일은 "있으면 쓴다" 까지입니다.
"""

import logging
import shutil
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

# 브라우저에서 읽은 것을 잠깐 들고 있습니다. 영상마다 쿠키 DB 를 다시
# 여는 것은 낭비이고, 크롬이 켜져 있으면 그때마다 잠금과 부딪힙니다.
_CACHE_SEC = 600
_cache: tuple[float, object | None] = (0.0, None)
# 같은 경고를 30초마다 남기면 로그가 그것만으로 찹니다.
_warned = False


def _browser_spec() -> tuple[str, str | None] | None:
    raw = (settings.youtube_cookies_browser or "").strip()
    if not raw:
        return None
    name, _, profile = raw.partition(":")
    return name.strip().lower(), (profile.strip() or None)


def _cookie_file() -> Path | None:
    raw = (settings.youtube_cookies_file or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.exists():
        _warn("[cookies] %s 가 없습니다 — 쿠키 없이 돕니다.", path)
        return None
    return path


def _warn(msg: str, *args) -> None:
    global _warned
    if not _warned:
        logger.warning(msg, *args)
        _warned = True


def enabled() -> bool:
    """쿠키를 쓰기로 되어 있는가. 화면·로그가 "왜 되는지"를 말할 때 씁니다."""
    return bool(_cookie_file() or _browser_spec())


def _js_runtime() -> dict:
    """자바스크립트 런타임. **차단과는 별개의 문제입니다.**

    유튜브는 재생 URL 의 서명을 JS 로 계산하게 해 두었고, yt-dlp 는 런타임이
    없으면 그 계산이 필요 없는 클라이언트로 물러섭니다 — 경고에 적힌 대로
    "일부 포맷이 빠질 수" 있습니다. 기본값은 deno 하나뿐인데 이 기계에는
    node 가 있으므로 그걸 알려 줍니다.

    (403 의 원인은 아니었습니다. 런타임을 붙여도 그대로 막혔습니다 —
    그건 IP 단위 차단이라 쿠키 쪽 일입니다.)
    """
    for name in ("deno", "node", "bun"):
        if shutil.which(name):
            return {"js_runtimes": {name: {}}}
    return {}


def ytdlp_opts() -> dict:
    """yt-dlp 에 얹을 공통 옵션. 쿠키 설정이 없으면 런타임만 나갑니다."""
    opts = _js_runtime()
    path = _cookie_file()
    if path:
        return {**opts, "cookiefile": str(path)}
    spec = _browser_spec()
    if spec:
        name, profile = spec
        # yt-dlp 는 (브라우저, 프로필, 키링, 컨테이너) 네 칸을 받습니다.
        return {**opts, "cookiesfrombrowser": (name, profile, None, None)}
    return opts


def jar():
    """`requests` 세션에 얹을 쿠키 꾸러미. 없으면 None.

    자막 1차 경로(youtube-transcript-api)는 yt-dlp 가 아니라 requests 를
    씁니다. **세 경로가 같은 쿠키를 봐야** 합니다 — 하나만 로그인 상태면
    어느 경로가 왜 되는지 설명할 수 없습니다.
    """
    global _cache
    now = time.time()
    if now - _cache[0] < _CACHE_SEC:
        return _cache[1]

    loaded = None
    path = _cookie_file()
    if path:
        cj = MozillaCookieJar(str(path))
        try:
            cj.load(ignore_discard=True, ignore_expires=True)
            loaded = cj
        except Exception as e:  # noqa: BLE001 — 형식이 깨진 파일도 여기로 옵니다
            _warn("[cookies] %s 를 읽지 못했습니다 (%s) — 쿠키 없이 돕니다.", path, e)
    else:
        spec = _browser_spec()
        if spec:
            name, profile = spec
            try:
                from yt_dlp.cookies import extract_cookies_from_browser

                cj = extract_cookies_from_browser(name, profile) if profile else (
                    extract_cookies_from_browser(name)
                )
                # **비어 있으면 붙은 것이 아닙니다.** 크롬이 실행 중이거나
                # 키체인 접근이 막히면 예외 없이 0개가 나옵니다 — 그대로
                # 두면 "쿠키를 붙였는데 왜 그대로냐" 로 남습니다.
                if not any("youtube.com" in (c.domain or "") for c in cj):
                    _warn(
                        "[cookies] %s 에서 유튜브 쿠키를 찾지 못했습니다 — "
                        "그 브라우저에서 유튜브에 로그인되어 있는지 확인해 주세요.",
                        name,
                    )
                else:
                    loaded = cj
            except Exception as e:  # noqa: BLE001 — 브라우저마다 다른 예외를 냅니다
                _warn("[cookies] %s 쿠키를 읽지 못했습니다 (%s) — 쿠키 없이 돕니다.", name, e)

    _cache = (now, loaded)
    return loaded


__all__ = ["enabled", "jar", "ytdlp_opts"]
