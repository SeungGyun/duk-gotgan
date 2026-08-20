"""**낡아서 서는 것을 막는 장치** — 그리고 그 장치가 가짜가 아닌지.

2026-08-20, 오디오가 통째로 403 이 났습니다. 메타데이터는 멀쩡히 읽히고
실제 내려받기만 막혀서 **IP 차단의 얼굴**을 하고 있었고, VPN 을 바꿔 가며
쫓다가 같은 IP 에서 버전만 올려 갈렸습니다 — 6주 낡은 yt-dlp 였습니다.

여기서 지키는 것은 두 가지입니다.

  1. 같은 버전을 매일 "올렸다"고 하지 않는가 (알림이 시끄러우면 안 읽힙니다)
  2. **검증이 진짜 갈라내는가** — 이게 핵심입니다
"""

from datetime import timedelta

from app.collector import upkeep
from config.time import now_kst


def test_같은_버전을_다르다고_보지_않는다():
    """설치된 것은 `2026.08.19`, PyPI 는 `2026.8.19` 라고 말합니다 — 같은
    버전인데 글자가 다릅니다(앞의 0).

    처음에 문자열로 견주었더니 **매일** 올릴 것이 있다고 판단했습니다.
    아무 일도 안 하면서 하루 한 번 "올렸습니다" 를 찍는 알림은 곧
    안 읽힙니다.
    """
    assert upkeep._same("2026.08.19", "2026.8.19")
    assert upkeep._same("2026.7.4", "2026.07.04")
    assert not upkeep._same("2026.07.04", "2026.08.19")
    assert not upkeep._same("2026.8.19", "2027.1.1")


def test_검증은_1MB_를_넘겨_받는다():
    """**가짜 안전망이었습니다.**

    처음엔 64KB 만 받아 보고 통과로 쳤는데, 깨진 버전(2026.7.4)에서도
    그대로 통과했습니다. 실측하니 403 은 첫 바이트가 아니라 1MB 언저리에서
    옵니다.

        64KB 요청 → 64KB 받음
      1464KB 요청 → HTTP Error 403: Forbidden

    검증이 실패를 못 잡으면, 되돌리기 로직 전체가 장식이 됩니다.
    """
    assert upkeep.PROBE_BYTES > 1024 * 1024, "1MB 문턱을 넘겨야 갈립니다"


def test_메타데이터가_아니라_바이트를_받아_본다():
    """`--simulate` 로는 안 됩니다 — 우리가 겪은 고장에서 메타데이터는
    멀쩡히 읽혔습니다. 막히는 자리는 미디어 URL 이라 거기까지 가야 합니다."""
    import inspect

    src = inspect.getsource(upkeep.probe)
    assert "Range" in src and "read(PROBE_BYTES)" in src
    assert "skip_download" in src, "받는 것은 조각뿐입니다 — 한 편을 통째로 받지 않습니다"


def test_검증에_쓸_영상을_박아_두지_않는다():
    """특정 영상 id 를 코드에 박아 두면 그 영상이 내려간 날 검증이 통째로
    거짓말을 합니다. 줄에 있는 것 중 최근 것을 씁니다."""
    import inspect

    src = inspect.getsource(upkeep.probe)
    assert "select(Video.id)" in src


def test_안_되면_되돌린다():
    """검증 없는 자동 업그레이드는 "낡아서 서는 것"을 "새 버전이 깨져서
    서는 것"으로 바꿀 뿐입니다."""
    import inspect

    src = inspect.getsource(upkeep.refresh)
    back = src[src.index("why = probe(db)"):]
    assert 'f"{PACKAGE}=={have}"' in back, "직전 버전으로 되돌려야 합니다"
    assert "set_str" in back, "무엇을 했는지 화면이 읽을 수 있게 남깁니다"


def test_하루에_한_번만_묻는다(monkeypatch):
    """유튜브가 깨지는 주기는 주 단위입니다. PyPI 를 자주 두드릴 이유가 없고,
    청소 잡은 6시간마다 도므로 스스로 세야 합니다."""
    now = now_kst()
    seen = {}

    class 장부:
        def get(self, model, key):
            return type("R", (), {"value": seen.get(key, "")})()

    monkeypatch.setattr(upkeep.state, "get_time", lambda db, k: seen.get(k))
    assert upkeep.due(장부(), now) is True, "한 번도 안 봤으면 봐야 합니다"

    seen[upkeep.CHECKED_KEY] = now - timedelta(hours=2)
    assert upkeep.due(장부(), now) is False

    seen[upkeep.CHECKED_KEY] = now - timedelta(hours=25)
    assert upkeep.due(장부(), now) is True


def test_못_물어봐도_조용히_넘어간다(monkeypatch):
    """네트워크가 잠깐 안 되는 것으로 파이프라인이 시끄러워지면 안 됩니다."""

    def 끊김(*a, **k):
        raise OSError("네트워크 없음")

    monkeypatch.setattr(upkeep.urllib.request, "urlopen", 끊김)
    assert upkeep.latest() is None


def test_청소가_이것도_돌린다():
    """따로 잡을 만들지 않았습니다 — 하루 한 번 도는 청소가 제자리입니다."""
    import inspect

    from app.collector import jobs

    src = inspect.getsource(jobs.cleanup_job)
    assert "upkeep.due(db)" in src and "upkeep.refresh(db)" in src


# ── 막혔을 때 어디를 보라고 할 것인가 ──────────────────────


def test_낡았는지_모르면_짐작하지_않는다(monkeypatch):
    """확인한 적 없는 것을 "최신입니다" 라고 적으면, 그 말을 믿고 엉뚱한
    데를 뒤지게 됩니다 — 2026-08-20 에 VPN 을 바꿔 가며 세 시간을 쓴 것이
    정확히 그런 헤맴이었습니다."""
    from app.api.routes import stats

    monkeypatch.setattr(stats.cookies, "enabled", lambda: False)
    monkeypatch.setattr(stats.upkeep, "stale", lambda db: None)
    assert stats._audio_fix(None) is None


def test_낡았으면_그것부터_말한다(monkeypatch):
    from app.api.routes import stats

    monkeypatch.setattr(stats.cookies, "enabled", lambda: False)
    monkeypatch.setattr(stats.upkeep, "stale", lambda db: True)
    monkeypatch.setattr(stats.upkeep, "installed", lambda: "2026.7.4")
    fix = stats._audio_fix(None)
    assert "낡았습니다" in fix and "2026.7.4" in fix


def test_최신인데도_막히면_그때가_쿠키_자리다(monkeypatch):
    """`cookies.py` 주석에만 있던 답을 화면으로 끌어올립니다. 다만 경고도
    같이 옮깁니다 — 붙이는 순간 이 수집이 그 계정에 붙습니다."""
    from app.api.routes import stats

    monkeypatch.setattr(stats.cookies, "enabled", lambda: False)
    monkeypatch.setattr(stats.upkeep, "stale", lambda db: False)
    fix = stats._audio_fix(None)
    assert "최신입니다" in fix and "YOUTUBE_COOKIES_FILE" in fix
    assert "전용 계정" in fix, "계정 위험을 빼고 권하면 안 됩니다"


def test_이미_붙여_뒀으면_더_권하지_않는다(monkeypatch):
    from app.api.routes import stats

    monkeypatch.setattr(stats.cookies, "enabled", lambda: True)
    monkeypatch.setattr(stats.upkeep, "stale", lambda db: False)
    assert stats._audio_fix(None) is None


def test_오디오가_막힌_갈래에만_붙인다():
    """자막 경로만 막힌 것은 받아쓰기로 우회되는 중이라 손댈 일이
    아닙니다. 멀쩡히 도는 줄에 "이렇게 해 보세요" 를 붙이면 그 문장이
    정작 필요할 때 안 읽힙니다."""
    import inspect

    from app.api.routes import stats

    src = inspect.getsource(stats._transcript_hold)
    head, _, tail = src.partition('"captions_blocked"')
    assert head.count("_audio_fix(db)") == 2, "둘 다 오디오가 걸린 갈래입니다"
    assert "_audio_fix" not in tail, "자막만 막힌 갈래에는 붙이지 않습니다"
