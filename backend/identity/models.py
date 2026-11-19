import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, TypedDict

from backend.tools.base import MSG

CACHE_NOT_READY = "종목 목록이 준비되지 않았습니다. python -m backend.identity.build 를 먼저 실행하세요."
INDUSTRY_NOT_READY = "산업 데이터 구축이 완료되지 않았습니다. python -m backend.identity.build 를 다시 실행하세요."
NO_RESULT_MSG = "대상을 찾을 수 없습니다."

Code = Literal["MISSING_API_KEY", "API_ERROR", "RATE_LIMIT", "TIMEOUT", "NO_RESULT", "INVALID_INPUT"]


class IdentityError(Exception):
    """D3~D5가 ToolFailure(e.code, e.message)로 1:1 변환. message는 고정 문구만."""

    def __init__(self, code: Code, message: str | None = None):
        message = message or {**MSG, "NO_RESULT": NO_RESULT_MSG}[code]
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class StockRef:
    stock_code: str
    corp_code: str
    name: str


@dataclass(frozen=True)
class StockMatch:
    status: Literal["ok", "none", "multiple"]
    matches: tuple[StockRef, ...]
    total: int
    matched_by: Literal["code", "exact", "prefix", "contains"] | None


@dataclass(frozen=True)
class BuildReport:
    ok: int
    none: int
    failed: int
    pending_left: int
    stopped_by: Literal["RATE_LIMIT", "MISSING_API_KEY", "NETWORK"] | None
    complete: bool


class IndustryOption(TypedDict):
    term: str
    prefixes: list[str]
    n_listed: int


class IndustryResolution(TypedDict):
    status: Literal["ok", "ambiguous", "unknown"]
    prefixes: list[str]
    label: str
    options: list[IndustryOption]


class IndustryStock(TypedDict):
    stock_code: str
    name: str
    induty_code: str


class CacheStatus(TypedDict):
    total: int
    ok: int
    pending: int
    failed: int
    complete: bool
    corp_synced_at: str | None


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = s.replace("(주)", "").replace("주식회사", "")
    return re.sub(r"[^\w]|_", "", s)
