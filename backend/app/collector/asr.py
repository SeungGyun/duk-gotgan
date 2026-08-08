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

import contextlib
import logging
import math
import os
import shutil
import subprocess
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
    """**이 영상의** 소리를 영영 받을 수 없습니다 (비공개·삭제·멤버십 전용).

    `AsrUnavailable` 과 갈라 두는 이유가 있습니다. 이걸 구분하지 않았더니
    영상 한 편의 403 이 사이클 전체를 죽였습니다 — 자막도 검토도 못 하고
    실행 기록이 `running` 인 채로 남았습니다. 영상 하나의 문제는 그 영상만
    실패로 적고 다음으로 넘어가야 합니다.
    """


class AudioTemporary(Exception):
    """지금은 못 받지만 **영상 탓은 아닙니다** (403, 네트워크, 스로틀링).

    이걸 `AudioUnavailable` 과 뭉뚱그렸더니 36편이 영구 탈락으로 쌓였고,
    나중에 그중 **34편이 그대로 받아졌습니다.** 사유에 예외 타입만
    (`(DownloadError)`) 적혀 있어서 로그만 봐서는 알 수도 없었습니다.

    요약 쪽에서 두 번 겪은 것과 같은 실수입니다 (llm/runner.py `_TRANSIENT`).
    """


# 영영 안 되는 것들. 이 말이 들어 있으면 다시 시도해도 소용없습니다.
_PERMANENT = (
    "members-only",
    "members only",
    "이 채널의 멤버",
    "private video",
    "video is private",
    "has been removed",
    "no longer available",
    "not available",
    "account associated with this video has been terminated",
    "removed by the uploader",
    "age-restricted",
    "sign in to confirm your age",
    "copyright",
)


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
        return _transcribe_file(path, language, duration_sec, workdir)
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

    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
            path = y.prepare_filename(info)
        size = os.path.getsize(path)
    except Exception as e:  # noqa: BLE001 — yt-dlp 는 예외를 세분화하지 않습니다
        # **예외 타입만 적으면 안 됩니다.** `(DownloadError)` 라고만 남겼더니
        # 36편이 왜 실패했는지 알 수 없었고, 그중 34편은 나중에 그대로
        # 받아졌습니다 — 일시적 403 이었는데 영구 탈락으로 적힌 것입니다.
        detail = " ".join(str(e).split())[:200]
        if any(sig in detail.lower() for sig in _PERMANENT):
            raise AudioUnavailable(f"오디오를 받을 수 없습니다 — {detail}") from e
        raise AudioTemporary(f"오디오를 지금 받지 못했습니다 — {detail}") from e
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


def _transcribe_file(path: str, language: str, duration_sec: int, workdir: str) -> AsrResult:
    """짧으면 통째로, 길면 **나눠서** 받아씁니다.

    상한을 90분에 묶어 둔 것은 시간이 아니라 **메모리** 때문이었습니다.
    위스퍼는 오디오를 통째로 16kHz float32 로 올려서, 두 시간이면 그것만
    460MB 이고 그 순간 요약 프로세스가 뜰 자리가 없어집니다(16GB 기계).
    그 상한에 95~142분짜리 33편이 걸려 탈락했습니다 — 길수록 값어치 있는
    강의인데 그게 통째로 빠졌습니다.

    **상한을 올리는 것으로는 못 풉니다.** 그러면 그 OOM 이 그대로 돌아옵니다.
    대신 나눠서 한 조각씩 올립니다 — 최대 사용량이 영상 길이가 아니라
    조각 길이로 정해지므로, 세 시간짜리도 20분짜리와 같은 메모리를 씁니다.
    """
    if duration_sec <= settings.asr_chunk_sec:
        return _run_whisper(path, language)

    chunk = settings.asr_chunk_sec
    n = math.ceil(duration_sec / chunk)
    logger.info(
        "[asr] %d분 — %d조각으로 나눠서 받아씁니다 (조각당 %d분)",
        duration_sec // 60, n, chunk // 60,
    )

    merged: list[dict] = []
    elapsed = 0.0
    detected = ""
    for i in range(n):
        offset = i * chunk
        piece = os.path.join(workdir, f"chunk-{i:03d}.wav")
        _cut(path, piece, offset, chunk)
        try:
            # **앞 조각이 알아낸 언어를 뒤에 물려줍니다.** 조각마다 따로
            # 감지하면 음악이나 침묵으로 시작하는 조각이 엉뚱한 언어로
            # 새고, 그 조각만 통째로 헛소리가 됩니다.
            part = _run_whisper(piece, detected or language)
        except AsrUnavailable:
            # 말소리가 없는 조각(음악·침묵)은 건너뜁니다. 한 조각이 비었다고
            # 두 시간짜리 강의를 통째로 버릴 이유가 없습니다.
            logger.info("[asr] %d/%d 조각에 말소리가 없습니다 — 건너뜁니다", i + 1, n)
            continue
        finally:
            # **조각은 바로 지웁니다.** 세 시간짜리를 20분씩 자르면 wav 가
            # 아홉 개 340MB 이고, 대기가 세 자리라 남겨 두면 디스크가 먼저
            # 찹니다.
            with contextlib.suppress(OSError):
                os.remove(piece)
        elapsed += part.elapsed_sec
        detected = detected or part.language
        for s in part.segments:
            merged.append({**s, "start": s["start"] + offset})

    if not merged:
        raise AsrUnavailable("받아쓴 내용이 비었습니다 — 말소리가 없는 영상일 수 있습니다.")

    audio_sec = merged[-1]["start"] + merged[-1]["dur"]
    logger.info(
        "[asr] 받아쓰기 %.0f초 · 음성 %.1f분 · %.1f배속 · %d세그먼트 (%d조각)",
        elapsed, audio_sec / 60, audio_sec / max(elapsed, 0.1), len(merged), n,
    )
    return AsrResult(
        language=detected or language or "ko",
        segments=merged,
        elapsed_sec=elapsed,
        audio_sec=audio_sec,
    )


def _cut(src: str, dst: str, start_sec: int, length_sec: int) -> None:
    """한 조각을 위스퍼가 바로 먹는 형태(16kHz 모노)로 잘라 냅니다.

    **`-ss` 를 `-i` 앞에 둡니다.** 뒤에 두면 매 조각마다 앞부분을 전부
    디코딩하고 버려서, 뒤로 갈수록 느려집니다.
    """
    cmd = [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
        "-ss", str(start_sec), "-t", str(length_sec), "-i", src,
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst,
    ]
    # `check=False` — 돌아온 코드를 우리가 봅니다. 여기서 예외가 나면
    # 조각 하나 때문에 두 시간짜리가 통째로 죽습니다.
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not os.path.exists(dst):
        raise AsrUnavailable(f"오디오를 자르지 못했습니다 — {proc.stderr.strip()[:200]}")


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
