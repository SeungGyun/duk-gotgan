"""발견 단계 — 키워드 하나로 후보 영상을 모아 임시 저장소에 넣습니다.

파이프라인 1~2단계(SPEC §4.2). 여기까지는 **LLM 이 전혀 없습니다.**
검색과 필터는 규칙이 명확해서 코드가 더 싸고 정확하고 재현됩니다.

같은 영상을 두 키워드가 데려와도 `videos` 행은 하나입니다. PK 가 유튜브
video id 라서 중복 처리가 구조적으로 막힙니다 — 자막 수집과 요약이 두 번
일어나면 그대로 두 배 비용입니다.
"""

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector import quota, rules
from app.collector.youtube import Candidate, YouTubeError, fetch_details, search_ids
from app.db.models import CrawlRun, Keyword, PipelineEvent, Video, VideoKeyword
from config.settings import settings
from config.time import now_kst

logger = logging.getLogger(__name__)

# 검색 1회에 가져올 후보 수. 50이 API 최대치이고, 유닛은 개수와 무관하게
# 호출당 100이라 적게 받을 이유가 없습니다.
SEARCH_PAGE_SIZE = 50


@dataclass
class DiscoverResult:
    keyword_term: str
    discovered: int = 0
    rule_passed: int = 0
    already_known: int = 0
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (제목, 사유)
    error: str | None = None

    @property
    def stats(self) -> dict:
        return {
            "discovered": self.discovered,
            "rulePassed": self.rule_passed,
            "transcribed": 0,
            "reviewed": 0,
            "published": 0,
        }


def discover_keyword(db: Session, kw: Keyword, run: CrawlRun) -> DiscoverResult:
    """키워드 하나를 검색해 후보를 적재합니다."""
    result = DiscoverResult(keyword_term=kw.term)

    # 쿼터를 먼저 확인하고 차감합니다. 호출하고 나서 재면 이미 늦습니다.
    _spend(db, run, quota.UNITS_SEARCH)

    published_after = now_kst() - timedelta(days=settings.rule_max_age_days)
    if kw.published_after:
        published_after = max(
            published_after,
            now_kst().replace(
                year=kw.published_after.year,
                month=kw.published_after.month,
                day=kw.published_after.day,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            ),
        )

    ids = search_ids(
        kw.term,
        language=kw.language,
        published_after=published_after,
        limit=SEARCH_PAGE_SIZE,
    )
    if not ids:
        return result

    _spend(db, run, quota.UNITS_VIDEOS)
    candidates = fetch_details(ids)
    result.discovered = len(candidates)

    for c in candidates:
        existing = db.get(Video, c.video_id)
        if existing is not None:
            # 이미 아는 영상 — 이 키워드와의 연결만 이어 줍니다.
            # 자막 수집도 요약도 다시 하지 않습니다.
            _link(db, c, kw, run)
            result.already_known += 1
            continue

        verdict = rules.evaluate(c, kw)
        video = _insert_video(db, c, passed=verdict.ok, reason=verdict.reason)
        _link(db, c, kw, run)
        _event(db, video.id, run.id, None, video.state, "discover", verdict.ok, verdict.reason)

        if verdict.ok:
            result.rule_passed += 1
        else:
            result.rejected.append((c.title, verdict.reason))

    db.flush()
    return result


def _spend(db: Session, run: CrawlRun, units: int) -> None:
    """쿼터를 깎고 실행 이력에도 반영합니다. 둘 다 즉시 커밋합니다 —
    뒤에서 적재가 실패해 롤백돼도 "유닛은 실제로 썼다"가 남아야 합니다."""
    quota.spend(db, units)  # 여기서 커밋
    run.youtube_units += units
    db.commit()


def _insert_video(db: Session, c: Candidate, *, passed: bool, reason: str) -> Video:
    video = Video(
        id=c.video_id,
        title=c.title,
        description=c.description[:4000] if c.description else None,
        channel_id=c.channel_id,
        channel_title=c.channel_title,
        published_at=c.published_at,
        duration_sec=c.duration_sec,
        view_count=c.view_count,
        like_count=c.like_count,
        comment_count=c.comment_count,
        thumbnail_url=c.thumbnail_url,
        default_language=c.default_language,
        has_official_caption=c.has_caption,
        state="TRANSCRIPT_PENDING" if passed else "REJECTED_RULE",
        state_reason=None if passed else reason,
        discovered_at=now_kst(),
    )
    db.add(video)
    db.flush()
    return video


def _link(db: Session, c: Candidate, kw: Keyword, run: CrawlRun) -> None:
    exists = db.get(VideoKeyword, {"video_id": c.video_id, "keyword_id": kw.id})
    if exists is not None:
        return
    db.add(
        VideoKeyword(
            video_id=c.video_id,
            keyword_id=kw.id,
            run_id=run.id,
            search_rank=c.search_rank,
            discovered_at=now_kst(),
        )
    )


def _event(db, video_id, run_id, frm, to, stage, ok, detail) -> None:
    db.add(
        PipelineEvent(
            video_id=video_id,
            run_id=run_id,
            from_state=frm,
            to_state=to,
            stage=stage,
            ok=ok,
            detail={"reason": detail} if detail else None,
        )
    )


# ── 실행 단위 ────────────────────────────────────────────────


def run_discovery(
    db: Session, keyword_ids: list[str] | None = None, trigger: str = "manual"
) -> tuple[CrawlRun, list[DiscoverResult]]:
    """대상 키워드를 골라 한 번 실행합니다.

    `keyword_ids` 를 주지 않으면 **pending(첫 실행 대기) + active** 를 모두
    돌립니다. pending 이 스케줄러의 트리거라, 첫 실행을 마치면 active 로
    올려 이후에는 주기를 따르게 합니다.
    """
    stmt = select(Keyword).where(Keyword.status.in_(("pending", "active")))
    if keyword_ids:
        stmt = stmt.where(Keyword.id.in_(keyword_ids))
    targets = db.scalars(stmt.order_by(Keyword.status.desc())).all()  # pending 먼저

    # 키가 없으면 쿼터를 깎기 전에 멈춥니다. 어차피 한 건도 못 부릅니다.
    if not settings.youtube_api_key:
        raise YouTubeError(
            "유튜브 API 키가 없습니다. backend/.env 의 YOUTUBE_API_KEY 를 채워 주세요. "
            "Google Cloud Console → API 및 서비스 → YouTube Data API v3 사용 설정 → 사용자 인증 정보에서 발급합니다."
        )

    run = CrawlRun(
        label=f"키워드 {len(targets)}개 · {'수동' if trigger == 'manual' else '정기'} 실행",
        trigger=trigger,
        status="running",
        started_at=now_kst(),
        stats={},
    )
    db.add(run)
    # 루프 전에 커밋합니다. flush 만 하면 키워드 하나가 실패해 롤백할 때
    # **실행 이력 자체가 사라져**, 정작 실패를 기록해야 할 행이 없어집니다.
    # 진행 중인 실행이 화면에 보이는 것도 맞는 동작입니다.
    db.commit()

    totals = {"discovered": 0, "rulePassed": 0, "transcribed": 0, "reviewed": 0, "published": 0}
    failures: list[str] = []
    results: list[DiscoverResult] = []

    for kw in targets:
        try:
            r = discover_keyword(db, kw, run)
            results.append(r)
            totals["discovered"] += r.discovered
            totals["rulePassed"] += r.rule_passed
            kw.last_run_at = now_kst()
            if kw.status == "pending":
                kw.status = "active"
            db.commit()
        except quota.QuotaExceeded as e:
            # 쿼터는 개별 키워드 실패가 아니라 실행 전체의 중단 사유입니다.
            # 남은 키워드를 계속 시도하면 전부 같은 이유로 실패합니다.
            db.rollback()
            kw.status = "quota_wait"
            db.commit()
            failures.append(str(e))
            logger.warning("[discover] 쿼터 소진 — 남은 키워드는 내일 처리합니다")
            break
        except YouTubeError as e:
            db.rollback()
            failures.append(f'"{kw.term}" — {e}')
            logger.error("[discover] %s 실패: %s", kw.term, e)

    run.stats = totals
    run.finished_at = now_kst()
    if failures and not totals["discovered"]:
        run.status = "failed"
    elif failures:
        run.status = "partial"
    else:
        run.status = "succeeded"
    run.error = " / ".join(failures) if failures else None
    db.commit()
    return run, results
