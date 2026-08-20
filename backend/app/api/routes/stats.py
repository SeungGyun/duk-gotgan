"""운영 지표 — docs/API.md §3.

수집 파이프라인이 아직 없으므로 대부분 0 입니다. 0 을 감추려고 값을 지어내지
않습니다 — UI 는 0 을 받으면 해당 칩·미터를 숨기도록 만들어져 있습니다.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import current_user, require_owner
from app.api.errors import ApiError
from app.api.routes.lectures import Filters, _filtered
from app.api.serializers import run_out
from app.blog import publish
from app.collector import cadence, quota, resources, transcript, upkeep
from app.collector.schedule import next_due_at
from app.llm import pace
from app.llm import usage as usage_guard
from app.db.models import (
    BlogPost,
    CrawlRun,
    Keyword,
    Lecture,
    Evaluation,
    PipelineEvent,
    Transcript,
    UsageLedger,
    User,
    UserKeyword,
    Video,
    VideoKeyword,
)
from app.db.session import get_db
from config.settings import settings
from config.time import KST, now_kst, to_utc_iso

router = APIRouter(tags=["stats"])

@router.get("/stats/overview")
def overview(db: Session = Depends(get_db), user: User = Depends(current_user)):
    today = now_kst().date()
    day_start = _midnight(today)
    week_start = _midnight(today - timedelta(days=7))

    # **보이는 범위를 목록 화면과 같은 함수로 셉니다.** 여기서 따로 조건을
    # 쓰면 상단바에는 332편인데 목록에는 41편인 상황이 생기고, 그러면
    # 어느 쪽이 고장인지 알 수 없게 됩니다.
    def _count(*where):
        stmt, _ = _filtered(Filters(user.id))
        if where:
            stmt = stmt.where(*where)
        return int(db.scalar(stmt.with_only_columns(func.count()).order_by(None)) or 0)

    published = _count()
    new_today = _count(Lecture.published_at >= day_start)
    week_added = _count(Lecture.published_at >= week_start)
    mine, _ul = _filtered(Filters(user.id))

    # 상단바 메뉴에 붙는 숫자입니다. **안 본 것만 셉니다** — 전체를 세면
    # 아무리 읽어도 숫자가 그대로라 "얼마나 밀렸나"를 읽을 수가 없습니다.
    # 조건은 조인된 별칭에 걸리므로 `_count` 를 못 씁니다(호출마다 별칭이
    # 새로 생깁니다).
    unread_stmt, unread_ul = _filtered(Filters(user.id))
    unread = int(
        db.scalar(
            unread_stmt.where(unread_ul.read_at.is_(None))
            .with_only_columns(func.count())
            .order_by(None)
        )
        or 0
    )
    avg_score = db.scalar(
        mine.with_only_columns(func.avg(Lecture.expert_score)).order_by(None)
    )

    # **"오늘 한 일" 기준입니다.**
    #
    # 예전에는 "오늘 발견된 영상"이 각 칸까지 갔는지를 셌습니다. 한 사이클이
    # 몇 분 안에 발견→자막→요약을 다 하던 때는 맞는 셈법이었지만, 셋을
    # 따로 돌리고 대기가 몇 시간씩 쌓이는 지금은 오늘 발견한 것이 내일
    # 요약됩니다. 그래서 꼬리 세 칸이 늘 0 이었습니다 — 오늘 16편을
    # 공개했는데도요.
    def _today_count(model, when):
        return int(db.scalar(select(func.count()).select_from(model).where(when >= day_start)) or 0)

    discovered = _count_videos(db, day_start)
    rule_passed = _count_videos(db, day_start, exclude_state="DISCOVERED")
    transcribed = _today_count(Transcript, Transcript.created_at)
    reviewed = _today_count(Evaluation, Evaluation.created_at)
    published_today = new_today or 0

    ledger = db.get(UsageLedger, today)

    # 오늘 어느 키워드가 몇 편을 데려왔는지. **내가 구독한 것만** 셉니다 —
    # 남의 키워드가 올린 실적은 내 화면에서 읽을 수 없는 강의입니다.
    contributions = db.execute(
        select(
            Keyword.id,
            Keyword.term,
            func.count(func.distinct(Lecture.video_id)),
            Keyword.archived_at,
        )
        .join(VideoKeyword, VideoKeyword.keyword_id == Keyword.id)
        .join(Lecture, Lecture.video_id == VideoKeyword.video_id)
        .join(UserKeyword, UserKeyword.keyword_id == Keyword.id)
        .where(
            Lecture.is_hidden.is_(False),
            Lecture.published_at >= day_start,
            UserKeyword.user_id == user.id,
            UserKeyword.archived_at.is_(None),
        )
        .group_by(Keyword.id, Keyword.term, Keyword.archived_at)
        .order_by(func.count(func.distinct(Lecture.video_id)).desc())
        .limit(8)
    ).all()

    last_run = db.scalar(select(CrawlRun).order_by(CrawlRun.started_at.desc()).limit(1))

    return {
        "newToday": new_today or 0,
        "totalLectures": published or 0,
        "unreadLectures": unread,
        "weekAdded": week_added or 0,
        "avgScore": round(float(avg_score)) if avg_score is not None else 0,
        "queued": {
            # 'RULE_PASSED' 라는 상태를 세고 있었습니다 — 그런 상태는 없어서
            # 자막 대기가 늘 0 으로 보였습니다. 실제로는 86건이었습니다.
            "transcript": _count_videos(db, states=("TRANSCRIPT_PENDING", "TRANSCRIBING")),
            "review": _count_videos(db, states=("TRANSCRIBED", "REVIEWING")),
        },
        "funnel": {
            "discovered": discovered,
            "rulePassed": rule_passed,
            "transcribed": transcribed,
            "reviewed": reviewed,
            "published": published_today,
        },
        "contributions": [
            # 지운 키워드는 괄호로 표시합니다. 이름만 그대로 두면 지금도
            # 도는 키워드처럼 보여서, 왜 저기서 안 나오나 헤매게 됩니다.
            {"keywordId": kid, "term": f"({term})" if archived else term, "published": cnt}
            for kid, term, cnt, archived in contributions
        ],
        "failures": _failures(db),
        "lastRunAt": to_utc_iso(last_run.started_at) if last_run else None,
    }


@router.get("/stats/usage")
def usage(db: Session = Depends(get_db), _: User = Depends(current_user)):
    today = now_kst().date()
    ledger = db.get(UsageLedger, today)
    win_input, win_output = usage_guard.window_totals(db)

    # **회사별로 나눠서도 내려보냅니다.** 상한이 각 구독에 따로 걸리는데
    # 합친 숫자만 보면 어느 쪽이 닿아서 멈췄는지 알 수 없습니다 — 실제로
    # 한쪽 쿼터가 떨어졌는데 화면에는 "많이 썼네"로만 보였습니다.
    providers = usage_guard.window_by_provider(db)

    # 합계 상한은 **회사별 상한의 합**입니다. 하나라도 무제한이면 합계도
    # 무제한입니다 — 남은 것들만 더해 놓으면 상단 미터가 실제보다 빨리
    # 차 보여서, 아직 여유가 있는데도 아껴 쓰게 만듭니다.
    caps = [p["limitTokens"] for p in providers]
    total_cap = None if any(c is None for c in caps) else sum(caps)

    # **토큰은 5시간 창, 유튜브는 하루** — 주기가 다릅니다. 한 숫자로
    # 합치면 둘 중 하나는 틀린 기준으로 보이게 됩니다.
    return {
        "inputTokens": win_input,
        "outputTokens": win_output,
        "limitTokens": total_cap,
        "providers": providers,
        "windowHours": settings.token_window_hours,
        "windowResetsAt": to_utc_iso(usage_guard.window_end()),
        # 오늘 하루 합계 — 창과 별개로 "오늘 얼마나 했나"를 보려는 값입니다.
        "todayTokens": (ledger.input_tokens + ledger.output_tokens) if ledger else 0,
        "youtubeUnits": ledger.youtube_units if ledger else 0,
        "youtubeUnitLimit": settings.youtube_unit_limit,
        # 유튜브 쿼터는 태평양 표준시 자정에 리셋됩니다. 우리 집계는 KST 날짜
        # 기준이므로, 여기서는 "다음 KST 자정"을 알려 줍니다.
        "resetsAt": to_utc_iso(_midnight(today + timedelta(days=1))),
    }


@router.get("/runs")
def list_runs(db: Session = Depends(get_db), _: User = Depends(current_user)):
    """**블로그 발행은 뺍니다.** 한 편에 실행 기록이 하나씩 남는데 30~60분마다
    한 편이 나가서, 반나절이면 이 목록이 통째로 블로그 줄로 덮였습니다 —
    검색·자막·요약이 무엇을 했는지 보러 오는 화면인데 그게 안 보입니다.

    게다가 발행 잡은 `pipeline_events` 를 남기지 않아서, 펼쳐 봐야
    "옮긴 영상이 없습니다" 한 줄뿐입니다. 자리만 먹고 읽을 것이 없었습니다.
    기록 자체는 그대로 쌓아 둡니다(토큰 집계가 봅니다). 발행 이력은
    `/stats/pipeline` 의 `blog` 에 최근 것만 묶어서 나갑니다.
    """
    rows = db.scalars(
        select(CrawlRun)
        .where(CrawlRun.job != "publish")
        .order_by(CrawlRun.started_at.desc())
        .limit(50)
    ).all()
    return [run_out(r) for r in rows]


# 파이프라인 각 칸에 지금 몇 개가 서 있는지, 그리고 **세 트랙이 각각
# 무엇을 하는 중인지**. 실행 기록만 봐서는 알 수 없습니다 — 기록은 지나간
# 일이고, 사용자가 궁금한 것은 지금 상태니까요.
#
# 셋을 따로 돌리게 된 뒤로 "지금 도는 실행" 하나만 보여 주면 거짓말이
# 됩니다. 자막과 요약이 나란히 도는데 화면에는 나중에 시작한 것만
# 떴습니다.
_FUNNEL = [
    ("discovered", "발견", ("DISCOVERED",)),
    ("transcript", "자막 대기", ("TRANSCRIPT_PENDING",)),
    ("review", "요약 대기", ("TRANSCRIBED",)),
    ("published", "공개", ("PUBLISHED",)),
]
_STUCK = [
    ("failedTranscript", "자막 실패", ("FAILED_TRANSCRIPT", "FAILED")),
    ("failedReview", "요약 실패", ("FAILED_REVIEW",)),
]

# 트랙마다 "지금 붙들고 있는 영상"이 어느 상태로 나타나는지.
_WORKING = {"transcript": "TRANSCRIBING", "review": "REVIEWING"}

# 블로그는 최근 몇 편만 보냅니다. 전부 보내면 실행 목록을 덮던 문제를
# 자리만 옮기는 셈입니다 — 여기서 알고 싶은 것은 "돌고 있나"이지
# 발행 이력 전부가 아닙니다. 전체는 블로그에 가서 봅니다.
_BLOG_RECENT = 5


def _blog(db: Session) -> dict:
    """블로그 발행의 지금 상태.

    **트랙으로 만들지 않았습니다.** 트랙은 "지금 붙들고 있는 영상"이 있는
    일인데, 발행은 한 편을 올리고 끝나 붙들고 있는 것이 없습니다. 알고
    싶은 것은 다음 차례가 언제고 그동안 몇 편이 나갔나입니다.
    """
    posted = db.scalars(
        select(BlogPost)
        .where(BlogPost.state == "POSTED")
        .order_by(BlogPost.posted_at.desc())
        .limit(_BLOG_RECENT)
    ).all()

    def count(st: str) -> int:
        return db.scalar(select(func.count()).select_from(BlogPost).where(BlogPost.state == st)) or 0

    return {
        # 꺼져 있으면 화면에서 통째로 감춥니다. 기본이 꺼짐이라, 안 켠
        # 사람에게 "대기 0 · 0편"을 보이면 고장으로 읽힙니다.
        "enabled": settings.blog_enabled,
        "nextAt": to_utc_iso(publish.next_at(db)),
        "waiting": publish.remaining(db),
        "posted": count("POSTED"),
        # 오늘 몇 편 / 하루 몇 편까지. **화면이 이걸 보여야 합니다** —
        # 없을 때는 "왜 안 올라가지" 의 답이 워커 로그 안에만 있었습니다.
        "postedToday": publish.posted_today(db),
        "dailyCap": settings.blog_daily_cap,
        # 세션이 죽으면 **사람이 카카오 로그인을 해야** 풀립니다. 우리가
        # 대신 할 수 없으니, 알아채는 길이 화면에 있어야 합니다.
        "sessionBadSince": to_utc_iso(publish.session_bad_since(db)),
        # 세 번 해 보고 접은 것. 사람이 손대야 풀리는 종류라 세어 둡니다.
        "failed": count("FAILED"),
        "recent": [
            {
                "at": to_utc_iso(b.posted_at),
                "title": b.title,
                "category": b.category,
                "postId": b.post_id,
                "url": b.url,
            }
            for b in posted
        ],
    }


# ── 지금 왜 이러고 있나 ──────────────────────────────────────
#
# **"쉬는 중" 한 마디로는 손댈지 기다릴지 정할 수 없습니다.** 자막 트랙이
# 반나절을 그 한 마디로 보낸 적이 있는데, 실제로는 오디오 내려받기가 막혀
# 자막이 있는 영상만 처리하는 중이었습니다. 그 사실도, 언제 풀리는지도
# 워커 로그 안에만 있었고 로그를 여는 사람은 이 집에 한 명뿐입니다.
#
# 그래서 트랙마다 **무엇 때문에 · 언제까지 · 그동안 무슨 일이 벌어지는지**를
# 문장으로 만들어 내려보냅니다. 셋으로 가릅니다.
#
#   info  기다리면 됩니다. 그동안 우회로로 일이 되고 있습니다.
#   warn  일부가 멎었습니다. 저절로 풀리지만 처리량이 줍니다.
#   stop  이 트랙은 지금 아무것도 못 합니다.
#
# 사람이 해야 할 일이 있으면 `fix` 에 적습니다 — 비어 있으면 기다리면
# 되는 일이라는 뜻입니다. 이 구분이 없으면 모든 줄이 똑같이 불안합니다.
_PROVIDER_LABEL = {"claude": "클로드", "antigravity": "안티그래비티"}


def _josa(word: str, with_batchim: str, without: str) -> str:
    """이름 뒤에 조사를 붙입니다.

    `f"{label} 가 쉬는 중"` 으로 두었더니 "클로드 가 쉬는 중입니다" 가
    나왔습니다. 회사 이름은 설정에서 오는 값이라 문장에 박아 둘 수 없고,
    띄어 쓰거나 "안티그래비티은" 처럼 틀리면 그 한 글자에서 기계가 쓴
    티가 납니다. 받침만 보면 되는 일입니다.
    """
    ch = word.strip()[-1:] or ""
    batchim = "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0
    return word + (with_batchim if batchim else without)


def _hold(
    code: str,
    title: str,
    detail: str,
    *,
    tone: str = "warn",
    until=None,
    since=None,
    fix: str | None = None,
    forcible: bool = False,
) -> dict:
    """`forcible` — **사람이 눌러서 앞당길 수 있는 멈춤인가.**

    처음에는 "막힌 것은 눌러서 넘기지 않는다"로 한 줄로 정했습니다. 차단된
    문을 두드리면 차단만 길어지니까요. 그런데 그건 **같은 IP 일 때** 맞는
    말이었습니다. 사람이 VPN 을 바꾸면 IP 가 달라지고, 냉각이 지키려던
    조건 자체가 사라집니다 — 그때는 04:09 까지 기다릴 이유가 없습니다.

    가르는 기준은 **사람이 조건을 바꿀 수 있는가**입니다.

      바꿀 수 있음   IP 차단(VPN·회선) · 회사 세션(계정 바꾸기)
      바꿀 수 없음   유튜브 하루 할당량(구글이 셉니다) · 메모리 · 모으는 중

    누르면 그냥 무시하는 게 아니라 **냉각을 지웁니다.** 조건이 바뀌었다고
    보는 것이므로 누적된 백오프도 같이 지웁니다 — 그 누적은 옛 IP 의
    것입니다. 그러고도 또 막히면 60분부터 다시 쌓입니다.
    """
    return {
        "code": code,
        "tone": tone,
        "title": title,
        "detail": detail,
        "until": to_utc_iso(until) if until else None,
        "since": to_utc_iso(since) if since else None,
        "fix": fix,
        "forcible": forcible,
    }


def _discover_hold(db: Session, now) -> dict | None:
    """검색이 멈추는 이유는 하나뿐입니다 — 유튜브 할당량.

    **장부를 읽기만 합니다.** `quota.remaining()` 은 오늘 행을 `FOR UPDATE`
    로 잠그고 없으면 만드는데, 화면이 5초마다 부르는 자리에서 할 일이
    아닙니다.
    """
    budget = int(settings.youtube_unit_limit * quota.SAFETY_MARGIN)
    ledger = db.get(UsageLedger, now.date())
    used = ledger.youtube_units if ledger else 0
    if used + quota.UNITS_SEARCH <= budget:
        return None
    return _hold(
        "youtube_quota",
        "오늘 쓸 유튜브 검색 할당량을 다 썼습니다",
        f"검색 한 번에 {quota.UNITS_SEARCH}유닛이 드는데 오늘 {used:,}/{budget:,} 유닛을 "
        "썼습니다. 이미 찾아 둔 영상은 그대로 자막·요약으로 넘어갑니다.",
        until=_midnight(now.date() + timedelta(days=1)),
    )


def _transcript_hold(db: Session, now) -> dict | None:
    """자막이 멈추는 이유는 **문이 둘**이라 셋으로 갈립니다.

    자막 경로만 막힌 것은 멈춤이 아니라 우회입니다 — 소리를 받아 직접
    받아쓰면 됩니다. 그런데 화면은 이 셋을 다 "쉬는 중" 으로 적었습니다.
    """
    caps = transcript.blocked_until(db)
    audio = transcript.audio_blocked_until(db)
    caps = caps if caps and caps > now else None
    audio = audio if audio and audio > now else None

    if caps and audio:
        return _hold(
            "transcript_blocked",
            "유튜브가 자막도 소리도 막았습니다",
            "자막 내려받기와 음성 파일 내려받기가 둘 다 막혔습니다. 여기서 더 두드리면 "
            "차단이 길어지기만 하므로 손을 뗍니다 — 시간이 지나면 저절로 풀립니다. "
            "회선을 바꿨다면(VPN·재접속) 기다릴 이유가 없으니 지금 시작하세요.",
            tone="stop",
            until=max(caps, audio),
            forcible=True,
        )
    if audio:
        return _hold(
            "audio_blocked",
            "음성 파일을 내려받지 못하고 있습니다",
            "소리를 받아 직접 받아쓰는 길이 막혔습니다. 그동안은 유튜브에 자막이 이미 "
            "있는 영상만 처리하고, 자막이 없는 영상은 줄에서 그대로 기다립니다. "
            "회선을 바꿨다면 지금 시작해도 됩니다.",
            until=audio,
            forcible=True,
        )
    if caps:
        return _hold(
            "captions_blocked",
            "유튜브 자막이 막혔습니다",
            "대신 소리를 받아 직접 받아쓰고 있습니다. 한 편에 2~7분이라 느리지만 "
            "이 길은 막히지 않습니다.",
            tone="info",
            until=caps,
            forcible=True,
        )
    return None


def _reviewers(db: Session) -> list[dict]:
    """요약을 나눠 하는 회사들의 지금.

    **한쪽만 쉬는 것과 둘 다 멎은 것은 완전히 다른 상황입니다.** 합쳐서
    "요약 쉬는 중" 이라고 적으면 그 차이가 사라집니다 — 실제로 안티그래비티
    쪽만 멎어 있는데 화면으로는 알 길이 없었습니다.

    **막히지 않은 것과 일하는 중인 것을 갈라 보냅니다.** 처음에는 막힘
    여부만 보냈는데, 화면이 그걸 "도는 중" 으로 읽었습니다. 그래서 요약
    대기가 0 이라 아무도 아무것도 안 하는 순간에 둘 다 "도는 중" 으로
    떴습니다 — 화면이 지어낸 말이 아니라, 우리가 답을 안 준 것입니다.

    누가 무엇을 쥐고 있는지는 `videos.claimed_by` 가 압니다. 좀비 회수가
    자기 회사 것만 골라내려고 회사 이름을 앞에 붙여 두었는데
    (`llm/runner.worker_id`), 같은 접두사로 여기서도 가릅니다.
    """
    busy: dict[str, Video] = {}
    for v in db.scalars(select(Video).where(Video.state == "REVIEWING")):
        name = (v.claimed_by or "").split(":", 1)[0]
        # 회사마다 락이 하나라 한 회사가 둘을 쥘 일은 없습니다. 그래도
        # 회수 직전의 좀비가 겹칠 수 있어 먼저 집은 쪽을 씁니다.
        if name and name not in busy:
            busy[name] = v

    out = []
    for name in usage_guard.PROVIDERS:
        resting = pace.resume_at(db, name)
        v = busy.get(name)
        out.append(
            {
                "provider": name,
                "label": _PROVIDER_LABEL.get(name, name),
                # 회사가 안 받아 주는 중 — 불러 봐야만 풀렸는지 알 수 있어
                # 타이머로 셉니다 (llm/pace.py).
                "restingUntil": to_utc_iso(resting) if resting else None,
                # 우리가 건 상한을 넘은 것 — **상한을 올리면 곧바로 재개**됩니다.
                "capped": pace.capped(db, name),
                # 지금 이 회사가 쥐고 있는 영상. 없으면 차례를 기다리는 중입니다.
                "working": (
                    {"title": v.title, "since": to_utc_iso(v.claimed_at or v.updated_at)}
                    if v is not None
                    else None
                ),
            }
        )
    return out


def _review_hold(db: Session, now, waiting: int, reviewers: list[dict]) -> dict | None:
    window_end = usage_guard.window_end()

    # 메모리가 먼저입니다. 여기 걸리면 회사가 멀쩡해도 아무것도 안 뜹니다.
    if resources.memory_tight():
        return _hold(
            "memory_tight",
            "메모리가 빡빡해 잠시 비켜서 있습니다",
            "받아쓰기가 긴 오디오를 통째로 올리는 중입니다. 이때 요약을 띄우면 뜨지도 "
            "못하고 죽으므로, 자리가 날 때까지 기다립니다.",
            tone="info",
        )

    down = [r for r in reviewers if r["restingUntil"] or r["capped"]]
    if not down:
        return _batching_hold(db, now, waiting)

    def why(r: dict) -> str:
        return (
            f"{_josa(r['label'], '은', '는')} 이번 창의 토큰 상한에 닿았습니다"
            if r["capped"]
            else f"{_josa(r['label'], '이', '가')} 지금 요청을 받지 않습니다"
        )

    # **늦게 풀리는 쪽을 적습니다.** 둘 다 멎었는데 이른 쪽을 적으면, 그
    # 시각이 지나도 아무 일이 없어 화면이 거짓말한 것이 됩니다.
    untils = [
        window_end if r["capped"] else pace.resume_at(db, r["provider"]) for r in down
    ]
    until = max([u for u in untils if u], default=None)
    fix = (
        "토큰 상한을 올리면 다음 차례에 곧바로 이어서 합니다 — 사용량 화면에서 바꿉니다."
        if any(r["capped"] for r in down)
        else None
    )

    live = [r for r in reviewers if r not in down]
    if live:
        return _hold(
            "provider_partial",
            # **이름을 여기 적지 않습니다.** 바로 아래 문장이 누가 왜
            # 쉬는지로 시작하는데, 제목이 같은 이름으로 시작하면 한 줄을
            # 두 번 읽게 됩니다. 제목이 할 일은 "절반만 돈다"는 것 하나입니다.
            "요약을 한쪽만 하고 있습니다",
            f"{' · '.join(why(r) for r in down)}. "
            f"{_josa(' · '.join(r['label'] for r in live), '이', '가')} 이어서 요약하므로 "
            "줄은 계속 줄어듭니다 — 다만 그만큼 느려집니다.",
            tone="info",
            until=until,
            fix=fix,
            forcible=True,
        )
    return _hold(
        "provider_down",
        "요약할 수 있는 곳이 없습니다",
        f"{' · '.join(why(r) for r in down)}. 자막은 그대로 쌓이고, 풀리는 대로 이어서 합니다.",
        tone="stop",
        until=until,
        fix=fix,
        forcible=True,
    )


def _batching_hold(db: Session, now, waiting: int) -> dict | None:
    """막힌 데는 없는데 안 도는 경우 — **모이기를 기다리는 중입니다.**

    이게 화면에 없으면 "대기 3건인데 왜 가만있지" 의 답이 어디에도
    없습니다. 고장이 아니라 그렇게 하기로 한 것입니다.
    """
    if waiting <= 0 or waiting >= cadence.REVIEW_BATCH:
        return None
    last = db.scalar(select(func.max(Evaluation.created_at)))
    if last is None:
        return None
    due = last + timedelta(minutes=cadence.REVIEW_MAX_WAIT_MIN)
    if due <= now:
        return None
    return _hold(
        "batching",
        f"{cadence.REVIEW_BATCH}건 모이면 시작합니다",
        f"지금 {waiting}건. 프롬프트 앞부분이 매번 18,700 토큰이라 몇 건 모아 연달아 "
        "돌리면 그만큼이 훨씬 싸집니다. 안 모여도 아래 시각에는 그냥 시작합니다.",
        tone="info",
        until=due,
    )


def _now_working(db: Session, state: str) -> dict | None:
    v = db.scalars(
        select(Video).where(Video.state == state).order_by(Video.updated_at.desc())
    ).first()
    if v is None:
        return None
    return {"title": v.title, "since": to_utc_iso(v.updated_at)}


class LimitPatch(BaseModel):
    """0 이나 null 이면 상한을 풉니다."""

    limitTokens: int | None = None
    # 어느 회사의 상한인가. 없으면 공용 값 — 자기 값이 없는 회사가 물려받습니다.
    provider: str | None = None
    # 이 회사만 걸어 둔 값을 지우고 공용으로 되돌립니다.
    inherit: bool = False


@router.put("/stats/usage/limit", status_code=204)
def set_limit(patch: LimitPatch, db: Session = Depends(get_db), _: User = Depends(require_owner)):
    """토큰 상한을 바꿉니다. **관리자만** 할 수 있습니다.

    **.env 가 아니라 DB 에 둡니다.** 설정 파일을 고치고 프로세스를
    재시작해야 한다면, 쓰다가 "조금만 올려 보자"를 할 수 없습니다.
    워커와 API 가 같은 값을 봅니다.

    회사를 지정하면 그 회사만 바뀝니다. 상한은 각 구독에 따로 걸리므로,
    한 값으로 묶으면 한쪽이 많이 쓴 것 때문에 아직 여유가 있는 쪽까지
    멈춥니다 — 토큰이 모자라서 회사를 늘렸는데 정반대가 됩니다.
    """
    provider = (patch.provider or "").strip() or None
    if provider is not None and provider not in usage_guard.PROVIDERS:
        raise ApiError(400, "UNKNOWN_PROVIDER", f"모르는 회사입니다: {provider}")

    if patch.inherit:
        if provider is None:
            raise ApiError(400, "INVALID_VALUE", "공용 값은 물려받을 곳이 없습니다.")
        # None 을 넣으면 그 회사의 값이 지워지고 공용 값을 다시 씁니다.
        usage_guard.set_limit(db, None, provider)
        return

    v = patch.limitTokens
    if v is not None and v < 0:
        raise ApiError(400, "INVALID_VALUE", "상한은 0 이상이어야 합니다.")
    # 0 은 "무제한", 값이 없으면 설정 기본값으로 되돌립니다.
    usage_guard.set_limit(db, v, provider)


@router.get("/stats/pipeline")
def pipeline(db: Session = Depends(get_db), _: User = Depends(current_user)):
    counts = dict(db.execute(select(Video.state, func.count()).group_by(Video.state)).all())

    def take(states):
        return sum(int(counts.get(s, 0)) for s in states)

    running = {
        r.job: r
        for r in db.scalars(
            select(CrawlRun).where(CrawlRun.status.in_(("running", "queued")))
        ).all()
    }
    last_by_stage = {
        stage: at
        for stage, at in db.execute(
            select(PipelineEvent.stage, func.max(PipelineEvent.created_at)).group_by(
                PipelineEvent.stage
            )
        ).all()
    }

    # 검색은 "붙들고 있는 영상"이 없습니다. 대신 다음 차례가 언제인지가
    # 알고 싶은 값입니다.
    upcoming = [
        n
        for n in (
            next_due_at(k)
            for k in db.scalars(
                select(Keyword).where(
                    Keyword.status.in_(("pending", "active")), Keyword.archived_at.is_(None)
                )
            )
        )
        if n is not None
    ]

    now = now_kst()
    reviewers = _reviewers(db)
    holds = {
        "discover": _discover_hold(db, now),
        "transcript": _transcript_hold(db, now),
        "review": _review_hold(db, now, take(("TRANSCRIBED",)), reviewers),
    }

    tracks = []
    for key, label, waiting_states in (
        ("discover", "검색", ("DISCOVERED",)),
        ("transcript", "자막", ("TRANSCRIPT_PENDING",)),
        ("review", "요약", ("TRANSCRIBED",)),
    ):
        run = running.get(key)
        hold = holds[key]
        tracks.append(
            {
                "key": key,
                "label": label,
                "status": "running" if run is not None else "idle",
                "waiting": take(waiting_states),
                "runLabel": run.label if run else None,
                "startedAt": to_utc_iso(run.started_at) if run else None,
                # 지금 붙들고 있는 영상 — 이게 있어야 "멈춘 건지 도는
                # 건지"가 구분됩니다.
                "working": _now_working(db, _WORKING[key]) if key in _WORKING else None,
                "lastAt": to_utc_iso(last_by_stage.get(key)),
                # **다음에 실제로 무슨 일이 일어나는 시각입니다.** 검색은
                # 키워드의 차례이고, 나머지 둘은 막힌 것이 풀리는 때입니다.
                # 막힌 데가 없으면 비어 있고, 그때는 `everySec` 이 답입니다
                # — 30초마다 도는 트랙에 "다음 차례 01:45" 를 적어 두면
                # 맞는 말인데도 쓸모가 없습니다.
                "nextAt": (
                    to_utc_iso(min(upcoming))
                    if key == "discover" and upcoming
                    else (hold or {}).get("until")
                ),
                # 몇 초마다 확인하는가. 워커와 같은 값을 봅니다(collector/cadence.py).
                "everySec": cadence.TICKS[key],
                # 멈춰 있다면 왜, 언제까지, 그동안 무슨 일이 벌어지는지.
                "hold": hold,
            }
        )

    return {
        "funnel": [
            {"key": k, "label": label, "count": take(states)} for k, label, states in _FUNNEL
        ],
        "tracks": tracks,
        # 요약을 나눠 하는 회사들. 트랙 한 줄 아래 펼쳐 보입니다 — 한쪽만
        # 멎었을 때 "요약 쉬는 중" 으로 뭉뚱그리지 않기 위해서입니다.
        "reviewers": reviewers,
        # **스스로 올린 것을 사람이 볼 수 있어야 합니다.** 보이지 않는
        # 자동 업그레이드는 믿을 수 없는 자동 업그레이드입니다 — 어느 날
        # 받아쓰기가 이상해졌을 때 "그저께 뭔가 올랐나?" 를 물을 자리가
        # 있어야 합니다 (collector/upkeep.py).
        "upkeep": upkeep.last_note(db),
        "blog": _blog(db),
        "stuck": [
            {"key": k, "label": label, "count": take(states)} for k, label, states in _STUCK
        ],
    }


@router.get("/runs/{run_id}/events")
def run_events(run_id: str, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """실행 하나가 실제로 무엇을 옮겼는지.

    **이미 쌓고 있던 것을 안 보여 주고 있었습니다.** 단계별 합계만으로는
    "검토 3건"이 무엇이었는지, 왜 실패했는지 알 수 없습니다.
    """
    rows = db.scalars(
        select(PipelineEvent)
        .where(PipelineEvent.run_id == run_id)
        .order_by(PipelineEvent.created_at)
        .limit(300)
    ).all()
    vids = {v.id: v for v in db.scalars(
        select(Video).where(Video.id.in_([e.video_id for e in rows]))
    ).all()} if rows else {}
    return [
        {
            "at": to_utc_iso(e.created_at),
            "stage": e.stage,
            "fromState": e.from_state,
            "toState": e.to_state,
            "ok": bool(e.ok),
            "videoId": e.video_id,
            "title": (vids.get(e.video_id).title if vids.get(e.video_id) else ""),
            "detail": (e.detail or {}).get("reason") or (e.detail or {}).get("error") or "",
        }
        for e in rows
    ]


# 눌러서 시작할 수 있는 잡과, 눌렀을 때 무엇이 달라지는지.
#
# **네 트랙 모두 정기적으로 돕니다.** 버튼은 그 주기를 앞당길 뿐이지 없던
# 일을 만들지 않습니다. 잡마다 건너뛰는 것이 다른데, 공통점은 **막힌 것은
# 건너뛰지 않는다**는 것입니다 — 차단·상한·세션은 눌러서 넘길 수 있는 값이
# 아니고, 두드리면 오히려 길어집니다.
RUNNABLE = {
    "discover": "검색",
    "transcript": "자막",
    "review": "요약",
    "publish": "블로그",
}


class RunRequest(BaseModel):
    """어느 트랙을 시작할 것인가. 없으면 검색 — 예전 버튼의 뜻입니다."""

    job: str = "discover"


@router.post("/runs", status_code=202)
def request_run(
    body: RunRequest | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_owner),
):
    """트랙 하나를 지금 시작합니다 — **요청만 남깁니다.** 워커가 다음 틱에
    집어갑니다.

    여기서 직접 돌리지 않는 이유: 한 사이클이 몇 분씩 걸려서 HTTP 요청이
    그동안 매달려 있게 되고, 브라우저가 먼저 끊으면 진행 상황을 알 수
    없습니다. 요청을 기록으로 남기면 실행 로그에 바로 보이고, 워커가
    집어가면서 상태가 이어집니다.

    **기다리는 요청은 잡마다 하나씩입니다.** 예전에는 전체에 하나였는데,
    트랙을 따로 누르게 된 지금 그대로 두면 검색을 눌러 놓고 요약을 못
    누릅니다 — 서로 다른 일인데 한 줄을 두고 다투게 됩니다.
    """
    job = (body.job if body else "discover") or "discover"
    if job not in RUNNABLE:
        raise ApiError(400, "UNKNOWN_JOB", f"시작할 수 없는 트랙입니다: {job}")

    waiting = db.scalar(
        select(CrawlRun).where(CrawlRun.status == "queued", CrawlRun.job == job)
    )
    if waiting is not None:
        raise ApiError(
            409, "RUN_ALREADY_QUEUED", f"{RUNNABLE[job]} 은(는) 이미 시작을 기다리고 있습니다."
        )

    run = CrawlRun(
        trigger="manual",
        job=job,
        status="queued",
        started_at=now_kst(),
        label=f"{RUNNABLE[job]} — 시작 대기 중",
        stats={},
    )
    db.add(run)
    db.commit()
    return run_out(run)


# ── 내부 ──────────────────────────────────────────────────


def _midnight(d: date):
    from datetime import datetime

    return datetime(d.year, d.month, d.day)


def _count_videos(db: Session, since=None, states=None, exclude_state=None) -> int:
    stmt = select(func.count()).select_from(Video)
    if since is not None:
        stmt = stmt.where(Video.discovered_at >= since)
    if states is not None:
        stmt = stmt.where(Video.state.in_(states))
    if exclude_state is not None:
        stmt = stmt.where(Video.state != exclude_state)
    return db.scalar(stmt) or 0


def _failures(db: Session) -> list[dict]:
    """최근 실패. 사용자에게 보여줄 문장은 state_reason 에 이미 사람 말로 들어 있습니다."""
    rows = db.scalars(
        select(Video)
        .where(Video.state == "FAILED")
        .order_by(Video.updated_at.desc())
        .limit(5)
    ).all()
    out = []
    for v in rows:
        reason = v.state_reason or ""
        kind = "transcript" if "자막" in reason else "review"
        out.append(
            {
                "kind": kind,
                "label": "자막 없음" if kind == "transcript" else "검토 실패",
                "title": v.title,
                "detail": reason,
            }
        )
    return out


__all__ = ["router", "KST"]
