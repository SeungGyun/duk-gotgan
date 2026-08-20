"""룰 필터 — AI 를 부르기 전에 거르는 곳.

**이 파일이 비용을 가장 크게 좌우합니다.** 헤드리스에는 Batch 할인이 없어서,
비용을 줄이는 유일한 대형 수단이 "AI 호출 수를 줄이는 것"입니다. 통과율을
40%에서 20%로 낮추면 그대로 절반이 됩니다.

그래서 **탈락 사유를 반드시 남깁니다.** 며칠 돌려보고 "이 기준 때문에 좋은
강의가 떨어졌다"를 눈으로 확인해야 기준을 다듬을 수 있습니다. 사유 없이
숫자만 줄이면 필터를 조일지 풀지 판단할 근거가 없습니다.
"""

import re

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.collector.youtube import Candidate
from app.db.models import Keyword
from config.settings import settings
from config.time import now_kst

# ── 검색 기간 ────────────────────────────────────────────────
# 며칠 안에 올라온 것까지 볼 것인가. **키워드마다 다릅니다.**
#
# 예전에는 전역 180일 하나였는데, 그러면 둘 중 한쪽이 반드시 손해를 봅니다.
# `경제`·`주식` 은 하루만 지나도 헌 이야기인데 반년 치가 같은 줄에 섞여
# 검색 상위를 차지하고, 거꾸로 전역값을 짧게 잡으면 `면역력`·`과학` 처럼
# 석 달 전 강의가 그대로 쓸모 있는 주제가 굶습니다.
#
# 위쪽은 석 달로 막습니다. 그보다 오래된 것을 데려오는 것은 "새로 올라온
# 것을 모은다"가 아니라 과거를 긁는 일이고, 한 번 긁으면 요약 비용이
# 통째로 그리 갑니다.
WINDOW_MAX_DAYS = 90
WINDOW_DEFAULT_DAYS = 90


@dataclass
class Verdict:
    ok: bool
    reason: str = ""


def window_label(days: int) -> str:
    """기간을 사람 말로 — 90 → "3개월", 7 → "1주", 1 → "1일".

    탈락 사유와 화면이 같은 말을 쓰게 두려고 여기 둡니다. "90일 이내"로
    적으면 화면의 "3개월"과 같은 값인지 한 번 더 세어 봐야 합니다.
    """
    if days >= 30 and days % 30 == 0:
        return f"{days // 30}개월"
    if days >= 7 and days % 7 == 0:
        return f"{days // 7}주"
    return f"{days}일"


def window_start(kw: Keyword, now: datetime | None = None) -> datetime:
    """이 키워드가 볼 수 있는 가장 오래된 업로드 시각.

    **검색과 룰 필터가 같은 값을 봐야 합니다.** 유튜브에 넘기는
    `publishedAfter` 와 여기 통과 기준이 갈리면, 100유닛 써서 받아온 후보를
    곧바로 "오래됨" 으로 떨어뜨립니다. 그래서 정의는 이 함수 하나뿐이고,
    발견 단계가 한 번 계산해 `evaluate` 에 그대로 넘깁니다.

    **못 돈 날은 그만큼 더 거슬러 봅니다.** 창이 1일인 키워드가 쿼터
    대기로 하루를 거르면 다음 실행에서 어제 것은 이미 창 밖입니다 — 영영
    못 봅니다. 마지막 실행 이후는 무슨 일이 있어도 훑도록 바닥을 낮춥니다.
    덕분에 주 1회로 도는 키워드에 1일을 넣어도 한 주 치를 다 봅니다.
    그래도 상한(90일)은 넘지 않습니다.
    """
    now = now or now_kst()
    # `or` 가 필요합니다 — 컬럼의 default 는 INSERT 때 붙는 값이라, 아직
    # 저장 전인 객체에서는 None 입니다 (blog/publish.py 의 attempts 와 같은 함정).
    days = min(kw.search_window_days or WINDOW_DEFAULT_DAYS, WINDOW_MAX_DAYS)
    start = now - timedelta(days=days)

    if kw.last_run_at is not None and kw.last_run_at < start:
        start = max(kw.last_run_at, now - timedelta(days=WINDOW_MAX_DAYS))

    # 등록할 때 "이 날짜 이후만" 을 못박아 둔 경우. 창보다 이쪽이 세면
    # 이쪽을 따릅니다 — 창은 "얼마나 최근까지" 이고 이건 바닥입니다.
    if kw.published_after is not None:
        # tz 를 붙이지 않습니다 — 이 곳간의 시각은 전부 KST naive 이고
        # (config/time.py), 하나만 aware 로 만들면 비교에서 TypeError 입니다.
        floor = datetime(
            kw.published_after.year, kw.published_after.month, kw.published_after.day
        )
        start = max(start, floor)

    return start


def evaluate(
    c: Candidate, kw: Keyword, blocked: set[str] | None = None, *, cutoff: datetime | None = None
) -> Verdict:
    """후보 1건이 자막 수집 단계로 갈 자격이 있는지.

    사유 문구는 화면에 그대로 나갑니다 — 숫자를 같이 적어야 기준을
    조정할 때 판단이 됩니다("15분 미만"이 아니라 "12분 · 기준 15분").

    `cutoff` 는 발견 단계가 검색에 쓴 것과 **같은** 값입니다. 안 주면 여기서
    다시 계산합니다 — 결과는 같지만 몇 초 어긋나므로, 경계에 걸친 영상이
    있을 수 있는 실제 수집 경로에서는 넘겨받는 편이 맞습니다.
    """
    title = (c.title or "").lower()

    # 차단한 채널 — AI 가 이미 무관·홍보로 여러 번 판정한 곳입니다.
    # 여기서 걸러야 자막도 AI 도 부르지 않습니다.
    if blocked and c.channel_id in blocked:
        return Verdict(False, f"차단한 채널 · {c.channel_title}")

    # 읽을 수 없는 언어 — **원본 제목으로 봅니다.** 위에서 소문자로 접은
    # `title` 이 아니라요. 대소문자 접기는 문자 종류를 바꾸지 않지만, 여기서
    # 보려는 것이 "무슨 글자로 쓰였나" 라서 원본을 보는 편이 뜻이 분명합니다.
    script = foreign_script(c.title or "")
    if script is not None:
        return Verdict(False, f"읽을 수 없는 언어 · {script} 문자")

    # 길이 — 키워드별 설정이 우선
    min_sec = kw.min_duration_sec or 0
    if min_sec and c.duration_sec < min_sec:
        return Verdict(False, f"길이 미달 · {_mmss(c.duration_sec)} (기준 {_mmss(min_sec)})")

    max_sec = kw.max_duration_sec or 0
    if max_sec and c.duration_sec > max_sec:
        # 너무 긴 것은 자막 토큰이 예산을 통째로 먹습니다. v1 은 스킵.
        return Verdict(False, f"길이 초과 · {_mmss(c.duration_sec)} (기준 {_mmss(max_sec)})")

    # 조회수 — **기본값은 0(끔)입니다.**
    #
    # 300회로 두고 돌려 보니 330건 중 157건이 여기서 떨어졌는데, 떨어진
    # 것들이 하필 이 곳간이 노리는 종류였습니다:
    #
    #   287회 · 24분 · [인프라 해부학] EP.03 — 온/오프램프
    #   225회 · 20분 · [스테이블코인 EP.05] 한국형 스테이블코인
    #   174회 · 26분 · [인프라 해부학] EP.06 — 상점·서비스 통합
    #
    # 틈새 전문 콘텐츠는 **정의상** 조회수가 적습니다. 조회수를 품질 신호로
    # 쓰면 목적과 반대로 걸립니다. 진짜 걸러내는 일은 AI 검토가 합니다.
    # 그래도 끄고 켤 수 있게 남겨 둡니다 — 0 이면 아예 안 봅니다.
    #
    # **구독한 채널에는 원래도 적용하지 않습니다.** 직접 고른 채널이니까요.
    if (
        settings.rule_min_view_count
        and kw.source_type != "channel"
        and c.view_count < settings.rule_min_view_count
    ):
        return Verdict(
            False, f"조회수 미달 · {c.view_count:,}회 (기준 {settings.rule_min_view_count:,})"
        )

    # 업로드 시점 — **키워드가 정한 기간**입니다. 시황 키워드는 1일,
    # 잘 안 변하는 주제는 3개월. 채널 구독은 업로드 목록에 기간 조건을
    # 걸 수 없어서, 여기가 유일한 관문입니다.
    if cutoff is None:
        cutoff = window_start(kw)
    if c.published_at and c.published_at < cutoff:
        span = window_label(kw.search_window_days or WINDOW_DEFAULT_DAYS)
        return Verdict(False, f"오래됨 · {c.published_at:%Y-%m-%d} (기준 {span} 이내)")

    # 제목 홍보성 패턴
    for word in settings.title_blocklist:
        if word in title:
            return Verdict(False, f'홍보성 제목 · "{word}" 포함')

    # 언어 — 키워드가 ko/en 을 지정했으면 그 언어만. `any` 면 안 봅니다.
    # 유튜브가 언어를 안 알려주는 경우가 많아, 값이 있을 때만 봅니다.
    #
    # **구독한 채널에는 적용하지 않습니다.** 조회수와 같은 이유입니다 —
    # 사용자가 그 채널을 직접 골랐는데 메타데이터로 다시 심사할 이유가
    # 없습니다. 게다가 `defaultLanguage` 는 업로더가 손으로 넣는 값이라
    # 믿을 게 못 됩니다: `박종훈의 지식한방`(한국어 채널)의 영상 27편이
    # `ja` 로 찍혀 있어 통째로 떨어지고 있었습니다.
    # **`defaultAudioLanguage` 로는 거르지 않습니다.** 예전에는 키워드
    # 언어가 `ko`·`en` 일 때 이 값을 봤는데, 업로더가 손으로 넣는 값이라
    # 틀립니다 — `박종훈의 지식한방`(한국어 채널) 27편이 `ja` 로 찍혀 있었고,
    # 요약 실패 43건 중 22건이 한국어 강의인데 `en-US`·`ja`·`zh` 였습니다.
    #
    # 지금은 모든 키워드가 `any` 라 이 검사가 아예 안 돌고 있었습니다.
    # 그래서 아무 해도 없어 보였지만, **누군가 키워드를 `ko` 로 바꾸는 순간**
    # 멀쩡한 한국어 강의가 조용히 떨어집니다. 그런 코드는 남겨 두면 함정이
    # 됩니다. 언어는 이제 제목의 문자로 봅니다(위 `foreign_script`).
    return Verdict(True)


# **읽을 수 없는 언어는 제목에서 갈립니다.**
#
# 곳간은 한국어·영어 강의를 모읍니다. 그런데 검색은 언어를 가리지 않아서
# 타밀·태국·힌디 영상이 꾸준히 들어왔고, 자막(GPU 2~7분)과 요약(편당 6~8만
# 토큰)을 다 치른 뒤에야 "무관"으로 판정됐습니다. 실제로 **18편이 공개까지
# 갔고 그중 15편이 `irrelevant`** 였습니다 — 태국어 "AI Girl Series" 8편,
# 타밀어 정치 연설, 힌디어 주식 영상.
#
# **유튜브가 주는 `defaultAudioLanguage` 로는 못 가릅니다.** 업로더가 손으로
# 넣는 값이라 틀립니다 — 요약 실패 43건을 뜯어보니 22건이 한국어 강의인데
# `en-US`·`ja`·`zh` 로 찍혀 있었습니다. 그 값을 믿고 거르면 멀쩡한 강의가
# 조용히 떨어집니다(받아쓰기가 그 값을 믿다가 한국어를 일본어로 옮겼습니다 —
# collector/transcript.py `_asr_language`).
#
# 제목의 문자는 다릅니다. 사람이 안 적고 유니코드가 정합니다. 실측:
#
#   낯선 문자 제목 131건 · 그중 한글이 섞인 것 **0건**
#
# 한국어·영어 강의와 완전히 갈립니다. 오탐이 0 인 신호는 흔치 않습니다.
#
# **가나와 한자는 넣지 않습니다.** 한국어 제목에 한자가 섞이고(`韓`·`美`),
# 나중에 일본어 강의를 원할 수도 있습니다. 실측에서도 이 둘은 문제를
# 일으키지 않았습니다.
FOREIGN_SCRIPTS = (
    ("타밀", "\u0b80-\u0bff"),
    ("크메르", "\u1780-\u17ff"),
    ("미얀마", "\u1000-\u109f"),
    ("태국", "\u0e00-\u0e7f"),
    ("아랍", "\u0600-\u06ff"),
    ("데바나가리", "\u0900-\u097f"),
    ("키릴", "\u0400-\u04ff"),
    ("히브리", "\u0590-\u05ff"),
    ("그리스", "\u0370-\u03ff"),
)
_FOREIGN = tuple((name, re.compile(f"[{rng}]")) for name, rng in FOREIGN_SCRIPTS)


def foreign_script(title: str) -> str | None:
    """제목에 읽을 수 없는 문자가 있으면 그 이름. 없으면 None."""
    for name, pat in _FOREIGN:
        if pat.search(title or ""):
            return name
    return None


def _mmss(sec: int) -> str:
    if sec >= 3600:
        return f"{sec // 3600}시간 {sec % 3600 // 60}분"
    return f"{sec // 60}분"
