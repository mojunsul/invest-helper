import asyncio
import json
import logging
import re
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, get_args
from urllib.parse import quote, unquote, urlparse

import httpx
from pydantic import BaseModel, ValidationError, model_validator

from backend.config import TOOL_TIMEOUT_S, Settings, settings  # settings는 모듈 전역(테스트에서 monkeypatch)

logger = logging.getLogger(__name__)

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


# ======== D1: Tool 기반 ========

MSG = {
    "MISSING_API_KEY": "API 키가 설정되지 않았습니다. .env를 확인하세요.",
    "INVALID_INPUT": "입력값이 올바르지 않습니다.",
    "TIMEOUT": "외부 API 응답이 지연되었습니다.",
    "RATE_LIMIT": "외부 API 호출 한도를 초과했습니다.",
    "API_ERROR": "외부 API 호출에 실패했습니다.",
}


# ---- NF3002 키 비노출 ----
def _key_forms() -> list[str]:
    forms = set()
    for k in Settings.__dataclass_fields__:
        v = getattr(settings, k)
        if k.endswith("_KEY") and v:
            forms |= {v, quote(v, safe=""), unquote(v), quote(unquote(v), safe="")}
    return sorted(forms - {""}, key=len, reverse=True)


def mask(text: str) -> str:
    for f in _key_forms():
        text = re.sub(re.escape(f), "***", text, flags=re.IGNORECASE)
    return text


def _m(x):
    if isinstance(x, str):
        return mask(x)
    if x is None or isinstance(x, (int, float, bool)):
        return x
    s = str(x)
    return mask(s) if mask(s) != s or mask(repr(x)) != repr(x) else x


class _MaskFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # args 타입·길이 보존: uvicorn AccessFormatter가 args를 5-튜플로 언팩한다.
        if isinstance(record.msg, str):
            record.msg = mask(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_m(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _m(v) for k, v in record.args.items()}
        try:
            full = record.getMessage()
        except Exception:
            full = None  # 잘못된 포맷은 logging이 기존대로 처리
        if full is not None and mask(full) != full:
            record.msg, record.args = mask(full), ()
        if record.exc_info:
            record.exc_text = mask(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = mask(record.exc_text)
        for k, v in list(record.__dict__.items()):
            if isinstance(v, str) and k not in ("msg", "exc_text"):
                record.__dict__[k] = mask(v)
        return True


_log_lock = threading.Lock()


def install_log_mask() -> None:
    """모든 핸들러에 마스킹 필터를 건다(멱등). 핸들러 추가 후 재호출."""
    with _log_lock:
        for n in ("httpx", "httpcore"):
            logging.getLogger(n).setLevel(logging.WARNING)
        loggers = [logging.getLogger()] + [
            x for x in logging.Logger.manager.loggerDict.values() if isinstance(x, logging.Logger)]
        for lg in loggers:
            for h in lg.handlers:
                if not any(isinstance(f, _MaskFilter) for f in h.filters):
                    h.addFilter(_MaskFilter())


# ---- D1b: 호스트별 최소 요청 간격 ----
HOST_INTERVAL_S = {"opendart.fss.or.kr": 0.2, "apis.data.go.kr": 0.1}  # 임의 상수. 없는 호스트는 간격 없음
_next_start: dict[str, float] = {}  # 호스트 -> 다음 요청 시작 가능 monotonic 시각
_sleep = asyncio.sleep  # 테스트에서 patch
_now = time.monotonic


async def throttle(url: str) -> None:
    """요청 직전 호출. 호스트별 슬롯을 예약(await 이전에 읽기·쓰기 → 락 불필요)하고 그 시각까지 대기."""
    host = urlparse(url).hostname or ""
    gap = HOST_INTERVAL_S.get(host, 0)
    if gap <= 0:
        return
    now = _now()
    start = max(now, _next_start.get(host, 0.0))
    _next_start[host] = start + gap
    if start > now:
        await _sleep(start - now)


# ---- Tool ----
class ToolFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def ok(data, *, requested=None, actual=None, estimated=False) -> ToolResult:
    return ToolResult(ok=True, data=data,
                      meta=ToolMeta(requested_basis=requested, actual_basis=actual, estimated=estimated))


def fail(code: ErrorCode, message: str) -> ToolResult:
    if mask(message) != message:
        message = MSG["API_ERROR"]
    return ToolResult(ok=False, error=ToolError(code=code, message=message))


ToolKind = Literal["external", "local"]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[BaseModel], Awaitable[ToolResult]]
    kind: ToolKind
    requires_keys: tuple[str, ...] = ()
    timeout_s: float | None = None

    def normalize(self, raw: dict) -> dict:
        return self.input_model.model_validate(raw).model_dump(mode="json")

    async def run(self, raw: dict, *, timeout_s: float | None = None) -> ToolResult:
        for k in self.requires_keys:
            if not getattr(settings, k):
                return fail("MISSING_API_KEY", MSG["MISSING_API_KEY"])
        try:
            inp = self.input_model.model_validate(self.normalize(raw))
        except ValidationError:
            return fail("INVALID_INPUT", MSG["INVALID_INPUT"])
        t = timeout_s if timeout_s is not None else (
            self.timeout_s if self.timeout_s is not None else TOOL_TIMEOUT_S)
        try:
            r = await asyncio.wait_for(self.fn(inp), t)
        except ToolFailure as e:
            code = e.code if e.code in get_args(ErrorCode) else "API_ERROR"
            return fail(code, e.message)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return self._log_fail("TIMEOUT")
        except httpx.HTTPStatusError as e:
            return self._log_fail("RATE_LIMIT" if e.response.status_code == 429 else "API_ERROR")
        except Exception:  # CancelledError/KeyboardInterrupt는 BaseException이라 전파됨
            return self._log_fail("API_ERROR")
        if (not isinstance(r, ToolResult) or r.ok != (r.error is None)
                or (r.ok and r.data is None) or (not r.ok and r.data is not None)):
            logger.warning("tool=%s code=API_ERROR detail=invalid return", self.name)
            return fail("API_ERROR", MSG["API_ERROR"])
        if r.error and mask(r.error.message) != r.error.message:
            return fail(r.error.code, MSG["API_ERROR"])
        return r

    def _log_fail(self, code: str) -> ToolResult:
        logger.warning("tool=%s code=%s detail=%s", self.name, code, mask(traceback.format_exc()))
        return fail(code, MSG[code])


_TOOLS: dict[str, Tool] = {}


def register(*, name, description, input_model, kind, requires_keys=(), timeout_s=None):
    def deco(fn):
        if kind not in get_args(ToolKind):
            raise ValueError(f"invalid tool kind: {kind}")
        for k in requires_keys:
            if not hasattr(settings, k):
                raise ValueError(f"unknown settings key: {k}")
        if name in _TOOLS:
            raise ValueError(f"duplicate tool: {name}")
        _TOOLS[name] = tool = Tool(name, description, input_model, fn, kind, tuple(requires_keys), timeout_s)
        return tool
    return deco
