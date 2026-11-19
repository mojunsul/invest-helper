import asyncio
import logging
import time
from typing import get_args

from backend.events import bus
from backend.events.bus import StageEnd, StageStart, ToolCall, ToolResultPayload
from backend.identity.resolve import resolve_stock
from backend.tools import registry
from backend.tools.base import MSG, ErrorCode, ToolResult, is_cacheable

logger = logging.getLogger(__name__)

FIN_CONCURRENCY = 5  # 임의 상수: 실호출로 한도 확인 후 조정


def terminal_error(errors) -> ErrorCode | None:
    """D0 규칙 1·2 입력: MISSING_API_KEY 우선, 없으면 후보 조회의 NO_RESULT 외 실패."""
    for e in errors:
        if e["code"] == "MISSING_API_KEY":
            return e["code"]
    for e in errors:
        if e["tool"] == "find_stock_candidates" and e["code"] != "NO_RESULT":
            return e["code"]
    return None


def fin_input(code: str, conditions) -> dict:
    raw = {"stock_code": code}
    if conditions.get("base_date"):
        raw["base_date"] = conditions["base_date"]
    return raw


def _summary(name: str, r: ToolResult) -> str:
    if not r.ok:
        return f"{r.error.code}: {r.error.message}"
    if name == "find_stock_candidates":
        return f"후보 {len(r.data)}건, 기준일 {r.meta.actual_basis}"
    if name == "get_financials":
        return f"{r.meta.actual_basis}, 추정={'예' if r.meta.estimated else '아니오'}"
    return f"공시 {len(r.data)}건"


async def _call(cache: dict, sid: str, name: str, raw: dict) -> ToolResult:
    key = registry.cache_key_for(name, raw)
    hit = key is not None and key in cache
    bus.publish(sid, "tool_call", ToolCall(tool=name, input=raw, cache_hit=hit))
    if hit:
        r, dur = ToolResult.model_validate(cache[key]), 0
    else:
        t0 = time.monotonic()
        r = await registry.call(name, raw)
        dur = int((time.monotonic() - t0) * 1000)
        if key and is_cacheable(r):
            cache[key] = r.model_dump()
    bus.publish(sid, "tool_result", ToolResultPayload(
        tool=name, ok=r.ok, error_code=r.error.code if r.error else None,
        duration_ms=dur, summary=_summary(name, r)))
    return r


async def call_batch(cache, sid, name, codes, raw_of) -> list[ToolResult | None]:
    """코드별 동시 호출. MISSING_API_KEY가 나오면 아직 시작 안 한 호출은 건너뛴다(None)."""
    sem, fatal = asyncio.Semaphore(FIN_CONCURRENCY), False

    async def one(code):
        nonlocal fatal
        async with sem:
            if fatal:
                return None
            r = await _call(cache, sid, name, raw_of(code))
            if not r.ok and r.error.code == "MISSING_API_KEY":
                fatal = True
            return r

    return await asyncio.gather(*(one(c) for c in codes))


def drop(errors: list, tool: str, code: str | None = None, any_code: bool = False) -> list:
    return [e for e in errors if not (e["tool"] == tool and (any_code or e["stock_code"] == code))]


def record_batch(errors, name, codes, results, *, skip_no_result=False) -> list:
    """결과 -> errors 최신 상태 반영. MISSING_API_KEY는 1건만 기록."""
    keyed = False
    for code, r in zip(codes, results):
        if r is None:
            continue
        errors = drop(errors, name, code)
        if r.ok or (skip_no_result and r.error.code == "NO_RESULT"):
            continue
        if r.error.code == "MISSING_API_KEY":
            if keyed:
                continue
            keyed = True
        errors.append({"tool": name, "stock_code": code, "code": r.error.code, "message": r.error.message})
    return errors


async def _resolve_target(state, errors: list) -> tuple[list, str | None]:
    """종목명->코드. (errors, 해석된 코드 또는 None)."""
    errors = drop(errors, "resolve_stock", any_code=True)

    def err(code, msg):
        return [*errors, {"tool": "resolve_stock", "stock_code": None, "code": code, "message": msg}], None

    q = state.get("target_stock")
    if not q:
        return err("INVALID_INPUT", MSG["INVALID_INPUT"])
    try:
        m = await asyncio.to_thread(resolve_stock, q)
    except Exception as e:  # IdentityError 외(sqlite 등)도 변환
        code = getattr(e, "code", None)
        if code in get_args(ErrorCode):
            return err(code, e.message)
        logger.warning("resolve_stock failed: %s", type(e).__name__)
        return err("API_ERROR", MSG["API_ERROR"])
    if m.status == "ok":
        return errors, m.matches[0].stock_code
    if m.status == "none":
        return err("NO_RESULT", "일치하는 종목이 없습니다.")
    names = ", ".join(x.name for x in m.matches[:5])
    more = f" 외 {m.total - min(5, len(m.matches))}개" if m.total > 5 else ""
    return err("INVALID_INPUT", f"종목명이 여러 종목과 일치합니다: {names}{more}")


async def execute(state) -> dict:
    sid = state["session_id"]
    bus.publish(sid, "stage_start", StageStart(stage="execute"))
    cache = dict(state.get("tool_cache") or {})
    errors = list(state.get("errors") or [])
    cands = list(state.get("candidates") or [])
    cond = state.get("conditions") or {}
    intent = state.get("intent")
    out: dict = {}

    if intent in ("screen", "refine"):
        errors = drop(errors, "find_stock_candidates", any_code=True)
        for step in state.get("plan") or []:
            r = await _call(cache, sid, step["tool"], step["input"])
            if step["tool"] != "find_stock_candidates":
                continue
            if r.ok:
                seen = {c["code"] for c in cands}
                cands += [c for c in r.data if c["code"] not in seen]
            else:
                errors.append({"tool": "find_stock_candidates", "stock_code": None,
                               "code": r.error.code, "message": r.error.message})
                break
        if cands and terminal_error(errors) is None:
            codes = [c["code"] for c in cands]
            res = await call_batch(cache, sid, "get_financials", codes, lambda c: fin_input(c, cond))
            errors = record_batch(errors, "get_financials", codes, res)
        out["candidates"] = cands
    elif intent in ("lookup", "report_save"):
        steps = [s for s in state.get("plan") or [] if s["tool"] == "get_financials"]
        code = steps[0]["input"]["stock_code"] if steps else None
        if code is None:
            errors, code = await _resolve_target(state, errors)
        if code:
            out["target_stock"] = code
        if intent == "lookup" and code:
            raw = steps[0]["input"] if steps else fin_input(code, cond)
            r = await _call(cache, sid, "get_financials", raw)
            errors = record_batch(errors, "get_financials", [code], [r])

    out.update(tool_cache=cache, errors=errors)
    bus.publish(sid, "stage_end", StageEnd(stage="execute"))
    return out
