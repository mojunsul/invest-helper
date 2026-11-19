"""D10 세션 메모리: 프로세스 메모리 dict(LRU). 요청 시작 state 생성, 종료 시 갱신."""
import copy
import logging
import threading
from collections import OrderedDict
from typing import TypedDict

from backend.agent.graph import run_agent
from backend.agent.nodes.analyze import default_conditions
from backend.agent.state import AgentState, Conditions, Intent, Status

logger = logging.getLogger(__name__)

MAX_SESSIONS = 1000  # 임의 상수


class MemoryEntry(TypedDict):
    conditions: Conditions
    last_intent: Intent | None
    last_status: Status | None
    last_result_summary: dict | None
    report_draft: dict | None


_store: OrderedDict[str, MemoryEntry] = OrderedDict()
_lock = threading.Lock()


def _default_entry() -> MemoryEntry:
    return {"conditions": default_conditions(), "last_intent": None, "last_status": None,
            "last_result_summary": None, "report_draft": None}


def _get(session_id: str) -> MemoryEntry | None:
    """복사본 반환, 있으면 최근 사용으로 갱신."""
    with _lock:
        e = _store.get(session_id)
        if e is None:
            return None
        _store.move_to_end(session_id)
        return copy.deepcopy(e)


def _put(session_id: str, entry: MemoryEntry) -> None:
    with _lock:
        _store[session_id] = entry
        _store.move_to_end(session_id)
        while len(_store) > MAX_SESSIONS:
            _store.popitem(last=False)
            logger.info("session evicted")


def build_state(session_id: str, user_msg: str) -> AgentState:
    mem = _get(session_id) or _default_entry()
    return {"session_id": session_id, "user_msg": user_msg, "conditions": mem["conditions"],
            "prev_intent": mem["last_intent"], "last_status": mem["last_status"],
            "last_result_summary": mem["last_result_summary"], "report_draft": mem["report_draft"]}


def update_memory(session_id: str, final_state: dict) -> None:
    intent, status = final_state.get("intent"), final_state.get("status")
    if intent is None or intent == "chitchat":
        return
    if status is None:
        logger.info("memory update skipped: no status")
        return
    mem = _get(session_id) or _default_entry()
    if intent in ("screen", "refine"):
        mem["conditions"] = copy.deepcopy(final_state.get("conditions") or mem["conditions"])
    mem["last_intent"], mem["last_status"] = intent, status
    mem["last_result_summary"] = copy.deepcopy(final_state.get("last_result_summary"))
    mem["report_draft"] = copy.deepcopy(final_state.get("report_draft"))
    _put(session_id, mem)


async def run_turn(session_id: str, user_msg: str) -> dict:
    final = await run_agent(build_state(session_id, user_msg))  # 예외는 전파, 메모리 미갱신
    update_memory(session_id, final)
    return final


def get_report_draft(session_id: str, stock_code: str) -> dict | None:
    mem = _get(session_id)
    return ((mem or {}).get("report_draft") or {}).get(stock_code)


def clear_session(session_id: str) -> None:
    with _lock:
        _store.pop(session_id, None)


def clear_all() -> None:
    with _lock:
        _store.clear()
