import asyncio
import logging

from backend.events import bus
from backend.events.bus import StageEnd, StageStart
from backend.identity.resolve import resolve_stock, similar_industries
from backend.tools.stock_candidates import MAX_LIMIT

logger = logging.getLogger(__name__)


async def plan(state) -> dict:
    sid = state["session_id"]
    bus.publish(sid, "stage_start", StageStart(stage="plan"))
    cond, intent = state["conditions"], state.get("intent")
    scope = {"market": cond["market"], "extra_industries": [], "candidate_limit": None}
    for rec in state.get("replan_log") or []:
        ch = rec["change"]
        if ch == "market_expand":
            scope["market"] = None
        elif ch == "similar_industry":
            try:
                scope["extra_industries"] = list(await asyncio.to_thread(similar_industries, cond["industry"]))
            except Exception:
                scope["extra_industries"] = []
        elif ch == "limit_up":
            scope["candidate_limit"] = MAX_LIMIT

    steps: list[dict] = []
    if intent in ("screen", "refine") and cond["industry"]:
        inp = {"industry": cond["industry"], "market": scope["market"], "base_date": cond["base_date"],
               "extra_industries": scope["extra_industries"], "limit": scope["candidate_limit"]}
        steps.append({"tool": "find_stock_candidates", "input": {k: v for k, v in inp.items() if v}})
    elif intent == "lookup" and state.get("target_stock"):
        try:
            m = await asyncio.to_thread(resolve_stock, state["target_stock"])
        except Exception:
            m = None
        if m is not None and m.status == "ok":
            inp = {"stock_code": m.matches[0].stock_code, "base_date": cond["base_date"]}
            steps.append({"tool": "get_financials", "input": {k: v for k, v in inp.items() if v}})
    bus.publish(sid, "stage_end", StageEnd(stage="plan"))
    return {"search_scope": scope, "plan": steps}
