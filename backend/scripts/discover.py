"""수집 1단계를 손으로 돌립니다 (스케줄러는 M6).

  python -m scripts.discover                 # pending + active 키워드 전부
  python -m scripts.discover --keyword kw_1  # 하나만
  python -m scripts.discover --quota         # 오늘 남은 검색 횟수만 확인

탈락 사유를 화면에 그대로 찍습니다. **이 목록을 눈으로 보는 것이 M2 의
핵심**입니다 — 좋은 강의가 어떤 기준에 걸려 떨어지는지 확인하고 나서
AI 를 붙여야 합니다.
"""

import argparse
import logging

from app.collector import quota
from app.collector.discover import run_discovery
from app.db.session import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="유튜브 후보 수집 (LLM 없음)")
    parser.add_argument("--keyword", action="append", dest="keywords", help="키워드 id (반복 가능)")
    parser.add_argument("--quota", action="store_true", help="남은 쿼터만 보고 종료")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        if args.quota:
            print(f"오늘 남은 검색 {quota.searches_left(db)}회 ({quota.remaining(db)} 유닛)")
            return

        run, results = run_discovery(db, keyword_ids=args.keywords, trigger="manual")

        print()
        print(f"■ {run.label} — {run.status}")
        print(f"  발견 {run.stats['discovered']} · 룰 통과 {run.stats['rulePassed']} "
              f"· 유닛 {run.youtube_units}")
        if run.error:
            print(f"  ! {run.error}")

        for r in results:
            print()
            print(f"  [{r.keyword_term}] 발견 {r.discovered} · 통과 {r.rule_passed} "
                  f"· 상한 대기 {r.deferred} · 이미 있음 {r.already_known}")
            for title, reason in r.rejected:
                print(f"    ✕ {title[:52]:<52} {reason}")

        print()
        print(f"  오늘 남은 검색 {quota.searches_left(db)}회")
    finally:
        db.close()


if __name__ == "__main__":
    main()
