"""이미 쌓인 판정으로 채널 차단 목록을 만듭니다 (1회성 보정).

  python -m scripts.block_channels          # 후보만 보기
  python -m scripts.block_channels --apply  # 실제로 차단

`keyword_relevance` 를 컬럼으로 저장하기 전의 판정들은 관련도가 `red_flags`
텍스트에만 남아 있습니다. 그 문구에서 되살려 후보를 뽑습니다.
"""

import argparse
import re

from sqlalchemy import func, select

from app.collector.channels import (
    ANY_REJECT_LIMIT,
    BAD_VERDICTS,
    IRRELEVANT_BELOW,
    STRIKE_LIMIT,
)
from app.db.models import ChannelBlock, Evaluation, Lecture, Video
from app.db.session import SessionLocal, init_db
from config.time import now_kst

# "검색 키워드와 관련도 낮음 (15점) — 실제 주제: …"
_RELEVANCE = re.compile(r"관련도 낮음 \((\d+)점\)")


def _off_topic(ev: Evaluation) -> str | None:
    """무관·홍보인가 (2회면 차단)."""
    if ev.verdict in BAD_VERDICTS:
        return {"promotional": "홍보물", "irrelevant": "주제 무관"}[ev.verdict]
    if ev.keyword_relevance < IRRELEVANT_BELOW:
        return f"관련도 {ev.keyword_relevance}점"
    for flag in ev.red_flags or []:
        m = _RELEVANCE.search(flag)
        if m and int(m.group(1)) < IRRELEVANT_BELOW:
            return f"관련도 {m.group(1)}점"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="판정 이력으로 채널 차단")
    parser.add_argument("--apply", action="store_true", help="실제로 차단 목록에 넣기")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        rows = db.execute(
            select(Evaluation, Video).join(Video, Video.id == Evaluation.video_id)
        ).all()

        off: dict[str, list] = {}
        rejects: dict[str, int] = {}
        titles: dict[str, str] = {}
        for ev, video in rows:
            if not video.channel_id or video.state == "PUBLISHED":
                continue
            titles[video.channel_id] = video.channel_title
            rejects[video.channel_id] = rejects.get(video.channel_id, 0) + 1
            reason = _off_topic(ev)
            if reason:
                off.setdefault(video.channel_id, []).append(reason)

        print(
            f"\n■ 탈락 이력이 있는 채널 {len(rejects)}개 "
            f"(무관·홍보 {STRIKE_LIMIT}회 또는 아무 사유로 {ANY_REJECT_LIMIT}회)\n"
        )
        blocked = 0
        for cid, total in sorted(rejects.items(), key=lambda x: -x[1]):
            reasons = off.get(cid, [])
            published = db.scalar(
                select(func.count())
                .select_from(Lecture)
                .join(Video, Video.id == Lecture.video_id)
                .where(Video.channel_id == cid, Lecture.is_hidden.is_(False))
            )
            already = db.get(ChannelBlock, cid) is not None
            if already:
                mark, note = "·", "이미 차단됨"
            elif published:
                mark, note = "·", f"공개 강의 {published}편 있음 — 막지 않습니다"
            elif len(reasons) >= STRIKE_LIMIT:
                mark, note = "✕", f"무관·홍보 {len(reasons)}회 — 차단 대상"
            elif total >= ANY_REJECT_LIMIT:
                mark, note = "✕", f"{total}번 검토, 한 번도 통과 못 함 — 차단 대상"
            else:
                need = min(STRIKE_LIMIT - len(reasons), ANY_REJECT_LIMIT - total)
                mark, note = "·", f"{need}회 더 걸리면 차단"

            label = ", ".join(reasons[:3]) or "기준 미달"
            print(f"  {mark} {titles[cid][:26]:<26} 탈락 {total}회  {label}")
            print(f"      {note}")

            if args.apply and mark == "✕":
                db.add(
                    ChannelBlock(
                        channel_id=cid,
                        channel_title=titles[cid],
                        reason=note.replace(" — 차단 대상", ""),
                        auto=True,
                        created_at=now_kst(),
                    )
                )
                blocked += 1

        if args.apply:
            db.commit()
            print(f"\n{blocked}개 채널을 차단했습니다.")
        else:
            print("\n--apply 를 붙이면 실제로 차단합니다.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
