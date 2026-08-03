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
