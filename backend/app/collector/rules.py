"""룰 필터 — AI 를 부르기 전에 거르는 곳.

**이 파일이 비용을 가장 크게 좌우합니다.** 헤드리스에는 Batch 할인이 없어서,
비용을 줄이는 유일한 대형 수단이 "AI 호출 수를 줄이는 것"입니다. 통과율을
40%에서 20%로 낮추면 그대로 절반이 됩니다.

그래서 **탈락 사유를 반드시 남깁니다.** 며칠 돌려보고 "이 기준 때문에 좋은
강의가 떨어졌다"를 눈으로 확인해야 기준을 다듬을 수 있습니다. 사유 없이
숫자만 줄이면 필터를 조일지 풀지 판단할 근거가 없습니다.
"""

from dataclasses import dataclass
from datetime import timedelta

from app.collector.youtube import Candidate
from app.db.models import Keyword
from config.settings import settings
from config.time import now_kst


@dataclass
class Verdict:
    ok: bool
    reason: str = ""


def evaluate(c: Candidate, kw: Keyword, blocked: set[str] | None = None) -> Verdict:
    """후보 1건이 자막 수집 단계로 갈 자격이 있는지.

    사유 문구는 화면에 그대로 나갑니다 — 숫자를 같이 적어야 기준을
    조정할 때 판단이 됩니다("15분 미만"이 아니라 "12분 · 기준 15분").
    """
    title = (c.title or "").lower()

    # 차단한 채널 — AI 가 이미 무관·홍보로 여러 번 판정한 곳입니다.
    # 여기서 걸러야 자막도 AI 도 부르지 않습니다.
    if blocked and c.channel_id in blocked:
        return Verdict(False, f"차단한 채널 · {c.channel_title}")

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

    # 업로드 시점 — 기술 강의는 오래되면 내용이 틀려집니다
    cutoff = now_kst() - timedelta(days=settings.rule_max_age_days)
    if c.published_at and c.published_at < cutoff:
        years = settings.rule_max_age_days / 365
        return Verdict(False, f"오래됨 · {c.published_at:%Y-%m-%d} (기준 {years:.0f}년 이내)")

    # 제목 홍보성 패턴
    for word in settings.title_blocklist:
        if word in title:
            return Verdict(False, f'홍보성 제목 · "{word}" 포함')

    # 언어 — 키워드가 ko/en 을 지정했으면 그 언어만. `any` 면 안 봅니다.
    # 유튜브가 언어를 안 알려주는 경우가 많아, 값이 있을 때만 봅니다.
    if kw.language in ("ko", "en") and c.default_language:
        if not c.default_language.lower().startswith(kw.language):
            return Verdict(False, f"언어 불일치 · {c.default_language} (기준 {kw.language})")

    return Verdict(True)


def _mmss(sec: int) -> str:
    if sec >= 3600:
        return f"{sec // 3600}시간 {sec % 3600 // 60}분"
    return f"{sec // 60}분"
