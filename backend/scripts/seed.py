"""목 데이터를 실제 테이블에 넣습니다.

  python -m scripts.seed          # 없는 것만 넣기 (여러 번 돌려도 안전)
  python -m scripts.seed --reset  # 싹 지우고 다시 넣기

수집 파이프라인(3단계)이 아직 없어서, 이걸 돌리지 않으면 강의 화면이 비어 있습니다.
데이터는 프론트의 `src/api/mock.ts` 에서 그대로 뽑아 `data/seed.json` 에 넣어 둔 것이라,
목 모드와 http 모드가 같은 화면을 보여 줍니다 — 붙였을 때 뭐가 달라졌는지 판단하기
쉬우라고 일부러 같게 뒀습니다.
"""

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select

from app.db.models import (
    CrawlRun,
    Evaluation,
    Keyword,
    Lecture,
    Transcript,
    UsageLedger,
    Video,
    VideoKeyword,
)
from app.db.session import SessionLocal, init_db
from config.time import KST, now_kst

SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed.json"


def _dt(value: str | None) -> datetime | None:
    """`2026-07-30T19:02:00.000Z` → KST naive.

    DB 는 KST naive 로 저장합니다(config/time.py). UTC 문자열을 그대로 넣으면
    9시간 밀린 값이 들어갑니다.
    """
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(KST).replace(tzinfo=None)


def _date(value: str | None) -> datetime | None:
    """`2026-06-18` (날짜만) → naive datetime."""
    if not value:
        return None
    return datetime.strptime(value[:10], "%Y-%m-%d")


def _search_text(detail: dict) -> str:
    """전문 검색 대상. 제목·채널·요약·태그·용어를 한 칸에 이어 붙입니다."""
    parts = [
        detail.get("title", ""),
        detail.get("channelTitle", ""),
        detail.get("oneLiner", ""),
        detail.get("abstract", ""),
        " ".join(detail.get("tags") or []),
        " ".join(t.get("term", "") for t in detail.get("terms") or []),
        " ".join(p.get("heading", "") for p in detail.get("keyPoints") or []),
    ]
    return "\n".join(p for p in parts if p)


def reset(db) -> None:
    # 자식부터 지웁니다 (FK)
    for model in (Lecture, Evaluation, Transcript, VideoKeyword, Video, CrawlRun, Keyword):
        db.execute(delete(model))
    db.execute(delete(UsageLedger))
    db.commit()


def run(do_reset: bool = False) -> None:
    init_db()
    payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    db = SessionLocal()
    try:
        if do_reset:
            reset(db)

        # ── 키워드 ─────────────────────────────────────────
        # 목의 id("kw_1")를 그대로 PK 로 씁니다. 강의-키워드 연결이 이 id 를
        # 참조하고 있어서, 새 uuid 를 발급하면 매칭이 끊깁니다.
        kw_terms = {}
        for k in payload["keywords"]:
            kw_terms[k["id"]] = k["term"]
            if db.get(Keyword, k["id"]):
                continue
            db.add(
                Keyword(
                    id=k["id"],
                    term=k["term"],
                    status=k["status"],
                    language=k["language"],
                    schedule=k["schedule"],
                    min_duration_sec=k["minDurationSec"],
                    min_expert_score=k["minExpertScore"],
                    max_per_run=k["maxPerRun"],
                    last_run_at=_dt(k.get("lastRunAt")),
                    created_at=_dt(k.get("createdAt")) or now_kst(),
                )
            )
        db.commit()

        # ── 영상 · 강의 ────────────────────────────────────
        for d in payload["details"]:
            vid = d["videoId"]
            if db.get(Video, vid):
                continue

            db.add(
                Video(
                    id=vid,
                    title=d["title"],
                    channel_title=d["channelTitle"],
                    published_at=_date(d.get("publishedAt")),
                    duration_sec=d["durationSec"],
                    has_official_caption=True,
                    state="PUBLISHED",
                    discovered_at=now_kst(),
                )
            )
            # 자식(evaluations·transcripts·lectures)이 이 행을 FK 로 참조합니다.
            # autoflush 순서에 맡기지 않고 부모를 먼저 내보냅니다.
            db.flush()

            for kid in d.get("keywordIds") or []:
                if kid in kw_terms:
                    db.add(VideoKeyword(video_id=vid, keyword_id=kid))

            review = d.get("review") or {}
            db.add(
                Evaluation(
                    video_id=vid,
                    model=review.get("model", ""),
                    prompt_version=review.get("promptVersion", "v1"),
                    verdict=d["verdict"],
                    expert_score=d["expertScore"],
                    confidence=review.get("confidence", "medium"),
                    criteria=review.get("criteria") or [],
                    red_flags=review.get("redFlags") or [],
                    speaker_credentials=review.get("speakerCredentials"),
                    input_tokens=review.get("inputTokens", 0),
                    output_tokens=review.get("outputTokens", 0),
                    turns=review.get("turns", 0),
                )
            )

            expires = _dt(d.get("transcriptExpiresAt"))
            db.add(
                Transcript(
                    video_id=vid,
                    source="youtube_manual",
                    language="ko",
                    # 자막 원문은 시드에 없습니다 — 보관 만료 표시만 살립니다
                    content=None,
                    char_count=0,
                    est_tokens=review.get("inputTokens", 0),
                    expires_at=expires,
                )
            )

            db.add(
                Lecture(
                    video_id=vid,
                    version=1,
                    expert_score=d["expertScore"],
                    verdict=d["verdict"],
                    duration_sec=d["durationSec"],
                    published_at=now_kst(),
                    is_favorite=bool(d.get("isFavorite")),
                    model=review.get("model", ""),
                    one_liner=d["oneLiner"],
                    abstract=d.get("abstract", ""),
                    target_audience=d.get("targetAudience", ""),
                    prerequisites=d.get("prerequisites") or [],
                    key_points=d.get("keyPoints") or [],
                    chapters=d.get("chapters") or [],
                    terms=d.get("terms") or [],
                    takeaways=d.get("takeaways") or [],
                    quotes=d.get("quotes") or [],
                    tags=d.get("tags") or [],
                    coverage_note=d.get("coverageNote"),
                    search_text=_search_text(d),
                )
            )
        db.commit()

        # ── 실행 이력 ──────────────────────────────────────
        for r in payload["runs"]:
            if db.get(CrawlRun, r["id"]):
                continue
            tokens = r.get("tokens", 0)
            db.add(
                CrawlRun(
                    id=r["id"],
                    label=r["label"],
                    trigger=r["trigger"],
                    status=r["status"],
                    started_at=_dt(r.get("startedAt")) or now_kst(),
                    finished_at=_dt(r.get("finishedAt")),
                    stats=r.get("stats") or {},
                    # 목은 합계만 들고 있습니다. 실제 수집기가 붙으면 입력/출력을
                    # 따로 기록하므로, 여기서는 전부 입력 쪽에 넣어 둡니다.
                    input_tokens=tokens,
                    output_tokens=0,
                    youtube_units=r.get("youtubeUnits", 0),
                    error=r.get("error"),
                )
            )
        db.commit()

        # ── 오늘 사용량 ────────────────────────────────────
        usage = payload["usage"]
        overview = payload["overview"]
        today = now_kst().date()
        ledger = db.get(UsageLedger, today)
        if ledger is None:
            ledger = UsageLedger(day=today)
            db.add(ledger)
        ledger.input_tokens = usage["inputTokens"]
        ledger.output_tokens = usage["outputTokens"]
        ledger.youtube_units = usage["youtubeUnits"]
        ledger.llm_calls = overview["funnel"]["reviewed"]
        ledger.early_exit_count = overview["earlyExitCount"]
        ledger.early_exit_saved_input_tokens = overview["earlyExitSavedInputTokens"]
        db.commit()

        # ── 실패 사례 ──────────────────────────────────────
        # 대시보드의 "최근 실패" 는 FAILED 영상에서 만들어집니다.
        for f in overview.get("failures", []):
            vid = f"fail_{abs(hash(f['title'])) % 10**8}"
            if db.get(Video, vid):
                continue
            db.add(
                Video(
                    id=vid,
                    title=f["title"],
                    channel_title="",
                    duration_sec=0,
                    state="FAILED",
                    state_reason=f["detail"],
                    discovered_at=now_kst() - timedelta(hours=1),
                )
            )
        db.commit()

        counts = {
            name: db.scalar(select(func.count()).select_from(model))
            for name, model in (
                ("keywords", Keyword),
                ("videos", Video),
                ("lectures", Lecture),
                ("runs", CrawlRun),
            )
        }
        print("적재 완료:", counts)
    finally:
        db.close()


def _has_real_data() -> bool:
    """실제로 수집한 덕질이 이미 있는가."""
    from sqlalchemy import func, select

    from app.db.models import Lecture
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        fake = {d["videoId"] for d in json.loads(SEED_PATH.read_text())["details"]}
        n = db.scalar(
            select(func.count()).select_from(Lecture).where(Lecture.video_id.notin_(fake))
        )
        return bool(n)
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="목 데이터를 DB 에 적재")
    parser.add_argument("--reset", action="store_true", help="기존 데이터를 지우고 다시 넣기")
    parser.add_argument(
        "--force", action="store_true", help="실제 데이터가 있어도 강행합니다"
    )
    args = parser.parse_args()

    # **실제 데이터가 있으면 막습니다.**
    #
    # 여기 영상 id 는 손으로 만든 자리표시자(aX7kQ2mN9pL 처럼 a·b·c·d·e 로
    # 시작)라 유튜브에 존재하지 않습니다. 화면을 만들던 시절의 예시인데,
    # 실제 수집이 도는 DB 에 섞이면 **링크를 눌러도 없는 영상**이 나오고
    # 평균 점수 같은 통계까지 흐려집니다. 실제로 그런 일이 있었습니다.
    if _has_real_data() and not args.force:
        raise SystemExit(
            "실제로 수집한 덕질이 이미 있습니다. 이 스크립트는 유튜브에 없는\n"
            "예시 영상을 심으므로, 섞이면 링크가 깨진 덕질이 생깁니다.\n"
            "정말 넣으려면 --force 를 붙이세요."
        )
    run(do_reset=args.reset)
