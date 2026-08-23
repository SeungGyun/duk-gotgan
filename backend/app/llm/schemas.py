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
    def _normalize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        for name in cls.model_fields:
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
        return cls._repair(out)

    @classmethod
    def _repair(cls, data: dict) -> dict:
        """고칠 수 있는 어긋남은 여기서 고칩니다. 기본은 그대로 두기.

        **검증기를 하나로 둡니다.** `mode="before"` 검증기를 상속 계층에
        여러 개 두면 도는 순서가 눈에 안 보입니다 — JSON 문자열을 풀기
        전에 손질이 돌면 아직 문자열인 값을 만지게 됩니다. 순서를 코드에
        적어 두려고 훅으로 뺐습니다.
        """
        return data


def _cap(data: dict, name: str, limit: int) -> dict:
    """상한을 넘는 목록을 자릅니다. 없는 키는 만들지 않습니다 —
    "빠졌다"가 "형식이 틀렸다"로 바뀌어 적히면 무엇이 문제인지 흐려집니다."""
    value = data.get(name)
    if not isinstance(value, list) or len(value) <= limit:
        return data
    return {**data, name: value[:limit]}


def _one_line(value: Any) -> Any:
    """객체·배열로 온 것을 읽을 수 있는 한 줄로 폅니다. 그 밖은 그대로."""
    if isinstance(value, dict):
        parts = [f"{v}" for v in value.values() if v not in (None, "", [], {})]
        return " · ".join(parts) or None
    if isinstance(value, list):
        parts = [f"{v}" for v in value if v not in (None, "")]
        return " · ".join(parts) or None
    return value


class CriterionScore(Coercing):
    criterion: Criterion
    score: int = Field(ge=0, le=100)
    evidence: str = Field(description="점수 근거가 되는 자막 인용 1~2문장")


class Section(Coercing):
    """번호 붙은 섹션 하나. 이 요약의 본체입니다.

    예전에는 개요(abstract) · 핵심 포인트(key_points) · 챕터(chapters)가
    따로 있었는데, 셋이 같은 이야기를 세 번 다르게 하고 있었습니다.
    읽는 쪽에서는 위아래를 오가야 했고요. 하나로 합쳤습니다.

    용어·인용·실무 적용도 별도 목록으로 빼지 않고 `bullets` 안에 녹입니다 —
    처음 나오는 자리에서 설명하는 편이 읽는 흐름에 맞습니다.
    """

    title: str = Field(description="그 구간이 무엇을 다루는지. '주제 — 부연' 형태 권장")
    start_sec: int = Field(ge=0, description="자막 [MM:SS] 마커에서 그대로")
    bullets: list[str] = Field(min_length=1, max_length=8, description="각 1~3문장")

    @classmethod
    def _repair(cls, data: dict) -> dict:
        return _cap(data, "bullets", 8)


class LectureSummary(Coercing):
    one_liner: str = Field(max_length=80, description="무엇을 가르치는 강의인지 한 문장")
    target_audience: str
    prerequisites: list[str] = Field(default_factory=list, max_length=5)
    sections: list[Section] = Field(min_length=3, max_length=12)
    closing: str = Field(description="이 강의가 결국 무슨 말을 하는지 2~3문장")
    tags: list[str] = Field(min_length=3, max_length=8)
    coverage_note: str | None = Field(
        default=None, description="자막 품질 문제로 불완전한 구간이 있으면 명시, 없으면 null"
    )

    @classmethod
    def _repair(cls, data: dict) -> dict:
        """**넘치는 것은 자르고, 섞인 것은 버립니다.**

        상한을 넘겼다고 요약을 통째로 버릴 이유가 없습니다 — 태그 아홉 개
        중 여덟 개만 담으면 되는 일입니다. 하한은 다릅니다. 없는 내용을
        지어낼 수는 없으니 그건 그대로 검증에 맡깁니다.

        `sections` 에 `null`·`true` 가 섞여 오는 일이 있었습니다
        (`at '/summary/sections/0': got null, want object`). 한 칸 때문에
        나머지 아홉 섹션까지 버리지 않습니다.
        """
        out = dict(data)
        sections = out.get("sections")
        if isinstance(sections, list):
            out["sections"] = [x for x in sections if isinstance(x, dict)]
        out = _cap(out, "sections", 12)
        out = _cap(out, "tags", 8)
        out = _cap(out, "prerequisites", 5)
        one_liner = out.get("one_liner")
        if isinstance(one_liner, str) and len(one_liner) > 80:
            out["one_liner"] = one_liner[:80].rstrip()
        return out


class LectureReview(Coercing):
    """판정 + (통과 시) 요약."""

    # ── 판정 — 통과·탈락 관계없이 항상 채웁니다 ──────────────
    verdict: Literal["expert", "practical", "introductory", "promotional", "irrelevant"]
    expert_score: int = Field(ge=0, le=100, description="루브릭 가중치로 계산한 총점 0~100")
    confidence: Literal["low", "medium", "high"] = Field(
        description="이 판정을 얼마나 믿을 수 있는가. 자막이 깨져 있으면 low"
    )
    topic: str = Field(description="실제로 다루는 주제 (검색 키워드와 다를 수 있음)")
    keyword_relevance: int = Field(
        ge=0, le=100, description="검색 키워드와 실제 주제가 얼마나 맞는가 0~100"
    )
    criteria: list[CriterionScore] = Field(
        min_length=6, max_length=6, description="여섯 항목 전부. 하나도 빼지 마세요"
    )
    red_flags: list[str] = Field(
        default_factory=list, description="의심스러운 점을 짧은 문장으로. 없으면 빈 배열"
    )
    # **문자열 한 줄입니다.** 여기 설명이 없던 동안 모델이 `{"name": ...,
    # "title": ...}` 객체를 냈고, agy 의 구조화 출력 검증기가 우리에게 오기
    # 전에 거부해 18편이 영구 탈락했습니다
    # (`at '/speaker_credentials': got object, want string`).
    speaker_credentials: str | None = Field(
        default=None,
        description="화자의 경력·소속을 한 줄 문자열로. 자막에 근거가 없으면 null. 객체가 아닙니다",
    )

    # ── 요약 — 자막이 깨졌을 때만 null ──────────────────────
    #
    # **점수와 엮지 않습니다.** 여기에 "기준 미달이면 null" 이라고 적혀
    # 있는 동안 프롬프트는 반대를 말하고 있었습니다("점수가 낮아도, 홍보성으로
    # 보여도 씁니다"). 모델에 서로 어긋나는 지시가 둘 간 셈입니다.
    # 낮은 점수는 버리는 기준이 아니라 **고르는 근거**입니다 (llm/policy.py).
    summary: LectureSummary | None = Field(
        default=None, description="자막이 깨져 요약이 불가능할 때만 null. 점수가 낮아도 씁니다"
    )

    @classmethod
    def _repair(cls, data: dict) -> dict:
        """화자 이력이 객체로 와도 받습니다 — 읽을 수 있는 한 줄로 폅니다.

        판정을 가르는 값이 아니라 곁들이는 정보입니다. 이것 하나 때문에
        요약까지 버리는 것은 손해가 너무 큽니다.
        """
        if "speaker_credentials" not in data:
            return data
        return {**data, "speaker_credentials": _one_line(data["speaker_credentials"])}


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


# 우리 쪽에서 고쳐 담을 수 있는 것들. 이 열쇠들을 agy 에 넘기는 스키마에서
# 뺍니다 — 저쪽 검증기에 남겨 두면 **우리가 고칠 기회를 얻기 전에** 거부당합니다.
#
# **상한만 뺍니다.** 넘치는 것은 잘라 담으면 되지만(`Coercing._repair`),
# 모자란 것은 지어낼 수 없습니다. 하한을 같이 빼면 모델이 덜 지키기만 하고
# 거부는 pydantic 으로 자리를 옮길 뿐입니다.
_REPAIRABLE = ("maxItems", "maxLength")

# 사람에게도 모델에게도 쓸모없는 잡음. `default: null` 은 특히 나쁩니다 —
# 채워야 할 자리에 "비워도 된다"고 적어 두는 셈입니다.
_NOISE = ("title", "default")


def lenient_schema(model: type[BaseModel]) -> dict[str, Any]:
    """agy 의 `--json-schema` 에 넘길 스키마. **모양만 걸고 나머지는 우리가 봅니다.**

    두 회사가 형식 오류를 다루는 방식이 다릅니다.

      클로드    도구 응답으로 되돌려 줍니다 → 모델이 고쳐서 다시 냅니다
      agy       CLI 가 우리에게 오기 전에 거부합니다 → 그대로 끝입니다

    그래서 저쪽 검증기가 엄할수록 손해가 큽니다. 실측(2026-08-21~23)에서
    요약 실패 18건 중 12건이 `invalid arguments:` 였고, 전부 우리
    `Coercing` 이 받아서 고칠 수 있는 것이었습니다 — 화자 이력이 객체로
    온 것, 요약이 JSON 문자열로 온 것.

    **엄한 쪽은 그대로 둡니다.** `flat_schema` 는 클로드 경로가 계속
    씁니다. 거기는 되물을 수 있으니 엄한 편이 낫습니다.
    """

    def loosen(node: Any) -> Any:
        """스키마 마디에서만 열쇠를 뺍니다.

        **`properties` 안쪽은 이름표입니다.** 통째로 훑으며 `title` 을
        지웠더니 `Section.title`(섹션 제목) 필드가 스키마에서 사라졌습니다 —
        모델이 그 필드의 생김새를 모른 채 내게 됩니다.
        """
        if isinstance(node, list):
            return [loosen(x) for x in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for k, v in node.items():
            if k in _REPAIRABLE or k in _NOISE:
                continue
            if k == "properties" and isinstance(v, dict):
                out[k] = {name: loosen(sub) for name, sub in v.items()}
            else:
                out[k] = loosen(v)
        return out

    schema = loosen(flat_schema(model))
    props = schema.get("properties", {})

    # 화자 이력 — 객체로 와도 받습니다. 우리가 한 줄로 펴서 담습니다.
    # 여기를 문자열로만 묶어 두면 저쪽에서 거부당해 요약까지 통째로 잃습니다.
    if "speaker_credentials" in props:
        props["speaker_credentials"] = {
            "anyOf": [{"type": "string"}, {"type": "object"}, {"type": "null"}],
            "description": props["speaker_credentials"].get("description", ""),
        }

    # 요약 — JSON 문자열로 오는 일이 있습니다(`got string, want object`).
    # `Coercing` 이 풀 수 있으니 통로를 열어 둡니다. 못 풀면 그때 걸립니다.
    summary = props.get("summary")
    if isinstance(summary, dict) and isinstance(summary.get("anyOf"), list):
        summary["anyOf"] = [*summary["anyOf"], {"type": "string"}]

    return schema


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
