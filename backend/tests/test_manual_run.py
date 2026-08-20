"""**사람이 눌러서 시작하는 길** — 그리고 실패한 것을 손보는 길.

정기 실행만 있으면 고쳐 놓고도 다음 차례까지 기다려야 합니다. 요약 실패는
대개 그 편의 문제가 아니라 세션이 죽었거나 모델이 스키마를 어긴 것이라,
고친 뒤 통째로 다시 돌리면 그냥 됩니다. 그 자리가 없어서 그동안
`scripts/revive_transcripts.py` 를 터미널에서 돌려야 했습니다.

**누른 것이 스케줄을 밀어야 합니다.** 방금 돌렸는데 1분 뒤 정기 실행이
또 도는 것은 눌러 준 사람의 뜻이 아닙니다.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api.main import app
from app.collector import failures, jobs
from app.db.models import (
    Base,
    CrawlRun,
    Evaluation,
    Keyword,
    PipelineEvent,
    Transcript,
    User,
    Video,
)
from app.db.session import get_db
from config.settings import settings
from config.time import now_kst

API = "/api/v1"


@pytest.fixture
def session_factory():
    url = settings.database_url.replace("/dukgotgan?", "/dukgotgan_test?")
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def db(session_factory):
    s = session_factory()
    yield s
    s.close()


@pytest.fixture
def client(session_factory, db):
    def _db():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    owner = User(name="관리자", is_owner=True)
    db.add(owner)
    db.commit()
    with TestClient(app) as c:
        assert c.post(f"{API}/session", json={"userId": owner.id}).status_code == 200
        yield c
    app.dependency_overrides.clear()


def a_video(db, vid, state, reason=None, title="어떤 강의"):
    db.add(
        Video(
            id=vid, title=title, channel_id="ch1", channel_title="채널",
            state=state, state_reason=reason, duration_sec=1800, published_at=now_kst(),
        )
    )
    db.commit()
    return db.get(Video, vid)


# ── 눌러서 시작 ──────────────────────────────────────────────


def test_트랙마다_따로_시작할_수_있다(client):
    """기다리는 요청이 전체에 하나뿐이면, 검색을 눌러 놓고는 요약을 못
    누릅니다 — 서로 다른 일인데 한 줄을 두고 다투게 됩니다."""
    assert client.post(f"{API}/runs", json={"job": "review"}).status_code == 202
    # 같은 트랙을 두 번 누르면 막습니다. 줄이 두 개 서 봐야 소용없습니다.
    assert client.post(f"{API}/runs", json={"job": "review"}).status_code == 409
    # 다른 트랙은 그대로 됩니다.
    assert client.post(f"{API}/runs", json={"job": "discover"}).status_code == 202
    assert client.post(f"{API}/runs", json={"job": "cleanup"}).status_code == 400


def test_본문이_없으면_예전처럼_검색이다(client):
    """대시보드의 "지금 실행" 이 그 뜻이었습니다. 옛 호출이 조용히
    다른 일을 하게 되면 안 됩니다."""
    r = client.post(f"{API}/runs")
    assert r.status_code == 202
    assert r.json()["job"] == "discover"


def test_잡은_자기_앞으로_온_요청만_집는다(db):
    """검색 잡이 잡을 가리지 않고 집어가던 때는, 요약을 눌러도 검색이
    그것을 삼키고 아무 일도 안 했습니다."""
    for job in ("discover", "review"):
        db.add(CrawlRun(job=job, trigger="manual", status="queued", started_at=now_kst()))
    db.commit()

    got = jobs.take_queued_run(db, "review")
    assert got is not None and got.job == "review"
    assert jobs.take_queued_run(db, "review") is None, "한 번 집으면 끝입니다"
    assert jobs.take_queued_run(db, "discover") is not None


def test_잡을_나누기_전_요청은_검색이_거둔다(db):
    """아무도 자기 것으로 치지 않으면 영영 `queued` 로 남아, 화면의
    "이미 기다리는 요청이 있습니다" 가 풀리지 않습니다."""
    db.add(CrawlRun(job="cycle", trigger="manual", status="queued", started_at=now_kst()))
    db.commit()
    assert jobs.take_queued_run(db, "review") is None
    assert jobs.take_queued_run(db, "discover") is not None


def test_검색을_누르면_차례가_아닌_키워드도_돈다(db, monkeypatch):
    """문서에는 처음부터 "주기를 무시하고 활성 키워드를 전부"라고 적혀
    있었는데 코드는 `due_keywords()` 를 그대로 썼습니다. 오늘 이미 돈
    키워드만 있는 날에는 **눌러도 아무 일이 없었습니다.**"""
    from app.collector import discover as D

    db.add(Keyword(term="오늘 이미 돎", status="active", last_run_at=now_kst()))
    db.commit()

    seen = {}

    def fake(db_, keyword_ids, trigger, run):
        seen["ids"] = list(keyword_ids or [])
        return run, []

    monkeypatch.setattr(D, "run_discovery", fake)

    jobs.discover_job(db, trigger="scheduled")
    assert "ids" not in seen, "차례가 아니면 정기 실행은 건드리지 않습니다"

    jobs.discover_job(db, trigger="manual")
    assert len(seen.get("ids", [])) == 1, "눌렀으면 차례를 무시하고 돕니다"


def test_요약은_눌러도_상한을_넘지_않는다(db, monkeypatch):
    """건너뛰고 싶은 것은 "5건 모일 때까지 기다리기" 쪽입니다. 눌렀다고
    상한을 넘겨 쓰면 그건 상한이 아닙니다."""
    from app.llm import pace, usage

    a_video(db, "v1", "TRANSCRIBED")
    # 방금 한 편 담았다고 두어야 "모으는 중" 상태가 됩니다. 판정 이력이
    # 아예 없으면 `review_due` 는 "처음"이라며 그냥 돌립니다.
    a_video(db, "done", "PUBLISHED")
    db.add(Evaluation(video_id="done", model="claude", verdict="expert", expert_score=80))
    db.commit()
    monkeypatch.setattr(pace, "resume_at", lambda db_, p: None)
    monkeypatch.setattr(pace, "clear_capped", lambda db_, p: None)
    monkeypatch.setattr(pace, "mark_capped", lambda db_, p, why: None)

    # 한 건뿐이라 평소에는 모으기를 기다립니다.
    monkeypatch.setattr(usage, "check", lambda db_: None)
    assert jobs.review_due(db)[0] is False
    assert jobs.review_due(db, force=True)[0] is True, "누르면 모으기는 건너뜁니다"

    # 상한에 닿아 있으면 눌러도 안 됩니다.
    def over(db_):
        raise usage.UsageExceeded(10, 5, now_kst())

    monkeypatch.setattr(usage, "check", over)
    go, _, why = jobs.review_due(db, force=True)
    assert go is False and "상한" in why


# ── 실패를 손보는 길 ─────────────────────────────────────────


def test_다시_세울_때_자막이_있으면_요약_줄로_간다(client, db):
    """요약 실패는 요약 줄로 돌아가야 합니다. 그런데 처리가 끝난 상태의
    자막 원문은 30일 뒤 지워지므로(collector/cleanup.py), 없는 것을 요약
    줄에 세우면 그 자리에서 다시 죽습니다."""
    a_video(db, "keep", "FAILED_REVIEW", "실행 실패: Claude Code returned an error result")
    a_video(db, "lost", "FAILED_REVIEW", "실행 실패: Claude Code returned an error result")
    db.add(Transcript(video_id="keep", language="ko", source="captions", content="자막", char_count=4))
    db.commit()

    r = client.post(f"{API}/queue/retry", json={"kind": "review"})
    assert r.status_code == 200, r.text
    assert r.json()["restored"] == 2

    db.rollback()
    assert db.get(Video, "keep").state == "TRANSCRIBED"
    assert db.get(Video, "lost").state == "TRANSCRIPT_PENDING", "원문이 없으면 자막부터"


def test_다시_세운_기록이_남아야_기회가_생긴다(client, db):
    """자막·요약 양쪽 재시도 횟수를 "줄에 새로 선 뒤"부터만 셉니다
    (`_retries`). 이 한 줄이 없으면 되살린 영상이 첫 딸꾹질에 그대로
    다시 죽습니다."""
    a_video(db, "v1", "FAILED_TRANSCRIPT", "자막 없음 · 403")
    db.commit()

    client.post(f"{API}/queue/retry", json={"videoIds": ["v1"]})
    db.rollback()
    ev = db.scalars(select(PipelineEvent).where(PipelineEvent.video_id == "v1")).all()
    assert [e.stage for e in ev] == ["revive"]
    assert ev[0].to_state == "TRANSCRIPT_PENDING"
    assert db.get(Video, "v1").state_reason is None, "사유를 지워야 실패 목록에서 빠집니다"


def test_자막이_못_쓸_것이면_자막부터_다시_받는다(client, db):
    """**요약만 다시 부르면 같은 자막을 읽고 같은 결론이 납니다.**

    받아쓰기가 유튜브의 잘못된 언어값을 그대로 써서 한국어 강의를 일본어로
    옮겨 놓은 것들이 그랬습니다 — 자막이 남아 있으니 평소 규칙대로라면
    요약 줄로 가는데, 그 자막이 망가진 것이라 가 봐야 소용이 없습니다.
    """
    a_video(db, "v1", "FAILED_REVIEW", "요약이 오지 않았습니다 · irrelevant — ASR 오인식")
    db.add(Transcript(video_id="v1", language="ja", source="local_asr",
                      content="7月29日は…", char_count=8))
    db.commit()

    # 평소 규칙: 자막이 있으니 요약 줄로.
    client.post(f"{API}/queue/retry", json={"videoIds": ["v1"]})
    db.rollback()
    assert db.get(Video, "v1").state == "TRANSCRIBED"

    # 자막부터 다시: 있는 것을 못 본 셈 치고 자막 줄로.
    db.get(Video, "v1").state = "FAILED_REVIEW"
    db.commit()
    r = client.post(f"{API}/queue/retry", json={"videoIds": ["v1"], "refetch": True})
    assert r.json()["transcript"] == 1
    db.rollback()
    assert db.get(Video, "v1").state == "TRANSCRIPT_PENDING"


def test_고른_것만_건드린다(client, db):
    """**일괄 처리가 사용자가 본 적 없는 줄을 건드리면 안 됩니다.**
    그래서 서버에 필터 언어를 두지 않고, 화면이 고른 것을 그대로 받습니다."""
    a_video(db, "picked", "FAILED_REVIEW", "실행 실패")
    a_video(db, "other", "FAILED_REVIEW", "실행 실패")
    db.commit()

    r = client.post(f"{API}/queue/retry", json={"videoIds": ["picked"]})
    assert r.json()["restored"] == 1
    db.rollback()
    assert db.get(Video, "other").state == "FAILED_REVIEW"


def test_무리_전체는_다시_해_볼_만한_것만(client, db):
    """자막 실패에는 "다시 해도 같은 것"(자막이 8자, 영상이 세 시간)이
    섞여 있습니다. 통째로 밀면 그것들이 매번 줄 앞을 차지합니다."""
    a_video(db, "worth", "FAILED_TRANSCRIPT", "자막 없음 · 403 Forbidden")
    a_video(db, "dud", "FAILED_TRANSCRIPT", "요약할 내용이 없습니다 · 453초 영상에 8자")
    db.commit()

    r = client.post(f"{API}/queue/retry", json={"kind": "transcript", "onlyRetryable": True})
    assert r.json()["restored"] == 1
    db.rollback()
    assert db.get(Video, "dud").state == "FAILED_TRANSCRIPT"


def test_완전히_빼면_다시_수집하지_않는다(client, db):
    """`SKIPPED`(미리 빼기)와 다릅니다 — 저건 다음 검색에 다시 들어옵니다.
    되풀이 실패를 끊으려면 발견 단계가 보고 지나치는 상태여야 합니다."""
    from app.collector import discover as D
    import inspect

    a_video(db, "v1", "FAILED_TRANSCRIPT", "자막 없음")
    db.commit()

    assert client.post(f"{API}/queue/exclude", json={"videoIds": ["v1"]}).json()["excluded"] == 1
    db.rollback()
    assert db.get(Video, "v1").state == "EXCLUDED"
    # 발견 단계가 실제로 이 상태를 거르는지 — 값이 갈리면 조용히 다시 들어옵니다.
    assert '"EXCLUDED"' in inspect.getsource(D.run_discovery) or "EXCLUDED" in inspect.getsource(D)


def test_다시_해도_같은_것을_가른다():
    """**"영구"만 셉니다.** 처음에는 반대로 "일시적" 표시를 세었는데, 실제
    장부의 실패 165건 중 한 건도 안 걸렸습니다 — 요약 실패의 사유는
    `Claude Code returned an error result` 처럼 미리 적어 둘 수 없는
    문장이었습니다."""
    assert failures.retryable("실행 실패: Claude Code returned an error result")
    assert failures.retryable("invalid arguments:\n- at '/summary': missing property")
    assert failures.retryable("자막 없음 · 403 Forbidden")
    assert failures.retryable(None), "모르는 실패는 한 번 더 해 봅니다"

    assert not failures.retryable("요약할 내용이 없습니다 · 453초 영상에 8자")
    assert not failures.retryable("자막 없음 · 영상이 너무 깁니다 (206분)")
    assert not failures.retryable("비공개 영상입니다")


def test_모델이_못_하겠다고_한_것은_고장이_아니다():
    """**실측 47건 중 43건이 이것이었습니다.** 모델이 `summary=null` 을
    보낸 것을 우리가 "실패"로 적고 재시도 목록에 계속 올려 두었습니다 —
    한 편이 38번을 그렇게 돌았습니다.

    다시 불러도 같은 자막을 읽고 같은 결론을 냅니다. 자막이 깨진 쪽은
    **자막부터 다시 받아야** 풀리는 것이라, 요약 재시도와는 다른 길입니다.
    """
    from app.llm.store import NO_SUMMARY

    assert not failures.retryable(f"{NO_SUMMARY} · irrelevant — 21초짜리 뉴스 쇼츠")
    # 근거가 없어도, 옛 기록(문장 하나뿐)이어도 같게 봅니다.
    assert not failures.retryable(f"{NO_SUMMARY} · promotional")
    assert not failures.retryable("요약이 오지 않았습니다.")


def test_왜_못_했는지를_사유에_담는다():
    """모델은 이유를 이미 말하고 있었습니다 — `red_flags` 에. 우리가 안
    옮겨서 화면에는 "요약이 오지 않았습니다." 한 문장만 남았고, 그걸로는
    자막을 다시 받을 일인지 아주 뺄 일인지 갈리지 않았습니다."""
    from app.llm.store import _no_summary_reason

    class 가짜:
        verdict = "irrelevant"
        red_flags = ["", "  ", "한국어 영상을 일본어(language: ja)로 오인식한 ASR 결과물"]

    r = _no_summary_reason(가짜())
    assert "일본어" in r and "irrelevant" in r
    assert not failures.retryable(r), "사유가 길어져도 갈래는 그대로여야 합니다"

    class 근거없음:
        verdict = "promotional"
        red_flags = []

    r2 = _no_summary_reason(근거없음())
    assert "promotional" in r2, "근거가 없어도 판정만은 적습니다"
    assert not r2.endswith("—"), "빈 근거로 문장을 끊지 않습니다"
