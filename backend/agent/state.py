from typing import Literal, Required, TypedDict

from backend.tools.base import ErrorCode

Intent = Literal["screen", "lookup", "report_save", "refine", "chitchat"]
Metric = Literal["ROE", "ROA", "OPM", "PER", "PBR", "PSR", "DEBT_RATIO"]
Market = Literal["KOSPI", "KOSDAQ"]
Status = Literal["answered", "asked", "limit", "error"]
Missing = Literal["industry", "threshold", "target_stock"]
ReasonCode = Literal[
    "THRESHOLD_NOT_MET",
    "NO_FINANCIALS", "FETCH_FAILED",
    "NO_MARKET_CAP", "NEGATIVE_EARNINGS", "NEGATIVE_EQUITY", "NONPOSITIVE_REVENUE", "NONPOSITIVE_ASSETS",
    "INDUSTRY_AVG_UNAVAILABLE"]


class Constraint(TypedDict):
    metric: Metric
    op: Literal[">=", "<="]
    value: float | None
    ref: Literal["industry_avg"] | None


class Conditions(TypedDict):
    industry: str | None
    market: Market | None
    base_date: str | None
    constraints: list[Constraint]
    want_disclosures: bool
    pending_metrics: list[Metric]


class SearchScope(TypedDict):
    market: Market | None
    extra_industries: list[str]
    candidate_limit: int | None


class PlanStep(TypedDict):
    tool: str
    input: dict


class Verdict(TypedDict):
    status: Literal["PASS", "FAIL", "UNDECIDABLE"]
    reasons: list[ReasonCode]
    failed: list[Metric]


class MetricRow(TypedDict):
    values: dict[str, float | None]
    fiscal_period: str | None
    actual_basis: str | None
    estimated: bool
    reasons: dict[Metric, ReasonCode]


class ErrorRecord(TypedDict):
    tool: str
    stock_code: str | None
    code: ErrorCode
    message: str


class ReplanRecord(TypedDict):
    count: int
    trigger: Literal["no_pass", "no_candidates"]
    change: Literal["market_expand", "similar_industry", "limit_up"]


class AgentState(TypedDict, total=False):
    session_id: Required[str]
    user_msg: Required[str]
    intent: Intent | None
    prev_intent: Intent | None
    last_status: Status | None
    intent_reason: str | None
    conditions: Conditions
    target_stock: str | None
    missing: list[Missing]
    search_scope: SearchScope
    expansion_step: int
    replan_count: int
    plan: list[PlanStep]
    tool_cache: dict[str, dict]
    candidates: list[dict]
    metrics: dict[str, MetricRow]
    disclosures: dict[str, list[dict]]
    verdicts: dict[str, Verdict]
    industry_avg: dict[str, dict]
    replan_log: list[ReplanRecord]
    last_result_summary: dict | None
    errors: list[ErrorRecord]
    status: Status | None
    clarify_question: str | None
    answer: str | None
    report_draft: dict | None
