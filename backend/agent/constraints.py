"""D6 constraints: 지표 계산·산업평균 PER·제약 판정. 순수 함수만(외부 호출 없음)."""
import math
import statistics
from typing import Literal, Mapping

from backend.agent.state import Constraint, Metric, MetricRow, ReasonCode, Verdict

SAMPLE_MIN = 3

# metric: (분자, 분모, 배수, 시총 필요, 분모 ≤0 사유)
_FORMULAS: dict[str, tuple[str, str, int, bool, ReasonCode]] = {
    "PER": ("mkt_cap", "net_income", 1, True, "NEGATIVE_EARNINGS"),
    "PBR": ("mkt_cap", "equity", 1, True, "NEGATIVE_EQUITY"),
    "PSR": ("mkt_cap", "revenue", 1, True, "NONPOSITIVE_REVENUE"),
    "ROE": ("net_income", "equity", 100, False, "NEGATIVE_EQUITY"),
    "ROA": ("net_income", "assets", 100, False, "NONPOSITIVE_ASSETS"),
    "OPM": ("op_profit", "revenue", 100, False, "NONPOSITIVE_REVENUE"),
}
_SOURCES = ("revenue", "op_profit", "net_income", "assets", "equity")


def _num(x) -> float | int | None:
    """bool/NaN/inf/비숫자는 None과 동일 취급."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        return None
    return x


def compute_metric_row(raw: dict, *, fiscal_period: str | None,
                       actual_basis: str | None, estimated: bool) -> MetricRow:
    src = {k: _num(raw.get(k)) for k in (*_SOURCES, "mkt_cap")}
    values: dict[str, float | None] = dict(src)
    reasons: dict[Metric, ReasonCode] = {}

    def put(metric, value, reason=None):
        values[metric] = value
        if value is None:
            reasons[metric] = reason

    for metric, (a, b, mul, needs_cap, code) in _FORMULAS.items():
        fin = [k for k in (a, b) if k != "mkt_cap"]
        if any(src[k] is None for k in fin):
            put(metric, None, "NO_FINANCIALS")
        elif needs_cap and (src["mkt_cap"] is None or src["mkt_cap"] <= 0):
            put(metric, None, "NO_MARKET_CAP")
        elif src[b] <= 0:
            put(metric, None, code)
        else:
            put(metric, src[a] * mul / src[b])

    debt = _num(raw.get("debt_ratio"))
    if debt is None:
        put("DEBT_RATIO", None, "NO_FINANCIALS")
    elif src["equity"] is not None and src["equity"] <= 0:
        put("DEBT_RATIO", None, "NEGATIVE_EQUITY")
    else:
        put("DEBT_RATIO", debt)
    return {"values": values, "fiscal_period": fiscal_period, "actual_basis": actual_basis,
            "estimated": estimated, "reasons": reasons}


def industry_avg_per(rows: Mapping[str, MetricRow]) -> dict:
    vals = [r["values"]["PER"] for r in rows.values() if r["values"].get("PER") is not None]
    return {"value": statistics.median(vals) if vals else None, "n": len(vals)}


def is_sample_short(n: int) -> bool:
    return n < SAMPLE_MIN


def failure_verdict(reason: Literal["NO_FINANCIALS", "FETCH_FAILED"]) -> Verdict:
    return {"status": "UNDECIDABLE", "reasons": [reason], "failed": []}


def _uniq(xs):
    return list(dict.fromkeys(xs))


def evaluate(row: MetricRow, constraints: list[Constraint],
             industry_avg: dict[str, dict]) -> Verdict | None:
    if not constraints:
        return None
    failed: list[Metric] = []
    unsure: list[ReasonCode] = []
    for c in constraints:
        metric, op, value, ref = c.get("metric"), c.get("op"), c.get("value"), c.get("ref")
        v = _num(row["values"].get(metric))
        bad = op not in (">=", "<=") or (ref is None) == (_num(value) is None) \
            or (ref is not None and ref != "industry_avg")
        if bad:
            t = None
            unsure.append("NO_FINANCIALS")
        elif ref is None:
            t = _num(value)
        else:
            t = _num((industry_avg.get(metric) or {}).get("value"))
        if v is None:
            unsure.append(row["reasons"].get(metric, "NO_FINANCIALS"))
        if not bad and t is None:
            unsure.append("INDUSTRY_AVG_UNAVAILABLE")
        if v is None or t is None:
            continue
        if not (v >= t if op == ">=" else v <= t):
            failed.append(metric)
    if failed:
        return {"status": "FAIL", "reasons": _uniq(["THRESHOLD_NOT_MET", *unsure]), "failed": _uniq(failed)}
    if unsure:
        return {"status": "UNDECIDABLE", "reasons": _uniq(unsure), "failed": []}
    return {"status": "PASS", "reasons": [], "failed": []}
