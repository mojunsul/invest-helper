import asyncio
import logging
import re
import sqlite3
from datetime import date, datetime, timedelta

import httpx
from pydantic import BaseModel, Field

from backend.config import settings
from backend.identity.models import IdentityError
from backend.identity import store
from backend.identity.resolve import get_crno
from backend.tools import stock_candidates as sc
from backend.tools.base import ToolFailure, fail, ok, register, throttle

logger = logging.getLogger(__name__)

URL = "https://apis.data.go.kr/1160100/service/GetFinaStatInfoService_V2/getSummFinaStat_V2"
_KEY_ERR, _RATE_ERR = {"30", "31", "32"}, {"22"}  # 가정값(미확인, H5)
_NO_FIN = "재무 정보를 찾을 수 없습니다."
NO_FIN_FINANCIAL = "금융업(은행·보험·증권 등)은 이 데이터 소스에서 지표 산출을 지원하지 않습니다."  # respond가 import
_CORE = ("enpSaleAmt", "enpBzopPft", "enpCrtmNpf", "enpTastAmt", "enpTcptAmt")


class FinInput(BaseModel):
    stock_code: str = Field(pattern=r"^[0-9A-Z]{6}$")
    base_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


def _today() -> date:
    return date.today()


def _is_financial(stock_code: str) -> bool:
    """induty_code 접두 64/65/66. DB 오류·행 없음·NULL은 False(NO_FINANCIALS가 API_ERROR로 바뀌지 않게)."""
    try:
        rows = store.read("SELECT induty_code FROM company WHERE stock_code=? AND listed=1", (stock_code,))
    except (IdentityError, sqlite3.Error):
        return False
    return bool(rows and rows[0][0] and str(rows[0][0]).startswith(("64", "65", "66")))


async def _fetch_fin(crno: str, biz_year: int) -> dict:
    await throttle(URL)
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(URL, params={"serviceKey": settings.DATA_GO_KR_KEY, "resultType": "json", "crno": crno,
                                     "bizYear": biz_year, "numOfRows": 10, "pageNo": 1})
        r.raise_for_status()
        return r.json()


async def _fetch_price(basDt: str, stock_code: str) -> dict:
    await throttle(sc.URL)
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(sc.URL, params={"serviceKey": settings.DATA_GO_KR_KEY, "resultType": "json",
                                        "numOfRows": 10, "pageNo": 1, "basDt": basDt, "likeSrtnCd": stock_code})
        r.raise_for_status()
        return r.json()


def _parse_fin(js: dict) -> list[dict]:
    try:
        resp = js["response"]
        rc = str(resp["header"]["resultCode"])
        if rc in _KEY_ERR:
            raise ToolFailure("MISSING_API_KEY", "API 키가 올바르지 않습니다. .env를 확인하세요.") from None
        if rc in _RATE_ERR:
            raise ToolFailure("RATE_LIMIT", "외부 API 호출 한도를 초과했습니다.") from None
        if rc != "00":
            raise ToolFailure("API_ERROR", "재무 API 호출에 실패했습니다.") from None
        items = resp["body"].get("items") or {}  # 0건이면 ""
        items = items.get("item", []) if isinstance(items, dict) else []
        return [items] if isinstance(items, dict) else list(items)  # 단건은 dict
    except ToolFailure:
        raise
    except (KeyError, TypeError, AttributeError):
        raise ToolFailure("API_ERROR", "재무 API 응답 형식이 올바르지 않습니다.") from None


def _num(v, as_int=True):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return int(f) if as_int else f


async def _pick_row(crno: str, year: int) -> dict | None:
    rows = [r for r in _parse_fin(await _fetch_fin(crno, year))
            if r.get("crno") == crno and str(r.get("bizYear")) == str(year)]
    for dcd in ("110", "120"):  # 연결 우선
        for r in rows:
            if str(r.get("fnclDcd")) == dcd:
                return r
    return None


async def _mkt_cap(stock_code: str, start: date) -> int | None:
    try:
        for i in range(sc.LOOKBACK_DAYS):
            _, items = sc._parse(await _fetch_price((start - timedelta(days=i)).strftime("%Y%m%d"), stock_code))
            for r in items:
                if r.get("srtnCd") == stock_code:
                    return sc._int(r.get("mrktTotAmt"))
    except httpx.TimeoutException:
        raise  # 타임아웃은 삼키지 않는다(D1 래퍼가 TIMEOUT 처리)
    except (ToolFailure, httpx.HTTPError) as e:
        logger.warning("mkt_cap skipped code=%s err=%s", stock_code, type(e).__name__)
    return None


@register(name="get_financials",
          kind="external",
          description="후보 종목의 PER·PBR·ROE·부채비율이 필요할 때 종목코드 하나로 조회한다",
          input_model=FinInput, requires_keys=("DATA_GO_KR_KEY",), timeout_s=40)
async def get_financials(inp: FinInput):
    try:
        start = datetime.strptime(inp.base_date, "%Y-%m-%d").date() if inp.base_date else _today()
    except ValueError:
        raise ToolFailure("INVALID_INPUT", "입력값이 올바르지 않습니다.") from None
    try:
        crno = await asyncio.to_thread(get_crno, inp.stock_code)
    except IdentityError as e:
        if e.code == "NO_RESULT":
            return fail("NO_FINANCIALS", _NO_FIN)
        raise ToolFailure(e.code, e.message) from None

    y = start.year - 1
    row, y_actual = await _pick_row(crno, y), y
    if row is None:
        row, y_actual = await _pick_row(crno, y - 1), y - 1  # H2: 한 번만 소급
    if row is None or all(_num(row.get(k)) is None for k in _CORE):
        return fail("NO_FINANCIALS", NO_FIN_FINANCIAL if await asyncio.to_thread(_is_financial, inp.stock_code) else _NO_FIN)

    mkt_cap = await _mkt_cap(inp.stock_code, start)
    data = {"code": inp.stock_code, "fiscal_period": f"FY{y_actual}",
            "revenue": _num(row.get("enpSaleAmt")), "op_profit": _num(row.get("enpBzopPft")),
            "net_income": _num(row.get("enpCrtmNpf")), "assets": _num(row.get("enpTastAmt")),
            "equity": _num(row.get("enpTcptAmt")), "debt_ratio": _num(row.get("fnclDebtRto"), as_int=False),
            "mkt_cap": mkt_cap}
    logger.info("financials code=%s year=%d fnclDcd=%s mkt_cap=%s", inp.stock_code, y_actual,
                row.get("fnclDcd"), mkt_cap is not None)
    requested, actual = f"bizYear={y}", f"FY{y_actual}"
    first = lambda s: re.search(r"\d{4}", s).group()  # noqa: E731
    return ok(data, requested=requested, actual=actual, estimated=first(requested) != first(actual))
