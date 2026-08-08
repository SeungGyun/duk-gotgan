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

    asr.transcribe("vid123", 600)
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
        asr.transcribe("vid123", 600)
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


def test_모델_해제는_한_번만_로그를_남긴다(monkeypatch, caplog):
    """자막 잡이 30초마다 도는데 놀 때마다 같은 줄을 찍으면 로그가 덮입니다."""
    import logging

    from mlx_whisper.transcribe import ModelHolder

    from app.collector import asr as A

    ModelHolder.model = None
    with caplog.at_level(logging.INFO):
        A.release_model()
    assert "모델을 내렸습니다" not in caplog.text


def test_받아쓰기_뒤에는_버퍼_캐시를_비운다():
    """실측: 3분 오디오 한 편 뒤에 캐시 778MB 가 남아 있었습니다. 다음
    파일에서 어차피 다시 잡는 것이라, 그걸 들고 있느라 요약 프로세스가
    뜰 자리를 잃는 것은 손해입니다."""
    import inspect

    from app.collector import asr as A

    assert "mx.clear_cache()" in inspect.getsource(A._run_whisper)


def test_메모리가_빡빡하면_묶음_끝에_모델을_내린다():
    """발견분 보충을 넣으면서 자막 잡이 쉬지 않고 돌게 됐습니다. 그러면
    위스퍼 1.6GB 를 계속 쥐고 있어 요약 잡이 뜰 자리를 영영 못 찾습니다 —
    메모리 가드가 매번 미루기만 하니까요."""
    import inspect

    from app.collector import jobs

    src = inspect.getsource(jobs.transcript_job)
    assert "resources.memory_tight()" in src
    assert "asr.release_model()" in src


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
