"""행 → 계약 형태(camelCase) 변환.

프론트의 `src/api/types.ts` 가 정본입니다. 여기가 그 형태를 만드는 유일한 곳이고,
라우트는 딕셔너리를 조립하지 않습니다 — 필드 하나가 빠지면 UI 가 조용히 빈 칸을
그리기 때문에, 형태를 만드는 자리를 한 곳으로 묶어 둡니다.
"""

from dataclasses import dataclass

from app.db.models import CrawlRun, Keyword, Lecture
from config.time import to_date_str, to_utc_iso


@dataclass(frozen=True)
class Marks:
    """읽음·즐겨찾기·제외 — **보는 사람마다 다른 값**입니다.

    예전에는 `lectures` 컬럼에서 바로 읽었는데, 그러면 한 사람이 읽음
    표시한 것이 모두에게 읽음으로 보입니다. 강의 행에서 떼어 내 여기로
    받으면, 값을 어디서 가져올지 정하는 자리가 라우트 한 곳으로 모입니다.
    """

    is_read: bool = False
    is_favorite: bool = False
    is_excluded: bool = False


NO_MARKS = Marks()


def keyword_out(k: Keyword, lecture_count: int = 0) -> dict:
    return {
        "id": k.id,
        "term": k.term,
        "sourceType": k.source_type,
        # 채널 구독이면 해석된 채널명. 화면은 이걸 제목으로 보여줍니다.
        "channelTitle": k.channel_title,
        "status": k.status,
        "language": k.language,
        "schedule": k.schedule,
        "minDurationSec": k.min_duration_sec,
        "minExpertScore": k.min_expert_score,
        "maxPerRun": k.max_per_run,
        "lectureCount": lecture_count,
        "lastRunAt": to_utc_iso(k.last_run_at),
        "createdAt": to_utc_iso(k.created_at),
        "archivedAt": to_utc_iso(k.archived_at),
    }


def lecture_summary_out(lec: Lecture, keyword_ids: list[str], marks: Marks = NO_MARKS) -> dict:
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
        "isFavorite": marks.is_favorite,
        "isRead": marks.is_read,
        "isExcluded": marks.is_excluded,
        # 곳간에 들어온 시각. "새로 온 것" 개수를 셀 때 기준으로 씁니다 —
        # 브라우저 시계를 쓰면 몇 초 어긋나 새 글을 놓칠 수 있습니다.
        "addedAt": to_utc_iso(lec.published_at),
        "keywordIds": keyword_ids,
    }


def lecture_detail_out(
    lec: Lecture, keyword_ids: list[str], ev, transcript, marks: Marks = NO_MARKS
) -> dict:
    out = lecture_summary_out(lec, keyword_ids, marks)
    out.update(
        {
            "youtubeUrl": f"https://youtu.be/{lec.video_id}",
            "abstract": lec.abstract,
            "abstractBeats": lec.abstract_beats or [],
            # 요약의 본체. 비어 있으면 옛 형식이라 UI 가 예전 배치로 떨어집니다
            "sections": lec.sections or [],
            "closing": lec.closing or "",
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
        "job": r.job or "cycle",
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
