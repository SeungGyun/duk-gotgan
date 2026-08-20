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
import importlib.util
import logging
import math
import multiprocessing
import os
import queue as queuelib
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
    # **있는지만 봅니다 — 불러오지는 않습니다.** 실제 받아쓰기는 자식
    # 프로세스에서 도는데, 여기서 import 해 버리면 워커 프로세스가 쓰지도
    # 않을 MLX 를 통째로 메모리에 올린 채 살게 됩니다.
    if importlib.util.find_spec("mlx_whisper") is None:
        return "mlx-whisper 가 없습니다. `uv pip install mlx-whisper` 로 설치해 주세요."
    return None


# ── 지켜보며 돌리기 ──────────────────────────────────────────
#
# **받아쓰기는 별도 프로세스에서 돕니다.** 2026-08-14, 87분짜리 한 편의
# 두 번째 조각에서 `mlx_whisper.transcribe()` 가 돌아오지 않았습니다. 멎은
# 것이 아니라 **헛돈** 것입니다 — 36시간 뒤에 스택을 떠 보니 그때도 GPU 에서
# 같은 조각을 갈고 있었습니다(위스퍼 디코더가 같은 토큰을 되풀이하며 앞으로
# 못 나가는, 알려진 실패입니다).
#
# 그동안 자막 잡은 한 바퀴도 못 돌았고, 대기가 76건 쌓였고, 로그에는 한 줄도
# 안 남았습니다. `asr_budget_sec`(사이클당 20분)은 **영상과 영상 사이**에서만
# 재기 때문에 한 편 안에서 늘어지는 것을 막지 못합니다.
#
# 스레드로는 못 끊습니다. 파이썬은 C++ 안에서 도는 스레드를 깨울 수단이
# 없습니다 — 프로세스여야 죽일 수 있습니다. 덤으로 위스퍼 1.6GB 가 그
# 프로세스와 함께 사라져서, 놀 때 모델을 내려놓던 장치가 통째로 필요 없어
# 졌습니다.

# 자식이 살아 있는지 이 간격으로 들여다봅니다.
_POLL_SEC = 5.0

_ERRORS = {c.__name__: c for c in (AsrUnavailable, AudioUnavailable, AudioTemporary)}


def _noop() -> None:
    pass


def _stall_sec(duration_sec: int) -> int:
    """이만큼 아무 소식이 없으면 고장으로 봅니다.

    가장 긴 침묵 구간은 **조각 하나**입니다(짧은 영상이면 영상 전체). 실측이
    5~18배속이므로 **1배속**을 바닥으로 잡습니다 — 그보다 느리면 잘 돌고 있는
    것이 아니라 헛돌고 있는 것입니다. 여기에 모델을 올리고 조각을 잘라 내는
    준비 시간을 더합니다.

    영상 길이가 아니라 조각 길이로 재는 것이 요점입니다. 세 시간짜리라고
    세 시간을 기다려 주면, 고장 한 번에 자막 줄이 세 시간 멈춥니다.
    """
    piece = min(settings.asr_chunk_sec, max(duration_sec, 60))
    return int(piece + settings.asr_stall_grace_sec)


def transcribe(video_id: str, duration_sec: int, language: str = "ko") -> AsrResult:
    """받아쓰기 — **자식 프로세스에 맡기고 지켜봅니다.**

    값싼 확인은 여기서 먼저 합니다. 프로세스를 띄우는 데 1~2초가 드는데,
    어차피 안 될 것에 그 값을 치를 이유가 없습니다.
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

    # **작업 폴더는 부모가 만듭니다.** 자식을 죽이면 그쪽 `finally` 는 돌지
    # 않습니다 — 실제로 예전에 끊긴 자리마다 임시 폴더가 남아 있었습니다.
    workdir = tempfile.mkdtemp(prefix=f"gotgan-asr-{video_id}-")
    ctx = multiprocessing.get_context("spawn")
    box = ctx.Queue()
    child = ctx.Process(
        target=_child, args=(box, video_id, duration_sec, language, workdir), daemon=True
    )
    child.start()
    try:
        return _await(box, child, video_id, _stall_sec(duration_sec))
    finally:
        _stop(child)
        shutil.rmtree(workdir, ignore_errors=True)


def _await(box, child, video_id: str, stall: int) -> AsrResult:
    """자식의 소식을 기다립니다. 조용하면 끊고, 죽었으면 그렇다고 말합니다."""
    quiet_since = time.monotonic()
    while True:
        try:
            kind, payload = box.get(timeout=_POLL_SEC)
        except queuelib.Empty:
            if not child.is_alive():
                # 죽으면서 남긴 말이 아직 파이프에 있을 수 있습니다 —
                # 한 번 더 들여다보고 나서 죽었다고 적습니다.
                try:
                    kind, payload = box.get(timeout=2.0)
                except queuelib.Empty:
                    raise AudioTemporary(
                        f"받아쓰기 프로세스가 예고 없이 끝났습니다 (코드 {child.exitcode})"
                    ) from None
            elif time.monotonic() - quiet_since > stall:
                spell = f"{stall // 60}분" if stall >= 60 else f"{stall}초"
                logger.warning("[asr] %s — %s째 한 발짝도 못 나가 끊습니다", video_id, spell)
                raise AudioTemporary(
                    f"받아쓰기가 {spell}째 진전이 없어 끊었습니다 — 다음에 다시 봅니다."
                )
            else:
                continue
        if kind == "beat":
            quiet_since = time.monotonic()
            continue
        if kind == "ok":
            return AsrResult(**payload)
        name, msg = payload
        # 모르는 예외는 **일시적인 것으로 봅니다.** 예전에는 그대로 위로
        # 올라가 사이클을 죽였습니다 — 한 편의 사고가 나머지를 데려가면 안
        # 됩니다. 줄 뒤로 미뤄 두면 다섯 번 만에 탈락으로 정리됩니다.
        raise _ERRORS.get(name, AudioTemporary)(msg)


def _stop(child) -> None:
    """끝났으면 거두고, 아직 돌고 있으면 끊습니다.

    TERM 을 먼저 보냅니다 — 자식이 임시 파일을 정리할 틈은 줍니다. GPU 커널
    한복판이면 그것도 안 먹으므로, 잠깐 기다렸다가 KILL 로 확실히 끊습니다.
    """
    if child.is_alive():
        child.terminate()
        child.join(timeout=10)
    if child.is_alive():
        child.kill()
        child.join(timeout=10)
    with contextlib.suppress(Exception):
        child.close()


def _child(box, video_id: str, duration_sec: int, language: str, workdir: str) -> None:
    """실제로 받아쓰는 쪽. **다른 프로세스입니다.**

    로그 설정을 여기서 다시 합니다 — spawn 으로 뜬 프로세스는 워커의
    `main()` 을 거치지 않아 `basicConfig` 가 돌지 않습니다. 표준 출력은
    물려받으므로 줄은 같은 로그 파일에 쌓입니다.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        r = _transcribe(
            video_id, duration_sec, language, workdir, beat=lambda: box.put(("beat", None))
        )
    except Exception as e:  # noqa: BLE001 — 무엇이 나든 부모에게 넘겨야 합니다
        # 예외 객체를 그대로 보내지 않습니다. 절일 수 없는 예외가 하나라도
        # 섞이면 그 순간 자식이 조용히 죽어, 진짜 사유 대신 "예고 없이
        # 끝났습니다"만 남습니다.
        box.put(("err", (type(e).__name__, " ".join(str(e).split())[:300])))
    else:
        box.put(
            (
                "ok",
                {
                    "language": r.language,
                    "segments": r.segments,
                    "elapsed_sec": r.elapsed_sec,
                    "audio_sec": r.audio_sec,
                },
            )
        )


def _transcribe(
    video_id: str,
    duration_sec: int,
    language: str = "ko",
    workdir: str | None = None,
    beat=_noop,
) -> AsrResult:
    """소리를 받아 받아씁니다.

    **오디오는 반드시 지웁니다.** 36분짜리 한 편이 18MB 이고 대기가 세 자리
    단위라, 남겨 두면 디스크가 먼저 찹니다. 받아쓰기가 실패해도 지웁니다 —
    그래서 `finally` 입니다.
    """
    why = available()
    if why:
        raise AsrUnavailable(why)
    if duration_sec > settings.asr_max_duration_sec:
        raise AudioUnavailable(
            f"영상이 너무 깁니다 ({duration_sec // 60}분) — "
            f"받아쓰기 상한 {settings.asr_max_duration_sec // 60}분."
        )

    workdir = workdir or tempfile.mkdtemp(prefix=f"gotgan-asr-{video_id}-")
    try:
        path = _download_audio(video_id, workdir)
        beat()
        return _transcribe_file(path, language, duration_sec, workdir, beat)
    finally:
        # 통째로 지웁니다. 파일 하나만 지우면 yt-dlp 가 남긴 조각(.part,
        # 원본 컨테이너)이 그대로 남습니다.
        shutil.rmtree(workdir, ignore_errors=True)


def _download_audio(video_id: str, workdir: str) -> str:
    """소리만 받습니다. **변환하지 않습니다** — 위스퍼가 어차피 ffmpeg 로
    다시 읽어서, mp3 로 옮기는 건 순수한 낭비입니다."""
    import yt_dlp

    from app.collector import cookies

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
        # 로그인 쿠키가 있으면 얹습니다. **여기가 가장 아쉬운 자리입니다** —
        # 오디오는 1MB 쯤에서 403 이 나는데, 그게 로그인 없는 요청에 걸리는
        # 상한입니다 (collector/cookies.py).
        **cookies.ytdlp_opts(),
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


# **모델을 내려놓는 장치는 없앴습니다.**
#
# 예전에는 워커 프로세스가 위스퍼 1.6GB 를 쥐고 살아서, 놀 때나 메모리가
# 빡빡할 때 손으로 내려놓아야 했습니다(16GB 기계에서 요약 프로세스가 뜰
# 자리를 못 찾아 60건이 죽은 적이 있습니다). 이제 모델은 자식 프로세스
# 안에서만 살고 한 편이 끝나면 프로세스와 함께 사라집니다 — 내려놓을 것이
# 애초에 남지 않습니다.


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


def _transcribe_file(
    path: str, language: str, duration_sec: int, workdir: str, beat=_noop
) -> AsrResult:
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
        # **조각마다 살아 있다고 알립니다.** 부모는 이 신호로만 진전을
        # 압니다 — 한 조각이 헛돌기 시작하면 여기서 소식이 끊기고, 그것이
        # 끊어 낼 근거가 됩니다.
        beat()
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
