"""AI 가 읽을 작업 폴더를 만들고, 끝나면 지웁니다.

**자막을 프롬프트 문자열에 넣지 않습니다.** 자막에는 따옴표·백틱·`$`·개행이
그대로 들어 있어 문자열 보간을 하면 셸 인젝션이 되고, 60분 강의 자막 45KB 는
인자 길이도 위험합니다. 파일로 쓰고 경로만 알려주는 것이 유일하게 안전하며,
덤으로 **모델이 필요한 만큼만 읽고 끊을 수 있게** 됩니다 (조기 종료).
"""

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.db.models import Keyword, Transcript, Video
from config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class Workspace:
    path: Path
    video_id: str

    @property
    def transcript(self) -> Path:
        return self.path / "transcript.md"

    @property
    def metadata(self) -> Path:
        return self.path / "metadata.json"

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def prepare(video: Video, transcript: Transcript, keywords: list[Keyword]) -> Workspace:
    """영상 1건짜리 격리 폴더를 만듭니다."""
    root = Path(settings.jobs_dir).expanduser()
    path = root / video.id
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)

    ws = Workspace(path=path, video_id=video.id)
    ws.transcript.write_text(transcript.content or "", encoding="utf-8")

    # 통과 기준을 같이 넘깁니다. 이 값이 없으면 모델이 어디서 끊어야 할지 몰라
    # 탈락시킬 강의의 자막까지 끝까지 읽습니다 (AI-PIPELINE §2.1).
    # 키워드가 여럿이면 가장 낮은 기준을 씁니다 — 하나라도 통과하면 공개됩니다.
    quality = transcript.quality or {}

    ws.metadata.write_text(
        json.dumps(
            {
                "title": video.title,
                "channel": video.channel_title,
                "duration_sec": video.duration_sec,
                "published_at": video.published_at.strftime("%Y-%m-%d")
                if video.published_at
                else None,
                "search_keywords": [k.term for k in keywords],
                "transcript": {
                    "source": transcript.source,
                    "language": transcript.language,
                    "est_tokens": transcript.est_tokens,
                    # 자동 자막이면 문장 경계를 믿지 말라는 신호입니다
                    "has_punctuation": quality.get("has_punctuation", True),
                    "line_count": quality.get("line_count", 0),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("[workspace] %s 준비 (%s 자)", video.id, f"{len(transcript.content or ''):,}")
    return ws
