"""강의 한 편 → 블로그 글 하나 (.spec/tistory.md §문서 처리).

**곳간에서 쓰던 것을 그대로 내보내지 않습니다.** 곳간 화면은 자막의 마디로
돌아가려고 시간 표시를 붙들고 있고, 판정 점수도 옆에 띄웁니다. 블로그에서는
둘 다 읽는 데 방해만 됩니다 — 돌아갈 영상이 글 안에 없고, 점수는 우리가
고르려고 매긴 값이지 읽는 사람에게 할 말이 아닙니다.

여기서 하는 일은 **빼는 쪽**이 대부분입니다.
"""

import json
import re

from app.db.models import Lecture

# 자막 마커. 실측에서 요약 400편 중 11편이 불릿 앞에 이것을 달고 있었습니다.
#
#     "[51:53] '목적 없이 내가 단순히 진짜로 이…"
#     "[1:24] \"이 과정에서 코딩을 한 줄도 몰…"
#
# **괄호에 싸인 것만 지웁니다.** 맨 숫자 `9:30` 까지 지우면 "오전 9:30 회의"
# 같은 멀쩡한 문장이 부서집니다 — 시계 시각과 구별할 방법이 없습니다.
_BRACKETED_TIME = re.compile(r"[\[(]\s*\d{1,2}:\d{2}(?::\d{2})?\s*[\])]")
# `1:02:03` 처럼 시:분:초 세 칸짜리는 시계 시각으로 쓰이는 일이 거의 없습니다.
_HMS = re.compile(r"(?<![\d:])\d{1,2}:\d{2}:\d{2}(?![\d:])")
_URL = re.compile(r"https?://\S+|(?:www\.)?youtu(?:\.be|be\.com)/\S+", re.IGNORECASE)
_EMPTY_PARENS = re.compile(r"[(\[]\s*[)\]]")


class Unrenderable(Exception):
    """본문으로 만들 수 없는 강의 — 옛 형식이라 섹션이 없는 경우."""


def scrub(text: str | None) -> str:
    """시간 표시와 링크를 뺍니다."""
    if not text:
        return ""
    out = _BRACKETED_TIME.sub("", text)
    out = _HMS.sub("", out)
    out = _URL.sub("", out)
    out = _EMPTY_PARENS.sub("", out)
    # 지운 자리에 남은 공백. 문장부호 앞의 빈칸까지 정리해야 티가 안 납니다.
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    return out.strip()


# 여기서 끝나면 문장이 잘린 티가 납니다. 실측에서 `…5가지 오해 해소 및` 이
# 그대로 제목이 될 뻔했습니다.
_DANGLING = ("및", "와", "과", "그리고", "또는", "등", "의", "를", "을", "에")


def fallback_title(one_liner: str, limit: int) -> str:
    """제목을 못 만들었을 때. **자를 뿐 발행을 멈추지 않습니다.**

    한 문장 요약(`one_liner`)은 평균 32자·최대 52자라 그대로는 못 씁니다.
    어절 경계에서 끊습니다 — 글자 수로만 자르면 낱말 가운데가 잘려 나갑니다.
    """
    text = scrub(one_liner)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    # 앞쪽에서 끊기면 제목이 너무 짧아집니다 — 그럴 바엔 낱말을 자릅니다.
    if space >= limit // 2:
        cut = cut[:space]
    cut = cut.rstrip(" ,·-—…")
    # 접속사·조사로 끝나면 그 낱말까지 뗍니다. 짧아지더라도 문장이 끊긴
    # 것처럼 보이지는 않습니다.
    head, sep, last = cut.rpartition(" ")
    if sep and last in _DANGLING:
        cut = head.rstrip(" ,·-—…")
    return cut or text[:limit]


def category_name(term: str, channel_title: str | None, source_type: str) -> str:
    """곳간 키워드 → 블로그 카테고리 이름.

    두 가지를 손봅니다.

    - **채널 구독은 핸들 대신 채널명.** `@gaingetv` 가 메뉴에 뜨면 무엇인지
      읽히지 않습니다.
    - **`/` 는 `-` 로.** 티스토리에서 `/` 는 부모/자식 구분자라, 그대로 두면
      엉뚱한 곳에 하위 카테고리가 생깁니다.
    """
    name = (channel_title or term) if source_type == "channel" else term
    name = (name or term or "").strip().lstrip("@").strip()
    name = name.replace("/", "-")
    name = re.sub(r"\s+", " ", name)
    return name[:50]


def to_markdown(lec: Lecture) -> str:
    """블로그에 올릴 마크다운. **시간 표시와 링크가 없습니다.**

    `sections[].startSec` 은 지우는 것이 아니라 애초에 그리지 않습니다 —
    빼는 규칙을 글자에 대고 돌리는 것보다, 구조에서 안 꺼내는 편이 확실합니다.
    """
    sections = _as_list(lec.sections)
    if not sections:
        # 시드로 들어온 옛 형식. 개요 문단만 있어서 글이 되지 않습니다.
        raise Unrenderable("섹션이 없는 옛 형식입니다.")

    blocks: list[str] = []

    lead = scrub(lec.one_liner)
    if lead:
        blocks.append(lead)

    audience = scrub(lec.target_audience)
    if audience:
        blocks.append(f"**이런 분께** {audience}")

    prereq = [scrub(p) for p in _as_list(lec.prerequisites)]
    prereq = [p for p in prereq if p]
    if prereq:
        blocks.append("**알고 있으면 좋은 것** " + " · ".join(prereq))

    for s in sections:
        if not isinstance(s, dict):
            continue
        title = scrub(s.get("title"))
        bullets = [scrub(b) for b in _as_list(s.get("bullets"))]
        bullets = [b for b in bullets if b]
        if not bullets:
            continue
        if title:
            blocks.append(f"## {title}")
        blocks.append("\n".join(f"- {b}" for b in bullets))

    closing = scrub(lec.closing)
    if closing:
        blocks.append(closing)

    # **출처를 남기지 않습니다.** 한동안 채널명 한 줄을 붙였는데, 블로그
    # 주인이 자기 글로 두기로 정했습니다 (2026-08-08). 유튜브 링크를 빼기로
    # 한 것과 같은 결정의 연장입니다.
    return "\n\n".join(blocks) + "\n"


def frontmatter(title: str, category: str, tags: list[str], visibility: str) -> str:
    """티스토리 CLI 가 읽는 머리말.

    **값을 JSON 으로 씁니다.** 제목에 `:` 이나 따옴표가 들어가는 일이 흔하고,
    태그에 쉼표가 있으면 `--tags a,b` 로는 두 개로 쪼개집니다. JSON 은 YAML 의
    부분집합이라 그대로 유효한 머리말이 됩니다.
    """
    lines = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"category: {json.dumps(category, ensure_ascii=False)}",
        f"tags: {json.dumps(tags, ensure_ascii=False)}",
        f"visibility: {visibility}",
        "---",
    ]
    return "\n".join(lines) + "\n\n"


def document(lec: Lecture, title: str, category: str, visibility: str) -> str:
    """머리말 + 본문. 이 문자열이 그대로 `.md` 파일이 됩니다."""
    tags = [t for t in (scrub(str(t)) for t in _as_list(lec.tags)) if t][:8]
    return frontmatter(title, category, tags, visibility) + to_markdown(lec)


def _as_list(value) -> list:
    """JSON 컬럼은 비어 있으면 None 으로 옵니다."""
    return value if isinstance(value, list) else []
