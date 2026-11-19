"""GMS(OpenAI 호환) chat completions 클라이언트. analyze(D7)와 respond(D9)가 공유."""
import json
import logging
from typing import Literal

import httpx

from backend.config import settings
from backend.tools.base import MSG, mask

logger = logging.getLogger(__name__)

LLM_TIMEOUT_S = 30.0  # 임의 초기값(실측 전, 미확인)


DIAG_MAX = 200  # 필드당 최대 길이(임의 상수)


def _clip(x) -> str:
    # mask 먼저, 자르기는 나중: 키가 잘린 채 남으면 mask가 못 잡는다.
    return mask(str(x)).replace("\n", " ")[:DIAG_MAX]


def _err_diag(resp: httpx.Response) -> str:
    """오류 응답 본문에서 진단 필드 추출. 절대 raise하지 않는다."""
    try:
        try:
            d = resp.json()
            if not isinstance(d, dict):
                raise ValueError
        except Exception:
            return "raw=" + _clip(resp.text)
        e = d.get("error")
        if isinstance(e, dict) and isinstance(e.get("error"), dict):
            e = e["error"]  # GMS 래핑: error.error
        if not isinstance(e, dict):
            e = {}
        return "msg=%s err_code=%s err_type=%s err_msg=%s" % tuple(
            _clip(v) for v in (d.get("message"), e.get("code"), e.get("type"), e.get("message")))
    except Exception:
        return ""


class LlmError(Exception):
    def __init__(self, code: Literal["MISSING_API_KEY", "TIMEOUT", "RATE_LIMIT", "API_ERROR"]):
        super().__init__(MSG[code])
        self.code, self.message = code, MSG[code]


async def complete(messages: list[dict], *, json_mode: bool, timeout_s: float = LLM_TIMEOUT_S) -> str:
    if not settings.GMS_API_KEY:
        raise LlmError("MISSING_API_KEY")
    body: dict = {"model": settings.GMS_MODEL, "messages": messages}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    code = "API_ERROR"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.post(settings.GMS_BASE_URL.rstrip("/") + "/chat/completions", json=body,
                             headers={"Authorization": f"Bearer {settings.GMS_API_KEY}"})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
    except httpx.TimeoutException as e:
        code = "TIMEOUT"
        logger.warning("llm code=%s type=%s", code, mask(type(e).__name__))
    except httpx.HTTPStatusError as e:
        code = "RATE_LIMIT" if e.response.status_code == 429 else "API_ERROR"
        logger.warning("llm code=%s type=%s status=%s %s", code, mask(type(e).__name__),
                       e.response.status_code, _err_diag(e.response))
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
        logger.warning("llm code=%s type=%s", code, mask(type(e).__name__))
    raise LlmError(code) from None


async def embed(texts: list[str], *, timeout_s: float = LLM_TIMEOUT_S, model: str = "text-embedding-3-small") -> list[list[float]]:
    """GMS(OpenAI 호환) /embeddings. 오류 매핑은 complete()와 동일."""
    if not settings.GMS_API_KEY:
        raise LlmError("MISSING_API_KEY")
    code = "API_ERROR"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.post(settings.GMS_BASE_URL.rstrip("/") + "/embeddings",
                             json={"model": model, "input": texts},
                             headers={"Authorization": f"Bearer {settings.GMS_API_KEY}"})
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            out = [[float(x) for x in d["embedding"]] for d in data]
            if len(out) != len(texts):
                raise ValueError("count mismatch")
            return out
    except httpx.TimeoutException as e:
        code = "TIMEOUT"
        logger.warning("embed code=%s type=%s", code, mask(type(e).__name__))
    except httpx.HTTPStatusError as e:
        code = "RATE_LIMIT" if e.response.status_code == 429 else "API_ERROR"
        logger.warning("embed code=%s type=%s status=%s %s", code, mask(type(e).__name__),
                       e.response.status_code, _err_diag(e.response))
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
        logger.warning("embed code=%s type=%s", code, mask(type(e).__name__))
    raise LlmError(code) from None
