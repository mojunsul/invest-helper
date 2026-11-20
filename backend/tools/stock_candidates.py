import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from backend.config import settings
from backend.identity.models import IdentityError
from backend.identity import theme
from backend.identity.resolve import resolve_industry, select_by_codes, select_by_industry
from backend.tools.base import ToolFailure, fail, ok, register, throttle

logger = logging.getLogger(__name__)

MAX_LIMIT = 100     # 임의 상수. D9가 candidate_limit 상한으로 사용
DEFAULT_LIMIT = 20  # 임의 상수
PAGE = 1000         # numOfRows (임의, 상한 미확인)
LOOKBACK_DAYS = 7   # 임의
URL = "https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2"
_KEY_ERR, _RATE_ERR = {"30", "31", "32"}, {"22"}  # 가정값(미확인)


class CandidateInput(BaseModel):
    industry: str = Field(min_length=1)
    market: Literal["KOSPI", "KOSDAQ"] | None = None
    base_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    extra_industries: list[str] | None = None
    limit: int | None = Field(default=None, ge=1, le=MAX_LIMIT)


def _today() -> date:
    return date.today()


async def _fetch_page(basDt: str, page: int, rows: int) -> dict:
    await throttle(URL)
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(URL, params={"serviceKey": settings.DATA_GO_KR_KEY, "resultType": "json",
                                     "numOfRows": rows, "pageNo": page, "basDt": basDt})
        r.raise_for_status()
        return r.json()


def _parse(js: dict) -> tuple[int, list[dict]]:
    try:
        resp = js["response"]
        rc = str(resp["header"]["resultCode"])
        if rc in _KEY_ERR:
            raise ToolFailure("MISSING_API_KEY", "API 키가 올바르지 않습니다. .env를 확인하세요.") from None
        if rc in _RATE_ERR:
            raise ToolFailure("RATE_LIMIT", "외부 API 호출 한도를 초과했습니다.") from None
        if rc != "00":
            raise ToolFailure("API_ERROR", "주가 API 호출에 실패했습니다.") from None
        body = resp["body"]
        total = int(body["totalCount"])
        items = body.get("items") or {}  # 0건이면 ""
        items = items.get("item", []) if isinstance(items, dict) else []
        return total, ([items] if isinstance(items, dict) else list(items))  # 단건은 dict
    except ToolFailure:
        raise
    except (KeyError, TypeError, ValueError):
        raise ToolFailure("API_ERROR", "주가 API 응답 형식이 올바르지 않습니다.") from None


def _int(v) -> int | None:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


async def _resolve_prefixes(text: str) -> list[str]:
    r = await asyncio.to_thread(resolve_industry, text)
    if r["status"] == "ambiguous":
        terms = ", ".join(o["term"] for o in r["options"])
        raise ToolFailure("INVALID_INPUT", f"산업이 모호합니다. 후보: {terms}")
    if r["status"] != "ok":
        raise ToolFailure("INVALID_INPUT", "산업을 해석할 수 없습니다.")
    return r["prefixes"]


@register(name="find_stock_candidates",
          kind="external",
          description="사용자가 산업·시장으로 종목을 찾아야 할 때, 비교 대상 후보 목록을 얻는다",
          input_model=CandidateInput, requires_keys=("DATA_GO_KR_KEY",), timeout_s=30)
async def find_stock_candidates(inp: CandidateInput):
    r = await asyncio.to_thread(resolve_industry, inp.industry)
    try:
        if r["status"] == "unknown" and (codes := theme.cached_codes(inp.industry)):
            stocks = await asyncio.to_thread(select_by_codes, codes)  # D2e: 일반지식 풀(extra_industries 무시)
        else:
            prefixes = await _resolve_prefixes(inp.industry)
            for e in inp.extra_industries or []:
                prefixes += await _resolve_prefixes(e)
            stocks = await asyncio.to_thread(select_by_industry, list(dict.fromkeys(prefixes)))
    except IdentityError as e:
        raise ToolFailure(e.code, e.message) from None
    if not stocks:
        return fail("NO_RESULT", "조건에 맞는 후보가 없습니다.")
    by_code = {s["stock_code"]: s for s in stocks}

    start = datetime.strptime(inp.base_date, "%Y-%m-%d").date() if inp.base_date else _today()
    rows, basdt = [], None
    for i in range(LOOKBACK_DAYS):
        d = start - timedelta(days=i)
        total, items = _parse(await _fetch_page(d.strftime("%Y%m%d"), 1, PAGE))
        if total > 0:
            basdt, rows = d, items
            for p in range(2, (total + PAGE - 1) // PAGE + 1):
                rows += _parse(await _fetch_page(d.strftime("%Y%m%d"), p, PAGE))[1]
            break
    if basdt is None:
        raise ToolFailure("API_ERROR", "주가 데이터를 찾지 못했습니다.")

    seen, cands = set(), []
    for r in rows:
        code = r.get("srtnCd")
        if code not in by_code or code in seen:
            continue
        seen.add(code)
        if inp.market and r.get("mrktCtg") != inp.market:
            continue
        cap, price = _int(r.get("mrktTotAmt")), _int(r.get("clpr"))
        if cap is None or price is None:
            continue
        cands.append({"code": code, "name": by_code[code]["name"], "market": r.get("mrktCtg"),
                      "industry": by_code[code]["induty_code"], "mkt_cap": cap, "price": price})
    cands.sort(key=lambda c: (-c["mkt_cap"], c["code"]))
    cands = cands[:inp.limit or DEFAULT_LIMIT]
    logger.info("candidates industry_stocks=%d price_rows=%d final=%d basDt=%s",
                len(stocks), len(rows), len(cands), basdt)
    if not cands:
        return fail("NO_RESULT", "조건에 맞는 후보가 없습니다.")
    return ok(cands, requested=inp.base_date, actual=basdt.isoformat())
