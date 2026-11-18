import json
from typing import Any, Literal

from pydantic import BaseModel, model_validator

ErrorCode = Literal["MISSING_API_KEY", "INVALID_INPUT", "API_ERROR", "RATE_LIMIT", "TIMEOUT",
                    "NO_RESULT", "NO_FINANCIALS", "NO_INDEX"]
FATAL_CODES = {"MISSING_API_KEY"}
_CACHEABLE_FAILURES = {"NO_FINANCIALS", "NO_RESULT"}


class ToolError(BaseModel):
    code: ErrorCode
    message: str  # 사용자 노출용 한국어 요약. 원문 오류/키/스택 금지


class ToolMeta(BaseModel):
    requested_basis: str | None = None
    actual_basis: str | None = None
    estimated: bool = False


class ToolResult(BaseModel):
    ok: bool
    data: Any | None = None
    error: ToolError | None = None
    meta: ToolMeta = ToolMeta()

    @model_validator(mode="after")
    def _invariants(self):
        if self.ok != (self.error is None):
            raise ValueError("ok must equal (error is None)")
        if self.ok and self.data is None:
            raise ValueError("ok=True requires data")
        if not self.ok and self.data is not None:
            raise ValueError("ok=False requires data=None")
        return self


def cache_key(tool: str, input: dict) -> str:
    return f"{tool}:{json.dumps(input, sort_keys=True, ensure_ascii=False)}"


def is_cacheable(result: ToolResult) -> bool:
    return result.ok or result.error.code in _CACHEABLE_FAILURES
