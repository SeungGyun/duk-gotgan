"""오류 규격 — docs/API.md §0.

`message` 는 **사용자에게 그대로 보여집니다.** UI 는 이 문자열을 가공하지 않습니다.
그래서 코드가 아니라 사람이 읽을 문장으로, 무엇이 잘못됐고 어떻게 고치는지까지 씁니다.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """의도한 오류. 라우트에서 이걸 던지면 계약대로 직렬화됩니다."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content=_body(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        # pydantic 의 영어 오류를 그대로 내보내면 사용자가 읽을 수 없습니다.
        # 어떤 필드가 문제인지만 뽑아 한 문장으로 만듭니다.
        fields = []
        for err in exc.errors():
            loc = [str(p) for p in err.get("loc", []) if p not in ("body", "query")]
            if loc:
                fields.append(".".join(loc))
        detail = f"({', '.join(fields)})" if fields else ""
        return JSONResponse(
            status_code=400,
            content=_body(
                "INVALID_REQUEST",
                f"요청 값이 올바르지 않습니다{detail}. 입력을 확인하고 다시 시도해 주세요.",
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        message = (
            "요청한 대상을 찾을 수 없습니다."
            if exc.status_code == 404
            else f"요청을 처리하지 못했습니다 ({exc.status_code})."
        )
        return JSONResponse(status_code=exc.status_code, content=_body(code, message))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        # 예기치 못한 예외의 내용은 사용자에게 흘리지 않습니다 (스택·SQL·연결 문자열).
        # 서버 로그에는 FastAPI 가 이미 남깁니다.
        return JSONResponse(
            status_code=500,
            content=_body(
                "INTERNAL_ERROR",
                "서버에서 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
            ),
        )
