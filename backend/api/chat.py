import asyncio
import logging
import weakref

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from backend.agent import memory
from backend.agent.nodes.respond import DISCLAIMER
from backend.events import stream
from backend.events.bus import Event, Final, publish
from backend.schemas import ChatRequest, ChatResponse, err_responses

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat")
_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()
_ERR_ANSWER = "요청을 처리하지 못했습니다.\n\n" + DISCLAIMER


def _round_of(evs: list[Event]):
    r = 0
    for e in evs:
        if e.type == "replan":
            r += 1
        yield r, e


def to_steps(evs: list[Event]) -> list[dict]:
    steps: list[dict] = []
    open_: dict[str, dict] = {}
    for r, e in _round_of(evs):
        st = e.payload.get("stage")
        if e.type == "stage_start":
            s = {"seq": e.seq, "stage": st, "round": r, "started_at": e.ts, "ended_at": None,
                 "intent": None, "reason": None}
            steps.append(s)
            open_[st] = s
        elif e.type == "stage_end" and st in open_:
            s = open_.pop(st)
            s.update(ended_at=e.ts, intent=e.payload.get("intent"), reason=e.payload.get("reason"))
    return steps


def to_tool_calls(evs: list[Event]) -> list[dict]:
    out = []
    for r, e in _round_of(evs):
        p = e.payload
        if e.type == "tool_call":
            out.append({"type": "call", "seq": e.seq, "round": r, "tool": p.get("tool"),
                        "input": p.get("input"), "cache_hit": p.get("cache_hit")})
        elif e.type == "tool_result":
            out.append({"type": "result", "seq": e.seq, "round": r, "tool": p.get("tool"), "ok": p.get("ok"),
                        "error_code": p.get("error_code"), "duration_ms": p.get("duration_ms"),
                        "summary": p.get("summary")})
    return out


def build_response(st: dict, evs: list[Event], failed: bool) -> dict:
    steps, calls = to_steps(evs), to_tool_calls(evs)
    if failed:
        return {"answer": _ERR_ANSWER, "status": "error", "verdicts": [], "industry_avg": {}, "steps": steps,
                "tool_calls": calls, "replans": [], "disclaimer": DISCLAIMER, "missing_api_key": False,
                "saveable": [], "intent": None, "conditions": None, "metrics": {}, "disclosures": {},
                "evidence": []}
    names = {c.get("code"): c.get("name") for c in st.get("candidates") or []}
    verdicts = [{"stock_code": c, "stock_name": names.get(c) or c, "status": v["status"],
                 "reasons": v.get("reasons", []), "failed": v.get("failed", [])}
                for c, v in (st.get("verdicts") or {}).items()]
    saveable = [{"stock_code": d.get("stock_code", k), "stock_name": d.get("stock_name") or names.get(k) or k}
                for k, d in (st.get("report_draft") or {}).items()]
    return {"answer": st.get("answer") or _ERR_ANSWER, "status": st.get("status") or "error",
            "verdicts": verdicts, "industry_avg": st.get("industry_avg") or {}, "steps": steps,
            "tool_calls": calls, "replans": [dict(r) for r in st.get("replan_log") or []],
            "disclaimer": DISCLAIMER,
            "missing_api_key": any(e.get("code") == "MISSING_API_KEY" for e in st.get("errors") or []),
            "saveable": saveable, "intent": st.get("intent"), "conditions": st.get("conditions"),
            "metrics": st.get("metrics") or {}, "disclosures": st.get("disclosures") or {},
            "evidence": st.get("filing_hits") or []}


@router.post("", responses={200: {"model": ChatResponse}, **err_responses(409, 422)})
async def chat(body: ChatRequest):
    sid = body.session_id  # 스키마에서 소문자 정규화됨
    lock = _locks.get(sid) or _locks.setdefault(sid, asyncio.Lock())
    if lock.locked():
        return JSONResponse({"code": "SESSION_BUSY", "message": "이전 요청을 처리 중입니다."}, status_code=409)
    async with lock:
        with stream.collect(sid) as evs:
            final_state, failed = {}, False
            try:
                final_state = await memory.run_turn(sid, body.message)
            except Exception:
                failed = True
                logger.exception("chat turn failed")
            finally:  # 취소에도 final 보장
                if not any(e.type == "final" for e in evs):
                    status = "error" if failed or not final_state else (final_state.get("status") or "error")
                    publish(sid, "final", Final(status=status))
        return build_response(final_state, evs, failed)
