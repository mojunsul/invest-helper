import threading
from datetime import datetime, timezone
from typing import Callable, Literal

from pydantic import BaseModel

from backend.tools.base import ErrorCode

EventType = Literal["stage_start", "stage_end", "tool_call", "tool_result", "llm_decision", "replan", "final"]
Stage = Literal["analyze", "plan", "execute", "judge", "respond"]


class Event(BaseModel):
    session_id: str
    seq: int
    ts: str
    type: EventType
    payload: dict


class StageStart(BaseModel):
    stage: Stage


class StageEnd(BaseModel):
    stage: Stage
    intent: str | None = None
    reason: str | None = None


class ToolCall(BaseModel):
    tool: str
    input: dict
    cache_hit: bool


class ToolResultPayload(BaseModel):
    tool: str
    ok: bool
    error_code: ErrorCode | None = None
    duration_ms: int
    summary: str


class LlmDecision(BaseModel):
    kind: Literal["intent", "conditions", "summary", "tools"]
    decision: str
    reason: str


class Replan(BaseModel):
    count: int
    trigger: Literal["no_pass", "no_candidates"]
    change: Literal["market_expand", "similar_industry", "limit_up"]


class Final(BaseModel):
    status: Literal["answered", "asked", "limit", "error"]


_subscribers: list[Callable[[Event], None]] = []
_seq: dict[str, int] = {}
_lock = threading.Lock()


def subscribe(fn: Callable[[Event], None]) -> None:
    _subscribers.append(fn)


def publish(session_id: str, type: EventType, payload_model: BaseModel) -> Event:
    with _lock:
        _seq[session_id] = seq = _seq.get(session_id, 0) + 1
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        ev = Event(session_id=session_id, seq=seq, ts=ts, type=type, payload=payload_model.model_dump())
    for fn in list(_subscribers):
        try:
            fn(ev)
        except Exception:
            pass  # 구독자 예외 삼킴
    return ev
