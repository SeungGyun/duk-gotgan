"""곳간 → 블로그 (.spec/tistory.md).

여기서 지키려는 것은 세 가지입니다.

  **빼기로 한 것이 빠졌나** — 시간 표시와 링크. 곳간 화면은 자막의 마디로
  돌아가려고 시간을 붙들고 있는데, 블로그에는 돌아갈 영상이 없습니다.

  **올릴 것만 올리나** — 판정 두 종류, 아직 안 올린 것, 점수 높은 것 먼저.

  **두 번 올리지 않나** — 공개 글이라 사람이 하나씩 내려야 합니다.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.blog import publish, render, tistory
from app.db.models import (
    Base,
    BlogPost,
    Keyword,
    Lecture,
    User,
    UserLecture,
    Video,
    VideoKeyword,
)
from config.settings import settings
from config.time import now_kst


@pytest.fixture
def db():
    url = settings.database_url.replace("/dukgotgan?", "/dukgotgan_test?")
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()
    engine.dispose()


# ── 빼기로 한 것 ─────────────────────────────────────────────


def test_불릿_앞의_자막_마커를_지운다():
    """실측에서 요약 400편 중 11편이 이렇게 생겼습니다."""
    assert render.scrub("[51:53] '목적 없이 단순히'") == "'목적 없이 단순히'"
    assert render.scrub("[1:24] 코딩을 한 줄도 몰라도") == "코딩을 한 줄도 몰라도"
    assert render.scrub("근거는 (13:39) 대목이다") == "근거는 대목이다"


def test_시분초는_지우고_시계_시각은_남긴다():
    """`9:30` 까지 지우면 "오전 9:30 회의" 같은 멀쩡한 문장이 부서집니다."""
    assert render.scrub("1:02:03 부터 설명한다") == "부터 설명한다"
    assert render.scrub("오전 9:30 회의에서") == "오전 9:30 회의에서"


def test_링크를_지운다():
    assert render.scrub("자세한 것은 https://youtu.be/abc123 참고") == "자세한 것은 참고"
    assert "youtube" not in render.scrub("영상 www.youtube.com/watch?v=x 에서")


def test_없는_값에도_깨지지_않는다():
    assert render.scrub(None) == ""
    assert render.scrub("") == ""


# ── 제목 ─────────────────────────────────────────────────────


def test_제목은_어절_경계에서_자른다():
    """글자 수로만 자르면 낱말 가운데가 잘려 나갑니다."""
    long = "일본 적극재정파의 경제 진단과 다섯 가지 오해 해소 및 경제 부활 전략"
    out = render.fallback_title(long, 30)
    assert len(out) <= 30
    assert not out.endswith(" ")
    assert long.startswith(out)


def test_짧은_제목은_그대로_둔다():
    assert render.fallback_title("쿠버네티스 네트워킹 입문", 30) == "쿠버네티스 네트워킹 입문"


def test_띄어쓰기가_없어도_길이는_지킨다():
    out = render.fallback_title("가" * 80, 30)
    assert len(out) == 30


def test_접속사로_끝나지_않는다():
    """실측에서 `…5가지 오해 해소 및` 이 그대로 제목이 될 뻔했습니다."""
    out = render.fallback_title("일본 적극재정파의 경제 진단과 5가지 오해 해소 및 부활 전략", 30)
    assert not out.endswith("및")
    assert out == "일본 적극재정파의 경제 진단과 5가지 오해 해소"


# ── 카테고리 ─────────────────────────────────────────────────


def test_채널_구독은_핸들_대신_채널명을_쓴다():
    """`@gaingetv` 가 메뉴에 뜨면 무엇인지 읽히지 않습니다."""
    assert render.category_name("@gaingetv", "가인지TV", "channel") == "가인지TV"


def test_슬래시는_바꾼다():
    """티스토리에서 `/` 는 부모/자식 구분자라, 그대로 두면 엉뚱한 곳에
    하위 카테고리가 생깁니다."""
    assert render.category_name("AI/ML", None, "search") == "AI-ML"


def test_검색_키워드는_그대로():
    assert render.category_name("LLM 서빙 최적화", None, "search") == "LLM 서빙 최적화"


# ── 본문 ─────────────────────────────────────────────────────


def _lecture(**kw) -> Lecture:
    base = dict(
        id="lec-1",
        video_id="vid1",
        one_liner="쿠버네티스 네트워킹의 기본을 정리한다",
        target_audience="인프라 담당자",
        prerequisites=["리눅스 기초"],
        sections=[
            {"title": "파드 네트워킹", "startSec": 0, "bullets": ["[5:33] 파드는 IP 를 갖는다"]},
            {"title": "서비스", "startSec": 930, "bullets": ["클러스터IP 가 기본이다"]},
        ],
        closing="결국 CNI 가 전부다",
        tags=["쿠버네티스", "네트워킹", "CNI"],
        verdict="expert",
        expert_score=90,
    )
    base.update(kw)
    return Lecture(**base)


def test_본문에_시작_초가_나오지_않는다():
    """`startSec` 은 지우는 것이 아니라 애초에 그리지 않습니다."""
    out = render.to_markdown(_lecture())
    assert "930" not in out
    assert "startSec" not in out


def test_본문이_섹션_구조를_따른다():
    out = render.to_markdown(_lecture())
    assert "## 파드 네트워킹" in out
    assert "- 파드는 IP 를 갖는다" in out  # 마커가 빠진 채로
    assert "결국 CNI 가 전부다" in out


def test_출처를_남기지_않는다():
    """한동안 채널명 한 줄을 붙였는데, 주인이 자기 글로 두기로 정했습니다
    (2026-08-08). 유튜브 링크를 빼기로 한 것과 같은 결정의 연장입니다."""
    out = render.to_markdown(_lecture())
    assert "출처" not in out
    assert "어느채널" not in out
    assert "youtu" not in out
    # 출처만 빼고 `---` 이 남으면 글 끝에 줄 하나가 덩그러니 남습니다.
    assert "---" not in out


def test_옛_형식은_올리지_않는다():
    """시드로 들어온 것은 개요 문단만 있어서 글이 되지 않습니다."""
    with pytest.raises(render.Unrenderable):
        render.to_markdown(_lecture(sections=[]))


def test_머리말이_쉼표와_따옴표를_견딘다():
    """태그에 쉼표가 있으면 `--tags a,b` 로는 두 개로 쪼개집니다."""
    fm = render.frontmatter('그는 "왜"라고 물었다', "AI", ["a,b", "c"], "public")
    assert '"a,b"' in fm
    assert '\\"왜\\"' in fm
    assert "visibility: public" in fm


# ── 차례 ─────────────────────────────────────────────────────


def test_적어_둔_것이_없으면_곧바로_차례다(db):
    assert publish.due(db) is True


def test_다음_차례는_정해진_범위_안이다(db):
    for _ in range(30):
        before = now_kst()
        publish.schedule_next(db)
        gap = (publish.state.get_time(db, publish.NEXT_KEY) - before).total_seconds() / 60
        assert settings.blog_min_interval_min - 1 <= gap <= settings.blog_max_interval_min + 1
    assert publish.due(db) is False


def test_지나간_차례는_다시_차례다(db):
    publish.state.set_time(db, publish.NEXT_KEY, now_kst() - timedelta(minutes=1))
    assert publish.due(db) is True


# ── 무엇을 올릴 것인가 ───────────────────────────────────────


def _seed(db, video_id: str, score: int, verdict: str = "expert") -> Lecture:
    db.add(Video(id=video_id, title=f"제목 {video_id}", channel_title="채널"))
    lec = Lecture(
        video_id=video_id,
        one_liner="한 문장",
        verdict=verdict,
        expert_score=score,
        published_at=datetime(2026, 8, 1),
        sections=[{"title": "가", "startSec": 0, "bullets": ["나"]}],
        tags=["가"],
    )
    db.add(lec)
    db.commit()
    return lec


def _seed_from_channel(db, video_id: str, score: int) -> Lecture:
    """채널 구독이 데려온 강의. 곳간에는 쌓이되 블로그에는 안 나갑니다."""
    lec = _seed(db, video_id, score)
    kw = Keyword(term="@somechannel", source_type="channel", channel_title="어느 채널")
    db.add(kw)
    db.flush()
    db.add(VideoKeyword(video_id=video_id, keyword_id=kw.id))
    db.commit()
    return lec


def test_점수가_높은_것부터_나간다(db):
    _seed(db, "low", 70)
    _seed(db, "high", 95)
    assert publish.candidate(db).video_id == "high"


def test_판정이_아닌_것은_올리지_않는다(db):
    """`irrelevant` 는 곳간이 "이건 강의가 아니다"라고 한 것입니다."""
    _seed(db, "junk", 99, verdict="irrelevant")
    _seed(db, "good", 60, verdict="practical")
    assert publish.candidate(db).video_id == "good"


def test_이미_올린_것은_다시_올리지_않는다(db):
    _seed(db, "done", 95)
    _seed(db, "next", 80)
    db.add(BlogPost(video_id="done", state="POSTED", attempts=1, title="t", category="c"))
    db.commit()
    assert publish.candidate(db).video_id == "next"


def test_세_번_실패한_것은_넘어간다(db):
    """한 편이 막혀 뒤가 통째로 밀리면 안 됩니다."""
    _seed(db, "stuck", 95)
    _seed(db, "next", 80)
    db.add(
        BlogPost(
            video_id="stuck",
            state="PENDING",
            attempts=publish.MAX_ATTEMPTS,
            title="t",
            category="c",
        )
    )
    db.commit()
    assert publish.candidate(db).video_id == "next"


def test_두_번_실패한_것은_아직_붙잡는다(db):
    _seed(db, "retry", 95)
    db.add(BlogPost(video_id="retry", state="PENDING", attempts=2, title="t", category="c"))
    db.commit()
    assert publish.candidate(db).video_id == "retry"


def test_주인이_뺀_것은_올리지_않는다(db):
    """자기 곳간에서 뺀 것을 자기 블로그에 올릴 이유가 없습니다."""
    owner = User(id="u1", name="관리자", is_owner=True)
    db.add(owner)
    _seed(db, "hidden", 95)
    _seed(db, "next", 80)
    db.add(UserLecture(user_id="u1", video_id="hidden", excluded_at=now_kst()))
    db.commit()
    assert publish.candidate(db).video_id == "next"


def test_남이_뺀_것은_그대로_올린다(db):
    """블로그는 주인의 것입니다 — 다른 사람의 취향이 발행을 바꾸지 않습니다."""
    db.add(User(id="u1", name="관리자", is_owner=True))
    db.add(User(id="u2", name="아내", is_owner=False))
    _seed(db, "keep", 95)
    db.add(UserLecture(user_id="u2", video_id="keep", excluded_at=now_kst()))
    db.commit()
    assert publish.candidate(db).video_id == "keep"


def test_옛_버전은_올리지_않는다(db):
    lec = _seed(db, "v", 95)
    lec.is_hidden = True
    db.commit()
    assert publish.candidate(db) is None


def test_카테고리는_먼저_데려온_키워드다(db):
    """영상 하나에 키워드가 둘인 경우가 2,317건 중 50건 있습니다."""
    _seed(db, "vid", 90)
    db.add(Keyword(id="k1", term="AI"))
    db.add(Keyword(id="k2", term="경제"))
    db.add(
        VideoKeyword(video_id="vid", keyword_id="k2", discovered_at=datetime(2026, 7, 1))
    )
    db.add(
        VideoKeyword(video_id="vid", keyword_id="k1", discovered_at=datetime(2026, 8, 1))
    )
    db.commit()
    assert publish.category_for(db, "vid") == "경제"


def test_키워드가_없으면_한곳에_모은다(db):
    _seed(db, "vid", 90)
    assert publish.category_for(db, "vid") == publish.DEFAULT_CATEGORY


# ── 한 편 올리기 ─────────────────────────────────────────────


@pytest.fixture
def cli(monkeypatch):
    """티스토리 CLI 를 가짜로 세웁니다. **진짜 블로그를 건드리지 않습니다.**"""

    class Fake:
        available = True
        session = True
        posted: list[tuple] = []
        raises: Exception | None = None
        found: object | None = None

    monkeypatch.setattr(publish.tistory, "available", lambda: Fake.available)
    monkeypatch.setattr(publish.tistory, "session_ok", lambda: Fake.session)
    monkeypatch.setattr(publish.tistory, "find_by_title", lambda t: Fake.found)
    monkeypatch.setattr(publish.title_maker, "make", lambda lec: "지어낸 제목")

    def _publish(path, category, visibility):
        if Fake.raises is not None:
            raise Fake.raises
        Fake.posted.append((path, category, visibility))
        return publish.tistory.PostRef(post_id="777", url="https://x.tistory.com/777")

    monkeypatch.setattr(publish.tistory, "publish", _publish)
    return Fake


def _rest_min(db) -> float:
    at = publish.state.get_time(db, publish.NEXT_KEY)
    return (at - now_kst()).total_seconds() / 60


def test_올리면_POSTED_로_적힌다(db, cli):
    _seed(db, "vid", 90)
    out = publish.publish_once(db)
    assert out.ok and out.did_work
    row = db.get(BlogPost, "vid")
    assert (row.state, row.post_id, row.attempts) == ("POSTED", "777", 1)
    assert publish.candidate(db) is None  # 두 번 올라가지 않습니다


def test_세션이_만료되면_30분_쉰다(db, cli):
    """**여기가 한 번 틀렸던 자리입니다.** 막힌 자리에서 30분을 적어 두고
    돌아 나왔더니, `publish_once` 가 평소 간격으로 한 번 더 덮어써서
    5분 뒤에 다시 두드렸습니다."""
    _seed(db, "vid", 90)
    cli.session = False
    out = publish.publish_once(db)
    assert out.did_work is False
    assert out.error and "login" in out.error
    assert _rest_min(db) > settings.blog_max_interval_min
    assert db.get(BlogPost, "vid") is None  # 시도조차 하지 않았습니다


def test_CLI_가_없어도_1분마다_두드리지_않는다(db, cli):
    _seed(db, "vid", 90)
    cli.available = False
    publish.publish_once(db)
    assert _rest_min(db) > settings.blog_max_interval_min


def test_올릴_것이_없으면_차례를_미루지_않는다(db, cli):
    """새 강의가 들어오면 기다리지 않고 곧바로 나가는 편이 맞습니다."""
    out = publish.publish_once(db)
    assert out.did_work is False
    assert publish.state.get_time(db, publish.NEXT_KEY) is None


def test_실패해도_다음_차례는_온다(db, cli):
    _seed(db, "vid", 90)
    cli.raises = publish.tistory.TistoryError("보내다 끊겼습니다")
    out = publish.publish_once(db)
    assert out.ok is False
    row = db.get(BlogPost, "vid")
    assert (row.state, row.attempts) == ("PENDING", 1)
    assert 0 < _rest_min(db) <= settings.blog_max_interval_min + 1


def test_세_번_실패하면_접고_다음으로_간다(db, cli):
    _seed(db, "vid", 90)
    cli.raises = publish.tistory.TistoryError("계속 실패")
    for _ in range(publish.MAX_ATTEMPTS):
        publish.publish_once(db)
    row = db.get(BlogPost, "vid")
    assert (row.state, row.attempts) == ("FAILED", publish.MAX_ATTEMPTS)
    assert publish.candidate(db) is None


def test_제목은_첫_시도_때_정하고_바꾸지_않는다(db, cli, monkeypatch):
    """매번 새로 지으면 재시도 때 조금씩 달라져서, "이미 올라간 같은 제목의
    글" 을 찾는 확인이 소용없어집니다."""
    _seed(db, "vid", 90)
    cli.raises = publish.tistory.TistoryError("끊김")
    publish.publish_once(db)
    monkeypatch.setattr(publish.title_maker, "make", lambda lec: "다른 제목")
    cli.raises = None
    publish.publish_once(db)
    assert db.get(BlogPost, "vid").title == "지어낸 제목"


def test_이미_올라간_글은_다시_올리지_않는다(db, cli):
    """CLI 가 글은 만들었는데 결과만 못 받은 경우입니다. 그냥 재시도하면
    같은 글이 두 번 올라가고, 공개 글이라 사람이 하나씩 내려야 합니다."""
    _seed(db, "vid", 90)
    cli.raises = publish.tistory.TistoryError("결과를 읽지 못했습니다")
    publish.publish_once(db)

    cli.raises = None
    cli.found = publish.tistory.PostRef(post_id="42", url="https://x.tistory.com/42")
    before = len(cli.posted)
    out = publish.publish_once(db)

    assert out.ok is True
    assert len(cli.posted) == before  # 새로 올리지 않았습니다
    assert db.get(BlogPost, "vid").post_id == "42"


def test_옛_형식은_재시도하지_않고_접는다(db, cli):
    _seed(db, "vid", 90)
    db.query(Lecture).filter(Lecture.video_id == "vid").one().sections = []
    db.commit()
    publish.publish_once(db)
    row = db.get(BlogPost, "vid")
    assert row.state == "FAILED"


def test_하루_상한은_그_글의_실패가_아니다(db, cli):
    """**실제로 하루에 21편이 이렇게 영구 제외됐습니다.**

    티스토리는 공개 발행을 하루 30편까지 받습니다. 31편째부터 글마다
    403 이 오는데, 사유는 그 글이 아니라 그날 이미 쓴 횟수입니다. 이걸
    그 글의 실패로 세면 2~10분마다 한 편씩 붙잡혀 세 번 만에 접히고,
    자정까지 남은 시간만큼 멀쩡한 글이 줄줄이 빠집니다.
    """
    _seed(db, "vid", 90)
    cli.raises = publish.tistory.TistoryError(
        f"tistory post 실패 (1): ✗ {tistory.DAILY_CAP_MARK} 최대 30개까지입니다.",
        daily_cap=True,
    )

    for _ in range(publish.MAX_ATTEMPTS + 2):
        publish.publish_once(db)

    row = db.get(BlogPost, "vid")
    # 횟수가 늘지 않으니 접히지 않고, 다음 차례에도 그대로 후보입니다
    assert (row.state, row.attempts) == ("PENDING", 0)
    assert publish.candidate(db) is not None


def test_하루_상한에_걸리면_날이_바뀔_때까지_쉰다(db, cli):
    """30분씩 두드려 봐야 같은 403 입니다. 풀릴 시각을 아는 종류이니
    그때까지 잡니다 — 그동안 다른 잡이 워커를 쓰면 됩니다."""
    _seed(db, "vid", 90)
    cli.raises = publish.tistory.TistoryError("상한", daily_cap=True)
    publish.publish_once(db)

    쉬는분 = _rest_min(db)
    자정까지 = (
        (now_kst() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        - now_kst()
    ).total_seconds() / 60
    assert 자정까지 <= 쉬는분 <= 자정까지 + 3


def test_상한_문구를_CLI_출력에서_알아본다():
    """분류가 문구 하나에 달려 있습니다. 티스토리가 말을 바꾸면 다시
    영구 제외가 시작되므로, 알아보는 자리를 시험으로 못박아 둡니다."""
    real = (
        "✗ /manage/post.json 요청이 403로 실패했습니다: "
        "하루에 새롭게 공개 발행할 수 있는 글은 최대 30개까지입니다."
    )
    assert tistory.DAILY_CAP_MARK in real


def test_오늘_몫을_다_쓰면_아예_손을_대지_않는다(db, cli, monkeypatch):
    """403 을 받아 보고 아는 방식은 한 번 헛걸음합니다 — 제목을 짓고 본문을
    만들고 CLI 를 띄운 뒤에야 거절당합니다. 우리가 올린 것은 우리가 셉니다."""
    monkeypatch.setattr(settings, "blog_daily_cap", 2)
    _seed(db, "a", 90)
    _seed(db, "b", 80)
    _seed(db, "c", 70)

    publish.publish_once(db)
    publish.publish_once(db)
    assert publish.posted_today(db) == 2

    before = list(cli.posted)
    out = publish.publish_once(db)

    assert out.did_work is False
    assert cli.posted == before          # CLI 를 부르지 않았습니다
    assert db.get(BlogPost, "c") is None  # 행도 만들지 않았습니다
    assert publish.candidate(db) is not None


def test_상한에_닿으면_다음_차례는_내일(db, cli, monkeypatch):
    monkeypatch.setattr(settings, "blog_daily_cap", 1)
    _seed(db, "a", 90)
    _seed(db, "b", 80)
    publish.publish_once(db)
    publish.publish_once(db)

    다음 = publish.next_at(db)
    assert 다음.date() == (now_kst() + timedelta(days=1)).date()
    assert 다음.hour == 0


def test_어제_올린_것은_오늘_몫에_안_들어간다(db, cli):
    """상한은 자정에 풀립니다. 24시간 슬라이딩으로 세면 아침에 남은 몫을
    실제보다 적게 봅니다 — 어제 23시에 올린 것이 오늘 오전을 잡아먹습니다."""
    _seed(db, "a", 90)
    publish.publish_once(db)
    row = db.get(BlogPost, "a")
    row.posted_at = now_kst().replace(hour=23, minute=30) - timedelta(days=1)
    db.commit()

    assert publish.posted_today(db) == 0
    assert publish.cap_reached(db) is False


def test_상한이_0이면_안_막는다(db, cli, monkeypatch):
    """저쪽 규칙을 적어 두는 자리라, 끄고 싶을 때가 있습니다."""
    monkeypatch.setattr(settings, "blog_daily_cap", 0)
    _seed(db, "a", 90)
    _seed(db, "b", 80)
    publish.publish_once(db)
    publish.publish_once(db)
    assert publish.posted_today(db) == 2
    assert publish.cap_reached(db) is False


def test_세션이_죽으면_화면이_알_수_있게_적어_둔다(db, cli):
    """예전에는 워커 로그의 경고 한 줄이 전부였고, 화면은 그동안에도
    "쉬는 중 · 다음 차례 04:12" 라고 멀쩡하게 적었습니다. 사람이 대신
    로그인해 줘야 풀리는 일이라, 알아채는 길이 화면에 있어야 합니다."""
    _seed(db, "vid", 90)
    cli.session = False
    publish.publish_once(db)
    assert publish.session_bad_since(db) is not None


def test_처음_죽은_시각을_지킨다(db, cli):
    """볼 때마다 지금 시각으로 덮으면 "방금 만료됨" 만 보이고, 반나절째
    막혀 있다는 것이 안 드러납니다."""
    _seed(db, "vid", 90)
    cli.session = False
    publish.publish_once(db)
    처음 = publish.session_bad_since(db)

    publish.state.set_time(db, publish.NEXT_KEY, None)
    publish.publish_once(db)
    assert publish.session_bad_since(db) == 처음


def test_로그인하고_돌아오면_지운다(db, cli):
    _seed(db, "vid", 90)
    cli.session = False
    publish.publish_once(db)
    assert publish.session_bad_since(db) is not None

    cli.session = True
    publish.state.set_time(db, publish.NEXT_KEY, None)
    publish.publish_once(db)
    assert publish.session_bad_since(db) is None


def test_채널_구독물은_안_올린다(db, cli):
    """검색 키워드는 주제를 정해 모은 것이라 글로 묶을 결이 있는데, 채널
    구독은 그 채널의 새 영상을 통째로 가져오는 것이라 결이 없습니다 —
    카테고리부터 주제가 아니라 채널 이름이 됩니다."""
    검색 = _seed(db, "s1", 90)
    _seed_from_channel(db, "c1", 95)  # 점수가 더 높아도

    assert publish.candidate(db).video_id == "s1"
    assert publish.remaining(db) == 1


def test_채널을_안_막을_수도_있다(db, cli, monkeypatch):
    monkeypatch.setattr(settings, "blog_skip_channel", False)
    _seed(db, "s1", 90)
    _seed_from_channel(db, "c1", 95)
    assert publish.candidate(db).video_id == "c1"
    assert publish.remaining(db) == 2
