import logging

from backend.agent.constraints import compute_metric_row, evaluate, failure_verdict, industry_avg_per
from backend.agent.nodes.execute import call_batch, drop, fin_input, record_batch, terminal_error
from backend.events import bus
from backend.events.bus import StageEnd, StageStart
from backend.tools import registry
from backend.tools.base import ToolResult

logger = logging.getLogger(__name__)

_RAW_KEYS = ("revenue", "op_profit", "net_income", "assets", "equity", "debt_ratio", "mkt_cap")


def _cached(cache, name, raw) -> ToolResult | None:
    key = registry.cache_key_for(name, raw)
    return ToolResult.model_validate(cache[key]) if key is not None and key in cache else None


def _row(r: ToolResult, fallback_cap=None):
    raw = {k: r.data.get(k) for k in _RAW_KEYS}
    if raw["mkt_cap"] is None:
        raw["mkt_cap"] = fallback_cap
    return compute_metric_row(raw, fiscal_period=r.data.get("fiscal_period"),
                              actual_basis=r.meta.actual_basis, estimated=r.meta.estimated)


async def judge(state) -> dict:
    sid = state["session_id"]
    bus.publish(sid, "stage_start", StageStart(stage="judge"))
    cache = dict(state.get("tool_cache") or {})
    errors = list(state.get("errors") or [])
    cond = state.get("conditions") or {}
    intent = state.get("intent")
    out: dict = {}

    if intent in ("screen", "refine"):
        rows, verdicts, disclosures = {}, {}, {}
        for c in state.get("candidates") or []:
            code = c["code"]
            r = _cached(cache, "get_financials", fin_input(code, cond))
            if r is not None and r.ok:
                rows[code] = _row(r, c.get("mkt_cap"))
                continue
            e = next((e for e in errors if e["tool"] == "get_financials" and e["stock_code"] == code), None)
            if e is None or e["code"] == "MISSING_API_KEY":
                continue
            verdicts[code] = failure_verdict("NO_FINANCIALS" if e["code"] == "NO_FINANCIALS" else "FETCH_FAILED")
        avg = {"PER": industry_avg_per(rows)}
        for code, row in rows.items():
            v = evaluate(row, cond.get("constraints") or [], avg)
            if v:
                verdicts[code] = v

        errors = drop(errors, "search_disclosures", any_code=True)
        passed = [c["code"] for c in state.get("candidates") or [] if verdicts.get(c["code"], {}).get("status") == "PASS"]
        if cond.get("want_disclosures") and passed and terminal_error(errors) is None:
            res = await call_batch(cache, sid, "search_disclosures", passed, lambda c: {"stock_code": c})
            for code, r in zip(passed, res):
                if r is not None and r.ok:
                    disclosures[code] = r.data
            errors = record_batch(errors, "search_disclosures", passed, res, skip_no_result=True)
        out.update(metrics=rows, verdicts=verdicts, industry_avg=avg, disclosures=disclosures)
    elif intent == "lookup" and state.get("target_stock"):
        code = state["target_stock"]
        r = _cached(cache, "get_financials", fin_input(code, cond))
        out["metrics"] = {code: _row(r)} if r is not None and r.ok else {}

    out.update(tool_cache=cache, errors=errors)
    bus.publish(sid, "stage_end", StageEnd(stage="judge"))
    return out
