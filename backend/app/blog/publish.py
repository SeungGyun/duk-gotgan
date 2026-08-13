"""한 번에 한 편씩 블로그로 내보냅니다 (.spec/tistory.md §스케줄).

**다음 차례를 DB 에 적어 둡니다.** 메모리에 두면 워커를 재시작할 때마다
초기화돼서 쿨링이 없던 일이 됩니다 — 자막 냉각이 정확히 그래서 깨져
있었습니다(`app/db/state.py`). launchd 가 워커를 다시 띄우면 곧바로 다음
글이 나가고, 30~60분 간격을 두기로 한 것이 아무 뜻이 없어집니다.

**올리기 전에 행을 만듭니다.** CLI 가 글을 만들었는데 우리가 결과를 못 받는
경우가 있습니다(타임아웃·프로세스 사망). 흔적이 없으면 다음 차례에 같은 글을
또 올리고, 공개 글이라 사람이 하나씩 내려야 합니다.
"""

import logging
import os
import random
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.blog import render, tistory
from app.blog import title as title_maker
from app.db import state
from app.db.models import BlogPost, Keyword, Lecture, User, UserLecture, VideoKeyword
from config.settings import settings
from config.time import now_kst

logger = logging.getLogger(__name__)

NEXT_KEY = "blog.next_at"

# 세션이 언제부터 죽어 있는가. **화면에 띄우려고 적어 둡니다.**
#
# 예전에는 워커 로그에 경고 한 줄이 전부였습니다. 화면은 그동안에도
# "쉬는 중 · 다음 차례 04:12" 라고 멀쩡하게 적고 있었으니, 사람이 알아채는
# 길이 로그를 열어 보는 것뿐이었습니다 — 간격을 30~60분으로 늘리고 하루
# 상한까지 붙은 지금은 반나절을 그렇게 보낼 수 있습니다.
SESSION_BAD_KEY = "blog.session_bad_since"

# 몇 번까지 다시 해 볼 것인가. 한 편이 막혀 뒤가 통째로 밀리면 안 됩니다 —
# 넘으면 그 강의는 영구 제외하고 다음으로 갑니다.
MAX_ATTEMPTS = 3

# 세션이 만료됐을 때 쉬는 시간(분). **사람이 브라우저에서 카카오 로그인을
# 해야** 풀리는 종류라, 1분마다 두드려 봐야 로그만 덮입니다.
#
# **평소 간격보다 길어야 뜻이 있습니다.** 30분이었는데 평소 간격을 2~10분에서
# 30~60분으로 늘리면서 뒤집혔습니다 — 막혔을 때 오히려 평소보다 자주 두드리는
# 값이 된 것입니다(시험 둘이 여기서 걸렸습니다). 두 시간이면 눈치챈 사람이
# 로그인하고 돌아올 만하고, 아무도 없는 새벽에는 로그가 두 시간에 한 줄입니다.
SESSION_REST_MIN = 120

# 어느 키워드도 안 붙은 영상이 왔을 때. 실제로는 거의 없지만, 카테고리를
# 못 정했다고 발행을 멈추는 것보다 한곳에 모아 두는 편이 낫습니다.
DEFAULT_CATEGORY = "강의노트"


@dataclass
class PublishResult:
    ok: bool = False
    did_work: bool = False
    label: str = ""
    error: str | None = None
    # 다음 차례까지 몇 분 쉴 것인가. None 이면 평소대로 30~60분 랜덤.
    #
    # **여기에 담아서 올려 보냅니다.** 처음엔 막힌 자리에서 곧바로
    # `schedule_next(30분)` 을 부르고 끝냈는데, 돌아 나오는 길에
    # `publish_once` 가 평소 간격으로 한 번 더 덮어썼습니다 — 30분 쉬기로
    # 한 것이 5분이 됐습니다. 시각을 적는 자리는 하나여야 합니다.
    rest_min: int | None = None


def due(db: Session, now=None) -> bool:
    """지금이 다음 차례인가. 적어 둔 것이 없으면 곧바로 차례입니다."""
    at = state.get_time(db, NEXT_KEY)
    return at is None or (now or now_kst()) >= at


def next_at(db: Session):
    """다음 차례 시각. 적어 둔 것이 없으면 None — 지금이 곧 차례입니다.

    `NEXT_KEY` 를 이 파일 밖으로 내보내지 않으려고 둡니다. 화면 쪽에서
    직접 꺼내 쓰면 키 이름이 두 곳에 박힙니다.
    """
    return state.get_time(db, NEXT_KEY)


def schedule_next(db: Session, minutes: int | None = None) -> None:
    """다음 차례를 적어 둡니다. 값을 안 주면 30~60분 사이 랜덤."""
    if minutes is None:
        lo, hi = settings.blog_min_interval_min, settings.blog_max_interval_min
        lo, hi = min(lo, hi), max(lo, hi)
        minutes = random.randint(max(1, lo), max(1, hi))
    at = now_kst() + timedelta(minutes=minutes)
    state.set_time(db, NEXT_KEY, at)
    logger.info("[blog] 다음 차례는 %s (%d분 뒤)", f"{at:%H:%M}", minutes)


def _pending(db: Session):
    """아직 안 올린 강의들 — 전문성 높은 것이 먼저.

    "다음 한 편"과 "몇 편 남았나"가 같은 자리를 봐야 합니다. 조건을 두 벌
    적어 두면 화면의 대기 수와 실제로 나가는 것이 조용히 갈립니다.
    """
    # 주인이 자기 곳간에서 뺀 것은 자기 블로그에도 올리지 않습니다.
    owner_excluded = (
        select(UserLecture.video_id)
        .join(User, User.id == UserLecture.user_id)
        .where(User.is_owner.is_(True), UserLecture.excluded_at.is_not(None))
    )
    # 이미 올렸거나, 세 번 해 보고 안 된 것.
    settled = select(BlogPost.video_id).where(
        or_(BlogPost.state == "POSTED", BlogPost.attempts >= MAX_ATTEMPTS)
    )

    where = [
        Lecture.is_hidden.is_(False),
        Lecture.verdict.in_(settings.blog_verdict_list),
        Lecture.video_id.not_in(owner_excluded),
        Lecture.video_id.not_in(settled),
    ]

    # **채널 구독으로 들어온 것은 안 올립니다** (settings.blog_skip_channel).
    #
    # 검색 키워드가 붙어 있어도 뺍니다. 한 영상에 둘이 같이 붙는 일은 지금
    # 0건이지만, 생긴다면 "채널에서 온 것" 이 맞고 안 올리는 쪽이 되돌리기
    # 쉽습니다 — 올라간 공개 글은 사람이 하나씩 내려야 합니다.
    if settings.blog_skip_channel:
        from_channel = (
            select(VideoKeyword.video_id)
            .join(Keyword, Keyword.id == VideoKeyword.keyword_id)
            .where(Keyword.source_type == "channel")
        )
        where.append(Lecture.video_id.not_in(from_channel))

    return (
        select(Lecture)
        .where(*where)
        .order_by(Lecture.expert_score.desc(), Lecture.published_at.desc())
    )


def candidate(db: Session) -> Lecture | None:
    """다음에 올릴 강의. **전문성 높은 것이 먼저 나갑니다.**

    목록을 미리 확정해 두지 않습니다 — 뒤에서 점수가 더 높은 강의가 들어오면
    그것이 다음 차례가 되는 편이 "전문성 순서"라는 말에 맞습니다.
    """
    return db.scalars(_pending(db).limit(1)).first()


def remaining(db: Session) -> int:
    """앞으로 올릴 것이 몇 편 남았는가 — 화면의 "대기" 값."""
    return db.scalar(select(func.count()).select_from(_pending(db).subquery())) or 0


def posted_today(db: Session, now: datetime | None = None) -> int:
    """오늘 몇 편 올렸는가. 하루 상한(30편)의 분자입니다.

    날짜는 **KST 자정 기준**입니다 — 상한이 풀리는 시점이 그때라, 24시간
    슬라이딩 창으로 세면 아침에 남은 몫을 실제보다 적게 봅니다.
    """
    now = now or now_kst()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.scalar(
            select(func.count())
            .select_from(BlogPost)
            .where(BlogPost.state == "POSTED", BlogPost.posted_at >= midnight)
        )
        or 0
    )


def cap_reached(db: Session, now: datetime | None = None) -> bool:
    """오늘 몫을 다 썼는가.

    **403 을 받기 전에 먼저 셉니다.** 받아 보고 아는 방식은 한 번 헛걸음을
    합니다 — 제목을 짓고 본문을 만들고 CLI 를 띄운 뒤에야 거절당하고,
    그 사이 시도 횟수가 오르내립니다. 우리가 올린 것은 우리가 세면 됩니다.

    그래도 403 처리를 지웁니까? 아닙니다. 티스토리는 **어디서 올렸든** 셉니다 —
    관리 화면에서 손으로 한 편 올리면 우리 장부에는 없는데 저쪽 몫은 줄어듭니다.
    이쪽이 평소를 막고, 저쪽이 그런 날의 안전망입니다.
    """
    cap = settings.blog_daily_cap
    return cap > 0 and posted_today(db, now) >= cap


def category_for(db: Session, video_id: str) -> str:
    """곳간 키워드 → 블로그 카테고리.

    **먼저 데려온 키워드**를 씁니다. 영상 하나에 키워드가 둘인 경우가
    2,317건 중 50건 있는데, 순서를 정해 두지 않으면 같은 영상이 실행할
    때마다 다른 카테고리로 갈 수 있습니다.
    """
    kw = db.scalars(
        select(Keyword)
        .join(VideoKeyword, VideoKeyword.keyword_id == Keyword.id)
        .where(VideoKeyword.video_id == video_id)
        .order_by(VideoKeyword.discovered_at, Keyword.created_at)
        .limit(1)
    ).first()
    if kw is None:
        return DEFAULT_CATEGORY
    return render.category_name(kw.term, kw.channel_title, kw.source_type) or DEFAULT_CATEGORY


def publish_once(db: Session) -> PublishResult:
    """차례가 된 한 편을 올리고, 다음 차례를 적어 둡니다."""
    lec = candidate(db)
    if lec is None:
        # **타이머를 건드리지 않습니다.** 올릴 것이 없어 넘긴 것뿐이라,
        # 새 강의가 들어오면 기다리지 않고 곧바로 나가는 편이 맞습니다.
        return PublishResult()

    # **오늘 몫을 다 썼으면 아예 손을 안 댑니다.** 행도 만들지 않고 CLI 도
    # 띄우지 않습니다 — 어차피 403 이 올 것을 알고 있으니 헛걸음할 이유가
    # 없고, 행을 만들면 시도 횟수가 올랐다 내렸다 합니다.
    if cap_reached(db):
        logger.info(
            "[blog] 오늘 %d편을 다 올렸습니다 — 날이 바뀌면 이어갑니다", settings.blog_daily_cap
        )
        result = PublishResult(rest_min=_until_midnight())
        schedule_next(db, result.rest_min)
        return result

    # **막혔으면 쉬었다 갑니다.** 1분마다 다시 두드려 봐야 풀리지 않고
    # 로그만 같은 줄로 덮입니다 — 요약 쪽이 정확히 그래서 `pace.py` 를
    # 두게 됐습니다. 둘 다 사람이 손을 대야 풀리는 종류입니다.
    if not tistory.available():
        result = PublishResult(
            error=f"{settings.tistory_bin} 를 찾을 수 없습니다.", rest_min=SESSION_REST_MIN
        )
    elif not tistory.session_ok():
        _mark_session(db, ok=False)
        result = PublishResult(
            error="티스토리 세션이 만료됐습니다 — 터미널에서 `tistory login` 을 한 번 해 주세요.",
            rest_min=SESSION_REST_MIN,
        )
    else:
        _mark_session(db, ok=True)
        row = _claim(db, lec)
        try:
            result = _post(db, lec, row)
        except Exception as e:  # noqa: BLE001 — 한 편이 죽어도 다음 차례는 와야 합니다
            logger.exception("[blog] %s 발행 중 예기치 못한 오류", lec.video_id)
            result = _fail(db, row, str(e))

    schedule_next(db, result.rest_min)
    return result


# ── 아래는 publish_once 의 속살 ──────────────────────────────


def _claim(db: Session, lec: Lecture) -> BlogPost:
    """올리기 **전에** 행을 만듭니다. 제목도 이때 정해 적어 둡니다.

    제목을 매번 새로 지으면 재시도 때 조금씩 달라져서, "이미 올라간 같은
    제목의 글" 을 찾는 확인이 소용없어집니다.
    """
    row = db.get(BlogPost, lec.video_id)
    if row is None:
        row = BlogPost(
            video_id=lec.video_id,
            lecture_id=lec.id,
            title=title_maker.make(lec),
            category=category_for(db, lec.video_id),
            state="PENDING",
        )
        db.add(row)
    # **`or 0` 이 필요합니다.** 컬럼의 `default=0` 은 INSERT 할 때 붙는 값이라,
    # 아직 저장 전인 객체에서는 `attempts` 가 None 입니다. 그대로 더하면
    # 첫 발행에서 TypeError 로 죽습니다.
    row.attempts = (row.attempts or 0) + 1
    row.lecture_id = lec.id
    db.commit()
    return row


def _post(db: Session, lec: Lecture, row: BlogPost) -> PublishResult:
    # 두 번째부터는 **이미 올라가 있는지 먼저 봅니다.** 지난번에 글은
    # 만들어졌는데 결과만 못 받았을 수 있습니다.
    if row.attempts > 1:
        found = tistory.find_by_title(row.title)
        if found is not None:
            logger.info("[blog] %s 는 이미 올라가 있었습니다 (#%s)", lec.video_id, found.post_id)
            return _done(db, row, found)

    try:
        doc = render.document(lec, row.title, row.category, settings.blog_visibility)
    except render.Unrenderable as e:
        # 다시 해도 같은 결과입니다 — 재시도하지 않고 여기서 접습니다.
        row.attempts = MAX_ATTEMPTS
        return _fail(db, row, str(e))

    path = _write_temp(doc)
    try:
        ref = tistory.publish(path, row.category, settings.blog_visibility)
    except tistory.TistoryError as e:
        if e.session:
            # 세션 문제는 이 강의 탓이 아닙니다 — 시도 횟수를 도로 물리고,
            # 사람이 로그인할 시간을 줍니다.
            row.attempts = max(0, row.attempts - 1)
            return _fail(db, row, str(e), rest_min=SESSION_REST_MIN)
        if e.daily_cap:
            # **오늘 몫을 다 썼을 뿐입니다.** 이걸 그 글의 실패로 세면,
            # 자정까지 남은 시간 동안 2~10분마다 한 편씩 세 번 걸려 접힙니다 —
            # 실제로 하루에 21편이 그렇게 영구 제외됐습니다. 글은 멀쩡한데
            # 티스토리가 안 받은 것뿐이라, 횟수를 물리고 날이 바뀔 때까지 쉽니다.
            row.attempts = max(0, row.attempts - 1)
            return _fail(db, row, str(e), rest_min=_until_midnight())
        return _fail(db, row, str(e))
    finally:
        os.unlink(path)

    return _done(db, row, ref)


def _done(db: Session, row: BlogPost, ref: tistory.PostRef) -> PublishResult:
    row.state = "POSTED"
    row.post_id = ref.post_id
    row.url = ref.url
    row.error = None
    row.posted_at = now_kst()
    db.commit()
    return PublishResult(
        ok=True,
        did_work=True,
        label=f"「{row.title}」 → {row.category}" + (f" (#{row.post_id})" if row.post_id else ""),
    )


def _fail(db: Session, row: BlogPost, message: str, rest_min: int | None = None) -> PublishResult:
    row.error = message[:2000]
    if row.attempts >= MAX_ATTEMPTS:
        row.state = "FAILED"
        logger.warning("[blog] %s 는 %d번 해 보고 접습니다 — %s", row.video_id, row.attempts, message)
    db.commit()
    return PublishResult(
        did_work=True, label=f"「{row.title}」 실패", error=message, rest_min=rest_min
    )


def session_bad_since(db: Session):
    """세션이 언제부터 죽어 있는가. 살아 있으면 None."""
    return state.get_time(db, SESSION_BAD_KEY)


def _mark_session(db: Session, *, ok: bool) -> None:
    """**처음 죽은 시각을 지킵니다.** 볼 때마다 지금 시각으로 덮으면
    "방금 만료됨" 만 보이고, 반나절째 막혀 있다는 것이 안 드러납니다."""
    if ok:
        if state.get_time(db, SESSION_BAD_KEY) is not None:
            state.set_time(db, SESSION_BAD_KEY, None)
        return
    if state.get_time(db, SESSION_BAD_KEY) is None:
        state.set_time(db, SESSION_BAD_KEY, now_kst())


def _until_midnight() -> int:
    """자정까지 남은 분(+2). 하루 상한은 **날짜가 바뀌어야** 풀립니다.

    30분씩 두드려 봐야 같은 403 이 오고, 그때마다 한 편씩 시도 횟수를
    깎아 먹습니다. 풀릴 시각을 아는 종류이니 그때까지 그냥 잡니다.
    """
    now = now_kst()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds() // 60) + 2)


def _write_temp(doc: str) -> str:
    """**확장자가 `.md` 여야 합니다.** CLI 가 확장자로 마크다운인지 판별합니다."""
    fd, path = tempfile.mkstemp(suffix=".md", prefix="dukgotgan-blog-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(doc)
    return path
