"""오디오 받아쓰기 — 유튜브 자막 경로가 막혔을 때의 마지막 수단.

**왜 이게 통하는가** (2026-08-01 실측):

  영상 페이지                HTTP 200 · captionTracks 있음
  오디오 (googlevideo.com)   정상 · 20분치 18MB 를 19초에
  /api/timedtext             429 — 서명된 URL 직접 요청도, Node 런타임도

자막 엔드포인트만 우리 IP 에 막혀 있습니다. 오디오는 다른 호스트라
멀쩡합니다. 그래서 소리를 받아 이 기기에서 직접 받아씁니다.

**품질은 유튜브 자동자막보다 낫습니다.** 같은 영상으로 비교해 보니
유튜브가 틀린 6곳 중 3곳을 바로잡았고(`스웨되는`→`스웨덴은` 등), 위스퍼만
틀린 곳은 없었습니다. 남은 3곳은 내레이션이 실제로 그렇게 발음한 것이라
어느 쪽도 고칠 수 없는 자리입니다. 세그먼트도 15초 고정이 아니라 문장
단위로 끊겨서 섹션 타임스탬프가 정확해집니다.

**대신 공짜가 아닙니다.** M4 GPU 로 실시간의 12.5배속이라 36분짜리 한 편에
3분쯤 걸립니다. 그래서 1차 경로가 살아 있으면 언제나 그쪽이 우선입니다 —
여기는 폴백입니다.
"""

import logging
import shutil
import tempfile
import time
from dataclasses import dataclass

from config.settings import settings

logger = logging.getLogger(__name__)


class AsrUnavailable(Exception):
    """받아쓰기 **자체**를 쓸 수 없습니다 (ffmpeg 없음, 모델 없음 등).

    이건 모든 영상에 해당하는 문제라, 다음 영상으로 넘어가도 똑같이 실패합니다.
    """


class AudioUnavailable(Exception):
    """**이 영상의** 소리를 받을 수 없습니다 (403, 비공개, 멤버십 전용 등).

    `AsrUnavailable` 과 갈라 두는 이유가 있습니다. 이걸 구분하지 않았더니
    영상 한 편의 403 이 사이클 전체를 죽였습니다 — 자막도 검토도 못 하고
    실행 기록이 `running` 인 채로 남았습니다. 영상 하나의 문제는 그 영상만
    실패로 적고 다음으로 넘어가야 합니다.
    """


@dataclass
class AsrResult:
    language: str
    segments: list[dict]  # [{start, dur, text}] — 자막 경로와 같은 모양
    elapsed_sec: float
    audio_sec: float


def available() -> str | None:
    """쓸 수 있으면 None, 아니면 막힌 이유를 돌려줍니다.

    워커가 뜰 때 한 번 찍어 두려고 사유를 문자열로 냅니다 — 폴백이 조용히
    빠져 있으면 "왜 자막이 안 되지"를 처음부터 다시 조사하게 됩니다.
    """
    if not settings.asr_enabled:
        return "설정에서 꺼져 있습니다 (ASR_ENABLED=false)."
    if shutil.which("ffmpeg") is None:
        return "ffmpeg 가 없습니다. `brew install ffmpeg` 로 설치해 주세요."
    try:
        import mlx_whisper  # noqa: F401
    except ImportError:
        return "mlx-whisper 가 없습니다. `uv pip install mlx-whisper` 로 설치해 주세요."
    return None


def transcribe(video_id: str, duration_sec: int, language: str = "ko") -> AsrResult:
    """소리를 받아 받아씁니다.

    **오디오는 반드시 지웁니다.** 36분짜리 한 편이 18MB 이고 대기가 세 자리
    단위라, 남겨 두면 디스크가 먼저 찹니다. 받아쓰기가 실패해도 지웁니다 —
    그래서 `finally` 입니다.
    """
    why = available()
    if why:
        raise AsrUnavailable(why)
    if duration_sec > settings.asr_max_duration_sec:
        # **이 영상만의 문제입니다.** AsrUnavailable 로 던지면 IP 차단으로
        # 취급되어, 긴 영상 한 편이 줄 맨 앞에 서 있는 것만으로 전체가
        # 60분씩 멈춥니다. 실제로 221분짜리가 그 자리에 있었습니다.
        raise AudioUnavailable(
            f"영상이 너무 깁니다 ({duration_sec // 60}분) — "
            f"받아쓰기 상한 {settings.asr_max_duration_sec // 60}분."
        )

    workdir = tempfile.mkdtemp(prefix=f"gotgan-asr-{video_id}-")
    try:
        path = _download_audio(video_id, workdir)
        return _run_whisper(path, language)
    finally:
        # 통째로 지웁니다. 파일 하나만 지우면 yt-dlp 가 남긴 조각(.part,
        # 원본 컨테이너)이 그대로 남습니다.
        shutil.rmtree(workdir, ignore_errors=True)


def _download_audio(video_id: str, workdir: str) -> str:
    """소리만 받습니다. **변환하지 않습니다** — 위스퍼가 어차피 ffmpeg 로
    다시 읽어서, mp3 로 옮기는 건 순수한 낭비입니다."""
    import yt_dlp

    t0 = time.time()
    opts = {
        "quiet": True,
        "no_warnings": True,
        # 진행바를 끕니다. `quiet` 만으로는 안 꺼지는데, 켜 두면 영상 한 편이
        # 로그 40줄을 먹어서 하룻밤 로그가 읽을 수 없게 됩니다.
        "noprogress": True,
        "format": "bestaudio",
        "outtmpl": f"{workdir}/%(id)s.%(ext)s",
        "socket_timeout": 30,
    }
    import os

    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
            path = y.prepare_filename(info)
        size = os.path.getsize(path)
    except Exception as e:  # noqa: BLE001 — yt-dlp 는 예외를 세분화하지 않습니다
        # 403·비공개·지역제한·멤버십 전용 등. **이 영상만의 문제입니다.**
        raise AudioUnavailable(f"오디오를 받지 못했습니다 ({type(e).__name__})") from e
    logger.info(
        "[asr] %s 오디오 %.1fMB · %.0f초", video_id, size / 1e6, time.time() - t0
    )
    return path


def release_model() -> None:
    """모델과 버퍼를 메모리에서 내립니다.

    **한 번 부르고 나면 계속 남아 있습니다** (실측):

      3분 오디오 처리 후   MLX 활성 1,618MB · 캐시 778MB · 최대 2,152MB

    캐시 778MB 는 다음 파일에서 어차피 다시 잡으므로 매번 비웁니다 —
    공짜입니다. 모델 1,618MB 는 다시 올리는 데 몇 초 걸리므로, 할 일이
    없을 때만 내립니다.

    16GB 기계에서 요약 프로세스(368MB)가 뜰 자리를 못 찾아 60건이 죽은
    적이 있습니다. 놀면서 1.6GB 를 쥐고 있을 이유가 없습니다.
    """
    try:
        import mlx.core as mx
        from mlx_whisper.transcribe import ModelHolder

        if ModelHolder.model is None:
            return  # 이미 내려가 있습니다 — 30초마다 같은 줄을 찍지 않습니다
        ModelHolder.model = None
        ModelHolder.model_path = None
        mx.clear_cache()
        logger.info("[asr] 모델을 내렸습니다 — 다음 작업에서 다시 올립니다")
    except Exception as e:  # noqa: BLE001
        logger.warning("[asr] 모델 해제 실패 (%s) — 그냥 둡니다", type(e).__name__)


def _whisper_language(language: str) -> str | None:
    """위스퍼가 아는 코드만 넘깁니다. 모르면 `None` — 스스로 알아냅니다.

    **위스퍼의 표를 직접 물어봅니다.** 우리가 목록을 들고 있으면 그 목록에
    없는 코드가 새로 오는 날 또 죽습니다. 실제로 유튜브가 `zxx`("언어적
    내용 없음")를 내려보냈고, 두 글자로 잘린 `zx` 가 그대로 넘어가
    `ValueError: Unsupported language: zx` 로 받아쓰기 잡이 죽었습니다.

    자동 감지가 조금 느리지만, 한 편도 못 받아쓰는 것보다 낫습니다.
    """
    if not language:
        return None
    try:
        from mlx_whisper.tokenizer import LANGUAGES, TO_LANGUAGE_CODE
    except ImportError:  # 위스퍼가 없으면 판단할 근거도 없습니다
        return language
    if language in LANGUAGES or language in TO_LANGUAGE_CODE:
        return language
    logger.warning("[asr] '%s' 는 위스퍼가 모르는 언어입니다 — 자동 감지로 넘깁니다", language)
    return None


def _run_whisper(path: str, language: str) -> AsrResult:
    import mlx.core as mx
    import mlx_whisper

    asked = _whisper_language(language)

    t0 = time.time()
    res = mlx_whisper.transcribe(
        path,
        path_or_hf_repo=settings.asr_model,
        language=asked,
        word_timestamps=False,
        verbose=None,
    )
    elapsed = time.time() - t0

    # **버퍼 캐시는 매번 비웁니다.** 다음 파일에서 어차피 다시 잡는
    # 것이라 들고 있어 봐야 이득이 없는데, 그 778MB 때문에 요약
    # 프로세스가 뜰 자리가 없어집니다.
    mx.clear_cache()

    segments = []
    for s in res.get("segments", []):
        text = (s.get("text") or "").strip()
        if not text:
            continue
        start = float(s.get("start", 0.0))
        segments.append({"start": start, "dur": max(0.0, float(s.get("end", start)) - start), "text": text})
    if not segments:
        raise AsrUnavailable("받아쓴 내용이 비었습니다 — 말소리가 없는 영상일 수 있습니다.")

    audio_sec = segments[-1]["start"] + segments[-1]["dur"]
    logger.info(
        "[asr] 받아쓰기 %.0f초 · 음성 %.1f분 · %.1f배속 · %d세그먼트",
        elapsed, audio_sec / 60, audio_sec / max(elapsed, 0.1), len(segments),
    )
    return AsrResult(
        # 자동 감지로 넘겼으면 감지된 것을 씁니다. `zx` 같은 가짜 코드를
        # 그대로 되돌려 적으면 다음 단계가 또 그것을 믿습니다.
        language=res.get("language") or asked or "ko",
        segments=segments,
        elapsed_sec=elapsed,
        audio_sec=audio_sec,
    )
