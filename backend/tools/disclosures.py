import asyncio
import logging
import re
from datetime import date, datetime, timedelta

import httpx
from pydantic import BaseModel, Field

from backend.config import settings
from backend.identity.models import IdentityError
from backend.identity.resolve import get_corp_code
from backend.tools.base import ToolFailure, fail, ok, register, throttle

logger = logging.getLogger(__name__)

URL = "https://opendart.fss.or.kr/api/list.json"
VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"  # G2(미실호출)
LOOKBACK_DAYS = 180  # G1(임의 상수)
TITLE_MAX = 100
_NO_DISC = "최근 공시가 없습니다."


class DisclosureInput(BaseModel):
    stock_code: str = Field(pattern=r"^[0-9A-Z]{6}$", description="종목코드 6자리")
    limit: int = Field(default=3, ge=1, le=10, description="limit 1~10")


def _today() -> date:
    return date.today()


async def _fetch_list(corp_code: str, bgn_de: str, count: int) -> dict:
    await throttle(URL)
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(URL, params={"crtfc_key": settings.DART_API_KEY, "corp_code": corp_code,
                                     "bgn_de": bgn_de, "page_count": count})
        r.raise_for_status()
        return r.json()


def _parse(js) -> list:
    try:
        st = str(js["status"])
        if st in ("010", "011"):
            raise ToolFailure("MISSING_API_KEY", "API 키가 올바르지 않습니다. .env를 확인하세요.") from None
        if st == "020":
            raise ToolFailure("RATE_LIMIT", "외부 API 호출 한도를 초과했습니다.") from None
        if st == "013":
            return []
        if st != "000":
            raise ToolFailure("API_ERROR", "공시 API 호출에 실패했습니다.") from None
        rows = js.get("list") or []
        if not isinstance(rows, list):
            raise TypeError
        return rows
    except ToolFailure:
        raise
    except (KeyError, TypeError, AttributeError):
        raise ToolFailure("API_ERROR", "공시 API 응답 형식이 올바르지 않습니다.") from None


def _convert(r, corp_code: str, stock_code: str) -> dict | None:
    if not isinstance(r, dict):
        return None
    if r.get("corp_code") not in (None, corp_code) or r.get("stock_code") not in (None, stock_code):
        return None
    dt, no, nm = r.get("rcept_dt"), r.get("rcept_no"), r.get("report_nm")
    if not (isinstance(dt, str) and isinstance(no, str) and isinstance(nm, str)):
        return None
    if not re.fullmatch(r"[0-9]{14}", no) or not re.fullmatch(r"[0-9]{8}", dt):
        return None
    try:
        day = datetime.strptime(dt, "%Y%m%d").date()
    except ValueError:
        return None
    title = re.sub(r"[\s\x00-\x1f\x7f]+", " ", nm).strip()
    if not title:
        return None
    if len(title) > TITLE_MAX:
        title = title[:TITLE_MAX] + "…"
    return {"title": title, "date": day.isoformat(), "url": VIEW_URL.format(no), "_k": (dt, no)}


@register(name="search_disclosures",
          description="종목의 최근 공시 제목·일자·링크를 보여줄 때 쓴다. 수치는 주지 않는다.",
          input_model=DisclosureInput, requires_keys=("DART_API_KEY",))
async def search_disclosures(inp: DisclosureInput):
    try:
        corp_code = await asyncio.to_thread(get_corp_code, inp.stock_code)
    except IdentityError as e:
        if e.code == "NO_RESULT":
            return fail("NO_RESULT", _NO_DISC)
        raise ToolFailure(e.code, e.message) from None

    bgn = (_today() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    rows = _parse(await _fetch_list(corp_code, bgn, inp.limit))
    conv = [c for c in (_convert(r, corp_code, inp.stock_code) for r in rows) if c]
    conv.sort(key=lambda c: c["_k"], reverse=True)
    data = [{k: c[k] for k in ("title", "date", "url")} for c in conv[:inp.limit]]
    logger.info("disclosures code=%s returned=%d excluded=%d", inp.stock_code, len(data), len(rows) - len(conv))
    return ok(data) if data else fail("NO_RESULT", _NO_DISC)
