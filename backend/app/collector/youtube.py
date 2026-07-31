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


def search_ids(term: str, *, language: str, published_after: datetime, limit: int) -> list[str]:
    """검색해서 video id 만 뽑습니다. 순서(= 검색 순위)를 유지합니다."""
    params: dict[str, Any] = {
        "part": "id",
        "q": term,
        "type": "video",
        "maxResults": min(limit, 50),
        "order": "relevance",
        "publishedAfter": published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
        # 강의는 대부분 20분 이상이라 short(4분 미만)를 API 단에서 미리 뺍니다.
        # 유닛은 그대로지만 후보 품질이 올라가고, 상세 조회 대상도 줄어듭니다.
        "videoDuration": "medium",
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
