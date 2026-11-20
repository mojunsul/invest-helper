from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

UUID_RE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
SessionId = Annotated[str, StringConstraints(pattern=UUID_RE)]  # chat 본문·logs 경로 공용


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: SessionId
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]

    @field_validator("session_id")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


# ---- 응답 문서화 전용 모델(D21): 라우트 `responses=`에서만 참조. response_model·반환 힌트로 쓰지 말 것(런타임 불변) ----
class ErrorBody(BaseModel):
    """오류 응답 {code, message}"""
    code: str
    message: str


def err_responses(*codes: int) -> dict:
    return {c: {"model": ErrorBody} for c in codes}


class SaveResult(BaseModel):
    """리포트 저장 결과"""
    id: int
    duplicate_of: list[int]


class ReportSummary(BaseModel):
    """리포트 목록 항목"""
    id: int
    name: str
    stock_code: str
    stock_name: str
    base_date: str | None
    created_at: str
    updated_at: str
    review_date: str | None
    target_per: float | None
    target_roe: float | None


class Report(ReportSummary):
    """리포트 상세"""
    metrics: dict[str, Any]
    industry_avg: dict[str, Any]
    disclosures: list[dict[str, Any]] | None
    disclaimer: str | None
    memo: str | None


class LogSession(BaseModel):
    """로그 세션 요약"""
    session_id: str
    started_at: str
    event_count: int


class LogEvent(BaseModel):
    """로그 이벤트(run = 실행 회차)"""
    session_id: str
    seq: int
    ts: str
    type: str
    payload: dict[str, Any]
    run: int


class ChatVerdict(BaseModel):
    """종목별 판정"""
    stock_code: str
    stock_name: str
    status: str
    reasons: list[str]
    failed: list[str]


class Step(BaseModel):
    """단계 이력"""
    seq: int
    stage: str
    round: int
    started_at: str
    ended_at: str | None
    intent: str | None
    reason: str | None


class ToolCall(BaseModel):
    """Tool 호출/결과(두 형태의 합집합; result 전용 필드는 call에 없음)"""
    type: str
    seq: int
    round: int
    tool: str | None = None
    input: Any = None
    cache_hit: bool | None = None
    ok: bool | None = None
    error_code: str | None = None
    duration_ms: int | float | None = None
    summary: Any = None


class Replan(BaseModel):
    """재계획 기록"""
    count: int
    trigger: str
    change: str


class Saveable(BaseModel):
    """저장 가능 종목"""
    stock_code: str
    stock_name: str


class ChatResponse(BaseModel):
    """POST /api/chat 응답(status: answered|asked|limit|error)"""
    answer: str
    status: str
    verdicts: list[ChatVerdict]
    industry_avg: dict[str, Any]
    steps: list[Step]
    tool_calls: list[ToolCall]
    replans: list[Replan]
    disclaimer: str
    missing_api_key: bool
    saveable: list[Saveable]
    intent: str | None
    conditions: dict[str, Any] | None
    metrics: dict[str, Any]
    disclosures: dict[str, Any]
    evidence: list[dict[str, Any]]
