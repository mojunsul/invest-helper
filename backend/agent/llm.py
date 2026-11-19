"""GMS(OpenAI 호환) chat completions 클라이언트. analyze(D7)와 respond(D9)가 공유."""
import json
import logging
from typing import Literal

import httpx

from backend.config import settings
from backend.tools.base import MSG, mask

logger = logging.getLogger(__name__)

LLM_TIMEOUT_S = 30.0  # 임의 초기값(실측 전, 미확인)


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
        logger.warning("llm code=%s type=%s status=%s", code, mask(type(e).__name__), e.response.status_code)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
        logger.warning("llm code=%s type=%s", code, mask(type(e).__name__))
    raise LlmError(code) from None
