"""발견 파이프라인 — 유튜브 호출을 가짜로 바꿔 DB 동작만 검증합니다.

여기서 보는 것은 두 가지입니다.
  1. 실패해도 실행 이력과 쿼터 장부가 남는가 (한 번 잃어버린 적이 있습니다)
  2. 같은 영상을 두 키워드가 데려와도 videos 행이 하나인가
"""

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.collector import discover as D
from app.collector.youtube import Candidate, YouTubeError
from app.db.models import Base, CrawlRun, Keyword, UsageLedger, Video, VideoKeyword
from config.settings import settings
from config.time import now_kst


@pytest.fixture
def db():
    """테스트 전용 DB. 운영 데이터와 섞이지 않게 별도 스키마를 씁니다."""
    url = settings.database_url.replace("/dukgotgan?", "/dukgotgan_test?")
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def add_keyword(db, kid="kw_a", term="쿠버네티스 네트워킹"):
    kw = Keyword(id=kid, term=term, status="pending", language="ko", schedule="daily",
                 min_duration_sec=900, max_duration_sec=14400, min_expert_score=75, max_per_run=10)
    db.add(kw)
    db.commit()
    return kw


def fake_candidate(vid="vid00000001", **over):
    base = dict(
        video_id=vid, title="CNI 플러그인 심층 분석", description="본문",
        channel_id="ch1", channel_title="인프라 노트",
        published_at=now_kst() - timedelta(days=30),
        duration_sec=4360, view_count=12_000, like_count=3, comment_count=1,
        thumbnail_url=None, default_language="ko", has_caption=True, search_rank=0,
    )
    base.update(over)
    return Candidate(**base)


def patch_youtube(monkeypatch, candidates, ids=None):
    monkeypatch.setattr(settings, "youtube_api_key", "test-key")
    monkeypatch.setattr(D, "search_ids", lambda *a, **k: ids or [c.video_id for c in candidates])
    monkeypatch.setattr(D, "fetch_details", lambda _ids: candidates)


def test_통과와_탈락이_사유와_함께_적재된다(db, monkeypatch):
    add_keyword(db)
    patch_youtube(monkeypatch, [
        fake_candidate("passing0001"),
        fake_candidate("tooshort001", duration_sec=300),
    ])

    run, results = D.run_discovery(db)

    assert run.status == "succeeded"
    assert run.stats == {"discovered": 2, "rulePassed": 1, "transcribed": 0,
                         "reviewed": 0, "published": 0}
    assert db.get(Video, "passing0001").state == "TRANSCRIPT_PENDING"
    rejected = db.get(Video, "tooshort001")
    assert rejected.state == "REJECTED_RULE"
    assert "길이 미달" in rejected.state_reason
    assert results[0].rejected  # CLI 가 찍을 목록


def test_첫_실행_후_pending이_active로(db, monkeypatch):
    kw = add_keyword(db)
    patch_youtube(monkeypatch, [fake_candidate()])
    D.run_discovery(db)
    db.refresh(kw)
    assert kw.status == "active"
    assert kw.last_run_at is not None


def test_같은_영상을_두_키워드가_데려와도_행은_하나(db, monkeypatch):
    add_keyword(db, "kw_a", "쿠버네티스 네트워킹")
    add_keyword(db, "kw_b", "컨테이너 오케스트레이션")
    patch_youtube(monkeypatch, [fake_candidate("shared00001")])

    D.run_discovery(db)

    assert db.scalar(select(func.count()).select_from(Video)) == 1
    assert db.scalar(select(func.count()).select_from(VideoKeyword)) == 2


def test_실패해도_실행_이력과_쿼터가_남는다(db, monkeypatch):
    """예전에 여기서 롤백이 실행 이력까지 지워, 정작 실패를 기록할 행이
    사라졌습니다."""
    add_keyword(db)
    monkeypatch.setattr(settings, "youtube_api_key", "test-key")
    monkeypatch.setattr(D, "search_ids", lambda *a, **k: (_ for _ in ()).throw(
        YouTubeError("유튜브 API 가 요청을 거절했습니다.")))

    run, _ = D.run_discovery(db)

    saved = db.get(CrawlRun, run.id)
    assert saved is not None, "실패한 실행이 이력에 남아야 합니다"
    assert saved.status == "failed"
    assert "거절" in saved.error
    # 검색 호출은 실제로 나갔으므로 유닛은 소비된 것으로 남아야 합니다
    ledger = db.get(UsageLedger, now_kst().date())
    assert ledger.youtube_units == 100
    assert saved.youtube_units == 100


def test_1회_상한을_넘으면_다음_실행으로_미룬다(db, monkeypatch):
    """max_per_run 은 비용 가드입니다. 이게 안 걸리면 첫 실행에서 백로그가
    통째로 AI 로 넘어갑니다 — 실제로 30건이 그렇게 통과한 적이 있습니다."""
    add_keyword(db)
    kw = db.get(Keyword, "kw_a")
    kw.max_per_run = 3
    db.commit()

    cands = [fake_candidate(f"vid{i:08d}", search_rank=i) for i in range(8)]
    patch_youtube(monkeypatch, cands)

    run, results = D.run_discovery(db)

    assert run.stats["rulePassed"] == 3
    assert results[0].deferred == 5
    promoted = db.scalars(select(Video).where(Video.state == "TRANSCRIPT_PENDING")).all()
    # 잘리는 것은 검색 순위 하위여야 합니다
    assert {v.id for v in promoted} == {"vid00000000", "vid00000001", "vid00000002"}
    waiting = db.scalars(select(Video).where(Video.state == "DISCOVERED")).all()
    assert len(waiting) == 5
    assert "상한" in waiting[0].state_reason


def test_미뤄둔_것을_다음_실행에서_이어받는다(db, monkeypatch):
    add_keyword(db)
    kw = db.get(Keyword, "kw_a")
    kw.max_per_run = 2
    db.commit()

    cands = [fake_candidate(f"vid{i:08d}", search_rank=i) for i in range(5)]
    patch_youtube(monkeypatch, cands)

    D.run_discovery(db)
    assert db.scalar(select(func.count()).select_from(Video).where(Video.state == "DISCOVERED")) == 3

    D.run_discovery(db)  # 같은 결과가 또 나옴 — 미뤄둔 것 중 2건이 올라가야
    assert db.scalar(
        select(func.count()).select_from(Video).where(Video.state == "TRANSCRIPT_PENDING")
    ) == 4
    assert db.scalar(select(func.count()).select_from(Video).where(Video.state == "DISCOVERED")) == 1


def test_API_키가_없으면_쿼터를_깎기_전에_멈춘다(db, monkeypatch):
    add_keyword(db)
    monkeypatch.setattr(settings, "youtube_api_key", "")
    with pytest.raises(YouTubeError, match="API 키"):
        D.run_discovery(db)
    assert db.get(UsageLedger, now_kst().date()) is None


def test_사이클이_준_기록에는_손대지_않는다(db, monkeypatch):
    """실행 기록을 사이클이 만들었으면 마감도 사이클이 합니다. 여기서
    succeeded 로 닫아버리면 뒤이어 도는 자막·검토 결과가 못 들어갑니다."""
    from app.db.models import CrawlRun
    from config.time import now_kst

    add_keyword(db)
    patch_youtube(monkeypatch, [fake_candidate()])
    outer = CrawlRun(trigger="scheduled", status="running", started_at=now_kst(), stats={})
    db.add(outer)
    db.commit()

    run, _ = D.run_discovery(db, run=outer)

    assert run.id == outer.id
    assert run.status == "running", "사이클이 마감할 때까지 열려 있어야 합니다"
    assert run.finished_at is None
    assert run.stats["discovered"] == 1


def test_차단한_채널은_룰에서_걸린다(db, monkeypatch):
    """검색어를 다듬는 방법은 실패했습니다 — 제외 연산자를 넣으니 관련도
    랭킹 자체가 무너졌습니다. 대신 AI 판정을 재사용해 채널을 막습니다."""
    from app.db.models import ChannelBlock

    add_keyword(db)
    db.add(ChannelBlock(channel_id="ch_bad", channel_title="코인 뉴스", reason="무관 2회"))
    db.commit()

    patch_youtube(monkeypatch, [
        fake_candidate("good0000001", channel_id="ch_ok"),
        fake_candidate("blocked00001", channel_id="ch_bad", channel_title="코인 뉴스"),
    ])
    run, results = D.run_discovery(db)

    assert run.stats["rulePassed"] == 1
    blocked = db.get(Video, "blocked00001")
    assert blocked.state == "REJECTED_RULE"
    assert "차단한 채널" in blocked.state_reason


def test_한_편이라도_남겨_둔_채널은_막지_않는다(db):
    """좋은 채널도 가끔 주제에서 벗어난 것을 올립니다. 그걸로 막으면
    이후의 좋은 것까지 통째로 놓칩니다."""
    from app.collector.channels import consider_block
    from app.db.models import Lecture
    from config.time import now_kst

    keep = Video(id="pub00000001", title="남겨 둔 것", channel_id="ch_mixed",
                 channel_title="괜찮은 채널", state="PUBLISHED", duration_sec=3000)
    db.add(keep)
    db.flush()
    db.add(Lecture(video_id=keep.id, version=1, expert_score=88, verdict="expert",
                   one_liner="ok", sections=[], tags=[]))
    # 같은 채널에서 세 편을 뺐지만, 남겨 둔 것이 하나 있습니다
    for i in range(3):
        v = Video(id=f"exc0000000{i}", title=f"뺀 것 {i}", channel_id="ch_mixed",
                  channel_title="괜찮은 채널", state="PUBLISHED", duration_sec=3000)
        db.add(v)
        db.flush()
        db.add(Lecture(video_id=v.id, version=1, expert_score=40, verdict="introductory",
                       one_liner="x", sections=[], tags=[], excluded_at=now_kst()))
    db.flush()

    assert consider_block(db, keep) is None, "남겨 둔 것이 있으면 막지 않아야 합니다"


def test_전부_뺀_채널은_자동으로_막는다(db):
    """AI 판정 대신 사용자의 제외를 신호로 씁니다 — 추정이 아니라 사람이
    실제로 "이건 아니다"라고 누른 것이라 훨씬 정확합니다."""
    from app.collector.channels import consider_block
    from app.db.models import Lecture
    from config.time import now_kst

    last = None
    for i in range(3):
        v = Video(id=f"bad0000000{i}", title=f"뺀 것 {i}", channel_id="ch_bad",
                  channel_title="안 맞는 채널", state="PUBLISHED", duration_sec=3000)
        db.add(v)
        db.flush()
        db.add(Lecture(video_id=v.id, version=1, expert_score=30, verdict="promotional",
                       one_liner="x", sections=[], tags=[], excluded_at=now_kst()))
        last = v
    db.flush()

    block = consider_block(db, last)
    assert block is not None and block.auto
    assert "3편" in block.reason


def test_두_번_뺀_정도로는_막지_않는다(db):
    from app.collector.channels import consider_block
    from app.db.models import Lecture
    from config.time import now_kst

    last = None
    for i in range(2):
        v = Video(id=f"two0000000{i}", title=f"뺀 것 {i}", channel_id="ch_two",
                  channel_title="두 번", state="PUBLISHED", duration_sec=3000)
        db.add(v)
        db.flush()
        db.add(Lecture(video_id=v.id, version=1, expert_score=30, verdict="introductory",
                       one_liner="x", sections=[], tags=[], excluded_at=now_kst()))
        last = v
    db.flush()
    assert consider_block(db, last) is None
