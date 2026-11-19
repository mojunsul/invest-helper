"""D9 재계획 래퍼: D8 execute/judge 반환에 종료·재계획 판정(D0 §4 규칙 1~6)을 합친다. 정점은 늘리지 않는다."""
import asyncio
import logging

from backend.agent.nodes.execute import execute, terminal_error
from backend.agent.nodes.judge import judge
from backend.config import settings
from backend.events import bus
from backend.events.bus import Replan
from backend.identity.resolve import similar_industries
from backend.tools.stock_candidates import DEFAULT_LIMIT, MAX_LIMIT

logger = logging.getLogger(__name__)

CHANGE = {1: "market_expand", 2: "similar_industry", 3: "limit_up"}
SR = ("screen", "refine")


def ended(errors, candidates) -> bool:
    """execute 직후 종료 경로: terminal_error 또는 후보 0."""
    return terminal_error(errors or []) is not None or not candidates


async def next_step(state, trigger: str) -> int | None:
    """D0 §12: expansion_step보다 큰 첫 유효 단계(없으면 None)."""
    scope = state.get("search_scope") or {}
    for step in (1, 2, 3):
        if step <= (state.get("expansion_step") or 0):
            continue
        if step == 1 and scope.get("market") is not None:
            return 1
        if step == 2:
            try:
                ok = bool(await asyncio.to_thread(similar_industries, (state.get("conditions") or {}).get("industry")))
            except Exception:
                ok = False
            if ok:
                return 2
        if step == 3 and trigger == "no_pass" and (scope.get("candidate_limit") or DEFAULT_LIMIT) < MAX_LIMIT:
            return 3
    return None


async def _replan_or(state, trigger: str, fallback: str) -> dict:
    count = state.get("replan_count") or 0
    step = await next_step(state, trigger) if count < settings.REPLAN_MAX else None
    if step is None:
        return {"status": fallback}
    count += 1
    rec = {"count": count, "trigger": trigger, "change": CHANGE[step]}
    bus.publish(state["session_id"], "replan", Replan(**rec))
    return {"replan_count": count, "expansion_step": step,
            "replan_log": [*(state.get("replan_log") or []), rec]}


async def decide_outcome(state) -> dict:
    """규칙 1~6 첫 일치. 종료면 {status}, 재계획이면 카운터 3종(status 키 없음)."""
    if terminal_error(state.get("errors") or []) is not None:  # 규칙 1·2
        return {"status": "error"}
    if not state.get("candidates"):  # 규칙 3
        return await _replan_or(state, "no_candidates", "error")
    vs = (state.get("verdicts") or {}).values()
    n_pass = sum(v["status"] == "PASS" for v in vs)
    n_dec = sum(v["status"] in ("PASS", "FAIL") for v in vs)
    if n_pass >= 1:  # 규칙 5
        return {"status": "answered"}
    if n_dec == 0:  # 규칙 4
        return {"status": "error"}
    return await _replan_or(state, "no_pass", "limit")  # 규칙 6


async def execute_step(state) -> dict:
    out = await execute(state)
    if state.get("intent") in SR and ended(out.get("errors"), out.get("candidates")):
        out |= await decide_outcome({**state, **out})
    return out


async def judge_step(state) -> dict:
    out = await judge(state)
    if state.get("intent") in SR:
        out |= await decide_outcome({**state, **out})
    return out


def route_after_execute(state) -> str:
    intent = state.get("intent")
    if intent in SR:
        if ended(state.get("errors"), state.get("candidates")):
            return "plan" if state.get("status") is None else "respond"
        return "judge"
    return "judge" if intent == "lookup" else "respond"


def route_after_judge(state) -> str:
    if state.get("intent") in SR:
        return "plan" if state.get("status") is None else "respond"
    return "respond"
