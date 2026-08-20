"""**멈춘 것을 어떻게 알아채고, 어떻게 말하는가.**

둘은 같은 문제의 양면입니다. 워커는 붙들린 잡을 로그에 적고, 화면은 멈춘
트랙을 문장으로 적습니다. 어느 쪽이든 틀리면 결과가 같습니다 — 다음부터
아무도 안 읽습니다.
"""

import time
from datetime import timedelta

from config.time import now_kst


# ── 워치독 ───────────────────────────────────────────────────


def test_일하는_중에는_붙들렸다고_하지_않는다():
    """실제로 이렇게 찍혔습니다.

        01:29:40 워커 시작 — review 60초
        01:32:13 [agy] pg9ga4ix5_M 담았습니다
        01:33:50 [agy] DwrwsRj8Z7s 담았습니다
        01:38:54 [agy] 67Uj3muPLpc 담았습니다
        01:39:40 [watchdog] review 잡이 10분째 한 바퀴도 못 돌았습니다

    세 편을 멀쩡히 담는 중이었습니다. `run_once` 가 **돌아왔는지**로만
    쟀기 때문인데, 요약 한 호출은 대기를 스무 편까지 붙잡고 돕니다.
    """
    from app.collector import beat
    from scripts import worker

    worker._ticked["review"] = time.monotonic() - 60 * 60  # 한 시간째 안 돌아옴
    beat._beats["review"] = (time.monotonic() - 300, "방금 담은 강의")  # 5분 전 한 편
    worker._warned.clear()

    assert worker.stalled(["review"]) == [], "한 편씩 담는 중이면 붙들린 것이 아닙니다"


def test_정말_붙들리면_무엇을_하다_멈췄는지까지_적는다():
    """잡 이름만 적으면 로그를 거슬러 올라가야 하는데, 붙들린 잡은
    정의상 아무 줄도 남기지 않습니다 — 거슬러 올라갈 것이 없습니다."""
    from app.collector import beat
    from scripts import worker

    worker._ticked["review"] = time.monotonic() - 40 * 60
    beat._beats["review"] = (time.monotonic() - 40 * 60, "두 시간짜리 강의")
    worker._warned.clear()

    out = worker.stalled(["review"])
    assert len(out) == 1
    name, minutes, what = out[0]
    assert (name, what) == ("review", "두 시간짜리 강의")
    assert minutes >= 40

    # 한 번 붙들리면 풀릴 때까지 조용할 테니 같은 경고로 로그를 덮지 않습니다.
    assert worker.stalled(["review"]) == []


def test_한_걸음의_크기가_다르면_기다리는_시간도_다르다():
    """검색은 초 단위로 끝나지만 받아쓰기 한 편은 두 시간짜리 영상이면
    12분입니다. 한 값으로 묶으면 둘 중 하나가 반드시 틀립니다."""
    from app.collector import beat
    from scripts import worker

    for name in ("discover", "transcript"):
        worker._ticked[name] = time.monotonic() - 20 * 60
        beat.forget(name)
    worker._warned.clear()

    flagged = {n for n, _, _ in worker.stalled(["discover", "transcript"])}
    assert flagged == {"discover"}, "20분 조용한 것은 검색에서만 이상한 일입니다"


# ── 화면이 하는 말 ───────────────────────────────────────────


def test_자막이_멈춘_이유를_문_두_개로_갈라_말한다(monkeypatch):
    """**자막 경로만 막힌 것은 멈춤이 아니라 우회입니다.** 소리를 받아
    직접 받아쓰면 되니까요. 그런데 화면은 셋을 다 "쉬는 중" 으로 적었고,
    정작 오디오까지 막혀 아무것도 못 하는 동안에도 같은 말이었습니다.
    """
    from app.api.routes import stats
    from app.collector import transcript

    now = now_kst()
    later = now + timedelta(minutes=30)
    much_later = now + timedelta(hours=4)

    # 이 시험이 보는 것은 **문 두 개를 어떻게 가르는가** 이지 그 아래
    # 붙는 조언이 아닙니다. 조언은 자기 시험이 따로 있습니다(test_upkeep).
    monkeypatch.setattr(stats, "_audio_fix", lambda db: None)

    def 문(caps, audio):
        monkeypatch.setattr(transcript, "blocked_until", lambda db: caps)
        monkeypatch.setattr(transcript, "audio_blocked_until", lambda db: audio)

    문(None, None)
    assert stats._transcript_hold(None, now) is None

    문(later, None)
    h = stats._transcript_hold(None, now)
    assert (h["code"], h["tone"]) == ("captions_blocked", "info"), "받아쓰기로 일이 됩니다"

    문(None, later)
    h = stats._transcript_hold(None, now)
    assert (h["code"], h["tone"]) == ("audio_blocked", "warn"), "자막 있는 것만 됩니다"

    문(later, much_later)
    h = stats._transcript_hold(None, now)
    assert (h["code"], h["tone"]) == ("transcript_blocked", "stop")
    # **늦게 풀리는 쪽을 적습니다.** 이른 쪽을 적으면 그 시각이 지나도
    # 아무 일이 없어, 화면이 거짓말을 한 것이 됩니다.
    assert h["until"] is not None and "T" in h["until"]

    # 이미 지난 냉각은 냉각이 아닙니다.
    문(now - timedelta(minutes=1), None)
    assert stats._transcript_hold(None, now) is None


def test_요약은_회사별로_갈라_말한다(monkeypatch):
    """한쪽만 쉬는 것과 둘 다 멎은 것은 완전히 다른 상황입니다. 합쳐서
    "요약 쉬는 중" 이라고 적으면 그 차이가 사라집니다 — 실제로
    안티그래비티만 멎어 있는데 화면으로는 알 길이 없었습니다."""
    from app.api.routes import stats

    monkeypatch.setattr(stats.resources, "memory_tight", lambda: False)
    monkeypatch.setattr(stats.pace, "resume_at", lambda db, p: None)
    now = now_kst()

    def 회사(label, *, capped=False, resting=None):
        return {
            "provider": label,
            "label": label,
            "restingUntil": resting,
            "capped": capped,
            "working": None,
        }

    둘_다_멀쩡 = [회사("클로드"), 회사("안티그래비티")]
    assert stats._review_hold(None, now, 0, 둘_다_멀쩡) is None

    한쪽만 = [회사("클로드", capped=True), 회사("안티그래비티")]
    h = stats._review_hold(None, now, 12, 한쪽만)
    assert (h["code"], h["tone"]) == ("provider_partial", "info")
    assert "안티그래비티가 이어서" in h["detail"], "줄이 계속 준다는 사실이 먼저입니다"
    assert h["fix"], "상한은 사람이 올리면 곧바로 풀립니다 — 할 일이 있습니다"

    둘_다 = [회사("클로드", capped=True), 회사("안티그래비티", resting="2026-08-20T00:00:00Z")]
    h = stats._review_hold(None, now, 12, 둘_다)
    assert (h["code"], h["tone"]) == ("provider_down", "stop")

    # 메모리가 먼저입니다 — 여기 걸리면 회사가 멀쩡해도 아무것도 안 뜹니다.
    monkeypatch.setattr(stats.resources, "memory_tight", lambda: True)
    h = stats._review_hold(None, now, 12, 둘_다_멀쩡)
    assert h["code"] == "memory_tight"


def test_막힌_데가_없는데_안_도는_이유도_말한다():
    """"대기 3건인데 왜 가만있지" 의 답이 화면 어디에도 없었습니다.
    고장이 아니라 그렇게 하기로 한 것인데도요."""
    from app.api.routes import stats

    now = now_kst()

    class 장부:
        def __init__(self, last):
            self.last = last

        def scalar(self, _stmt):
            return self.last

    h = stats._batching_hold(장부(now - timedelta(minutes=10)), now, 3)
    assert h["code"] == "batching" and h["tone"] == "info"
    assert h["until"] is not None, "안 모여도 언제는 시작한다는 것이 답의 절반입니다"

    # 다 모였으면 기다릴 이유가 없고, 오래 조용했으면 그냥 시작합니다.
    assert stats._batching_hold(장부(now), now, 9) is None
    assert stats._batching_hold(장부(now - timedelta(hours=3)), now, 3) is None


def test_회사_이름_뒤에_조사를_붙여_쓴다():
    """`f"{label} 가"` 로 두었더니 "클로드 가 쉬는 중입니다" 가 나왔습니다.
    회사 이름은 설정에서 오는 값이라 문장에 박아 둘 수 없는데, 그 한
    글자에서 기계가 쓴 티가 납니다."""
    from app.api.routes.stats import _josa

    assert _josa("클로드", "이", "가") == "클로드가"
    assert _josa("안티그래비티", "은", "는") == "안티그래비티는"
    # 받침이 있으면 반대쪽입니다.
    assert _josa("사람", "이", "가") == "사람이"


def test_막히지_않은_것과_일하는_중인_것을_가른다(monkeypatch):
    """요약 대기가 0 인데 화면이 클로드·안티그래비티 둘 다 "도는 중"
    이라고 적었습니다. 아무도 아무것도 안 하는 순간이었습니다.

    `reviewers` 가 **막힘 여부만** 알고 있어서, 화면이 "안 막혔다"를
    "돌고 있다"로 읽을 수밖에 없었습니다 — 화면이 지어낸 말이 아니라
    우리가 답을 안 준 것입니다. 누가 무엇을 쥐고 있는지는
    `videos.claimed_by` 가 회사 이름을 앞에 달고 알고 있습니다.
    """
    from app.api.routes import stats

    monkeypatch.setattr(stats.pace, "resume_at", lambda db, p: None)
    monkeypatch.setattr(stats.pace, "capped", lambda db, p: False)

    class 붙든것:
        def __init__(self, owner, title):
            self.claimed_by, self.title = owner, title
            self.claimed_at = self.updated_at = now_kst()

    class 디비:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self, _stmt):
            return self.rows

    쉬는중 = stats._reviewers(디비([]))
    assert [r["working"] for r in 쉬는중] == [None, None], "대기 0 이면 쥔 것이 없습니다"

    한쪽만 = {r["provider"]: r for r in stats._reviewers(디비([붙든것("antigravity:mac:9", "어떤 강의")]))}
    assert 한쪽만["antigravity"]["working"]["title"] == "어떤 강의"
    assert 한쪽만["claude"]["working"] is None, "남이 쥔 것을 내 것으로 세면 안 됩니다"


def test_사람이_바꿀_수_있는_멈춤은_눌러서_앞당긴다(monkeypatch):
    """**같은 IP 일 때만 맞는 말이었습니다.**

    "차단된 문을 두드리면 차단만 길어진다"고 버튼을 통째로 잠갔는데,
    사람이 VPN 을 바꾸면 냉각이 지키려던 조건 자체가 사라집니다. 그때
    화면은 04:09 까지 기다리라고만 했습니다 — 우리가 모르는 사실을
    이유로 사람을 붙잡아 둔 셈입니다.

    가르는 기준은 **사람이 조건을 바꿀 수 있는가** 입니다.
    """
    from app.api.routes import stats
    from app.collector import transcript

    now = now_kst()
    later = now + timedelta(hours=4)
    monkeypatch.setattr(stats, "_audio_fix", lambda db: None)
    monkeypatch.setattr(transcript, "blocked_until", lambda db: later)
    monkeypatch.setattr(transcript, "audio_blocked_until", lambda db: later)

    h = stats._transcript_hold(None, now)
    assert h["tone"] == "stop"
    assert h["forcible"] is True, "회선을 바꾸면 사라지는 조건입니다"

    # 유튜브 하루 할당량은 구글이 셉니다 — 눌러도 소용없습니다.
    ledger = type("L", (), {"youtube_units": 10**9})()
    db = type("D", (), {"get": lambda self, m, k: ledger})()
    q = stats._discover_hold(db, now)
    assert q is not None and q["forcible"] is False


def test_눌러서_시작하면_냉각을_무시하지_않고_지운다():
    """무시와 지우기는 다릅니다. 무시하면 다음 사이클에 그 기록이 그대로
    남아 또 막고, 누적된 백오프(60·120·240·480분)도 옛 IP 의 것이
    그대로 이어집니다 — 새 회선에서 첫 실패가 곧바로 8시간짜리가 됩니다."""
    import inspect

    from app.collector import jobs, transcript

    src = inspect.getsource(jobs.transcript_job)
    assert "clear_cooldowns(db)" in src

    body = inspect.getsource(transcript.clear_cooldowns)
    assert "set_time" in body and "_set_streak" in body, "냉각과 누적을 같이 지웁니다"
    assert "AUDIO_COOLDOWN_KEY" in body and "COOLDOWN_KEY" in body, "두 문 다"
