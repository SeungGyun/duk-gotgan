"""오디오 받아쓰기 폴백 — 네트워크도 GPU 도 없이 돕니다.

여기서 지키려는 것은 두 가지입니다.

  1. **받은 오디오는 반드시 지운다.** 36분짜리 한 편이 20MB 이고 대기가
     세 자리라, 한 번만 새도 디스크가 찹니다. 성공했을 때만 지우면 실패가
     쌓이는 날 그대로 터집니다.
  2. **자막 경로가 언제나 먼저다.** 이미 만들어진 자막은 1초, 받아쓰기는
     3분입니다. 순서가 뒤집히면 조용히 스무 배 비싸집니다.
"""


import tempfile
from pathlib import Path

import pytest

from app.collector import asr, transcript


class FakeVideo:
    id = "vid123"
    duration_sec = 600
    default_language = "ko"
    title = "테스트"


# ── 정리 ────────────────────────────────────────────────────


def _capture_workdir(monkeypatch) -> dict:
    """transcribe() 가 만드는 임시 폴더 경로를 붙잡아 둡니다."""
    seen = {}
    real = tempfile.mkdtemp

    def spy(*a, **kw):
        seen["path"] = real(*a, **kw)
        return seen["path"]

    monkeypatch.setattr(tempfile, "mkdtemp", spy)
    monkeypatch.setattr(asr, "available", lambda: None)
    return seen


def test_받아쓰기가_끝나면_오디오를_지운다(monkeypatch):
    seen = _capture_workdir(monkeypatch)

    def fake_download(video_id, workdir):
        path = Path(workdir) / "audio.webm"
        path.write_bytes(b"x" * 1024)
        return str(path)

    monkeypatch.setattr(asr, "_download_audio", fake_download)
    monkeypatch.setattr(
        asr, "_run_whisper",
        lambda p, lang: asr.AsrResult("ko", [{"start": 0.0, "dur": 1.0, "text": "안녕"}], 1.0, 1.0),
    )

    asr._transcribe("vid123", 600)
    assert not Path(seen["path"]).exists()


def test_받아쓰기가_실패해도_오디오를_지운다(monkeypatch):
    """성공 경로에서만 지우면, 실패가 쌓이는 날 디스크가 찹니다."""
    seen = _capture_workdir(monkeypatch)

    def fake_download(video_id, workdir):
        path = Path(workdir) / "audio.webm"
        path.write_bytes(b"x" * 1024)
        return str(path)

    monkeypatch.setattr(asr, "_download_audio", fake_download)
    monkeypatch.setattr(asr, "_run_whisper", lambda p, lang: (_ for _ in ()).throw(RuntimeError("펑")))

    with pytest.raises(RuntimeError):
        asr._transcribe("vid123", 600)
    assert not Path(seen["path"]).exists()


def test_너무_긴_영상은_받아쓰지_않는다(monkeypatch):
    monkeypatch.setattr(asr, "available", lambda: None)
    with pytest.raises(asr.AudioUnavailable, match="너무 깁니다"):
        asr.transcribe("vid123", 10 * 3600)


# ── 순서 ────────────────────────────────────────────────────


def test_자막이_있으면_받아쓰기로_가지_않는다(monkeypatch):
    """1초짜리 경로를 두고 3분짜리 GPU 를 돌리면 안 됩니다."""
    called = []
    monkeypatch.setattr(
        transcript, "fetch",
        lambda v: transcript.Fetched("youtube_auto", "ko", [{"start": 0, "dur": 1, "text": "가"}]),
    )
    monkeypatch.setattr(transcript, "fetch_via_asr", lambda v: called.append(v) or None)

    got = transcript._fetch_with_retry(FakeVideo())
    assert got.source == "youtube_auto"
    assert not called


def test_두_경로가_막히면_받아쓰기로_넘어간다(monkeypatch):
    def blocked(v):
        raise transcript.Blocked("429")

    monkeypatch.setattr(transcript, "fetch", blocked)
    monkeypatch.setattr(transcript, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))
    monkeypatch.setattr(
        transcript, "fetch_via_ytdlp",
        lambda v: (_ for _ in ()).throw(transcript.TranscriptUnavailable("없음")),
    )
    monkeypatch.setattr(
        transcript, "fetch_via_asr",
        lambda v: transcript.Fetched(transcript.LOCAL_ASR, "ko", [{"start": 0, "dur": 1, "text": "가"}]),
    )

    got = transcript._fetch_with_retry(FakeVideo())
    assert got.source == transcript.LOCAL_ASR


def test_냉각_중이면_자막_경로를_아예_건너뛴다(monkeypatch):
    """막힌 문을 영상마다 25초씩 두드릴 이유가 없습니다."""
    knocked = []
    monkeypatch.setattr(transcript, "fetch", lambda v: knocked.append(v))
    monkeypatch.setattr(transcript, "fetch_via_ytdlp", lambda v: knocked.append(v))
    monkeypatch.setattr(
        transcript, "fetch_via_asr",
        lambda v: transcript.Fetched(transcript.LOCAL_ASR, "ko", [{"start": 0, "dur": 1, "text": "가"}]),
    )

    got = transcript._fetch_with_retry(FakeVideo(), skip_youtube=True)
    assert got.source == transcript.LOCAL_ASR
    assert not knocked


def test_받아쓰기도_못_하면_차단으로_다룬다(monkeypatch):
    """자막 없음으로 적어 버리면 나중에 차단이 풀려도 다시 시도하지 않습니다."""
    monkeypatch.setattr(asr, "transcribe", lambda *a, **k: (_ for _ in ()).throw(
        asr.AsrUnavailable("ffmpeg 가 없습니다.")))
    with pytest.raises(transcript.Blocked, match="받아쓰기도 못 했습니다"):
        transcript.fetch_via_asr(FakeVideo())


def test_너무_긴_영상은_차단이_아니라_그_영상만의_문제다(monkeypatch):
    """AsrUnavailable 로 던지면 IP 차단으로 취급되어, 긴 영상 한 편이 줄
    맨 앞에 서 있는 것만으로 전체가 60분씩 멈춥니다. 실제로 221분짜리가
    그 자리에 있었습니다."""
    from app.collector import transcript

    monkeypatch.setattr(asr, "available", lambda: None)

    class V:
        id, duration_sec, default_language, title = "v", 10 * 3600, "ko", "긴 영상"

    with pytest.raises(transcript.TranscriptUnavailable, match="너무 깁니다"):
        transcript.fetch_via_asr(V())


# ── 헛도는 한 편이 줄 전체를 세우지 못하게 ──────────────────
#
# 2026-08-14, 87분짜리 한 편의 두 번째 조각에서 위스퍼가 돌아오지 않았습니다.
# 멎은 것이 아니라 헛돈 것이라(디코더가 같은 토큰을 되풀이하며 못 나감),
# 36시간 뒤에도 GPU 는 같은 조각을 갈고 있었습니다. 그동안 자막 잡은 한
# 바퀴도 못 돌았고 대기가 76건 쌓였습니다.


class _FakeChild:
    """살아는 있는데 아무 말이 없는 자식."""

    def __init__(self):
        self.stopped = False
        self.exitcode: int | None = None

    def is_alive(self):
        return not self.stopped

    def terminate(self):
        self.stopped = True

    def kill(self):
        self.stopped = True

    def join(self, timeout=None):
        pass

    def close(self):
        pass


def test_소식이_끊기면_끊어_낸다(monkeypatch):
    """스레드로는 못 끊습니다 — C++ 안에서 도는 것을 파이썬이 깨울 수단이
    없습니다. 프로세스여야 죽일 수 있습니다."""
    import queue as queuelib

    child = _FakeChild()
    empty = type("Box", (), {"get": staticmethod(lambda timeout=None: (_ for _ in ()).throw(queuelib.Empty()))})()
    monkeypatch.setattr(asr, "_POLL_SEC", 0.01)

    with pytest.raises(asr.AudioTemporary, match="진전이 없어"):
        asr._await(empty, child, "vid123", stall=0)


def test_기다리는_시간은_영상이_아니라_조각_길이로_잰다():
    """세 시간짜리라고 세 시간을 기다려 주면, 고장 한 번에 자막 줄이 세
    시간 멈춥니다."""
    from config.settings import settings as s

    긴것 = asr._stall_sec(3 * 3600)
    assert 긴것 == s.asr_chunk_sec + s.asr_stall_grace_sec
    # 짧은 영상은 그만큼만 기다립니다 — 5분짜리에 23분을 줄 이유가 없습니다.
    assert asr._stall_sec(5 * 60) < 긴것


def test_조각마다_살아_있다고_알린다(monkeypatch):
    """부모는 이 신호로만 진전을 압니다. 없으면 긴 영상과 헛도는 영상을
    구분할 방법이 없습니다."""
    _fake_chunks(monkeypatch, [_res("첫"), _res("둘"), _res("셋")])
    monkeypatch.setattr(asr.settings, "asr_chunk_sec", 600)

    beats = []
    asr._transcribe_file("a.webm", "ko", 1800, "/tmp", beat=lambda: beats.append(1))
    assert len(beats) == 3


def test_자식이_말없이_죽으면_그렇다고_적는다(monkeypatch):
    """"진전이 없어 끊었습니다" 로 뭉뚱그리면, 메모리 부족으로 죽은 것과
    헛돌아 끊은 것이 로그에서 구분되지 않습니다."""
    import queue as queuelib

    child = _FakeChild()
    child.stopped = True
    child.exitcode = -9
    empty = type("Box", (), {"get": staticmethod(lambda timeout=None: (_ for _ in ()).throw(queuelib.Empty()))})()
    monkeypatch.setattr(asr, "_POLL_SEC", 0.01)

    with pytest.raises(asr.AudioTemporary, match="예고 없이 끝났습니다"):
        asr._await(empty, child, "vid123", stall=999)


def test_끊긴_한_편은_줄_뒤로_갈_뿐_탈락이_아니다(monkeypatch):
    """탈락으로 적으면 다시는 안 봅니다. 기계가 바빠서 늦은 것일 수도
    있는데, 한 번 느렸다고 영영 버릴 이유가 없습니다."""
    monkeypatch.setattr(asr, "transcribe", lambda *a, **k: (_ for _ in ()).throw(
        asr.AudioTemporary("받아쓰기가 23분째 진전이 없어 끊었습니다")))
    with pytest.raises(transcript.TranscriptRetry, match="진전이 없어"):
        transcript.fetch_via_asr(FakeVideo())


def test_모델은_워커_프로세스에_남지_않는다():
    """놀면서 1.6GB 를 쥐고 있으면 요약 프로세스가 뜰 자리가 없어집니다 —
    실제로 그래서 60건이 죽었습니다. 손으로 내려놓는 대신, 모델이 자식
    프로세스와 함께 사라지게 했습니다."""
    import inspect

    from app.collector import asr as A

    assert not hasattr(A, "release_model")
    # 있는지만 봅니다 — 불러오면 워커가 쓰지도 않을 MLX 를 들고 살게 됩니다.
    assert "find_spec" in inspect.getsource(A.available)


def test_받아쓰기_뒤에는_버퍼_캐시를_비운다():
    """실측: 3분 오디오 한 편 뒤에 캐시 778MB 가 남아 있었습니다. 다음
    파일에서 어차피 다시 잡는 것이라, 그걸 들고 있느라 요약 프로세스가
    뜰 자리를 잃는 것은 손해입니다."""
    import inspect

    from app.collector import asr as A

    assert "mx.clear_cache()" in inspect.getsource(A._run_whisper)


def test_붙들린_잡을_감시자가_알아챈다():
    """자막 잡이 36시간을 헛돌았는데 로그에 한 줄도 안 남았습니다. 다른
    잡은 멀쩡히 돌아서 "워커가 죽었나" 로도 안 보였습니다."""
    import time

    from scripts import worker

    worker._ticked.clear()
    worker._warned.clear()
    worker._ticked["transcript"] = time.monotonic() - 2 * 3600  # 두 시간째 소식 없음
    worker._ticked["discover"] = time.monotonic()  # 멀쩡히 도는 중

    이름들 = ["transcript", "discover"]
    # 셋째 칸은 붙들린 자리에서 하던 일입니다. 여기서는 한 걸음도 못
    # 뗀 채로 멈춘 경우라 비어 있습니다 (collector/beat.py).
    assert worker.stalled(이름들) == [("transcript", 120, "")]
    # **같은 경고로 로그를 덮지 않습니다.** 한 번 붙들리면 풀릴 때까지
    # 조용할 텐데, 1분마다 같은 줄을 찍으면 그게 로그를 먹습니다.
    assert worker.stalled(이름들) == []


# ── 나눠서 받아쓰기 ──────────────────────────────────────────
#
# 상한을 90분에 묶어 둔 것은 시간이 아니라 **메모리** 때문이었습니다.
# 위스퍼가 오디오를 통째로 16kHz float32 로 올려서, 두 시간이면 그것만
# 460MB 이고 그 순간 요약 프로세스가 뜰 자리가 없어집니다(16GB 기계).
# 그 상한에 95~142분짜리 33편이 걸려 탈락했습니다.
#
# 상한을 올리는 것으로는 못 풉니다 — 그 OOM 이 그대로 돌아옵니다. 대신
# 나눠서 한 조각씩 올려, 최대 사용량이 **영상 길이가 아니라 조각 길이로**
# 정해지게 했습니다.


def _fake_chunks(monkeypatch, per_chunk):
    """`_cut` 을 껍데기로 바꾸고, 조각마다 정해진 결과를 돌려줍니다."""
    cut: list[tuple[int, int]] = []

    def fake_cut(src, dst, start_sec, length_sec):
        cut.append((start_sec, length_sec))
        Path(dst).write_bytes(b"fake")

    calls: list[str] = []

    def fake_whisper(path, language):
        calls.append(language)
        got = per_chunk[len(calls) - 1]
        if got is None:
            raise asr.AsrUnavailable("받아쓴 내용이 비었습니다")
        return got

    monkeypatch.setattr(asr, "_cut", fake_cut)
    monkeypatch.setattr(asr, "_run_whisper", fake_whisper)
    return cut, calls


def _res(*texts, lang="ko"):
    segs = [{"start": float(i * 10), "dur": 5.0, "text": t} for i, t in enumerate(texts)]
    return asr.AsrResult(language=lang, segments=segs, elapsed_sec=1.0, audio_sec=100.0)


def test_짧은_영상은_나누지_않는다(monkeypatch):
    """멀쩡히 통째로 되는 것을 굳이 자르면 ffmpeg 만 한 번 더 돕니다."""
    cut, _ = _fake_chunks(monkeypatch, [_res("가")])
    monkeypatch.setattr(asr.settings, "asr_chunk_sec", 20 * 60)

    asr._transcribe_file("a.webm", "ko", 10 * 60, "/tmp")
    assert cut == [], "20분 안쪽이면 자르지 않아야 합니다"


def test_긴_영상은_조각내어_이어_붙인다(monkeypatch):
    """**시각을 밀어 줘야 합니다.** 조각마다 0초부터 다시 세므로, 그대로
    이으면 두 시간짜리 강의의 타임스탬프가 전부 앞 20분에 몰립니다 —
    원본 링크의 시각이 어긋나면 요약에 붙은 근거를 짚을 수 없습니다."""
    cut, _ = _fake_chunks(monkeypatch, [_res("첫"), _res("둘"), _res("셋")])
    monkeypatch.setattr(asr.settings, "asr_chunk_sec", 600)

    out = asr._transcribe_file("a.webm", "ko", 1800, "/tmp")

    assert cut == [(0, 600), (600, 600), (1200, 600)]
    assert [s["start"] for s in out.segments] == [0.0, 600.0, 1200.0]
    assert [s["text"] for s in out.segments] == ["첫", "둘", "셋"]


def test_말소리_없는_조각은_건너뛰고_나머지를_살린다(monkeypatch):
    """음악이나 침묵으로 채워진 조각 하나 때문에 두 시간짜리 강의를
    통째로 버릴 이유가 없습니다."""
    _fake_chunks(monkeypatch, [_res("첫"), None, _res("셋")])
    monkeypatch.setattr(asr.settings, "asr_chunk_sec", 600)

    out = asr._transcribe_file("a.webm", "ko", 1800, "/tmp")
    assert [s["text"] for s in out.segments] == ["첫", "셋"]
    assert [s["start"] for s in out.segments] == [0.0, 1200.0]


def test_전부_비면_그때는_실패다(monkeypatch):
    _fake_chunks(monkeypatch, [None, None])
    monkeypatch.setattr(asr.settings, "asr_chunk_sec", 600)

    with pytest.raises(asr.AsrUnavailable):
        asr._transcribe_file("a.webm", "ko", 1200, "/tmp")


def test_앞_조각이_알아낸_언어를_뒤에_물려준다(monkeypatch):
    """조각마다 따로 감지하면 음악이나 침묵으로 시작하는 조각이 엉뚱한
    언어로 새고, 그 조각만 통째로 헛소리가 됩니다."""
    _, calls = _fake_chunks(monkeypatch, [_res("첫", lang="en"), _res("둘"), _res("셋")])
    monkeypatch.setattr(asr.settings, "asr_chunk_sec", 600)

    asr._transcribe_file("a.webm", "", 1800, "/tmp")
    assert calls == ["", "en", "en"], "첫 조각이 감지한 언어를 뒤가 물려받아야 합니다"


def test_조각_파일은_바로_지운다(monkeypatch, tmp_path):
    """세 시간짜리를 20분씩 자르면 wav 가 아홉 개 340MB 입니다. 대기가
    세 자리라 남겨 두면 디스크가 먼저 찹니다."""
    _fake_chunks(monkeypatch, [_res("첫"), _res("둘")])
    monkeypatch.setattr(asr.settings, "asr_chunk_sec", 600)

    asr._transcribe_file("a.webm", "ko", 1200, str(tmp_path))
    assert list(tmp_path.iterdir()) == [], "조각이 남아 있으면 안 됩니다"


def test_상한은_이제_메모리가_아니라_시간이_정한다():
    """90분은 메모리 상한이었습니다. 나눠서 올리게 된 지금은 메모리와
    무관하고, 3시간짜리도 M4 실측 5배속이면 36분입니다."""
    from config.settings import settings as s

    assert s.asr_max_duration_sec == 180 * 60
    assert s.asr_chunk_sec < s.asr_max_duration_sec


def test_받아쓰기에는_유튜브의_언어값을_넘기지_않는다():
    """**한쪽만 고쳐 두었던 것.**

    `defaultAudioLanguage` 는 업로더가 손으로 넣는 값이라 못 믿습니다 —
    `박종훈의 지식한방`(한국어 채널)의 영상 27편이 `ja` 로 찍혀 있습니다.
    룰 필터는 그 사실을 알고 이 값을 이미 뺐는데, 받아쓰기는 그대로
    1순위로 써서 위스퍼에게 "일본어로 받아써라" 고 시켰습니다.

        [0:00] 7月29日は、私たちの最悪の日中の一つです。 コスピーとコスタクの市場で…
               (실제 발화: 7월 29일은 우리에게 최악의 날 중 하나입니다…)

    실측으로 요약 실패 43건 중 22건이 이 경로였습니다.
    """
    from app.collector import transcript

    class 잘못찍힌영상:
        default_language = "ja"

    class 영어로찍힌영상:
        default_language = "en-US"

    assert transcript._asr_language(잘못찍힌영상()) == "", "빈 값이면 위스퍼가 듣고 정합니다"
    assert transcript._asr_language(영어로찍힌영상()) == ""


def test_자막을_찾을_때는_힌트로_써도_된다():
    """받아쓰기와 다릅니다 — 틀려도 다음 후보로 넘어갈 뿐 잃는 것이
    없습니다. 그래서 이쪽은 그대로 둡니다."""
    from app.collector import transcript

    class 영상:
        default_language = "ja"

    assert transcript._pick_languages(영상())[0] == "ja"


def test_받아쓰기_호출에_기본값이_새지_않는다():
    """`language=langs[0] if langs else "ko"` 로 되돌아가면 같은 자막이
    같은 이유로 또 망가집니다."""
    import inspect

    from app.collector import transcript

    src = inspect.getsource(transcript.fetch_via_asr)
    assert "_asr_language(video)" in src
    assert "_pick_languages" not in src, "받아쓰기는 자막 찾기와 다른 판단입니다"
