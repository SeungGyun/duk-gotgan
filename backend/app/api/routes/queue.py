"""대기 목록 — 앞으로 처리할 영상을 미리 봅니다.

**처리 전에 빼는 것이 이 화면의 값어치입니다.** 한 편당 받아쓰기 2~7분에
검토 6~8만 토큰이 듭니다. 제목만 봐도 아닌 것이 보이면, 일이 벌어지기
전에 빼는 편이 요약을 만들어 놓고 제외하는 것보다 훨씬 쌉니다.

순서는 지어내지 않습니다 — `queue.next_ids()` 가 워커와 **같은 함수**라,
여기 보이는 차례가 실제 처리 차례입니다. 화면과 동작이 갈리면 미리 보는
의미가 없습니다.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import current_user, require_owner
from app.api.errors import ApiError
from app.collector import failures
from app.collector import queue as q
from app.db.models import Keyword, PipelineEvent, Transcript, User, Video, VideoKeyword
from app.db.session import get_db
from config.settings import settings
from config.time import to_utc_iso

router = APIRouter(tags=["queue"])

# 한 칸에서 미리 보여 줄 최대 편수. 106건을 전부 그리면 화면이 목록이
# 아니라 벽이 됩니다. 총 편수는 따로 알려 주므로 정보가 사라지지는 않습니다.
PREVIEW = 40

# 처리 전에 뺀 것. 완전삭제(EXCLUDED)와 **다릅니다** — 저건 다시는 손대지
# 않겠다는 뜻이고, 이건 이번엔 넘어가지만 되돌릴 수 있다는 뜻입니다.
SKIPPED = "SKIPPED"

# 완전 제외. 발견 단계가 이 상태를 보고 **다시 데려오지 않습니다**
# (collector/discover.py). 되풀이해 실패하는 것을 여기 넣으면 줄에서
# 영영 빠집니다.
EXCLUDED = "EXCLUDED"

# 손봐야 할 실패. **자막과 요약을 가릅니다** — 되살릴 곳이 다르고
# (자막 줄 / 요약 줄), 사람이 판단하는 근거도 다릅니다.
FAILED_KINDS = {
    "transcript": ("자막 실패", ("FAILED_TRANSCRIPT", "FAILED")),
    "review": ("요약 실패", ("FAILED_REVIEW",)),
}

# 실패는 한 번에 이만큼만 보여 줍니다. 전부 그리면 화면이 벽이 되고,
# **보이는 것만 처리하는 것이 안전한 쪽**입니다 — 일괄 처리가 사용자가
# 본 적 없는 줄까지 건드리면 안 됩니다.
FAILED_PREVIEW = 60

STAGES = [
    ("review", "요약 대기", "TRANSCRIBED"),
    ("transcript", "자막 대기", "TRANSCRIPT_PENDING"),
    ("discovered", "발견 — 다음 수집에서 올라감", "DISCOVERED"),
]


def _rows(db: Session, video_ids: list[str]) -> dict[str, list[str]]:
    if not video_ids:
        return {}
    # **지운 키워드도 보여 줍니다.** 이 줄의 목적은 "왜 이게 여기 있나"라
    # 답하는 것인데, 지운 키워드가 데려온 영상이 이름 없이 뜨면 답이 안
    # 됩니다. 지운 것은 괄호로 표시해 지금 기준과 구분합니다.
    out: dict[str, list[str]] = {}
    for vid, term, title, archived in db.execute(
        select(
            VideoKeyword.video_id, Keyword.term, Keyword.channel_title, Keyword.archived_at
        )
        .join(Keyword, Keyword.id == VideoKeyword.keyword_id)
        .where(VideoKeyword.video_id.in_(video_ids))
    ).all():
        name = title or term
        out.setdefault(vid, []).append(f"({name})" if archived else name)
    return out


def _item(v: Video, kws: dict[str, list[str]], order: int | None = None) -> dict:
    return {
        "videoId": v.id,
        "title": v.title,
        "channelTitle": v.channel_title,
        "durationSec": v.duration_sec,
        "publishedAt": to_utc_iso(v.published_at),
        "keywords": kws.get(v.id, []),
        "order": order,
        "reason": v.state_reason or "",
    }


@router.get("/queue")
def get_queue(db: Session = Depends(get_db), _: User = Depends(current_user)):
    stages = []
    for key, label, state in STAGES:
        total, raw = db.execute(
            select(func.count(), func.coalesce(func.sum(Video.duration_sec), 0)).where(
                Video.state == state
            )
        ).one()
        # MySQL 의 SUM 은 Decimal 로 옵니다. 그대로 float 와 나누면 터집니다.
        total, secs = int(total), int(raw or 0)

        # 자막·검토는 번갈아 처리하므로 그 순서를 그대로 씁니다. 발견은
        # 다음 수집 때 키워드별 상한에 따라 올라가서 차례가 정해지지
        # 않습니다 — 최신순으로만 보여 줍니다.
        if state in ("TRANSCRIBED", "TRANSCRIPT_PENDING"):
            ids = q.next_ids(db, state, PREVIEW)
        else:
            ids = list(
                db.scalars(
                    select(Video.id)
                    .where(Video.state == state)
                    .order_by(Video.discovered_at.desc())
                    .limit(PREVIEW)
                ).all()
            )

        found = {v.id: v for v in db.scalars(select(Video).where(Video.id.in_(ids))).all()}
        kws = _rows(db, ids)
        stages.append(
            {
                "key": key,
                "label": label,
                "count": total,
                "totalSec": secs,
                # 받아쓰기만 시간을 어림합니다. 검토는 자막 길이가 아니라
                # 토큰 수에 좌우돼서 영상 길이로 재면 틀립니다.
                "etaSec": int(secs / settings.asr_realtime_factor)
                if key == "transcript"
                else None,
                "items": [
                    _item(found[i], kws, order=n)
                    for n, i in enumerate(ids, 1)
                    if i in found
                ],
            }
        )

    skipped = db.scalars(
        select(Video).where(Video.state == SKIPPED).order_by(Video.updated_at.desc()).limit(100)
    ).all()
    return {
        "stages": stages,
        "skipped": [_item(v, _rows(db, [v.id for v in skipped])) for v in skipped],
        "failed": [_failed_group(db, kind) for kind in FAILED_KINDS],
        # 화면이 "5배속 기준"이라고 말할 수 있게 근거를 같이 보냅니다.
        "asrRealtimeFactor": settings.asr_realtime_factor,
    }


def _attempts(db: Session, video_ids: list[str]) -> dict[str, int]:
    """영상마다 지금까지 몇 번 실패했나.

    **이력에서 셉니다** — 자막·요약 양쪽이 이미 같은 방식으로 재시도를
    세고 있고(`_retries`), 컬럼을 더하면 그 둘과 어긋날 여지가 생깁니다.
    되살린 뒤에도 그대로 쌓입니다: "몇 번이나 해 봤나"가 알고 싶은 값이라
    기준점을 옮기면 안 됩니다.
    """
    if not video_ids:
        return {}
    rows = db.execute(
        select(PipelineEvent.video_id, func.count())
        .where(PipelineEvent.video_id.in_(video_ids), PipelineEvent.ok.is_(False))
        .group_by(PipelineEvent.video_id)
    ).all()
    return {vid: int(n) for vid, n in rows}


def _failed_group(db: Session, kind: str) -> dict:
    """손봐야 할 실패 한 무리.

    **최근에 죽은 것부터** 보여 줍니다. 지금 되는지 확인하는 데는 어제 것이
    맞고, 오래전에 죽은 것은 대개 사유가 영구적입니다.
    """
    label, states = FAILED_KINDS[kind]
    total = int(
        db.scalar(select(func.count()).select_from(Video).where(Video.state.in_(states))) or 0
    )
    rows = db.scalars(
        select(Video)
        .where(Video.state.in_(states))
        .order_by(Video.updated_at.desc())
        .limit(FAILED_PREVIEW)
    ).all()
    ids = [v.id for v in rows]
    kws = _rows(db, ids)
    tries = _attempts(db, ids)
    return {
        "kind": kind,
        "label": label,
        "count": total,
        "items": [
            {
                **_item(v, kws),
                "failedAt": to_utc_iso(v.updated_at),
                "attempts": tries.get(v.id, 0),
                # 어림짐작입니다. 자동으로 무엇을 하지 않고, 화면에서
                # 걸러 보는 데만 씁니다 (collector/failures.py).
                "retryable": failures.retryable(v.state_reason),
            }
            for v in rows
        ],
    }


class Picked(BaseModel):
    """무엇을 처리할 것인가.

    **화면이 고른 것을 그대로 받습니다.** 서버에 "3번 이상 실패한 것" 같은
    필터 언어를 두지 않는 이유: 그러면 화면이 보여 준 목록과 서버가 고른
    목록이 조용히 갈릴 수 있고, 그 차이가 나타나는 자리가 하필 **일괄
    삭제**입니다. 사용자가 본 줄만 처리하는 편이 안전합니다.

    `kind` 만 주면 그 무리 전체입니다 — 요약 실패는 대개 세션·모델 쪽
    문제라 통째로 다시 돌리는 것이 실제로 하고 싶은 일입니다.
    """

    videoIds: list[str] = []
    kind: str | None = None
    # 무리 전체를 다룰 때, 다시 해 볼 만한 것만 고릅니다.
    onlyRetryable: bool = False
    # **자막부터 다시 받습니다.** 지금 있는 자막이 못 쓸 것일 때 씁니다 —
    # 요약을 다시 부르는 것과 다른 길입니다. 받아쓰기가 언어를 잘못 잡아
    # 한국어 강의를 일본어로 옮겨 놓은 것들이 그랬습니다: 요약을 백 번
    # 다시 불러도 그 자막을 읽고 같은 결론을 냅니다.
    refetch: bool = False


def _targets(db: Session, body: Picked) -> list[Video]:
    if body.videoIds:
        rows = db.scalars(select(Video).where(Video.id.in_(body.videoIds))).all()
        return [v for v in rows if any(v.state in st for _, st in FAILED_KINDS.values())]
    if body.kind not in FAILED_KINDS:
        raise ApiError(400, "NOTHING_PICKED", "무엇을 처리할지 골라 주세요.")
    _, states = FAILED_KINDS[body.kind]
    rows = db.scalars(select(Video).where(Video.state.in_(states))).all()
    if body.onlyRetryable:
        rows = [v for v in rows if failures.retryable(v.state_reason)]
    return list(rows)


@router.post("/queue/retry")
def retry(body: Picked, db: Session = Depends(get_db), _: User = Depends(require_owner)):
    """실패한 것을 줄에 다시 세웁니다.

    **어느 줄로 보낼지는 자막이 남아 있는지가 정합니다.** 요약 실패는
    자막 줄이 아니라 요약 줄로 돌아가야 하는데, 처리가 끝난 상태의 원문은
    30일 뒤 지워집니다(collector/cleanup.py). 원문 없이 요약 줄에 세우면
    그 자리에서 다시 죽으므로, 없으면 자막부터 다시 받습니다.

    **줄에 세운 기록을 남깁니다.** 자막·요약 양쪽 재시도 횟수를 그 기록
    이후로만 세기 때문에(`_retries`), 이 한 줄이 있어야 기회가 다시
    생깁니다. 이력은 지우지 않고 기준점만 옮깁니다.
    """
    targets = _targets(db, body)
    have = (
        set()
        if body.refetch
        else set(
            db.scalars(
                select(Transcript.video_id).where(
                    Transcript.video_id.in_([v.id for v in targets] or [""])
                )
            ).all()
        )
    )

    moved = {"transcript": 0, "review": 0}
    for v in targets:
        # 자막부터 다시 받기로 했으면 있는 것도 못 본 셈 칩니다. 새로 받은
        # 것이 그 자리를 덮어씁니다 (collector/transcript.py `store`).
        back = "TRANSCRIBED" if v.id in have else "TRANSCRIPT_PENDING"
        db.add(
            PipelineEvent(
                video_id=v.id,
                from_state=v.state,
                to_state=back,
                stage="revive",
                ok=True,
                detail={
                    "reason": (
                        "자막부터 다시 받으려고 사람이 줄에 다시 세웠습니다."
                        if body.refetch
                        else "실패한 것을 사람이 줄에 다시 세웠습니다."
                    )
                },
            )
        )
        v.state = back
        v.state_reason = None
        moved["review" if back == "TRANSCRIBED" else "transcript"] += 1
    db.commit()
    return {"restored": len(targets), **moved}


@router.post("/queue/exclude")
def exclude(body: Picked, db: Session = Depends(get_db), _: User = Depends(require_owner)):
    """되풀이해 실패하는 것을 **완전히 뺍니다.**

    `SKIPPED`(미리 빼기)와 다릅니다 — 저건 이번엔 넘어간다는 뜻이라 다음
    검색에 다시 들어옵니다. 여기 넣은 것은 발견 단계가 보고 **다시 데려오지
    않습니다**(collector/discover.py). 한 편이 매 사이클 줄 앞을 차지하며
    재시도만 태우는 것을 끊는 자리입니다.
    """
    targets = _targets(db, body)
    for v in targets:
        db.add(
            PipelineEvent(
                video_id=v.id,
                from_state=v.state,
                to_state=EXCLUDED,
                stage="skip",
                ok=True,
                detail={"reason": "되풀이 실패로 사람이 완전히 뺐습니다."},
            )
        )
        v.state = EXCLUDED
        v.state_reason = "되풀이 실패로 완전히 뺐습니다 — 다시 수집하지 않습니다."
    db.commit()
    return {"excluded": len(targets)}


@router.post("/queue/{video_id}/skip", status_code=204)
def skip(video_id: str, db: Session = Depends(get_db), _: User = Depends(require_owner)):
    """처리 전에 뺍니다. 되돌릴 수 있습니다."""
    v = db.get(Video, video_id)
    if v is None:
        raise ApiError(404, "VIDEO_NOT_FOUND", "해당 영상을 찾을 수 없습니다.")
    if v.state == SKIPPED:
        return
    if v.state not in {s for _, _, s in STAGES}:
        raise ApiError(
            409, "NOT_IN_QUEUE", "이미 처리됐거나 처리 중인 영상입니다."
        )

    # **어디로 되돌릴지는 이력에 남깁니다.** 컬럼을 새로 만들 필요가 없고,
    # 어차피 남기고 있는 기록이라 되돌리기가 공짜가 됩니다.
    db.add(
        PipelineEvent(
            video_id=v.id,
            from_state=v.state,
            to_state=SKIPPED,
            stage="skip",
            ok=True,
            detail={"reason": "대기 목록에서 미리 뺐습니다."},
        )
    )
    v.state = SKIPPED
    v.state_reason = "대기 목록에서 미리 뺐습니다 — 되돌릴 수 있습니다."
    db.commit()


@router.post("/queue/{video_id}/restore", status_code=204)
def restore(video_id: str, db: Session = Depends(get_db), _: User = Depends(require_owner)):
    """뺀 것을 원래 줄로 돌려놓습니다."""
    v = db.get(Video, video_id)
    if v is None or v.state != SKIPPED:
        raise ApiError(404, "NOT_SKIPPED", "빼 둔 영상이 아닙니다.")

    last = db.scalars(
        select(PipelineEvent)
        .where(PipelineEvent.video_id == video_id, PipelineEvent.to_state == SKIPPED)
        .order_by(PipelineEvent.created_at.desc())
    ).first()
    # 이력이 없으면 자막 대기로 보냅니다 — 자막이 있으면 다음 사이클이
    # 곧바로 검토로 올리므로, 잘못 돌려놔도 한 칸 늦어질 뿐입니다.
    back = (last.from_state if last else None) or "TRANSCRIPT_PENDING"
    v.state = back
    v.state_reason = None
    db.add(
        PipelineEvent(
            video_id=v.id, from_state=SKIPPED, to_state=back, stage="skip", ok=True,
            detail={"reason": "대기 목록으로 되돌렸습니다."},
        )
    )
    db.commit()
