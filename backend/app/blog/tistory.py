"""`tistory` CLI 를 부르는 자리.

티스토리 Open API 는 2024년 2월에 끝났습니다. 이 CLI 는 관리 화면이
내부적으로 쓰는 요청을 저장된 로그인 세션으로 직접 부릅니다. **API 키를
찾지 마세요 — 없습니다.**

CLI 계약(실제 소스에서 확인):

  whoami --json   → {"valid": true, ...}          세션 만료면 종료 코드 2
  login --headless→ {"ok": true, "manual": false} 창을 안 띄웁니다
  post new --json → {"id": "184", "entryUrl": …}  사람이 읽는 로그는 stderr
  post list --json→ {"total": n, "items": [{id, title, …}]}

**stdout 은 JSON 전용입니다.** 진행 로그는 전부 stderr 로 나가므로 섞이지
않습니다 — 그래서 출력을 그대로 `json.loads` 할 수 있습니다.
"""

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass

from config.settings import settings

logger = logging.getLogger(__name__)


# 티스토리가 **하루에 받아 주는 공개 발행은 30편**입니다. 넘기면 글마다
# 403 이 오는데, 사유는 그 글이 아니라 그날 이미 쓴 횟수입니다.
DAILY_CAP_MARK = "하루에 새롭게 공개 발행할 수 있는 글은"


class TistoryError(Exception):
    """CLI 가 일을 못 했습니다."""

    def __init__(self, message: str, *, session: bool = False, daily_cap: bool = False):
        super().__init__(message)
        # 세션 만료는 **사람이 브라우저에서 카카오 로그인을 해야** 풀립니다.
        # 재시도 횟수를 깎을 이유가 없어서 따로 표시합니다.
        self.session = session
        # 하루 상한은 **자정에 저절로 풀립니다.** 이것도 그 글의 잘못이
        # 아니라, 세 번 세어 접어 버리면 멀쩡한 글이 영구 제외됩니다.
        self.daily_cap = daily_cap


@dataclass(frozen=True)
class PostRef:
    post_id: str | None
    url: str | None


# 세션 확인 결과. **"안 된다"를 둘로 가릅니다.**
#
# 예전에는 bool 하나였습니다 — `whoami` 가 무슨 이유로 실패하든 "세션 만료"
# 였습니다. 그래서 네트워크가 한 번 튄 것도 만료로 세고 두 시간을 잤습니다:
# 8월 9일 00:01 에 만료라고 적었는데, 아무도 로그인하지 않은 02:01 에 발행이
# 그대로 이어졌습니다. 그 두 시간은 통째로 버린 시간이고, 로그에는 사람더러
# 로그인하라는 거짓 안내만 남았습니다.
OK = "ok"
# 종료 코드 2 — 새 세션을 받아 와야 합니다. CLI 의 `SessionExpiredError` 와
# `whoami` 의 `valid: false` 가 둘 다 이 코드입니다.
EXPIRED = "expired"
# CLI 가 대답을 못 했습니다. **세션 탓이라고 단정하지 않습니다.**
UNKNOWN = "unknown"


@dataclass(frozen=True)
class SessionCheck:
    state: str
    # 왜 아닌지. 로그에 그대로 실어 보냅니다 — 예전에는 이걸 버려서, 무엇이
    # 막혔는지 알 길이 로그를 열어 봐도 없었습니다.
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == OK


def available() -> bool:
    return shutil.which(settings.tistory_bin) is not None


def check_session() -> SessionCheck:
    """로그인 세션이 살아 있는가. **왜 아닌지까지 들고 옵니다.**"""
    if not available():
        return SessionCheck(UNKNOWN, f"{settings.tistory_bin} 를 찾을 수 없습니다.")
    try:
        out = _run(["whoami"], check=False)
    except TistoryError as e:
        return SessionCheck(UNKNOWN, str(e))
    data = _json(out.stdout)
    if data and data.get("valid"):
        return SessionCheck(OK)
    detail = (out.stderr or out.stdout or "").strip()[-400:]
    if out.returncode == 2:
        return SessionCheck(EXPIRED, detail)
    return SessionCheck(UNKNOWN, detail or f"whoami 가 {out.returncode} 로 끝났습니다.")


def login() -> bool:
    """창 없이 세션을 되살려 봅니다. 됐으면 True.

    **대개 됩니다.** 티스토리 세션 쿠키(TSSESSION)는 만료시각이 없는 세션
    쿠키라 서버가 사나흘이면 끊는데, 같이 저장된 카카오 SSO 쿠키(`_kau`)는
    1년을 삽니다 — 살아 있는 카카오 세션이 새 티스토리 세션을 그냥 받아
    옵니다. 비밀번호도, 사람도 필요 없습니다(실측 5.5초).

    캡차·기기인증·2FA 처럼 정말 사람이 해야 하는 단계에서는 `--headless` 가
    기다리지 않고 곧바로 실패합니다. 창이 없으니 기다릴 이유가 없습니다.
    """
    try:
        out = _run(["login", "--headless"])
    except TistoryError as e:
        logger.warning("[blog] 세션을 되살리지 못했습니다 — %s", e)
        return False
    data = _json(out.stdout) or {}
    logger.info("[blog] 세션을 되살렸습니다 (쿠키 %s개).", data.get("cookies", "?"))
    return True


def publish(md_path: str, category: str, visibility: str) -> PostRef:
    """마크다운 파일 하나를 글로 만듭니다.

    **`--create-category` 를 같이 줍니다.** 곳간 키워드가 블로그 카테고리라,
    새 키워드로 모은 첫 강의는 카테고리가 아직 없습니다.

    **공개 발행에는 `-y` 가 필수입니다.** CLI 는 되돌리기 번거로운 일이라
    한 번 더 묻는데, 워커는 터미널이 아니라서 물으면 그대로 실패합니다.
    """
    args = ["post", "new", md_path, "--category", category, "--create-category"]
    if visibility == "public":
        args += ["--publish", "-y"]
    elif visibility == "protected":
        args += ["--visibility", "protected", "-y"]
    # private 은 기본값입니다 — 아무것도 붙이지 않습니다.

    out = _run(args)
    data = _json(out.stdout) or {}
    ref = PostRef(post_id=_str_or_none(data.get("id")), url=_str_or_none(data.get("entryUrl")))
    if ref.post_id is None and ref.url is None:
        # 저장은 됐는데 결과를 못 읽은 경우입니다. 올린 것을 못 올렸다고
        # 적으면 다음 차례에 같은 글을 또 올립니다 — 오류로 올려서, 재시도
        # 전에 제목으로 찾아보게 합니다 (publish.py).
        raise TistoryError(f"글은 보냈는데 결과를 읽지 못했습니다: {out.stdout[:200]}")
    return ref


def find_by_title(title: str) -> PostRef | None:
    """제목이 정확히 같은 글을 찾습니다. **중복 발행을 막는 마지막 확인입니다.**

    CLI 가 글을 만들었는데 우리가 결과를 못 받는 경우가 있습니다(타임아웃,
    프로세스 사망). 그때 그냥 재시도하면 같은 글이 두 번 올라가고, 공개
    글이라 사람이 하나씩 내려야 합니다.
    """
    try:
        out = _run(["post", "list", "--keyword", title, "--visibility", "all"])
    except TistoryError as e:
        logger.warning("[blog] 제목으로 찾기 실패 — %s", e)
        return None
    data = _json(out.stdout) or {}
    for item in data.get("items") or []:
        if str(item.get("title", "")).strip() == title.strip():
            return PostRef(
                post_id=_str_or_none(item.get("id")),
                url=_str_or_none(item.get("permalink")),
            )
    return None


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    if not available():
        raise TistoryError(f"{settings.tistory_bin} 를 찾을 수 없습니다.")

    cmd = [settings.tistory_bin]
    if settings.tistory_blog:
        cmd += ["--blog", settings.tistory_blog]
    cmd += [*args, "--json"]

    try:
        # **환경변수를 그대로 넘깁니다.** CLI 가 세션을 태우려고 크로미움을
        # 띄우고 키체인을 읽습니다 — 요약 쪽처럼 최소 환경으로 줄이면 뜨지
        # 않습니다. 여기 넘어가는 파일은 우리가 만든 마크다운이고, CLI 가
        # 그것을 명령으로 해석하지 않습니다.
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.blog_cli_timeout_sec,
            # 종료 코드는 아래에서 직접 봅니다 — 세션 만료(2)와 나머지를
            # 갈라야 해서, 예외로 올려 버리면 그 구분을 잃습니다.
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise TistoryError(f"{settings.blog_cli_timeout_sec}초 안에 끝나지 않았습니다.") from e
    except Exception as e:  # noqa: BLE001
        raise TistoryError(f"실행 실패: {e}") from e

    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-400:]
        # 종료 코드 2 는 whoami 의 "세션 만료" 입니다. 다른 명령도 세션이
        # 없으면 같은 말을 stderr 에 적습니다.
        expired = proc.returncode == 2 or "세션" in detail
        raise TistoryError(
            f"tistory {args[0]} 실패 ({proc.returncode}): {detail}",
            session=expired,
            daily_cap=DAILY_CAP_MARK in detail,
        )
    return proc


def _json(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _str_or_none(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
