"""판정 결과를 받아 판단하고 담는 곳 — **회사를 가리지 않습니다.**

여기서 하는 일은 저장이 아니라 **판단**입니다.

  검증 → 점수 재계산 → 소유권 확인 → 판정 기록 → (통과 시) 정식 저장소 적재

공개 여부를 AI 가 정하지 않습니다. AI 는 의견을 낼 뿐이고, 그 의견을 받아들일지는
우리 코드가 가릅니다. 프롬프트가 뚫려도 피해가 "이상한 요약 1건"에서 멈추는
이유입니다.

**왜 실행기에서 떼어냈나.** 이 로직은 클로드 SDK 의 `create_sdk_mcp_server`
클로저 안에 있었습니다. 두 번째 회사(안티그래비티)를 붙이려면 그 안의 코드를
쓸 방법이 없어 복사해야 하는데, 두 벌로 갈라지면 **판정 기준이 조용히
어긋납니다** — 한쪽에서 점수 계산을 고쳐도 다른 쪽은 옛날 기준으로 계속
담습니다. 실행기는 결과를 어떻게 받아오느냐만 다르고, 받은 뒤는 같아야 합니다.

  클로드    save_review 도구 호출의 인자로 받습니다 (llm/tools.py)
  안티그래비티  CLI 의 구조화 출력(--json-schema)으로 받습니다 (llm/agy.py)
"""

import logging
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from app.db.models import Evaluation, Lecture, PipelineEvent, Video
from app.db.session import SessionLocal
from app.llm.policy import should_publish
from app.llm.schemas import LectureReview, weighted_score
from config.time import now_kst

logger = logging.getLogger(__name__)

# 모델이 보낸 총점과 우리가 계산한 값이 이보다 벌어지면 기록해 둡니다.
# 프롬프트가 흔들리거나 루브릭을 무시하고 있다는 신호입니다.
SCORE_GAP_ALERT = 10

# 형식 오류 재시도 상한. 실측에서 여덟 번 연속 실패에 입력 토큰 52만이
# 나갔습니다 — 자막을 매번 다시 읽기 때문입니다. 몇 번 안에 못 고치면
# 프롬프트나 스키마 문제이지 모델이 더 시도해서 풀릴 일이 아닙니다.
MAX_FORMAT_RETRY = 3

PROMPT_VERSION = "v1"


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


class Rejected(Exception):
    """저장하지 않고 돌려보냅니다. `retry` 는 모델이 고쳐서 다시 낼 수 있는가."""

    def __init__(self, message: str, *, retry: bool = False):
        self.retry = retry
        super().__init__(message)


def save(
    *,
    video_id: str,
    args: dict[str, Any],
    outcome: ReviewOutcome,
    run_id: str | None = None,
    owner: str | None = None,
    model: str | None = None,
) -> str:
    """판정 결과 1건을 받아 판단하고 담습니다. 성공하면 사람이 읽을 메시지.

    실패는 `Rejected` 로 던집니다 — 실행기가 그것을 자기 방식(도구 오류 응답
    또는 실행 실패)으로 옮깁니다.
    """
    if outcome.called:
        raise Rejected("이미 저장했습니다. 다시 호출하지 마세요.")

    try:
        review = LectureReview.model_validate(args)
    except ValidationError as e:
        outcome.format_errors += 1
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:5]
        )
        logger.warning(
            "[store] 형식 오류 %d/%d — %s", outcome.format_errors, MAX_FORMAT_RETRY, problems
        )
        if outcome.format_errors >= MAX_FORMAT_RETRY:
            outcome.error = f"형식 오류 {MAX_FORMAT_RETRY}회 — {problems}"
            raise Rejected("형식 오류가 반복됩니다. 더 시도하지 말고 종료하세요.") from e
        # 무엇이 틀렸는지 알려주면 모델이 고칠 수 있습니다.
        raise Rejected(
            f"형식이 맞지 않습니다. 고쳐서 다시 호출하세요. — {problems}", retry=True
        ) from e

    db = SessionLocal()
    try:
        # **행을 잠그고 소유권을 봅니다.** 확인과 저장이 같은 트랜잭션 안에
        # 있어야, 확인한 뒤 저장하기 전에 회수당하는 틈이 없습니다.
        video = db.scalar(select(Video).where(Video.id == video_id).with_for_update())
        if video is None:
            raise Rejected("대상 영상을 찾을 수 없습니다.")

        if video.state != "REVIEWING" or (owner and video.claimed_by != owner):
            outcome.error = (
                f"이 영상은 더 이상 내 것이 아닙니다 "
                f"(상태 {video.state} · 임자 {video.claimed_by})."
            )
            logger.warning("[store] %s %s", video_id, outcome.error)
            db.rollback()
            raise Rejected("이 영상은 다른 워커가 처리 중입니다. 저장하지 말고 종료하세요.")

        # 총점은 우리가 다시 계산합니다. 공개 여부를 가르는 값이라
        # 모델의 산수를 믿지 않습니다.
        computed = weighted_score(review.criteria)
        gap = abs(computed - review.expert_score)
        outcome.score_gap = gap
        if gap > SCORE_GAP_ALERT:
            logger.warning(
                "[store] %s 점수 불일치 — 모델 %d / 계산 %d",
                video_id, review.expert_score, computed,
            )

        # **거르지 않습니다.** 요약이 나왔으면 담고, 버릴지는 사람이
        # 제외 버튼으로 정합니다. 판정과 점수는 그 판단을 돕는 근거로
        # 함께 저장합니다.
        passed = should_publish(review.summary is not None)
        model_name = model or _active_model()

        ev = Evaluation(
            video_id=video_id,
            model=model_name,
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
            _publish(db, video, review, computed, model_name)
            video.state = "PUBLISHED"
            video.state_reason = None
        else:
            # **왜 못 했는지를 같이 적습니다.**
            #
            # 예전에는 이 한 문장이 전부였습니다 — "요약이 오지 않았습니다."
            # 그래서 화면에서는 고장인지 판정인지 갈리지 않았고, 사람이 할
            # 수 있는 일은 다시 눌러 보는 것뿐이었습니다. 실제로 한 편이
            # **38번** 그렇게 돌았습니다.
            #
            # 모델은 이유를 이미 말하고 있었습니다 — `red_flags` 에
            # "한국어 영상을 일본어로 오인식한 ASR 결과물", "21초짜리 뉴스
            # 쇼츠" 같은 문장이 들어 있었는데 우리가 안 옮겼을 뿐입니다.
            # 그 한 줄이 있으면 자막을 다시 받을 일인지, 아주 뺄 일인지가
            # 그 자리에서 갈립니다.
            video.state = "FAILED_REVIEW"
            video.state_reason = _no_summary_reason(review)

        # 다 썼으니 놓습니다. 남겨 두면 좀비 회수가 끝난 영상을 계속
        # 훑고, 화면의 "지금 붙들고 있는 것"도 틀리게 나옵니다.
        video.claimed_by = None
        video.claimed_at = None

        db.add(
            PipelineEvent(
                video_id=video_id,
                run_id=run_id,
                from_state="REVIEWING",
                to_state=video.state,
                stage="review",
                ok=True,
                detail={"verdict": review.verdict, "score": computed, "topic": review.topic},
            )
        )
        db.commit()

        outcome.called = True
        outcome.published = passed
        outcome.expert_score = computed
        outcome.verdict = review.verdict

        if passed:
            return f"담았습니다. 전문성 {computed}점 · {review.verdict}."
        return "요약이 없어 담지 못했습니다. 재시도하지 마세요."
    except Rejected:
        db.rollback()
        raise
    except Exception as e:  # noqa: BLE001 — 저장이 죽어도 실행기는 살아야 합니다
        db.rollback()
        outcome.error = str(e)
        logger.exception("[store] 저장 실패")
        raise Rejected("저장 중 오류가 발생했습니다. 재시도하지 마세요.") from e
    finally:
        db.close()


def _active_model() -> str:
    from config.settings import settings

    return settings.active_review_model


# 사유 문장의 머리. **바꾸지 마세요** — 실패를 갈라 보는 쪽이 이 말을
# 보고 "다시 해도 같은 것"으로 판단합니다 (collector/failures.py).
NO_SUMMARY = "요약이 오지 않았습니다"


def _no_summary_reason(review: LectureReview) -> str:
    """모델이 요약을 못 하겠다고 한 이유를 사람 말로.

    `red_flags` 의 첫 줄이 대개 그 이유입니다. 없으면 판정만이라도
    적습니다 — "irrelevant" 한 낱말도 아무것도 없는 것보다 낫습니다.
    """
    why = next((f.strip() for f in review.red_flags if f.strip()), "")
    head = f"{NO_SUMMARY} · {review.verdict}"
    return f"{head} — {why}"[:400] if why else head


def _red_flags(review: LectureReview, gap: int) -> list[str]:
    flags = list(review.red_flags)
    if gap > SCORE_GAP_ALERT:
        flags.append(f"모델이 보고한 총점과 루브릭 계산값이 {gap}점 어긋남")
    if review.keyword_relevance < 40:
        flags.append(
            f"검색 키워드와 관련도 낮음 ({review.keyword_relevance}점) — 실제 주제: {review.topic}"
        )
    return flags


def _publish(db, video: Video, review: LectureReview, score: int, model: str) -> None:
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
            model=model,
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
