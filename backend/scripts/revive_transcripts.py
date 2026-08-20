"""일시적 차단으로 죽은 자막 실패를 되살립니다.

**왜 필요한가.** 유튜브가 오디오에 403 을 주는 동안 줄에 있던 영상이
재시도 횟수를 다 태우고 `FAILED_TRANSCRIPT` 로 적힙니다. 차단이 풀려도
탈락한 것은 다시 시도하지 않으므로, 그 하루치가 통째로 사라집니다 —
실제로 이틀에 129편이 그렇게 죽었고, 그 뒤 같은 URL 이 멀쩡히 받아졌습니다.

사이클 쪽은 고쳤지만(연속 일시 실패면 접고 냉각), **이미 죽은 것은
누군가 되살려야 합니다.**

> 이제 **대기 목록 화면에서 골라 되살릴 수 있습니다** — 실패 목록을
> 필터로 좁히고 고른 것만 줄에 다시 세우거나 아주 뺍니다. 이 스크립트는
> 화면 없이(SSH·크론) 한꺼번에 밀 때만 쓰면 됩니다. 무엇이 "다시 해도
> 같은 것"인지는 양쪽이 `app/collector/failures.py` 를 같이 봅니다 —
> 갈라 두면 화면에서 되살린 것을 스크립트가 거르는 일이 생깁니다.

  python -m scripts.revive_transcripts             # 미리보기 (아무것도 안 바꿉니다)
  python -m scripts.revive_transcripts --apply     # 전부 줄에 다시 세웁니다
  python -m scripts.revive_transcripts --apply -n 5  # 다섯 편만 (되는지 먼저 보기)
  python -m scripts.revive_transcripts --no-captions --apply  # 자막 없어 죽은 것들

되살릴 때 **줄에 세운 기록을 남깁니다.** 재시도 횟수를 그 기록 이후로만
세기 때문에(`collector/transcript.py` `_retries`), 이 한 줄이 있어야
다섯 번의 기회가 다시 생깁니다. 이력은 지우지 않습니다.
"""

import sys

from sqlalchemy import select

from app.collector import failures
from app.db.models import PipelineEvent, Video
from app.db.session import SessionLocal

# 자막이 없어서 죽은 것들. **한동안 받아쓰기로 넘어가지 않는 버그가
# 있었습니다** — `_fetch_with_retry` 가 차단만 잡고 "자막 없음" 은 그대로
# 흘려보내서, 정작 받아쓰기가 가장 필요한 경우에 한 번도 안 갔습니다.
# 그때 죽은 것들은 이제 소리로 받으면 됩니다.
NO_CAPTIONS = ("자막이 제공되지 않는", "쓸 수 있는 자막이 없")


def main() -> int:
    apply = "--apply" in sys.argv
    no_captions = "--no-captions" in sys.argv
    limit = 0
    for flag in ("-n", "--limit"):
        if flag in sys.argv:
            limit = int(sys.argv[sys.argv.index(flag) + 1])
    db = SessionLocal()
    try:
        rows = db.scalars(select(Video).where(Video.state == "FAILED_TRANSCRIPT")).all()
        if no_captions:
            marks, label = NO_CAPTIONS, "자막 없음(받아쓰기 미시도)"
            targets = [v for v in rows if any(m in (v.state_reason or "") for m in marks)]
        else:
            label = "다시 해 볼 만한"
            targets = [v for v in rows if failures.retryable(v.state_reason)]

        print(f"자막 실패 {len(rows)}편 중 {label} 사유 {len(targets)}편")
        if limit:
            # 최근에 죽은 것부터 — 지금 되는지 보는 데는 어제 것이 맞습니다.
            targets = sorted(targets, key=lambda v: v.updated_at, reverse=True)[:limit]
            print(f"이번에는 그중 {len(targets)}편만 되살립니다 (-n {limit})")
        for v in targets[:5]:
            print(f"  · {v.title[:40]} — {(v.state_reason or '')[:70]}")
        if len(targets) > 5:
            print(f"  … 외 {len(targets) - 5}편")

        if not apply:
            print("\n미리보기입니다. 실제로 되살리려면 --apply 를 붙이세요.")
            return 0

        for v in targets:
            v.state = "TRANSCRIPT_PENDING"
            v.state_reason = None
            db.add(
                PipelineEvent(
                    video_id=v.id,
                    from_state="FAILED_TRANSCRIPT",
                    to_state="TRANSCRIPT_PENDING",
                    stage="revive",
                    ok=True,
                    detail={"reason": "일시 차단으로 죽은 것을 줄에 다시 세웠습니다."},
                )
            )
        db.commit()
        print(f"\n{len(targets)}편을 줄에 다시 세웠습니다. 워커가 다음 사이클부터 집어갑니다.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
