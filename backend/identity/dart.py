import io
import json
import logging
import zipfile
from typing import Protocol

import defusedxml.ElementTree as ET
import httpx

from backend.config import settings
from backend.identity.models import IdentityError
from backend.tools.base import mask

logger = logging.getLogger(__name__)

BASE = "https://opendart.fss.or.kr/api"
CORP_TIMEOUT_S = 60.0
COMPANY_TIMEOUT_S = 10.0
CORP_ZIP_MAX_BYTES = 20 * 1024 * 1024   # 미실측 임의 상한(구현 시 확인 항목)
CORP_XML_MAX_BYTES = 200 * 1024 * 1024  # 미실측 임의 상한


class DartSource(Protocol):
    async def fetch_corp_list(self) -> list[tuple[str, str, str]]: ...  # (stock_code, corp_code, name)
    async def fetch_company(self, corp_code: str) -> dict | None: ...   # {jurir_no, induty_code, corp_cls}; 013 -> None


class DartNetworkError(IdentityError):
    """연결 수준 실패(응답을 못 받음). code는 API_ERROR 그대로."""

    def __init__(self):
        super().__init__("API_ERROR")


def _fail(code: str, exc: BaseException | None = None, status: int | None = None) -> IdentityError:
    logger.warning(mask(f"dart code={code} type={type(exc).__name__ if exc else '-'} http={status}"))
    return IdentityError(code)  # 호출부에서 `from None`으로 raise


def parse_corp_xml(zip_bytes: bytes) -> list[tuple[str, str, str]]:
    if len(zip_bytes) > CORP_ZIP_MAX_BYTES:
        raise IdentityError("API_ERROR")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        infos = z.infolist()
        if len(infos) != 1 or infos[0].filename != "CORPCODE.xml" or infos[0].file_size > CORP_XML_MAX_BYTES:
            raise IdentityError("API_ERROR")
        xml = z.read(infos[0])
    out: dict[str, tuple[str, str, str]] = {}
    for el in ET.fromstring(xml).iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        cc = (el.findtext("corp_code") or "").strip()
        nm = (el.findtext("corp_name") or "").strip()
        if len(sc) == 6 and sc.isascii() and sc.isalnum() and sc == sc.upper() and cc and nm:
            out[sc] = (sc, cc, nm)
    return list(out.values())


class HttpDartSource:
    async def _get(self, path: str, params: dict, timeout: float, max_bytes: int | None = None) -> bytes:
        params = {"crtfc_key": settings.DART_API_KEY, **params}
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                async with c.stream("GET", f"{BASE}/{path}", params=params) as r:
                    if r.status_code == 429:
                        raise _fail("RATE_LIMIT", status=429)
                    if r.status_code >= 400:
                        raise _fail("API_ERROR", status=r.status_code)
                    buf = bytearray()
                    async for chunk in r.aiter_bytes():
                        buf += chunk
                        if max_bytes and len(buf) > max_bytes:
                            raise _fail("API_ERROR")
                    return bytes(buf)
        except IdentityError:
            raise
        except httpx.TimeoutException as e:
            raise _fail("TIMEOUT", e) from None
        except httpx.TransportError as e:  # ConnectError/ReadError/RemoteProtocolError 등
            _fail("API_ERROR", e)
            raise DartNetworkError() from None
        except Exception as e:  # 예외 문자열에 키 포함 가능 -> 원인 체인 차단
            raise _fail("API_ERROR", e) from None

    async def fetch_corp_list(self) -> list[tuple[str, str, str]]:
        data = await self._get("corpCode.xml", {}, CORP_TIMEOUT_S, CORP_ZIP_MAX_BYTES)
        try:
            return parse_corp_xml(data)
        except IdentityError:
            raise
        except Exception as e:
            raise _fail("API_ERROR", e) from None

    async def fetch_company(self, corp_code: str) -> dict | None:
        data = await self._get("company.json", {"corp_code": corp_code}, COMPANY_TIMEOUT_S)
        try:
            body = json.loads(data)
            status = str(body.get("status"))
        except Exception as e:
            raise _fail("API_ERROR", e) from None
        if status == "000":
            return body
        if status == "013":
            return None
        if status in ("010", "011"):
            raise _fail("MISSING_API_KEY")
        if status == "020":
            raise _fail("RATE_LIMIT")
        raise _fail("API_ERROR")
