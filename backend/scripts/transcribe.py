"""자막 대기 중인 영상의 자막을 받아옵니다 (M3).

  python -m scripts.transcribe            # 최대 20건
  python -m scripts.transcribe --limit 5

**확보율이 이 단계의 실측 항목입니다.** 후보 중 몇 %가 자막을 갖고
있는지가 이후 파이프라인 처리량을 결정합니다.
"""

import argparse
import logging

from app.collector.transcript import transcribe_pending
from app.db.session import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="자막 수집")
    parser.add_argument("--limit", type=int, default=20, help="한 번에 처리할 최대 건수")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        r = transcribe_pending(db, limit=args.limit)

        print()
        for title, mark, detail in r["rows"]:
            print(f"  {mark} {title[:46]:<46} {detail}")

        rate = (r["ok"] / r["attempted"] * 100) if r["attempted"] else 0
        print()
        print(f"■ 시도 {r['attempted']} · 확보 {r['ok']} · 실패 {r['failed']} "
              f"— 확보율 {rate:.0f}%")
        if r["blocked"]:
            print(f"  ! {r['error']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
