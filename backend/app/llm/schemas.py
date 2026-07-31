"""`save_review` 도구의 입력 스키마.

AI 가 이 형태로 인자를 넘기면 워커가 검증하고 DB 에 반영합니다. 최종 응답이
아니라 **도구 입력**입니다 — `output_format` 은 쓰지 않습니다. 두 경로로 결과가
들어오면 어느 쪽이 정본인지 모호해집니다 (AI-PIPELINE §2.4).
"""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Criterion = Literal["structure", "depth", "evidence", "authority", "density", "commercial"]

# 판정 가중치. 프롬프트(prompts/lecture-review.md)의 표와 **같은 값이어야 합니다.**
# 여기서만 바꾸면 모델이 계산한 점수와 우리가 검증하는 점수가 어긋납니다.
WEIGHTS: dict[str, float] = {
    "structure": 0.20,
    "depth": 0.25,
    "evidence": 0.20,
    "authority": 0.15,
    "density": 0.10,
    "commercial": 0.10,  # 역방향 — (100 - score) 로 계산
}
REVERSED = {"commercial"}


class Coercing(BaseModel):
    """배열·객체가 JSON **문자열**로 도착해도 받아들입니다.

    MCP 도구 인자는 클라이언트에 따라 중첩 구조를 문자열로 직렬화해 보냅니다.
    실측에서 `criteria` 와 `red_flags` 가 `"[...]"` 문자열로 와서 검증이
    여덟 번 연속 실패했고, 그 재시도에 입력 토큰 52만이 나갔습니다.

    형식 오류를 모델에게 돌려주며 고치라고 하는 것보다, 받는 쪽에서 한 번
    풀어주는 편이 훨씬 쌉니다.
    """

    @model_validator(mode="before")
    @classmethod
    def _parse_json_strings(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        for name, field in cls.model_fields.items():
            value = out.get(name)
            if not isinstance(value, str):
                continue
            stripped = value.strip()
            if not stripped.startswith(("[", "{")):
                continue
            try:
                out[name] = json.loads(stripped)
            except json.JSONDecodeError:
                pass  # 진짜 문자열 필드일 수 있습니다 — 그대로 두고 검증에 맡깁니다
        return out


class CriterionScore(Coercing):
    criterion: Criterion
    score: int = Field(ge=0, le=100)
    evidence: str = Field(description="점수 근거가 되는 자막 인용 1~2문장")


class KeyPoint(Coercing):
    heading: str = Field(description="핵심 주장 한 줄")
    detail: str = Field(description="2~4문장 설명")
    timestamp_sec: int = Field(ge=0)


class Chapter(Coercing):
    title: str
    start_sec: int = Field(ge=0)
    end_sec: int = Field(ge=0)
    summary: str = Field(default="", description="2~3문장")


class Term(Coercing):
    term: str
    definition: str = Field(description="강의에서 설명한 방식대로 1~2문장")


class Quote(Coercing):
    text: str = Field(description="자막 원문 그대로")
    timestamp_sec: int = Field(ge=0)
    why: str


class LectureSummary(Coercing):
    one_liner: str = Field(max_length=80, description="무엇을 가르치는 강의인지 한 문장")
    abstract: str = Field(description="3~5문장 개요")
    target_audience: str
    prerequisites: list[str] = Field(default_factory=list, max_length=5)
    key_points: list[KeyPoint] = Field(min_length=3, max_length=10)
    chapters: list[Chapter] = Field(min_length=1, max_length=20)
    terms: list[Term] = Field(default_factory=list, max_length=20)
    takeaways: list[str] = Field(default_factory=list, max_length=7)
    quotes: list[Quote] = Field(default_factory=list, max_length=5)
    tags: list[str] = Field(min_length=3, max_length=8)
    coverage_note: str | None = Field(
        default=None, description="자막 품질 문제로 불완전한 구간이 있으면 명시, 없으면 null"
    )


class LectureReview(Coercing):
    """판정 + (통과 시) 요약."""

    # ── 판정 — 통과·탈락 관계없이 항상 채웁니다 ──────────────
    verdict: Literal["expert", "practical", "introductory", "promotional", "irrelevant"]
    expert_score: int = Field(ge=0, le=100)
    confidence: Literal["low", "medium", "high"]
    topic: str = Field(description="실제로 다루는 주제 (검색 키워드와 다를 수 있음)")
    keyword_relevance: int = Field(ge=0, le=100)
    criteria: list[CriterionScore] = Field(min_length=6, max_length=6)
    red_flags: list[str] = Field(default_factory=list)
    speaker_credentials: str | None = None

    # ── 요약 — 기준 미달이면 null ────────────────────────────
    summary: LectureSummary | None = Field(
        default=None, description="expert_score 가 기준 미달이면 null"
    )


def flat_schema(model: type[BaseModel]) -> dict[str, Any]:
    """중첩 정의(`$defs`)를 전부 펼친 JSON 스키마.

    pydantic 이 만드는 스키마는 중첩 타입을 `{"$ref": "#/$defs/CriterionScore"}`
    로 가리킵니다. 이 참조가 MCP 도구 정의를 거치며 풀리지 않아, **모델이 항목의
    생김새를 모른 채 추측해서 보냅니다.** 실측에서 `criteria` 안쪽의
    `criterion` 필드가 통째로 빠져 세 번 연속 실패했습니다.

    참조를 미리 펼쳐 두면 모델이 형태를 보고 채웁니다.
    """
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def resolve(node: Any, seen: tuple[str, ...] = ()) -> Any:
        if isinstance(node, list):
            return [resolve(x, seen) for x in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.split("/")[-1]
            if name in seen:  # 자기 참조는 펼칠 수 없습니다
                return {"type": "object"}
            target = defs.get(name, {})
            merged = {**resolve(target, seen + (name,))}
            # $ref 옆에 붙어 있던 description 등을 살립니다
            for k, v in node.items():
                if k != "$ref":
                    merged[k] = resolve(v, seen)
            return merged
        return {k: resolve(v, seen) for k, v in node.items()}

    return resolve(raw)


def weighted_score(criteria: list[CriterionScore]) -> int:
    """루브릭 가중치로 점수를 다시 계산합니다.

    모델이 보낸 `expert_score` 를 그대로 믿지 않습니다. 산수를 틀리거나,
    항목 점수와 총점이 어긋나게 보내는 경우가 있습니다. 공개 여부를 가르는
    값이라 **우리가 계산한 것을 씁니다.**
    """
    by = {c.criterion: c.score for c in criteria}
    total = 0.0
    for name, weight in WEIGHTS.items():
        score = by.get(name, 0)
        total += (100 - score if name in REVERSED else score) * weight
    return round(total)
