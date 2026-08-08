"""`tistory` CLI 를 부르는 자리.

티스토리 Open API 는 2024년 2월에 끝났습니다. 이 CLI 는 관리 화면이
내부적으로 쓰는 요청을 저장된 로그인 세션으로 직접 부릅니다. **API 키를
찾지 마세요 — 없습니다.**

CLI 계약(실제 소스에서 확인):

  whoami --json   → {"valid": true, ...}          세션 만료면 종료 코드 2
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


def available() -> bool:
    return shutil.which(settings.tistory_bin) is not None


def session_ok() -> bool:
    """로그인 세션이 살아 있는가. CLI 가 없으면 False."""
    if not available():
        return False
    try:
        out = _run(["whoami"], check=False)
    except TistoryError:
        return False
    data = _json(out.stdout)
    return bool(data and data.get("valid"))


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
