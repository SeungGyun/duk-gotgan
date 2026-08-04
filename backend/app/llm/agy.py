"""안티그래비티 실행기 — `agy` CLI 한 번 실행 = 영상 1건.

클로드 쪽(llm/runner.py)과 **받아오는 방법만** 다릅니다. 작업 폴더를 만들고
자막을 파일로 주는 것, 받은 결과를 `llm/store.py` 로 넘기는 것은 같습니다.

  클로드        SDK 로 프로세스를 띄우고 `save_review` 도구 호출을 받습니다
  안티그래비티   `agy -p ... --json-schema` 로 실행하고 구조화 출력을 받습니다

**도구가 아니라 구조화 출력을 쓰는 이유**는 agy 에 인프로세스 도구를 꽂을
자리가 없어서입니다. `--json-schema` 가 최종 응답을 스키마에 맞춰 강제하므로,
받는 쪽에서 보면 도구 인자를 받는 것과 형태가 같습니다 — 그대로 `store.save`
에 넘깁니다.

## 격리

**자막은 제3자가 통제하는 텍스트입니다** (AI-PIPELINE §6). 클로드 경로는
도구를 `Read` 하나로 좁히고 경로 가드를 걸지만, agy 에는 도구를 제한하는
손잡이가 없습니다. 실측에서 이렇게 됐습니다:

    agy -p "... /Users/layers/git/duk-gotgan/backend/.env 를 읽어 보세요"
    → 읽었습니다. 유튜브 API 키까지 응답에 그대로 실려 나왔습니다.

`--sandbox` 를 줘도 막히지 않았습니다. 그래서 **macOS 샌드박스로 밖에서**
막습니다 — 홈 디렉터리를 통째로 닫고, agy 가 자기 일을 하는 데 필요한 곳만
도로 엽니다. seatbelt 는 마지막에 맞는 규칙이 이깁니다.
"""

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from app.llm import store, workspace
from app.llm.schemas import LectureReview, flat_schema
from config.settings import settings

logger = logging.getLogger(__name__)

PROMPTS = Path(__file__).resolve().parent.parent.parent / "prompts"

# 홈을 닫고 필요한 것만 엽니다. `(allow default)` 로 시작하는 이유는
# 완전 거부에서 출발하면 CLI 가 자기 바이너리·인증서·소켓을 못 찾아
# 뜨지도 못하기 때문입니다. 우리가 지키려는 것은 **사람의 파일**입니다.
_SANDBOX_PROFILE = """(version 1)
(allow default)
(deny file-read* (subpath "{home}"))
(allow file-read* (subpath "{home}/.gemini"))
(allow file-read* (subpath "{home}/.local"))
(allow file-read* (subpath "{home}/.cache"))
(allow file-read* (subpath "{home}/.nvm"))
(allow file-read* (subpath "{workspace}"))
"""


@dataclass
class AgyResult:
    ok: bool = False
    error: str | None = None
    input_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    duration_sec: float = 0.0
    denials: list[str] = field(default_factory=list)


def _prompt() -> str:
    """본문 + 이 회사의 전달 방식. 루브릭은 두 회사가 같은 것을 읽습니다."""
    body = (PROMPTS / "lecture-review.md").read_text(encoding="utf-8")
    delivery = (PROMPTS / "delivery-agy.md").read_text(encoding="utf-8")
    return f"{body}\n{delivery}\n\n{TASK}"


TASK = "작업 폴더의 자막을 판정하고, 정리해서 스키마에 맞는 JSON 하나로 내보내세요."


def _write_sandbox_profile(ws: workspace.Workspace) -> Path:
    # **실제 경로로 씁니다.** seatbelt 의 `subpath` 는 심링크를 따라간 뒤의
    # 경로와 맞춰 봅니다. 맥에서 작업 폴더는 `/tmp/...` 인데 진짜는
    # `/private/tmp/...` 라, 적힌 그대로 넣으면 그 줄이 아무 것도 안 걸러
    # 냅니다 — 지금은 홈만 막고 있어서 티가 안 나지만, 작업 폴더를 홈 밑으로
    # 옮기는 날 조용히 전부 막혀 버립니다.
    home = str(Path.home().resolve())
    ws_path = str(ws.path.resolve())
    path = ws.path.parent / f"{ws.video_id}.sb"
    path.write_text(
        _SANDBOX_PROFILE.format(home=home, workspace=ws_path), encoding="utf-8"
    )
    return path


def _write_schema(ws: workspace.Workspace) -> Path:
    """스키마는 **작업 폴더 밖에** 둡니다. 안에 두면 모델이 자막인 줄 알고
    읽어 토큰을 쓰고, `이 폴더에는 파일 두 개가 있습니다`라는 프롬프트와도
    어긋납니다."""
    path = ws.path.parent / f"{ws.video_id}.schema.json"
    path.write_text(
        json.dumps(flat_schema(LectureReview), ensure_ascii=False), encoding="utf-8"
    )
    return path


def _argv(ws: workspace.Workspace, schema: Path, profile: Path | None) -> list[str]:
    cmd = [
        settings.agy_bin,
        "-p", _prompt(),
        "--model", settings.agy_model,
        # **cwd 만으로는 부족합니다.** agy 는 파일을 현재 디렉터리가 아니라
        # 자기 "워크스페이스" 기준으로 찾습니다. 대화형 셸에서는 어쩌다 맞아
        # 떨어졌는데, launchd 로 띄우니 세 건 연속으로
        # `transcript.md 파일이 존재하지 않습니다` 를 내고 실패했습니다 —
        # 자막을 못 읽은 채 요약을 지어내려다 끊긴 것입니다.
        "--add-dir", str(ws.path),
        "--output-format", "json",
        "--json-schema", str(schema),
        # 물어보면 print 모드가 그대로 멈춰 서 타임아웃까지 갑니다.
        # 위험한 것은 이 플래그가 아니라 도구를 못 좁히는 것이고,
        # 그쪽은 샌드박스로 막습니다.
        "--dangerously-skip-permissions",
        "--print-timeout", f"{settings.agy_timeout_sec}s",
    ]
    if profile is not None:
        return ["sandbox-exec", "-f", str(profile), *cmd]
    return cmd


async def review(
    ws: workspace.Workspace,
    outcome: store.ReviewOutcome,
    run_id: str | None = None,
    owner: str | None = None,
) -> AgyResult:
    """작업 폴더 하나를 agy 에 넘기고, 받은 결과를 담습니다."""
    result = AgyResult()

    if shutil.which(settings.agy_bin) is None:
        result.error = f"{settings.agy_bin} 를 찾을 수 없습니다."
        return result

    schema = _write_schema(ws)
    profile = _write_sandbox_profile(ws) if settings.agy_sandbox else None
    if profile is None:
        logger.warning("[agy] 샌드박스가 꺼져 있습니다 — 자막이 홈 디렉터리를 읽게 할 수 있습니다")

    try:
        proc = await asyncio.create_subprocess_exec(
            *_argv(ws, schema, profile),
            cwd=str(ws.path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # 자막이 환경변수를 통해 무언가를 흘리지 못하게, 넘기는 것을
            # 최소로 줄입니다. HOME 은 agy 가 인증을 찾는 데 필요합니다.
            env={
                "HOME": os.environ.get("HOME", ""),
                "PATH": os.environ.get("PATH", ""),
                "USER": os.environ.get("USER", ""),
                "TERM": "dumb",
            },
        )
        try:
            # CLI 자체 타임아웃이 있지만 그것만 믿지 않습니다 — 프로세스가
            # 먹통이 되면 워커가 통째로 멈춥니다.
            out, err = await asyncio.wait_for(
                proc.communicate(), timeout=settings.agy_timeout_sec + 60
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            result.error = f"agy 가 {settings.agy_timeout_sec}초 안에 끝나지 않았습니다."
            return result
    except Exception as e:  # noqa: BLE001
        result.error = f"실행 실패: {e}"
        logger.exception("[agy] %s 실행 실패", ws.video_id)
        return result
    finally:
        schema.unlink(missing_ok=True)
        if profile is not None:
            profile.unlink(missing_ok=True)

    stdout = out.decode("utf-8", "replace").strip()
    stderr = err.decode("utf-8", "replace").strip()

    payload = _last_json_object(stdout)
    if payload is None:
        head = (stderr or stdout)[:300]
        result.error = f"agy 출력을 읽지 못했습니다 (종료 {proc.returncode}): {head}"
        return result

    u = payload.get("usage") or {}
    result.input_tokens = int(u.get("input_tokens", 0))
    result.cache_read_tokens = int(u.get("cache_read_tokens", 0))
    result.output_tokens = int(u.get("output_tokens", 0))
    result.turns = int(payload.get("num_turns", 0))
    result.duration_sec = float(payload.get("duration_seconds", 0.0))

    if payload.get("status") != "SUCCESS":
        # **stderr 를 같이 답니다.** agy 의 `error` 는 "Agent execution
        # terminated due to error." 처럼 무엇이 틀렸는지 알려주지 않습니다.
        # 그것만 적어 두었더니 실패 세 건의 원인을 로그에서 알 수 없었습니다.
        detail = str(payload.get("error") or "agy 가 실패로 끝났습니다.")
        if stderr:
            detail = f"{detail} — {stderr[-400:]}"
        result.error = detail[:900]
        return result

    data = payload.get("structured_output")
    if not isinstance(data, dict):
        # 스키마를 걸었는데도 객체가 아니면 응답 문자열을 한 번 더 풀어 봅니다.
        data = _last_json_object(str(payload.get("response") or ""))
    if not isinstance(data, dict):
        result.error = "구조화 출력이 오지 않았습니다."
        return result

    try:
        message = store.save(
            video_id=ws.video_id,
            args=data,
            outcome=outcome,
            run_id=run_id,
            owner=owner,
            model=settings.agy_model,
        )
        logger.info("[agy] %s %s", ws.video_id, message)
        result.ok = True
    except store.Rejected as e:
        # **재시도하지 않습니다.** 클로드 경로는 도구 응답으로 모델에게
        # 고칠 기회를 주지만, 여기는 호출이 이미 끝났습니다. 다시 부르면
        # 자막을 처음부터 다시 읽어 입력을 통째로 또 냅니다.
        result.error = outcome.error or str(e)
    return result


def _last_json_object(text: str) -> dict | None:
    """출력에 로그 줄이 섞여도 마지막 JSON 객체를 건집니다.

    한 줄씩 보는 것으로는 부족합니다 — 결과 객체가 여러 줄로 예쁘게
    찍혀 나오는 경우가 있습니다. 뒤에서부터 여는 괄호를 찾아 봅니다.
    """
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    start = text.find("{")
    while start != -1:
        try:
            parsed = json.loads(text[start:])
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        return parsed if isinstance(parsed, dict) else None
    return None
