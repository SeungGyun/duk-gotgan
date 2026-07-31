"""행 → 계약 형태(camelCase) 변환.

프론트의 `src/api/types.ts` 가 정본입니다. 여기가 그 형태를 만드는 유일한 곳이고,
라우트는 딕셔너리를 조립하지 않습니다 — 필드 하나가 빠지면 UI 가 조용히 빈 칸을
그리기 때문에, 형태를 만드는 자리를 한 곳으로 묶어 둡니다.
"""

from app.db.models import CrawlRun, Keyword, Lecture
from config.time import to_date_str, to_utc_iso


def keyword_out(k: Keyword, lecture_count: int = 0) -> dict:
    return {
        "id": k.id,
        "term": k.term,
        "status": k.status,
        "language": k.language,
        "schedule": k.schedule,
        "minDurationSec": k.min_duration_sec,
        "minExpertScore": k.min_expert_score,
        "maxPerRun": k.max_per_run,
        "lectureCount": lecture_count,
        "lastRunAt": to_utc_iso(k.last_run_at),
        "createdAt": to_utc_iso(k.created_at),
    }


def lecture_summary_out(lec: Lecture, keyword_ids: list[str]) -> dict:
    v = lec.video
    return {
        "videoId": lec.video_id,
        "title": v.title if v else "",
        "channelTitle": v.channel_title if v else "",
        "durationSec": lec.duration_sec,
        # 계약상 날짜만 — 영상 공개일이지 우리가 수집한 날이 아닙니다
        "publishedAt": to_date_str(v.published_at) if v and v.published_at else None,
        "expertScore": lec.expert_score,
        "verdict": lec.verdict,
        "oneLiner": lec.one_liner,
        "tags": lec.tags or [],
        "keyPointOffsets": [p.get("timestampSec", 0) for p in (lec.key_points or [])],
        "isFavorite": bool(lec.is_favorite),
        "keywordIds": keyword_ids,
    }


def lecture_detail_out(lec: Lecture, keyword_ids: list[str], ev, transcript) -> dict:
    out = lecture_summary_out(lec, keyword_ids)
    out.update(
        {
            "youtubeUrl": f"https://youtu.be/{lec.video_id}",
            "abstract": lec.abstract,
            "targetAudience": lec.target_audience,
            "prerequisites": lec.prerequisites or [],
            "keyPoints": lec.key_points or [],
            "chapters": lec.chapters or [],
            "terms": lec.terms or [],
            "takeaways": lec.takeaways or [],
            "quotes": lec.quotes or [],
            "coverageNote": lec.coverage_note,
            "review": _review_out(lec, ev),
            "transcriptExpiresAt": to_utc_iso(transcript.expires_at) if transcript else None,
        }
    )
    return out


def _review_out(lec: Lecture, ev) -> dict:
    """판정 근거. 판정 이력이 지워졌어도 UI 가 깨지지 않게 빈 값을 채웁니다."""
    if ev is None:
        return {
            "model": lec.model or "",
            "promptVersion": "v1",
            "confidence": "medium",
            "criteria": [],
            "redFlags": [],
            "speakerCredentials": "",
            "inputTokens": 0,
            "outputTokens": 0,
            "turns": 0,
        }
    return {
        "model": ev.model,
        "promptVersion": ev.prompt_version,
        "confidence": ev.confidence,
        "criteria": ev.criteria or [],
        "redFlags": ev.red_flags or [],
        "speakerCredentials": ev.speaker_credentials or "",
        "inputTokens": ev.input_tokens,
        "outputTokens": ev.output_tokens,
        "turns": ev.turns,
    }


def run_out(r: CrawlRun) -> dict:
    stats = r.stats or {}
    return {
        "id": r.id,
        "label": r.label,
        "trigger": r.trigger,
        "status": r.status,
        "startedAt": to_utc_iso(r.started_at),
        "finishedAt": to_utc_iso(r.finished_at),
        "stats": {
            "discovered": stats.get("discovered", 0),
            "rulePassed": stats.get("rulePassed", 0),
            "transcribed": stats.get("transcribed", 0),
            "reviewed": stats.get("reviewed", 0),
            "published": stats.get("published", 0),
        },
        "tokens": r.input_tokens + r.output_tokens,
        "youtubeUnits": r.youtube_units,
        "error": r.error,
    }
