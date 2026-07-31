"""자막이 준비된 영상을 AI 로 판정·요약합니다 (M4).

  python -m scripts.review              # 최대 10건, 한 배치로
  python -m scripts.review --limit 1    # 한 건만 (첫 실측용)
  python -m scripts.review --dry-run    # 대상만 확인하고 호출은 안 함

**한 번에 몰아서 돌리세요.** 프롬프트 캐시가 1시간짜리라, 연속 처리하면
두 번째 호출부터 오버헤드가 거의 공짜입니다.
"""

import argparse
import asyncio
import logging

from sqlalchemy import select

from app.db.models import Transcript, Video
from app.db.session import SessionLocal, init_db
from app.llm.runner import recover_zombies, review_pending

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


async def main() -> None:
    parser = argparse.ArgumentParser(description="AI 판정 · 요약")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true", help="대상만 보고 종료")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        recovered = recover_zombies(db)
        if recovered:
            print(f"  좀비 {recovered}건 회수")

        if args.dry_run:
            rows = db.scalars(
                select(Video).where(Video.state == "TRANSCRIBED").limit(args.limit)
            ).all()
            print(f"\n■ 검토 대기 {len(rows)}건")
            for v in rows:
                t = db.get(Transcript, v.id)
                print(f"  · {v.title[:50]:<50} {t.est_tokens if t else 0:>7,} 토큰")
            return

        runs = await review_pending(db, limit=args.limit)

        print()
        for r in runs:
            if r.error:
                mark, detail = "!", r.error
            elif r.published:
                mark, detail = "○", f"공개 · {r.score}점 {r.verdict}"
            else:
                mark, detail = "·", f"보류 · {r.score}점 {r.verdict}"
            print(f"  {mark} {r.title[:44]:<44} {detail}")
            if r.ok:
                print(
                    f"      신규 {r.input_new:>6,} · 캐시쓰기 {r.cache_created:>6,} "
                    f"· 캐시읽기 {r.cache_read:>7,} → 환산 {r.input_weighted:>6,}"
                )
                print(
                    f"      출력 {r.output_tokens:>6,} · {r.turns}턴"
                    f"{' · 조기종료' if r.early_exit else ''}"
                )
            for d in r.denials:
                print(f"      차단: {d}")

        done = [r for r in runs if r.ok]
        published = [r for r in done if r.published]
        early = [r for r in done if r.early_exit]
        print()
        print(f"■ 검토 {len(done)}/{len(runs)} · 공개 {len(published)} · 조기종료 {len(early)}")
        if done:
            print(
                f"  입력 환산 {sum(r.input_weighted for r in done):,} "
                f"(총량 {sum(r.input_total for r in done):,}) · "
                f"출력 {sum(r.output_tokens for r in done):,} 토큰 "
                f"(사용량 상당 ${sum(r.cost_usd for r in done):.3f})"
            )
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
