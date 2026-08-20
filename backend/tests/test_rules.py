"""룰 필터 검증 — API 키 없이 돌아갑니다.

필터는 비용을 가장 크게 좌우하는 곳이라, 기준을 바꿀 때마다 무엇이
떨어지고 무엇이 통과하는지 여기서 확인합니다.
"""

from datetime import timedelta

import pytest

from app.collector.rules import WINDOW_MAX_DAYS, evaluate, window_label, window_start
from app.collector.youtube import Candidate, duration_bucket, parse_duration
from app.db.models import Keyword
from config.settings import settings
from config.time import now_kst


def make_candidate(**over) -> Candidate:
    base = dict(
        video_id="abc12345678",
        title="쿠버네티스 CNI 플러그인 심층 분석",
        description="",
        channel_id="ch1",
        channel_title="인프라 노트",
        # 기본 창(90일) 안입니다. 예전 전역 기준이 180일이라 100일로 두었는데,
        # 상한이 석 달로 내려오면서 이 값 자체가 "오래됨" 이 됐습니다.
        published_at=now_kst() - timedelta(days=30),
        duration_sec=4360,
        view_count=12_000,
        like_count=300,
        comment_count=20,
        thumbnail_url=None,
        default_language="ko",
        has_caption=True,
        search_rank=0,
    )
    base.update(over)
    return Candidate(**base)


def make_keyword(**over) -> Keyword:
    base = dict(
        id="kw_t",
        term="쿠버네티스 네트워킹",
        status="active",
        language="ko",
        schedule="daily",
        min_duration_sec=1200,
        max_duration_sec=14400,
        min_expert_score=75,
        max_per_run=10,
    )
    base.update(over)
    return Keyword(**base)


def test_통과하는_후보():
    assert evaluate(make_candidate(), make_keyword()).ok


@pytest.mark.parametrize(
    "over,expect",
    [
        ({"duration_sec": 900}, "길이 미달"),
        ({"duration_sec": 20000}, "길이 초과"),
        ({"published_at": now_kst() - timedelta(days=1000)}, "오래됨"),
        ({"title": "무료특강 신청하세요"}, "홍보성 제목"),
        ({"title": "சட்டப்பேரவையில் OPS"}, "읽을 수 없는 언어"),
    ],
)
def test_탈락_사유(over, expect):
    v = evaluate(make_candidate(**over), make_keyword())
    assert not v.ok
    assert expect in v.reason
    # 사유에 실제 값이 들어가야 기준 조정 판단이 됩니다
    assert v.reason != expect


def test_언어를_모르면_통과시킨다():
    """유튜브가 언어를 안 주는 경우가 흔합니다. 모른다고 떨어뜨리면
    한국어 강의도 같이 떨어집니다."""
    assert evaluate(make_candidate(default_language=None), make_keyword()).ok


def test_키워드_설정이_전역값보다_우선():
    kw = make_keyword(min_duration_sec=3600)
    assert not evaluate(make_candidate(duration_sec=1800), kw).ok
    assert evaluate(make_candidate(duration_sec=1800), make_keyword()).ok


def test_컨퍼런스_세션이_통과한다():
    """M2 실측에서 나온 사례. 조회수 300~400 대인 한국 기술 컨퍼런스
    발표가 예전 기준(1,000)에 통째로 걸렸습니다."""
    c = make_candidate(title="[CNKCD2025] Cilium과 Istio Ambient 모드", view_count=380)
    assert evaluate(c, make_keyword()).ok


@pytest.mark.parametrize(
    "iso,sec",
    [
        ("PT1H34M5S", 5645),
        ("PT45M", 2700),
        ("PT58S", 58),
        ("P1DT2H", 93600),
        ("", 0),
    ],
)
def test_기간_파싱(iso, sec):
    assert parse_duration(iso) == sec


@pytest.mark.parametrize(
    "min_sec,bucket",
    [(0, "any"), (240, "any"), (900, "any"), (1200, "long"), (3600, "long")],
)
def test_검색_길이_구간(min_sec, bucket):
    """유튜브는 short(4분 미만)·medium(4~20분)·long(20분 초과) 세 칸뿐입니다.

    **medium 은 절대 쓰지 않습니다** — 20분에서 잘려 긴 강의가 통째로
    사라집니다. 실제로 그렇게 넣었다가 50건 전부 탈락했습니다.

    **20분 미만을 원하면 조건을 아예 안 겁니다.** long 을 걸면 5~20분대가
    검색 단계에서 사라지는데, 우리가 직접 거르는 편이 정확합니다.
    """
    assert duration_bucket(min_sec) == bucket
    assert duration_bucket(min_sec) != "medium"


def test_조회수는_기본으로_보지_않는다():
    """300회로 두었더니 룰 탈락 330건 중 157건이 여기서 걸렸고, 걸린 것들이
    하필 틈새 전문 콘텐츠였습니다(287회 24분짜리 인프라 해설 등). 틈새
    강의는 정의상 조회수가 적어서, 품질 신호로 쓰면 목적과 반대로 걸립니다."""
    assert settings.rule_min_view_count == 0
    assert evaluate(make_candidate(view_count=3), make_keyword()).ok


def test_조회수_기준을_켜면_다시_걸린다(monkeypatch):
    """끈 것이지 없앤 것이 아닙니다 — .env 로 되돌릴 수 있어야 합니다."""
    monkeypatch.setattr(settings, "rule_min_view_count", 300)
    v = evaluate(make_candidate(view_count=120), make_keyword())
    assert not v.ok and "조회수 미달" in v.reason


# ── 검색 기간 ────────────────────────────────────────────────


def test_기간은_키워드마다_다르다():
    """`경제` 는 하루, `면역력` 은 석 달. 같은 영상이 한쪽에서는 통과하고
    한쪽에서는 떨어져야 이 기능이 있는 뜻이 있습니다."""
    week_old = make_candidate(published_at=now_kst() - timedelta(days=7))
    assert not evaluate(week_old, make_keyword(search_window_days=1)).ok
    assert evaluate(week_old, make_keyword(search_window_days=90)).ok


def test_기간_사유에_기준이_적힌다():
    v = evaluate(
        make_candidate(published_at=now_kst() - timedelta(days=10)),
        make_keyword(search_window_days=1),
    )
    assert not v.ok
    assert "오래됨" in v.reason and "1일" in v.reason


@pytest.mark.parametrize(
    "days,label", [(1, "1일"), (3, "3일"), (7, "1주"), (14, "2주"), (30, "1개월"), (90, "3개월")]
)
def test_기간_이름(days, label):
    assert window_label(days) == label


def test_상한은_석_달():
    """값이 어떻게 들어와도 석 달을 넘겨 긁지 않습니다. 그 위로 열면
    "새로 올라온 것을 모은다" 가 아니라 과거를 긁는 일이 되고, 요약
    비용이 통째로 그쪽으로 갑니다."""
    kw = make_keyword(search_window_days=3650)
    span = now_kst() - window_start(kw)
    assert span <= timedelta(days=WINDOW_MAX_DAYS, seconds=5)


def test_아직_저장_전이면_기본값():
    """컬럼 default 는 INSERT 때 붙습니다 — 새로 만든 객체는 None 이라,
    그대로 빼면 TypeError 로 죽습니다."""
    kw = make_keyword()
    kw.search_window_days = None
    assert evaluate(make_candidate(), kw).ok


def test_못_돈_날만큼_더_거슬러_본다():
    """창이 1일인데 쿼터 대기로 하루를 거르면, 어제 것은 다음 실행에서
    이미 창 밖입니다 — 영영 못 봅니다. 마지막 실행 이후는 무슨 일이
    있어도 훑어야 합니다."""
    kw = make_keyword(search_window_days=1, last_run_at=now_kst() - timedelta(days=5))
    assert window_start(kw) <= now_kst() - timedelta(days=5)
    # 그렇다고 상한을 넘지는 않습니다
    old = make_keyword(search_window_days=1, last_run_at=now_kst() - timedelta(days=400))
    assert now_kst() - window_start(old) <= timedelta(days=WINDOW_MAX_DAYS, seconds=5)


def test_최근에_돌았으면_창_그대로():
    """평소에는 늘어나지 않아야 합니다 — 밀린 날을 메우는 장치이지
    창을 넓히는 장치가 아닙니다."""
    kw = make_keyword(search_window_days=1, last_run_at=now_kst() - timedelta(hours=6))
    assert window_start(kw) >= now_kst() - timedelta(days=1, seconds=5)


def test_언어_무관이면_영어도_통과한다():
    """룰 탈락 124건이 전부 영어였습니다(en·en-US·en-GB·en-IN·en-CA).
    요약은 어차피 한국어로 쓰므로, 원하면 받을 수 있어야 합니다."""
    kw = make_keyword(language="any")
    assert evaluate(make_candidate(default_language="en-US"), kw).ok


def test_지운_키워드는_기준을_쥐지_않는다():
    """실제로 있었던 일: `결제 시스템 설계`(기준 80점)를 삭제했는데, 그
    키워드가 데려온 영상들이 계속 80점으로 판정돼 58점짜리가 탈락했습니다.
    살아 있는 키워드 기준은 45점이었는데도요.

    여기서는 쿼리 조건만 잠급니다. 이 목록은 `workspace.prepare` 를 거쳐
    프롬프트의 `search_keywords` 로 들어갑니다 — 지운 키워드가 섞이면
    AI 가 엉뚱한 기준으로 관련도를 재고, 그 값이 `red_flags` 에 남습니다.

    담을지 말지는 이제 키워드 기준을 보지 않습니다(policy.should_publish 는
    요약이 나왔는지만 봅니다). 그래서 잠글 곳이 한 군데로 줄었습니다."""
    import inspect

    from app.llm import runner

    assert "Keyword.archived_at.is_(None)" in inspect.getsource(runner.review_video)


def test_구독한_채널에는_언어_필터를_걸지_않는다():
    """`defaultLanguage` 는 업로더가 손으로 넣는 값이라 믿을 게 못 됩니다.
    `박종훈의 지식한방`(한국어 채널)의 영상 27편이 `ja` 로 찍혀 있어
    통째로 떨어지고 있었습니다. 직접 고른 채널을 메타데이터로 다시
    심사할 이유가 없습니다 — 조회수 규칙과 같은 이유입니다."""
    kw = make_keyword(source_type="channel", language="ko")
    v = evaluate(make_candidate(default_language="ja"), kw)
    assert v.ok, v.reason


def test_검색_키워드도_언어값으로는_거르지_않는다():
    """**뒤집었습니다.** 예전에는 키워드 언어가 `ko` 면 `defaultAudioLanguage`
    가 다른 것을 떨어뜨렸습니다. 그런데 그 값이 틀립니다 —
    `박종훈의 지식한방`(한국어 채널) 27편이 `ja` 로 찍혀 있고, 요약 실패
    43건 중 22건이 한국어 강의인데 `en-US`·`ja`·`zh` 였습니다.

    채널 구독에만 예외를 두고 있었는데, 값 자체를 못 믿는 것이라 예외를
    둘 자리가 아니었습니다. 언어는 이제 제목의 문자로 봅니다.
    """
    kw = make_keyword(source_type="search", language="ko")
    v = evaluate(make_candidate(default_language="ja"), kw)
    assert v.ok, "잘못 찍힌 한국어 강의를 떨어뜨리면 안 됩니다"


# ── 읽을 수 없는 언어 ────────────────────────────────────────


def test_읽을_수_없는_언어는_제목에서_갈린다():
    """곳간은 한국어·영어 강의를 모으는데 검색은 언어를 가리지 않습니다.
    타밀·태국·힌디 영상이 자막(GPU 2~7분)과 요약(편당 6~8만 토큰)을 다
    치른 뒤에야 "무관"으로 판정됐습니다 — **18편이 공개까지 갔고 그중
    15편이 irrelevant** 였습니다.
    """
    from app.collector.rules import foreign_script

    assert foreign_script("சட்டப்பேரவையில் OPS, செங்கோட்டையன்") == "타밀"
    assert foreign_script("AI Girl Series จะ Gen จนกว่าจะเจอ") == "태국"
    assert foreign_script("Жуткое будущее ИИ | Варламов") == "키릴"
    assert foreign_script("Energy Drinks ज़हर हैं") == "데바나가리"


def test_한국어_영어_강의는_걸리지_않는다():
    """실측: 낯선 문자 제목 131건 중 **한글이 섞인 것은 0건**이었습니다.
    오탐이 0 인 신호는 흔치 않아서, 이 성질이 깨지면 알아야 합니다."""
    from app.collector.rules import foreign_script

    assert foreign_script("반도체 주가 역대급 싼 데, 왜 떨어졌나?") is None
    assert foreign_script("PHOTON: LLM 추론 효율 극대화, 수직 스캐닝") is None
    assert foreign_script("AI Is Changing Time: Those Who Can't Catch Up") is None


def test_한자와_가나는_막지_않는다():
    """한국어 제목에 한자가 섞이고(`韓`·`美`), 나중에 일본어 강의를 원할
    수도 있습니다. 실측에서도 이 둘은 문제를 일으키지 않았습니다."""
    from app.collector.rules import foreign_script

    assert foreign_script("美 증시 전망과 韓 반도체") is None
    assert foreign_script("日本語のタイトル") is None


def test_못_믿는_언어값으로는_거르지_않는다():
    """`defaultAudioLanguage` 는 업로더가 손으로 넣는 값이라 틀립니다 —
    요약 실패 43건 중 22건이 한국어 강의인데 `en-US`·`ja`·`zh` 였습니다.

    지금은 모든 키워드가 `any` 라 이 검사가 안 돌고 있었지만, **누군가
    키워드를 `ko` 로 바꾸는 순간** 멀쩡한 강의가 조용히 떨어집니다.
    그런 코드는 남겨 두면 함정이 됩니다.
    """
    import inspect

    from app.collector import rules

    code = "\n".join(
        ln.split("#", 1)[0] for ln in inspect.getsource(rules.evaluate).splitlines()
    )
    assert "default_language" not in code, "못 믿는 값으로 거르면 안 됩니다"
