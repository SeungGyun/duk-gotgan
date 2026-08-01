"""한 사이클 — 파이프라인 전 단계를 한 번씩 훑습니다.

**놓친 작업을 따로 추적하지 않습니다.** 할 일이 전부 상태로 표현되어 있어서,
틱이 스무 번 건너뛰어도 조건은 그대로 참입니다. 큐도 미처리 목록도 없습니다.

  자막 대기 → `state = TRANSCRIPT_PENDING`
  검토 대기 → `state = TRANSCRIBED`
  수집 대상 → `last_run_at + 주기 <= 지금`

**사이클마다 상한을 둡니다.** 대기가 50건이면 한 사이클이 몇 시간 돌고, 그동안
새 요청에 반응하지 못합니다. 끊어서 처리하고 다음 틱이 이어받습니다 — 검토
10건이면 이미 30분이라 1분 간격은 무시할 수준이고, 프롬프트 캐시(1시간)도
유지됩니다.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.collector import discover as D
from app.collector import quota
from app.collector.schedule import due_keywords
from app.collector.transcript import transcribe_pending
from app.collector.youtube import YouTubeError
from app.llm.runner import recover_zombies, review_pending

logger = logging.getLogger(__name__)

# 사이클당 상한
TRANSCRIBE_PER_CYCLE = 20
REVIEW_PER_CYCLE = 10


@dataclass
class CycleResult:
    zombies: int = 0
    keywords_run: int = 0
    discovered: int = 0
    rule_passed: int = 0
    transcribed: int = 0
    reviewed: int = 0
    published: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def did_anything(self) -> bool:
        return bool(
            self.zombies or self.keywords_run or self.transcribed or self.reviewed
        )

    def __str__(self) -> str:
        parts = []
        if self.zombies:
            parts.append(f"좀비회수 {self.zombies}")
        if self.keywords_run:
            parts.append(f"키워드 {self.keywords_run} → 발견 {self.discovered}·통과 {self.rule_passed}")
        if self.transcribed:
            parts.append(f"자막 {self.transcribed}")
        if self.reviewed:
            parts.append(f"검토 {self.reviewed}·공개 {self.published}")
        return " · ".join(parts) if parts else "할 일 없음"


async def run_cycle(db: Session) -> CycleResult:
    """한 바퀴. 락은 호출부(워커)가 잡습니다."""
    r = CycleResult()

    # 1) 죽은 워커가 잡아둔 것부터 풀어줍니다. 이게 먼저여야 이번 사이클에서
    #    다시 처리됩니다.
    r.zombies = recover_zombies(db)

    # 2) 수집 — 쿼터가 모자라면 알아서 멈춥니다
    due = due_keywords(db)
    if due:
        try:
            run, results = D.run_discovery(db, [k.id for k in due], trigger="scheduled")
            r.keywords_run = len(results)
            r.discovered = run.stats.get("discovered", 0)
            r.rule_passed = run.stats.get("rulePassed", 0)
            if run.error:
                r.notes.append(run.error)
        except quota.QuotaExceeded as e:
            r.notes.append(str(e))
        except YouTubeError as e:
            r.notes.append(str(e))

    # 3) 자막 — 순차. 동시에 던지면 IP 가 막히고 그날 전체가 멈춥니다.
    t = transcribe_pending(db, limit=TRANSCRIBE_PER_CYCLE)
    r.transcribed = t["ok"]
    if t.get("blocked"):
        r.notes.append(t.get("error", "자막 요청이 차단되었습니다."))

    # 4) 검토 — 몰아서 순차. 프롬프트 캐시가 살아 있어야 사용량이 1/18 입니다.
    runs = await review_pending(db, limit=REVIEW_PER_CYCLE)
    done = [x for x in runs if x.ok]
    r.reviewed = len(done)
    r.published = len([x for x in done if x.published])
    for x in runs:
        if x.error:
            r.notes.append(f"{x.title[:30]} — {x.error}")

    return r
