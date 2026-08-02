"""`save_review` — AI 가 결과를 넘기는 유일한 통로.

**구현체가 워커 프로세스 안에 있습니다.** AI 는 정해진 형식의 인자를 보낼 뿐,
셸도 DB 커넥션도 만지지 않습니다. Bash 로 저장 스크립트를 돌리는 방식을 쓰지
않은 이유가 이것입니다 (AI-PIPELINE §2.4).

여기서 하는 일은 저장이 아니라 **판단**입니다.

  검증 → 점수 재계산 → 임계값 판단 → 판정 기록 → (통과 시) 정식 저장소 적재

공개 여부를 AI 가 정하지 않습니다. AI 는 의견을 낼 뿐이고, 그 의견을 받아들일지는
우리 코드가 키워드의 기준으로 가릅니다. 프롬프트가 뚫려도 피해가 "이상한 요약 1건"
에서 멈추는 이유입니다.
"""

import logging
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool
from pydantic import ValidationError
from sqlalchemy import select

from app.db.models import Evaluation, Keyword, Lecture, PipelineEvent, Video, VideoKeyword
from app.db.session import SessionLocal
from app.llm.policy import should_publish
from app.llm.schemas import LectureReview, flat_schema, weighted_score
from config.time import now_kst

logger = logging.getLogger(__name__)

# 모델이 보낸 총점과 우리가 계산한 값이 이보다 벌어지면 기록해 둡니다.
# 프롬프트가 흔들리거나 루브릭을 무시하고 있다는 신호입니다.
SCORE_GAP_ALERT = 10


# 형식 오류 재시도 상한. 실측에서 여덟 번 연속 실패에 입력 토큰 52만이
# 나갔습니다 — 자막을 매번 다시 읽기 때문입니다. 몇 번 안에 못 고치면
# 프롬프트나 스키마 문제이지 모델이 더 시도해서 풀릴 일이 아닙니다.
MAX_FORMAT_RETRY = 3


class ReviewOutcome:
    """호출 1건의 결과. 실행기가 계측에 씁니다."""

    def __init__(self):
        self.called = False
        self.published = False
        self.expert_score: int | None = None
        self.verdict: str | None = None
        self.error: str | None = None
        self.score_gap: int | None = None
        self.format_errors = 0


def build_server(video_id: str, outcome: ReviewOutcome):
    """이 영상 1건 전용 도구 서버를 만듭니다.

    `video_id` 를 클로저에 묶어 둡니다 — 인자로 받으면 자막에 심긴 문장이
    **다른 영상의 판정을 덮어쓰게** 만들 수 있습니다.
    """

    @tool(
        "save_review",
        "강의 판정 결과와 (통과 시) 요약을 저장합니다. 정확히 한 번만 호출하세요.",
        flat_schema(LectureReview),
    )
    async def save_review(args: dict[str, Any]) -> dict[str, Any]:
        if outcome.called:
            return _err("이미 저장했습니다. 다시 호출하지 마세요.")

        try:
            review = LectureReview.model_validate(args)
        except ValidationError as e:
            outcome.format_errors += 1
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:5]
            )
            logger.warning(
                "[save_review] 형식 오류 %d/%d — %s",
                outcome.format_errors, MAX_FORMAT_RETRY, problems,
            )
            if outcome.format_errors >= MAX_FORMAT_RETRY:
                outcome.error = f"형식 오류 {MAX_FORMAT_RETRY}회 — {problems}"
                return _err("형식 오류가 반복됩니다. 더 시도하지 말고 종료하세요.")
            # 무엇이 틀렸는지 알려주면 모델이 고칠 수 있습니다.
            return _err(f"형식이 맞지 않습니다. 고쳐서 다시 호출하세요. — {problems}")

        db = SessionLocal()
        try:
            video = db.get(Video, video_id)
            if video is None:
                return _err("대상 영상을 찾을 수 없습니다.")

            # 총점은 우리가 다시 계산합니다. 공개 여부를 가르는 값이라
            # 모델의 산수를 믿지 않습니다.
            computed = weighted_score(review.criteria)
            gap = abs(computed - review.expert_score)
            outcome.score_gap = gap
            if gap > SCORE_GAP_ALERT:
                logger.warning(
                    "[save_review] %s 점수 불일치 — 모델 %d / 계산 %d",
                    video_id, review.expert_score, computed,
                )

            # **거르지 않습니다.** 요약이 나왔으면 담고, 버릴지는 사람이
            # 제외 버튼으로 정합니다. 판정과 점수는 그 판단을 돕는 근거로
            # 함께 저장합니다.
            passed = should_publish(review.summary is not None)

            ev = Evaluation(
                video_id=video_id,
                model=_model_name(),
                prompt_version=PROMPT_VERSION,
                verdict=review.verdict,
                expert_score=computed,
                confidence=review.confidence,
                topic=review.topic[:300],
                keyword_relevance=review.keyword_relevance,
                criteria=[c.model_dump() for c in review.criteria],
                red_flags=_red_flags(review, gap),
                speaker_credentials=review.speaker_credentials,
            )
            db.add(ev)
            db.flush()

            if passed:
                _publish(db, video, review, computed)
                video.state = "PUBLISHED"
                video.state_reason = None
            else:
                # 요약이 없는 것은 판정 결과가 아니라 **실패**입니다.
                video.state = "FAILED_REVIEW"
                video.state_reason = "요약이 오지 않았습니다."

            db.add(
                PipelineEvent(
                    video_id=video_id,
                    from_state="REVIEWING",
                    to_state=video.state,
                    stage="review",
                    ok=True,
                    detail={
                        "verdict": review.verdict,
                        "score": computed,
                        "topic": review.topic,
                    },
                )
            )
            db.commit()

            outcome.called = True
            outcome.published = passed
            outcome.expert_score = computed
            outcome.verdict = review.verdict

            if passed:
                return _ok(f"담았습니다. 전문성 {computed}점 · {review.verdict}.")
            return _ok("요약이 없어 담지 못했습니다. 재시도하지 마세요.")
        except Exception as e:  # noqa: BLE001 — 도구는 절대 죽으면 안 됩니다
            db.rollback()
            outcome.error = str(e)
            logger.exception("[save_review] 저장 실패")
            return _err("저장 중 오류가 발생했습니다. 재시도하지 마세요.")
        finally:
            db.close()

    return create_sdk_mcp_server(name="gotgan", version="1.0.0", tools=[save_review])


PROMPT_VERSION = "v1"


def _model_name() -> str:
    from config.settings import settings

    return settings.review_model


def _keywords_of(db, video_id: str) -> list[Keyword]:
    """이 영상을 데려온 키워드 중 **살아 있는 것만**.

    지운 키워드가 기준을 쥐고 있으면 안 됩니다. 실제로 그랬습니다 —
    `결제 시스템 설계`(기준 80점)를 삭제했는데, 그 키워드가 데려온 영상들이
    계속 80점 기준으로 판정돼 58점짜리가 탈락했습니다. 활성 키워드 기준은
    45점이었는데도요.

    전부 지워졌으면 기본값으로 봅니다. 영상은 이미 자막까지 받아 뒀으니
    "데려온 키워드가 없다"는 이유로 버리는 것이 더 아깝습니다.
    """
    return list(
        db.scalars(
            select(Keyword)
            .join(VideoKeyword, VideoKeyword.keyword_id == Keyword.id)
            .where(VideoKeyword.video_id == video_id, Keyword.archived_at.is_(None))
        ).all()
    )


def _red_flags(review: LectureReview, gap: int) -> list[str]:
    flags = list(review.red_flags)
    if gap > SCORE_GAP_ALERT:
        flags.append(f"모델이 보고한 총점과 루브릭 계산값이 {gap}점 어긋남")
    if review.keyword_relevance < 40:
        flags.append(f"검색 키워드와 관련도 낮음 ({review.keyword_relevance}점) — 실제 주제: {review.topic}")
    return flags


def _publish(db, video: Video, review: LectureReview, score: int) -> None:
    """정식 저장소로 옮깁니다. 여기를 지나야 사용자에게 보입니다."""
    s = review.summary
    assert s is not None

    prev = db.scalar(
        select(Lecture).where(Lecture.video_id == video.id).order_by(Lecture.version.desc())
    )
    version = (prev.version + 1) if prev else 1
    if prev:
        # 재요약이면 이전 판은 감춥니다 — UI 는 최신본만 보여줍니다
        prev.is_hidden = True

    db.add(
        Lecture(
            video_id=video.id,
            version=version,
            expert_score=score,
            verdict=review.verdict,
            duration_sec=video.duration_sec,
            published_at=now_kst(),
            is_favorite=prev.is_favorite if prev else False,
            model=_model_name(),
            one_liner=s.one_liner,
            sections=[_camel(sec.model_dump()) for sec in s.sections],
            closing=s.closing,
            # 검색·목록은 문단을 봅니다 — 섹션 제목과 불릿을 이어 붙여 채웁니다
            abstract=" ".join(sec.title for sec in s.sections),
            target_audience=s.target_audience,
            prerequisites=s.prerequisites,
            tags=s.tags,
            coverage_note=s.coverage_note,
            search_text=_search_text(video, s),
        )
    )


def _camel(d: dict) -> dict:
    """JSON 컬럼은 UI 가 그대로 읽습니다 — 계약(camelCase)에 맞춰 넣습니다."""
    out = {}
    for k, v in d.items():
        parts = k.split("_")
        out[parts[0] + "".join(p.title() for p in parts[1:])] = v
    return out


def _search_text(video: Video, s) -> str:
    return "\n".join(
        p
        for p in [
            video.title,
            video.channel_title,
            s.one_liner,
            s.closing,
            " ".join(s.tags),
            " ".join(sec.title for sec in s.sections),
            " ".join(b for sec in s.sections for b in sec.bullets),
        ]
        if p
    )


def _ok(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}]}


def _err(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "is_error": True}
