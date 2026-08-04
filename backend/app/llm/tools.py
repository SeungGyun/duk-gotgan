"""`save_review` — 클로드가 결과를 넘기는 통로.

**구현체가 워커 프로세스 안에 있습니다.** AI 는 정해진 형식의 인자를 보낼 뿐,
셸도 DB 커넥션도 만지지 않습니다. Bash 로 저장 스크립트를 돌리는 방식을 쓰지
않은 이유가 이것입니다 (AI-PIPELINE §2.4).

**여기는 껍데기입니다.** 검증·점수 재계산·적재는 `llm/store.py` 에 있고,
안티그래비티 실행기도 같은 함수를 부릅니다 — 두 벌로 갈라지면 판정 기준이
조용히 어긋납니다.
"""

import logging
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.llm import store
from app.llm.schemas import LectureReview, flat_schema
from app.llm.store import ReviewOutcome

logger = logging.getLogger(__name__)

__all__ = ["ReviewOutcome", "build_server"]


def build_server(
    video_id: str, outcome: ReviewOutcome, run_id: str | None = None, owner: str | None = None
):
    """이 영상 1건 전용 도구 서버를 만듭니다.

    `video_id` 를 클로저에 묶어 둡니다 — 인자로 받으면 자막에 심긴 문장이
    **다른 영상의 판정을 덮어쓰게** 만들 수 있습니다.

    `owner` 는 **내가 아직 이 영상을 붙들고 있는지** 확인하기 위한 것입니다.
    요약 워커를 둘 이상 띄우면 마지막 안전망이 여기입니다 (store.save 참고).

    `run_id` 는 **이벤트를 실행 기록에 묶기 위한 것**입니다. 없이 두었더니
    요약 이벤트 145건이 전부 실행과 연결되지 않아, 실행 로그에서 펼쳐도
    "무엇을 했는지"가 빈칸이었습니다.
    """

    @tool(
        "save_review",
        "강의 판정 결과와 (통과 시) 요약을 저장합니다. 정확히 한 번만 호출하세요.",
        flat_schema(LectureReview),
    )
    async def save_review(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _ok(
                store.save(
                    video_id=video_id, args=args, outcome=outcome, run_id=run_id, owner=owner
                )
            )
        except store.Rejected as e:
            # 도구는 절대 죽으면 안 됩니다 — 오류도 값으로 돌려줍니다.
            return _err(str(e))

    return create_sdk_mcp_server(name="gotgan", version="1.0.0", tools=[save_review])


def _ok(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}]}


def _err(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "is_error": True}
