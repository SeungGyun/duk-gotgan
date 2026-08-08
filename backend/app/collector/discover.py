"""발견 단계 — 키워드 하나로 후보 영상을 모아 임시 저장소에 넣습니다.

파이프라인 1~2단계(SPEC §4.2). 여기까지는 **LLM 이 전혀 없습니다.**
검색과 필터는 규칙이 명확해서 코드가 더 싸고 정확하고 재현됩니다.

같은 영상을 두 키워드가 데려와도 `videos` 행은 하나입니다. PK 가 유튜브
video id 라서 중복 처리가 구조적으로 막힙니다 — 자막 수집과 요약이 두 번
일어나면 그대로 두 배 비용입니다.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector import channels, quota, rules
from app.collector.youtube import (
    UNITS_PLAYLIST,
    Candidate,
    YouTubeError,
    fetch_details,
    playlist_video_ids,
    search_ids,
)
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
    deferred: int = 0  # 룰은 통과했지만 이번 실행 상한에 걸려 다음으로 미룬 것
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

    # **한 번만 계산합니다.** 검색에 넘기는 값과 룰 필터의 기준이 같아야
    # 합니다 — 따로 재면 그 사이 몇 초 때문에 경계에 걸친 영상을 100유닛
    # 써서 받아 놓고 "오래됨" 으로 떨어뜨립니다.
    cutoff = rules.window_start(kw)

    # 쿼터를 먼저 확인하고 차감합니다. 호출하고 나서 재면 이미 늦습니다.
    if kw.source_type == "channel":
        # 업로드 목록은 1유닛 — 검색(100유닛)의 1/100 입니다.
        _spend(db, run, UNITS_PLAYLIST)
        if not kw.uploads_playlist_id:
            result.error = "채널 정보가 없습니다. 키워드를 다시 등록해 주세요."
            return result
        ids = playlist_video_ids(kw.uploads_playlist_id, limit=SEARCH_PAGE_SIZE)
    else:
        _spend(db, run, quota.UNITS_SEARCH)
        ids = search_ids(
            kw.term,
            language=kw.language,
            published_after=cutoff,
            limit=SEARCH_PAGE_SIZE,
            min_duration_sec=kw.min_duration_sec,
        )

    if not ids:
        return result

    _spend(db, run, quota.UNITS_VIDEOS)
    candidates = fetch_details(ids)
    result.discovered = len(candidates)

    # 차단 목록은 후보마다 조회하면 N+1 이라 한 번만 읽습니다
    blocked = channels.blocked_ids(db)

    # 1회 실행당 자막·AI 로 넘길 편수 상한. **비용 가드입니다.**
    # 이게 없으면 첫 실행에서 백로그 50건이 통째로 AI 로 넘어갑니다.
    budget = kw.max_per_run

    # 검색 순위대로 처리합니다 — 상한에 걸려 잘리는 것이 하위 순위여야 합니다.
    for c in sorted(candidates, key=lambda x: x.search_rank):
        existing = db.get(Video, c.video_id)
        if existing is not None:
            # **완전삭제(EXCLUDED)와 미리 뺀 것(SKIPPED)은 다시 데려오지
            # 않습니다.** 링크도 걸지 않습니다 — 링크가 남으면 키워드
            # 화면의 편수에 잡혀서, 지운 것이 목록에는 없는데 숫자로는
            # 세어지는 상태가 됩니다.
            if existing.state in ("EXCLUDED", "SKIPPED"):
                continue
            _link(db, c, kw, run)
            result.already_known += 1
            # 지난 실행에서 상한에 걸려 미뤄둔 것이면 이번에 올려 보냅니다.
            # 안 그러면 영영 대기 상태로 남습니다.
            if existing.state == "DISCOVERED" and budget > 0:
                existing.state = "TRANSCRIPT_PENDING"
                existing.state_reason = None
                _event(db, existing.id, run.id, "DISCOVERED", existing.state, "discover", True, None)
                budget -= 1
                result.rule_passed += 1
            continue

        verdict = rules.evaluate(c, kw, blocked, cutoff=cutoff)
        if not verdict.ok:
            state, reason = "REJECTED_RULE", verdict.reason
            result.rejected.append((c.title, verdict.reason))
        elif budget > 0:
            state, reason = "TRANSCRIPT_PENDING", None
            budget -= 1
            result.rule_passed += 1
        else:
            # 룰은 통과했는데 이번 실행 상한을 넘었습니다. 버리지 않고
            # DISCOVERED 로 남겨 다음 실행에서 이어받습니다.
            state, reason = "DISCOVERED", f"1회 상한 {kw.max_per_run}편 초과 — 다음 실행에서 처리"
            result.deferred += 1

        video = _insert_video(db, c, state=state, reason=reason)
        _link(db, c, kw, run)
        _event(db, video.id, run.id, None, state, "discover", verdict.ok, reason)

    db.flush()
    return result


def _spend(db: Session, run: CrawlRun, units: int) -> None:
    """쿼터를 깎고 실행 이력에도 반영합니다. 둘 다 즉시 커밋합니다 —
    뒤에서 적재가 실패해 롤백돼도 "유닛은 실제로 썼다"가 남아야 합니다."""
    quota.spend(db, units)  # 여기서 커밋
    run.youtube_units += units
    db.commit()


def _insert_video(db: Session, c: Candidate, *, state: str, reason: str | None) -> Video:
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
        state=state,
        state_reason=reason,
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
    db: Session,
    keyword_ids: list[str] | None = None,
    trigger: str = "manual",
    run: CrawlRun | None = None,
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

    # 사이클이 이미 만들어 둔 실행 기록이 있으면 거기에 이어 씁니다.
    # 없을 때만 새로 만들고, **만든 쪽이 마감까지 책임집니다** — 안 그러면
    # CLI 로 직접 부를 때 기록이 running 인 채로 남습니다.
    owns_run = run is None
    if owns_run:
        run = CrawlRun(trigger=trigger, status="running", started_at=now_kst(), stats={})
        db.add(run)
    # **어떤 키워드로 돌았는지 이름을 남깁니다.** "키워드 8개"만으로는
    # 실행 로그를 봐도 무엇 때문에 돈 것인지 알 수 없습니다. 많으면
    # 앞 세 개만 적고 나머지는 수로 줄입니다 — 한 줄에 들어가야 읽힙니다.
    names = [k.channel_title or k.term for k in targets]
    head = ", ".join(names[:3])
    what = head + (f" 외 {len(names) - 3}개" if len(names) > 3 else "") or "대상 없음"
    run.label = f"{what} · {'수동' if trigger == 'manual' else '정기'} 실행"
    run.status = "running"
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

    stats = dict(run.stats or {})
    stats.update(totals)
    run.stats = stats
    if failures:
        run.error = " / ".join(failures)
    if owns_run:
        run.finished_at = now_kst()
        if failures and not totals["discovered"]:
            run.status = "failed"
        elif failures:
            run.status = "partial"
        else:
            run.status = "succeeded"
    db.commit()
    return run, results
