"""헤드리스 실행기 — 영상 1건 = 호출 1회.

**배치로 몰아서 돌립니다.** 프롬프트 캐시가 1시간짜리인데, 실측에서 캐시가
도는 순간 사용량이 18.6배 싸졌습니다(AI-PIPELINE §5.1). 띄엄띄엄 돌리면
매번 캐시 생성값을 다시 냅니다. 그래서 대기 중인 영상을 한 루프에서 연속
처리합니다.

프롬프트·도구 구성은 호출마다 **완전히 같아야** 합니다. 하나라도 바뀌면
캐시 프리픽스가 깨집니다.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector import queue
from app.db.models import Keyword, PipelineEvent, Transcript, Video, VideoKeyword
from app.llm import usage, workspace
from app.llm.guard import make_path_guard, make_pretool_hook
from app.llm.tools import ReviewOutcome, build_server
from config.settings import settings
from config.time import now_kst

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "lecture-review.md"

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


def _options(ws: workspace.Workspace, outcome: ReviewOutcome, denials: list[str]):
    def note_denial(tool_name: str, reason: str) -> None:
        entry = f"{tool_name}: {reason}"
        if entry not in denials:  # 훅과 콜백이 같은 건을 두 번 기록하지 않게
            denials.append(entry)

    return ClaudeAgentOptions(
        model=settings.review_model,
        effort=settings.review_effort,
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        cwd=str(ws.path),
        # 읽기 하나 + 저장 도구 하나. 나머지는 전부 막습니다.
        allowed_tools=["Read", "mcp__gotgan__save_review"],
        disallowed_tools=DISALLOWED,
        mcp_servers={"gotgan": build_server(ws.video_id, outcome)},
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


async def review_video(db: Session, video: Video) -> ReviewRun:
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
    outcome = ReviewOutcome()
    denials: list[str] = []

    try:
        async for message in query(
            # 문자열이 아니라 이터러블로 넘깁니다 — `can_use_tool`(경로 가드)이
            # 스트리밍 모드에서만 동작합니다. 가드를 포기할 수는 없습니다.
            prompt=_prompt_stream(),
            options=_options(ws, outcome, denials),
        ):
            if isinstance(message, ResultMessage):
                _absorb(run, message, transcript.est_tokens)
    except Exception as e:  # noqa: BLE001
        run.error = f"실행 실패: {e}"
        logger.exception("[review] %s 실행 실패", video.id)
    finally:
        ws.cleanup()

    run.denials = denials
    if outcome.error:
        run.error = outcome.error

    # 도구를 안 부르고 끝난 경우가 가장 애매합니다 — 모델이 말로만 답하고
    # 끝냈다는 뜻이라, 성공으로 처리하면 조용히 아무것도 안 남습니다.
    if not outcome.called and not run.error:
        run.error = "모델이 save_review 를 호출하지 않았습니다."

    run.ok = outcome.called and not run.error
    run.published = outcome.published
    run.score = outcome.expert_score
    run.verdict = outcome.verdict
    return run


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
_TRANSIENT = (
    "control request timeout",
    "initialize",
    "timed out",
    "timeout",
    "connection",
    "broken pipe",
    "econnreset",
    "lost connection",
)


def _is_transient(error: str | None) -> bool:
    low = (error or "").lower()
    return any(sig in low for sig in _TRANSIENT)


async def review_pending(db: Session, limit: int = 10) -> list[ReviewRun]:
    """검토 대기 중인 영상을 연속으로 처리합니다."""
    # 자막과 같은 이유로 키워드끼리 번갈아 집습니다 (queue.py 참고).
    videos = [db.get(Video, i) for i in queue.next_ids(db, "TRANSCRIBED", limit)]
    videos = [v for v in videos if v is not None]

    runs: list[ReviewRun] = []
    for video in videos:
        try:
            usage.check(db)
        except usage.UsageExceeded as e:
            logger.warning("[review] %s", e)
            runs.append(ReviewRun(video_id=video.id, title=video.title, error=str(e)))
            break

        # 워커가 잡았음을 먼저 표시합니다. 여기서 죽으면 REVIEWING 으로 남고,
        # 좀비 회수 잡이 30분 뒤 TRANSCRIBED 로 되돌립니다.
        video.state = "REVIEWING"
        video.updated_at = now_kst()
        db.commit()

        run = await review_video(db, video)
        runs.append(run)

        if not run.ok:
            # **일시적 고장은 탈락이 아닙니다.** 받아쓰기가 GPU 를 붙들고 있는
            # 동안 SDK 가 시작 시간을 못 맞춰 `Control request timeout:
            # initialize` 로 죽는 일이 밤새 18번 있었습니다. 이걸 FAILED_REVIEW
            # 로 적었더니 그 영상들은 두 번 다시 검토되지 않았습니다 —
            # 영상에는 아무 문제가 없는데도요.
            transient = _is_transient(run.error)
            video.state = "TRANSCRIBED" if transient else "FAILED_REVIEW"
            video.state_reason = None if transient else run.error
            if transient:
                logger.warning("[review] 일시적 실패 — 다음 사이클에 다시 봅니다: %s", run.error)
            db.add(
                PipelineEvent(
                    video_id=video.id,
                    from_state="REVIEWING",
                    to_state=video.state,
                    stage="review",
                    ok=False,
                    detail={"error": run.error, "denials": run.denials},
                )
            )
            db.commit()

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


def recover_zombies(db: Session, minutes: int = 30) -> int:
    """워커가 죽어 REVIEWING 에 갇힌 영상을 되돌립니다.

    이게 없으면 어느 날 조용히 아무것도 처리되지 않습니다.
    """
    from datetime import timedelta

    cutoff = now_kst() - timedelta(minutes=minutes)
    # 자막도 같이 봅니다. 받아쓰기 도중에 워커가 죽으면 TRANSCRIBING 에
    # 갇혀, 그 영상만 영영 처리되지 않습니다.
    stuck = db.scalars(
        select(Video).where(
            Video.state.in_(("REVIEWING", "TRANSCRIBING")), Video.updated_at < cutoff
        )
    ).all()
    # **되돌릴 자리가 다릅니다.** 검토 중이던 것은 자막이 이미 있으니
    # TRANSCRIBED 로, 받아쓰기 중이던 것은 자막이 없으니 대기로 돌립니다.
    # 한꺼번에 TRANSCRIBED 로 밀면 자막 없는 영상이 검토로 넘어가 AI 를
    # 자막 없이 부르게 됩니다.
    back = {"REVIEWING": "TRANSCRIBED", "TRANSCRIBING": "TRANSCRIPT_PENDING"}
    for v in stuck:
        v.state = back[v.state]
        v.state_reason = None
    db.commit()
    if stuck:
        logger.warning("[review] 좀비 %d건 회수", len(stuck))
    return len(stuck)
