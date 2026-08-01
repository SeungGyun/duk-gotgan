"""YouTube Data API v3 클라이언트 — 검색과 상세 조회만.

두 개의 호출만 씁니다.

  search.list  100유닛  제목·채널만 주고 길이·조회수는 안 줍니다
  videos.list    1유닛  최대 50개를 한 번에, 길이·조회수·자막 유무를 줍니다

**검색 결과로 바로 거르지 않는 이유**가 여기 있습니다. 룰 필터의 핵심
기준(길이·조회수)이 `search.list` 응답에 없어서, 상세를 한 번 더 받아야
합니다. 다행히 `videos.list` 는 50개당 1유닛이라 사실상 공짜입니다.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import re

from config.settings import settings

logger = logging.getLogger(__name__)

API = "https://www.googleapis.com/youtube/v3"
TIMEOUT = 15


class YouTubeError(Exception):
    """API 가 거절했습니다. message 는 사용자에게 그대로 보여집니다."""


@dataclass
class Candidate:
    """검색 + 상세를 합친 후보 1건. 룰 필터가 보는 형태."""

    video_id: str
    title: str
    description: str
    channel_id: str
    channel_title: str
    published_at: datetime | None
    duration_sec: int
    view_count: int
    like_count: int
    comment_count: int
    thumbnail_url: str | None
    default_language: str | None
    has_caption: bool
    search_rank: int


def _get(path: str, params: dict[str, Any]) -> dict:
    if not settings.youtube_api_key:
        raise YouTubeError(
            "유튜브 API 키가 없습니다. backend/.env 의 YOUTUBE_API_KEY 를 채워 주세요."
        )
    query = urlencode({**params, "key": settings.youtube_api_key})
    req = Request(f"{API}/{path}?{query}", headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=TIMEOUT) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — 원인별 문구를 사람 말로 바꿔 올립니다
        detail = getattr(e, "read", None)
        body = ""
        if detail:
            try:
                body = detail().decode("utf-8", "replace")[:400]
            except Exception:
                body = ""
        code = getattr(e, "code", None)
        if code == 403 and "quota" in body.lower():
            raise YouTubeError(
                "유튜브 일일 할당량을 모두 썼습니다. 내일 태평양 표준시 자정에 초기화됩니다."
            ) from e
        if code == 403:
            raise YouTubeError(
                "유튜브 API 가 요청을 거절했습니다. API 키가 유효한지, "
                "YouTube Data API v3 가 사용 설정되어 있는지 확인해 주세요."
            ) from e
        if code == 400:
            raise YouTubeError(f"유튜브 API 요청이 잘못되었습니다. ({body[:120]})") from e
        raise YouTubeError(f"유튜브 API 호출에 실패했습니다. ({e})") from e


# ── ISO 8601 기간 파싱 ───────────────────────────────────────
# "PT1H34M5S" → 5645. 라이브러리를 쓸 만큼 복잡하지 않고,
# 의존성 하나를 아끼는 편이 낫습니다.
_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_duration(text: str) -> int:
    m = _DUR.fullmatch(text or "")
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def _parse_dt(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone(timedelta(hours=9))).replace(tzinfo=None)


# ── 호출 ────────────────────────────────────────────────────


def duration_bucket(min_duration_sec: int) -> str:
    """우리 최소 길이를 유튜브가 아는 세 칸 중 하나로 옮깁니다.

    유튜브는 `short`(4분 미만) · `medium`(4~20분) · `long`(20분 초과) 세 가지만
    압니다. 임의의 분 단위 하한을 줄 수 없습니다.

    **`medium` 은 절대 쓰지 않습니다.** 20분에서 잘리기 때문에, "15분 이상"을
    뜻하려고 medium 을 넣으면 오히려 4~20분짜리만 받아와 긴 강의가 전부
    사라집니다. 실제로 그렇게 넣었다가 50건 전부 탈락했습니다.

    **20분 미만을 원하면 조건을 아예 안 겁니다.** `long` 은 20분 초과만
    주므로, 하한이 그보다 낮은데 `long` 을 걸면 5~20분대 영상이 검색
    단계에서 통째로 사라집니다 — 우리가 직접 거르는 편이 정확합니다.
    검색 유닛은 조건과 무관하게 100 이라 비용 차이도 없습니다.

    대가는 있습니다. 조건을 풀면 50칸에 쇼츠가 섞여 쓸 수 있는 후보가
    줄어듭니다. 그래서 하한이 20분 이상이면 그대로 `long` 을 씁니다.
    """
    return "long" if min_duration_sec >= 1200 else "any"


def search_ids(
    term: str, *, language: str, published_after: datetime, limit: int, min_duration_sec: int = 0
) -> list[str]:
    """검색해서 video id 만 뽑습니다. 순서(= 검색 순위)를 유지합니다."""
    params: dict[str, Any] = {
        "part": "id",
        "q": term,
        "type": "video",
        "maxResults": min(limit, 50),
        "order": "relevance",
        "publishedAfter": published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "videoDuration": duration_bucket(min_duration_sec),
    }
    if language in ("ko", "en"):
        params["relevanceLanguage"] = language
        params["regionCode"] = "KR" if language == "ko" else "US"

    data = _get("search", params)
    out = []
    for item in data.get("items", []):
        vid = (item.get("id") or {}).get("videoId")
        if vid:
            out.append(vid)
    logger.info('[youtube] search "%s" → %d건', term, len(out))
    return out


# ── 채널 구독 ────────────────────────────────────────────────
# 검색은 호출당 100유닛인데 업로드 목록은 1유닛입니다. **50배 차이**라,
# 관심 채널이 분명하면 검색보다 구독이 압도적으로 쌉니다. 관련도 문제도
# 없습니다 — 사용자가 채널을 직접 골랐으니까요.

UNITS_CHANNELS = 1
UNITS_PLAYLIST = 1


@dataclass
class ChannelInfo:
    channel_id: str
    title: str
    uploads_playlist_id: str
    subscriber_count: int


def resolve_channel(handle: str) -> ChannelInfo:
    """`@gaingetv` 같은 핸들을 채널로 바꿉니다 (1유닛)."""
    handle = handle.strip()
    if not handle.startswith("@"):
        handle = "@" + handle

    data = _get("channels", {"part": "snippet,contentDetails,statistics", "forHandle": handle})
    items = data.get("items") or []
    if not items:
        raise YouTubeError(
            f"{handle} 채널을 찾지 못했습니다. 유튜브 채널 주소의 @이름을 그대로 넣어 주세요."
        )
    it = items[0]
    uploads = ((it.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    if not uploads:
        raise YouTubeError(f"{handle} 채널의 업로드 목록을 읽을 수 없습니다.")
    return ChannelInfo(
        channel_id=it["id"],
        title=(it.get("snippet") or {}).get("title", handle),
        uploads_playlist_id=uploads,
        subscriber_count=int((it.get("statistics") or {}).get("subscriberCount", 0) or 0),
    )


def playlist_video_ids(playlist_id: str, limit: int = 50) -> list[str]:
    """업로드 목록의 최신 영상 id (1유닛). 최신순으로 옵니다."""
    data = _get(
        "playlistItems",
        {"part": "contentDetails", "playlistId": playlist_id, "maxResults": min(limit, 50)},
    )
    out = []
    for item in data.get("items", []):
        vid = (item.get("contentDetails") or {}).get("videoId")
        if vid:
            out.append(vid)
    logger.info("[youtube] 업로드 목록 %s → %d건", playlist_id, len(out))
    return out


def fetch_details(video_ids: list[str]) -> list[Candidate]:
    """상세를 채웁니다. 50개까지 한 번에 (1유닛)."""
    if not video_ids:
        return []
    rank = {vid: i for i, vid in enumerate(video_ids)}
    data = _get(
        "videos",
        {"part": "snippet,contentDetails,statistics", "id": ",".join(video_ids[:50])},
    )

    out: list[Candidate] = []
    for item in data.get("items", []):
        vid = item.get("id")
        sn = item.get("snippet") or {}
        cd = item.get("contentDetails") or {}
        st = item.get("statistics") or {}
        thumbs = sn.get("thumbnails") or {}
        thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get(
            "url"
        )
        out.append(
            Candidate(
                video_id=vid,
                title=sn.get("title", ""),
                description=sn.get("description", "") or "",
                channel_id=sn.get("channelId", ""),
                channel_title=sn.get("channelTitle", ""),
                published_at=_parse_dt(sn.get("publishedAt")),
                duration_sec=parse_duration(cd.get("duration", "")),
                view_count=int(st.get("viewCount", 0) or 0),
                like_count=int(st.get("likeCount", 0) or 0),
                comment_count=int(st.get("commentCount", 0) or 0),
                thumbnail_url=thumb,
                default_language=sn.get("defaultAudioLanguage") or sn.get("defaultLanguage"),
                # "true"/"false" 문자열로 옵니다. 다만 이 값은 **공식 자막만**
                # 가리키고 자동 자막은 빠져 있어서, 자막 확보 가능 여부의
                # 판단 근거로 쓰기엔 부족합니다 (M3 에서 실제로 받아 봅니다).
                has_caption=str(cd.get("caption", "")).lower() == "true",
                search_rank=rank.get(vid, 999),
            )
        )
    out.sort(key=lambda c: c.search_rank)
    return out
