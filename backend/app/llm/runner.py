"""헤드리스 실행기 — 영상 1건 = 호출 1회.

**배치로 몰아서 돌립니다.** 프롬프트 캐시가 1시간짜리인데, 실측에서 캐시가
도는 순간 사용량이 18.6배 싸졌습니다(AI-PIPELINE §5.1). 띄엄띄엄 돌리면
매번 캐시 생성값을 다시 냅니다. 그래서 대기 중인 영상을 한 루프에서 연속
처리합니다.

프롬프트·도구 구성은 호출마다 **완전히 같아야** 합니다. 하나라도 바뀌면
캐시 프리픽스가 깨집니다.
"""

import logging
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.collector import queue
from app.db.models import Keyword, PipelineEvent, Transcript, Video, VideoKeyword
from app.llm import agy, pace, usage, workspace
from app.llm.guard import make_path_guard, make_pretool_hook
from app.llm.store import ReviewOutcome
from app.llm.tools import build_server
from config.settings import settings
from config.time import now_kst

logger = logging.getLogger(__name__)

PROMPTS = Path(__file__).resolve().parent.parent.parent / "prompts"

# 일시적 실패로 되돌릴 수 있는 횟수. 자막 쪽과 같은 이유로 같은 값입니다
# (collector/transcript.py `MAX_TRANSCRIPT_RETRY`) — 되살리려다 큐를 맴도는
# 영상을 만들면 뒤의 멀쩡한 것들이 계속 밀립니다. 요약은 한 편에 몇 분씩
# 걸리므로 상한이 없으면 손해가 자막 쪽보다 큽니다.
MAX_REVIEW_RETRY = 5


def _system_prompt() -> str:
    """본문 + 이 회사의 전달 방식.

    본문은 **두 회사가 같은 것을 읽습니다.** 루브릭이 갈라지면 같은 강의가
    어느 워커에 걸리느냐에 따라 다른 점수를 받습니다. 다른 것은 결과를
    어떻게 넘기느냐뿐이라, 그 부분만 파일을 나눠 두었습니다.
    """
    body = (PROMPTS / "lecture-review.md").read_text(encoding="utf-8")
    delivery = (PROMPTS / "delivery-claude.md").read_text(encoding="utf-8")
    return f"{body}\n{delivery}"

# 자막을 다 읽지 않고 끝냈다고 볼 기준. 오버헤드(약 18,700)를 뺀 순수 입력이
# 자막 추정 토큰의 절반 미만이면 조기 종료로 봅니다.
EARLY_EXIT_RATIO = 0.5

# 절대 허용하지 않습니다. 이 작업에 셸·파일쓰기·네트워크가 필요한 지점이
# 없고, 입력(자막)은 제3자가 통제하는 텍스트입니다.
DISALLOWED = [
    "Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch",
    "Task", "Glob", "Grep", "TodoWrite", "ToolSearch",
]


@dataclass
class ReviewRun:
    video_id: str
    title: str
    ok: bool = False
    published: bool = False
    score: int | None = None
    verdict: str | None = None
    # 입력을 셋으로 나눠 봅니다. 합쳐 놓으면 캐시 재사용분이 실제 부담처럼
    # 보여서, 사용량 상한이 엉뚱하게 빨리 찹니다.
    input_new: int = 0  # 이번에 처음 보낸 것 (자막 포함)
    cache_created: int = 0  # 캐시에 새로 올린 것
    cache_read: int = 0  # 캐시에서 다시 읽은 것 — 턴마다 누적됩니다
    output_tokens: int = 0
    cost_usd: float = 0.0
    turns: int = 0
    early_exit: bool = False

    denials: list[str] = field(default_factory=list)
    error: str | None = None
    # 실패가 일시적인가. `None` 이면 `_is_transient` 가 오류 글자를 보고
    # 짐작합니다 — 남이 준 메시지(SDK·CLI stderr)에는 그 방법뿐입니다.
    # **우리가 우리말로 쓴 사유는 반드시 여기에 적어야 합니다.**
    transient: bool | None = None

    @property
    def input_total(self) -> int:
        """모델이 실제로 본 입력의 총량 (표시용)."""
        return self.input_new + self.cache_created + self.cache_read

    @property
    def input_weighted(self) -> int:
        """부담 기준 환산. 캐시 읽기는 1/10, 캐시 쓰기는 1.25배입니다.

        사용량 상한은 이 값으로 셉니다. 총량으로 세면 5턴짜리 호출 하나가
        20만 토큰으로 잡혀 하루 다섯 건이면 상한에 닿습니다 — 실제 부담은
        그 1/5도 안 되는데요.
        """
        return round(self.input_new + self.cache_created * 1.25 + self.cache_read * 0.1)


def _options(
    ws: workspace.Workspace,
    outcome: ReviewOutcome,
    denials: list[str],
    run_id: str | None = None,
    owner: str | None = None,
):
    def note_denial(tool_name: str, reason: str) -> None:
        entry = f"{tool_name}: {reason}"
        if entry not in denials:  # 훅과 콜백이 같은 건을 두 번 기록하지 않게
            denials.append(entry)

    return ClaudeAgentOptions(
        model=settings.review_model,
        effort=settings.review_effort,
        system_prompt=_system_prompt(),
        cwd=str(ws.path),
        # 읽기 하나 + 저장 도구 하나. 나머지는 전부 막습니다.
        allowed_tools=["Read", "mcp__gotgan__save_review"],
        disallowed_tools=DISALLOWED,
        mcp_servers={"gotgan": build_server(ws.video_id, outcome, run_id, owner)},
        # 경로 가드는 **훅으로** 겁니다. can_use_tool 은 allowed_tools 에 통째로
        # 올린 도구를 거치지 않아, 정작 막아야 할 Read 를 그냥 통과시킵니다.
        hooks={"PreToolUse": [make_pretool_hook(ws.path, on_denied=note_denial)]},
        can_use_tool=make_path_guard(ws.path, on_denied=note_denial),
        # 사용자 설정 파일을 읽지 않습니다 — 실행 환경이 사람마다 달라지면
        # 캐시 프리픽스도 갈라지고, 의도치 않은 권한이 붙을 수 있습니다.
        setting_sources=[],
        permission_mode="default",
        max_turns=settings.review_max_turns,
    )


TASK = "작업 폴더의 자막을 판정하고, 통과하면 정리해서 save_review 로 저장하세요."


async def _prompt_stream():
    yield {"type": "user", "message": {"role": "user", "content": TASK}}


def worker_id() -> str:
    """이 프로세스를 가리키는 이름. `videos.claimed_by` 에 들어갑니다.

    **회사 이름이 앞에 옵니다.** 좀비 회수가 이 접두사로 "내 회사가 붙든
    것"을 가리기 때문입니다 (recover_zombies 참고). 컬럼이 64자라 호스트
    이름은 잘라 넣습니다.
    """
    return f"{settings.review_provider}:{socket.gethostname()[:24]}:{os.getpid()}"


async def review_video(
    db: Session, video: Video, run_id: str | None = None, owner: str | None = None
) -> ReviewRun:
    """영상 1건을 판정·요약합니다."""
    run = ReviewRun(video_id=video.id, title=video.title)

    transcript = db.get(Transcript, video.id)
    if transcript is None or not transcript.content:
        run.error = "자막이 없습니다."
        return run

    # **살아 있는 키워드만.** 지운 키워드의 기준 점수가 프롬프트의
    # summary_threshold 로 들어가면, AI 가 "미달"로 보고 요약을 생략해
    # 판정 쪽을 고쳐도 소용이 없습니다. 두 곳이 같은 집합을 봐야 합니다.
    keywords = list(
        db.scalars(
            select(Keyword)
            .join(VideoKeyword, VideoKeyword.keyword_id == Keyword.id)
            .where(VideoKeyword.video_id == video.id, Keyword.archived_at.is_(None))
        ).all()
    )

    ws = workspace.prepare(video, transcript, keywords)

    # **읽기 트랜잭션을 여기서 닫습니다.** 아래 호출이 몇 분씩 걸리는데,
    # 위에서 연 트랜잭션을 그대로 들고 있으면 그동안 `videos` 에 메타데이터
    # 락이 걸립니다. 평소에는 아무 일도 없다가 스키마를 고치는 날
    # `ALTER TABLE` 이 그 뒤에 줄을 서고, MDL 요청은 선착순이라 그 뒤의
    # **모든 조회가 같이 멈춥니다** — 화면이 통째로 굳습니다.
    # 세션이 expire_on_commit=False 라 위에서 읽은 값은 그대로 남습니다.
    db.commit()

    outcome = ReviewOutcome()

    try:
        if settings.review_provider == "antigravity":
            await _via_agy(run, ws, outcome, run_id, owner)
        else:
            await _via_claude(run, ws, outcome, run_id, owner, transcript.est_tokens)
    except Exception as e:  # noqa: BLE001
        run.error = f"실행 실패: {e}"
        logger.exception("[review] %s 실행 실패", video.id)
    finally:
        ws.cleanup()

    if outcome.error:
        run.error = outcome.error

    # 도구를 안 부르고 끝난 경우가 가장 애매합니다 — 모델이 말로만 답하고
    # 끝냈다는 뜻이라, 성공으로 처리하면 조용히 아무것도 안 남습니다.
    if not outcome.called and not run.error:
        run.error = "모델이 결과를 넘기지 않았습니다."

    run.ok = outcome.called and not run.error
    run.published = outcome.published
    run.score = outcome.expert_score
    run.verdict = outcome.verdict
    return run


async def _via_claude(
    run: ReviewRun,
    ws: workspace.Workspace,
    outcome: ReviewOutcome,
    run_id: str | None,
    owner: str | None,
    transcript_tokens: int,
) -> None:
    denials: list[str] = []
    async for message in query(
        # 문자열이 아니라 이터러블로 넘깁니다 — `can_use_tool`(경로 가드)이
        # 스트리밍 모드에서만 동작합니다. 가드를 포기할 수는 없습니다.
        prompt=_prompt_stream(),
        options=_options(ws, outcome, denials, run_id, owner),
    ):
        if isinstance(message, ResultMessage):
            _absorb(run, message, transcript_tokens)
    run.denials = denials


async def _via_agy(
    run: ReviewRun,
    ws: workspace.Workspace,
    outcome: ReviewOutcome,
    run_id: str | None,
    owner: str | None,
) -> None:
    """안티그래비티는 CLI 한 번 실행입니다 (llm/agy.py)."""
    r = await agy.review(ws, outcome, run_id, owner)
    run.input_new = r.input_tokens
    run.cache_read = r.cache_read_tokens
    run.output_tokens = r.output_tokens
    run.turns = r.turns
    # **조기 종료는 재지 않습니다.** 클로드 쪽은 고정 오버헤드가 18,700 으로
    # 안정적이라 "총 입력 - 오버헤드"로 얼마나 읽었는지 잴 수 있는데, agy 는
    # 같은 조건에서 실측이 42k~95k 로 흔들렸습니다. 그 값을 빼서 얻은 수를
    # "읽은 양"이라고 적으면 화면의 절감 지표가 거짓말을 합니다.
    run.early_exit = False
    # 구독 정액이라 청구액이 아니고, agy 는 그 환산치도 주지 않습니다.
    run.cost_usd = 0.0
    if r.error and not outcome.error:
        run.error = r.error
        run.transient = r.transient


def _absorb(run: ReviewRun, msg: ResultMessage, transcript_tokens: int) -> None:
    u = msg.usage or {}
    run.input_new = int(u.get("input_tokens", 0))
    run.cache_created = int(u.get("cache_creation_input_tokens", 0))
    run.cache_read = int(u.get("cache_read_input_tokens", 0))
    run.output_tokens = int(u.get("output_tokens", 0))
    run.cost_usd = float(msg.total_cost_usd or 0.0)
    run.turns = int(msg.num_turns or 0)

    # 자막을 얼마나 읽었는지는 **새로 들어온 입력**으로 봅니다. 캐시 읽기는
    # 턴마다 같은 내용이 다시 세어지므로 "얼마나 읽었나"의 지표가 못 됩니다.
    read = max(0, run.input_new + run.cache_created - settings.overhead_tokens)
    run.early_exit = transcript_tokens > 0 and read < transcript_tokens * EARLY_EXIT_RATIO


# 영상 탓이 아닌 고장들. 다음 사이클에 그대로 다시 시도합니다.
#
# `Claude Code returned an error result` 가 빠져 있어서 60건이 영구 탈락으로
# 쌓였습니다. 받아쓰기가 긴 오디오를 메모리에 올리는 동안 클로드 프로세스가
# 뜨지 못해 죽는 것인데, 영상에는 아무 문제가 없습니다 — 같은 영상을 손으로
# 다시 돌리면 그대로 성공합니다.
# **회사 쪽 사정도 영상 탓이 아닙니다.** 안티그래비티 쿼터가 떨어진 8분
# 사이에 51편이 `Individual quota reached` 를 사유로 영구 탈락했습니다.
# 계정을 바꾸면 그대로 되는 영상들이었습니다 — 같은 실수를 두 번째로
# 한 셈이라, 이번엔 회사 사정(쿼터·한도·인증)을 통째로 묶어 둡니다.
_PROVIDER_DOWN = (
    "quota reached",
    "quota exceeded",
    "resource_exhausted",
    "rate limit",
    "429",
    "too many requests",
    "upgrade your subscription",
    "authentication",
    "not logged in",
    "unauthorized",
    "401",
    "403",
)

_TRANSIENT = (
    *_PROVIDER_DOWN,
    "control request timeout",
    "initialize",
    "timed out",
    "timeout",
    "connection",
    "broken pipe",
    "econnreset",
    "lost connection",
    "returned an error result",
    "process exited",
    "cannot allocate",
    "out of memory",
    # agy 의 catch-all. 뒤에는 서버가 준 `INVALID_ARGUMENT (code 400)` 이
    # 들어 있는데, **자막 크기와 상관이 없습니다** — 성공한 것과 실패한 것의
    # 크기 분포가 겹칩니다(중앙값 5,342 vs 5,631). 같은 영상을 다시 돌리면
    # 그대로 됩니다. 클로드의 `returned an error result` 와 같은 성격이라
    # 같은 방식으로 둡니다. 이걸 탈락으로 적었더니 12분에 28편이 날아갔습니다.
    "agent execution terminated",
)


def _is_transient(error: str | None, declared: bool | None = None) -> bool:
    """일시적 실패인가. **적어 둔 것이 있으면 그것을 믿습니다.**

    글자 맞히기는 남이 준 메시지에만 씁니다. 우리가 만든 사유는 우리말이라
    영어 시그니처에 걸리지 않습니다 — `agy 가 900초 안에 끝나지 않았습니다`
    가 그래서 영구 탈락으로 적혔고, 그렇게 15편이 다시 시도되지 않았습니다.
    """
    if declared is not None:
        return declared
    low = (error or "").lower()
    return any(sig in low for sig in _TRANSIENT)


def _retries(db: Session, video_id: str) -> int:
    """이 영상을 일시적 실패로 몇 번 되돌렸나.

    **이력에서 셉니다.** 자막 쪽과 같은 방식입니다
    (collector/transcript.py `_retries`) — 컬럼을 더할 만한 값이 아니고,
    `pipeline_events` 는 이미 영상별 이력을 들고 있습니다. 되돌릴 때만
    `REVIEWING → TRANSCRIBED` 이벤트를 남기므로 그것만 세면 됩니다.
    """
    return int(
        db.scalar(
            select(func.count())
            .select_from(PipelineEvent)
            .where(
                PipelineEvent.video_id == video_id,
                PipelineEvent.stage == "review",
                PipelineEvent.from_state == "REVIEWING",
                PipelineEvent.to_state == "TRANSCRIBED",
            )
        )
        or 0
    )


def _provider_down(error: str | None) -> str | None:
    """회사 쪽이 안 받아 주는 상태인가. 맞으면 그 사유를 돌려줍니다.

    **이건 다음 영상이라고 나아지지 않습니다.** 쿼터가 떨어졌는데 계속
    돌면 대기 줄을 그대로 훑으며 전부 실패로 만듭니다. 실제로 8분 동안
    51편이 그렇게 지나갔습니다 — 사이클을 즉시 접어야 합니다.
    """
    low = (error or "").lower()
    return next((sig for sig in _PROVIDER_DOWN if sig in low), None)


def _streak_key(error: str | None) -> str:
    """오류를 묶어 세기 위한 열쇠.

    **원문을 그대로 비교하면 안 됩니다.** 연속 실패를 세는 장치가 있었는데도
    51편이 지나간 이유가 이것입니다 — 사유에 매번 다른 재시도 시각과 stderr
    꼬리가 붙어 있어서, 같은 쿼터 오류인데도 매번 "다른 오류"로 세어져
    연속 카운트가 1 을 넘지 못했습니다.

    숫자를 지우고 앞부분만 봅니다. 영상마다 다른 실패(자막 없음 등)는
    여전히 서로 다른 열쇠가 됩니다.
    """
    low = (error or "").lower()
    down = _provider_down(low)
    if down:
        return down
    return re.sub(r"\d+", "#", low)[:80]


# 후보를 처리 상한보다 넉넉히 뽑습니다. 남이 먼저 집은 것은 건너뛰므로,
# 딱 맞게 뽑으면 경합이 있는 날에 상한보다 적게 처리합니다.
CANDIDATE_FACTOR = 3


async def review_pending(
    db: Session, limit: int = 10, run_id: str | None = None
) -> list[ReviewRun]:
    """검토 대기 중인 영상을 연속으로 처리합니다.

    **한 건씩 집습니다.** 예전에는 처리할 목록을 통째로 받아 순회했는데,
    `next_ids` 가 잠금 없는 SELECT 라 요약 워커를 둘 띄우면 같은 목록을
    받아 같은 영상을 두 번 요약했습니다. 이제 조건부 UPDATE 로 하나씩
    집고, 진 쪽은 조용히 다음 후보로 넘어갑니다 (queue.claim 참고).
    """
    owner = worker_id()

    # 자막과 같은 이유로 키워드끼리 번갈아 집습니다 (queue.py 참고).
    candidates = queue.next_ids(db, "TRANSCRIBED", limit * CANDIDATE_FACTOR)

    # **같은 오류가 이어지면 멈춥니다.**
    #
    # `name 'threshold' is not defined` 가 여섯 시간 동안 매 사이클 열 건씩
    # 실패하며 토큰만 태웠습니다. 코드 버그는 다음 영상이라고 나아지지
    # 않습니다 — 같은 사유로 세 번 연속 실패하면 그 사이클은 접습니다.
    # 자막 없음처럼 영상마다 다른 실패는 여기 걸리지 않습니다.
    STOP_AFTER = 3
    streak: tuple[str, int] = ("", 0)

    runs: list[ReviewRun] = []
    taken = 0
    for video_id in candidates:
        if taken >= limit:
            break

        video = db.get(Video, video_id)
        if video is None:
            continue

        # **사용량은 집기 전에 봅니다.** 집고 나서 확인하면 상한에 닿은
        # 순간의 한 건이 REVIEWING 으로 갇혀, 회수될 때까지 아무도 못 만집니다.
        try:
            usage.check(db)
        except usage.UsageExceeded as e:
            # 사이클 도중에 창이 찰 수 있습니다. **여기서는 그냥 나갑니다** —
            # 다음 틱에 `review_due` 가 장부를 다시 보고 판단합니다. 여기서
            # 시각을 적어 두면, 그 사이에 상한을 올려도 적어 둔 시각이 남아
            # 여유가 생겼는데도 계속 놉니다 (llm/pace.py).
            logger.info("[review] 상한을 넘어 이번 사이클은 여기까지 — %s", e)
            runs.append(ReviewRun(video_id=video.id, title=video.title, error=str(e)))
            break

        # 여기서 죽으면 REVIEWING 으로 남고, **같은 회사의** 좀비 회수 잡이
        # 30분 뒤 TRANSCRIBED 로 되돌립니다.
        if not queue.claim(
            db, video.id, from_state="TRANSCRIBED", to_state="REVIEWING", owner=owner
        ):
            logger.info("[review] %s 는 다른 워커가 먼저 집었습니다 — 건너뜁니다", video.id)
            continue
        # 세션이 expire_on_commit=False 라 커밋해도 ORM 객체는 옛 값을 쥐고
        # 있습니다. 방금 세운 상태를 코드가 그대로 읽어야 합니다.
        db.refresh(video)
        taken += 1

        run = await review_video(db, video, run_id, owner=owner)
        runs.append(run)

        if run.ok:
            streak = ("", 0)
            # **누적을 지웁니다.** 안 지우면 어제 몇 번 막혔던 것 때문에
            # 오늘 첫 실패가 곧바로 30분짜리 휴식이 됩니다.
            pace.clear(db, settings.review_provider)
        else:
            same = _streak_key(run.error)
            streak = (same, streak[1] + 1) if same == streak[0] else (same, 1)

        if not run.ok:
            # **일시적 고장은 탈락이 아닙니다.** 받아쓰기가 GPU 를 붙들고 있는
            # 동안 SDK 가 시작 시간을 못 맞춰 `Control request timeout:
            # initialize` 로 죽는 일이 밤새 18번 있었습니다. 이걸 FAILED_REVIEW
            # 로 적었더니 그 영상들은 두 번 다시 검토되지 않았습니다 —
            # 영상에는 아무 문제가 없는데도요.
            transient = _is_transient(run.error, run.transient)
            # **끝없이 다시 보지는 않습니다.** 다섯 번을 미룬 영상은 일시적
            # 고장이 아니라 그 영상의 문제로 봅니다 — 상한이 없으면 안 되는
            # 한 편이 큐를 맴돌며 뒤의 멀쩡한 것들을 계속 밀어냅니다.
            tried = _retries(db, video.id) + 1 if transient else 0
            if transient and tried >= MAX_REVIEW_RETRY:
                transient = False
                run.error = f"{MAX_REVIEW_RETRY}번 시도했습니다 — {run.error}"
            to_state = "TRANSCRIBED" if transient else "FAILED_REVIEW"
            if transient:
                logger.warning(
                    "[review] 일시적 실패 %d/%d — 다음 사이클에 다시 봅니다: %s",
                    tried, MAX_REVIEW_RETRY, run.error,
                )
            # **놓기도 조건부입니다.** 이 건이 오래 걸려 회수당하고 다른
            # 워커가 이어받았을 수 있습니다. 그때 조건 없이 쓰면 지금 잘
            # 돌고 있는 남의 작업을 실패로 덮어씁니다.
            mine = queue.release(
                db,
                video.id,
                owner=owner,
                to_state=to_state,
                reason=None if transient else run.error,
            )
            # **회사가 안 받아 주면 그 자리에서 접습니다.** 쿼터가 떨어진
            # 상태로 계속 돌면 대기 줄을 그대로 훑으며 전부 실패로 만듭니다.
            # 위에서 이미 TRANSCRIBED 로 되돌렸으니(회사 사정은 전부
            # `_TRANSIENT`) 그대로 나가면 다음 사이클에 다시 봅니다.
            down = _provider_down(run.error)
            if down:
                # **언제 풀릴지 모릅니다.** 쿼터가 떨어진 상대에게 1분마다
                # 두드리는 것은 풀리는 데 도움이 안 되고, 상대가 더 세게
                # 막는 빌미가 됩니다 — 쉬는 시간을 한 칸씩 늘립니다.
                pace.back_off(db, settings.review_provider, f"{down} · {run.error}"[:200])
                break

            if mine:
                db.add(
                    PipelineEvent(
                        video_id=video.id,
                        run_id=run_id,
                        from_state="REVIEWING",
                        to_state=to_state,
                        stage="review",
                        ok=False,
                        detail={"error": run.error, "denials": run.denials},
                    )
                )
                db.commit()

        if streak[1] >= STOP_AFTER:
            logger.error(
                "[review] 같은 오류로 %d회 연속 실패 — 이번 사이클은 접습니다: %s",
                streak[1], streak[0],
            )
            usage.record(
                db,
                input_tokens=run.input_weighted,
                output_tokens=run.output_tokens,
                cost_usd=run.cost_usd,
            )
            break

        usage.record(
            db,
            input_tokens=run.input_weighted,
            output_tokens=run.output_tokens,
            cost_usd=run.cost_usd,
            early_exit=run.early_exit,
            saved_input_tokens=int((db.get(Transcript, video.id).est_tokens or 0) * 0.5)
            if run.early_exit
            else 0,
        )

    return runs


# 남이 붙든 것을 뺏기까지 기다리는 시간. 요약 한 건이 여섯 시간이면 그
# 프로세스는 죽은 것입니다. 이 유예가 없으면, 회사를 하나 내렸을 때 그
# 회사가 붙들고 있던 영상이 영영 REVIEWING 에 갇힙니다.
ORPHAN_HOURS = 6


def recover_zombies(db: Session, minutes: int = 30, orphan_hours: int = ORPHAN_HOURS) -> int:
    """워커가 죽어 REVIEWING 에 갇힌 영상을 되돌립니다.

    이게 없으면 어느 날 조용히 아무것도 처리되지 않습니다.

    **남이 붙든 것은 함부로 회수하지 않습니다.** 요약 워커가 둘이 되면서
    생긴 규칙입니다. 예전에는 30분 넘은 REVIEWING 을 누가 쥐고 있든 되돌렸는데,
    그러면 다른 회사가 40분째 돌고 있는 영상을 회수해 같은 영상을 두 번
    요약합니다 — 그리고 나중에 끝난 쪽이 앞의 결과를 덮습니다.

    내 회사가 붙든 것은 30분이면 확실히 죽은 것입니다. 같은 회사의 요약
    잡은 락으로 직렬화되어 있어(jobs.REVIEW_LOCK), 회수가 도는 동안 그
    회사의 요약이 진행 중일 수 없기 때문입니다.
    """
    from datetime import timedelta

    now = now_kst()
    cutoff = now - timedelta(minutes=minutes)
    orphan_cutoff = now - timedelta(hours=orphan_hours)
    # `claimed_at` 이 없는 것은 이 컬럼이 생기기 전에 갇힌 행입니다.
    held_since = func.coalesce(Video.claimed_at, Video.updated_at)

    # 받아쓰기 트랙은 소비자가 하나뿐이라 예전 그대로 봅니다. 받아쓰기
    # 도중에 워커가 죽으면 TRANSCRIBING 에 갇혀 영영 처리되지 않습니다.
    stuck = list(
        db.scalars(
            select(Video).where(Video.state == "TRANSCRIBING", Video.updated_at < cutoff)
        ).all()
    )
    stuck += list(
        db.scalars(
            select(Video).where(
                Video.state == "REVIEWING",
                or_(
                    # 내 회사가 붙든 것 — 30분이면 죽은 것입니다.
                    and_(
                        Video.claimed_by.like(f"{settings.review_provider}:%"),
                        held_since < cutoff,
                    ),
                    # 임자가 없거나(옛 행) 남이 쥔 채 오래된 것.
                    and_(Video.claimed_by.is_(None), held_since < cutoff),
                    held_since < orphan_cutoff,
                ),
            )
        ).all()
    )
    # **되돌릴 자리가 다릅니다.** 검토 중이던 것은 자막이 이미 있으니
    # TRANSCRIBED 로, 받아쓰기 중이던 것은 자막이 없으니 대기로 돌립니다.
    # 한꺼번에 TRANSCRIBED 로 밀면 자막 없는 영상이 검토로 넘어가 AI 를
    # 자막 없이 부르게 됩니다.
    back = {"REVIEWING": "TRANSCRIBED", "TRANSCRIBING": "TRANSCRIPT_PENDING"}
    for v in stuck:
        v.state = back[v.state]
        v.state_reason = None
        v.claimed_by = None
        v.claimed_at = None
    db.commit()
    if stuck:
        logger.warning("[review] 좀비 %d건 회수", len(stuck))
    return len(stuck)
