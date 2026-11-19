"""D17 search_filing_text: 로컬 chromadb 색인에서 공시 원문 문단 검색(kind=local)."""
import asyncio
import hashlib
import logging
import math
import os
import re

from pydantic import BaseModel, Field

from backend.agent import llm
from backend.tools.base import MSG, ToolFailure, fail, ok, register
from backend.tools.disclosures import VIEW_URL

logger = logging.getLogger(__name__)

INDEX_DIR = "data/filing_index"
COLLECTION = "filings"
LOCAL_EMBED = "local-hash-v1"
EMBED_MODEL = "text-embedding-3-small"  # GMS 불가 시 LOCAL_EMBED로 전환(이 상수 1곳)
EMBED_TIMEOUT_S = 15.0  # Tool timeout_s(20)보다 짧게
TOOL_TIMEOUT_S = 20
HASH_DIM = 1024
# 실측 후 조정(임의 초기값). 해시 임베딩은 코사인 분포가 달라 별도 값.
MIN_SCORE = 0.3
MIN_SCORE_LOCAL = 0.1
EXCERPT_MAX = 300
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


def _hash_embed(text: str) -> list[float]:
    s = re.sub(r"\s+", "", text)
    v = [0.0] * HASH_DIM
    for i in range(len(s) - 1):
        h = int.from_bytes(hashlib.blake2b(s[i:i + 2].encode(), digest_size=4).digest(), "big")
        v[h % HASH_DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if EMBED_MODEL == LOCAL_EMBED:
        return [_hash_embed(t) for t in texts]
    return await llm.embed(texts, timeout_s=EMBED_TIMEOUT_S, model=EMBED_MODEL)


def open_collection(create: bool = False):
    """chromadb 지연 import. create=False면 없을 때 None(디렉터리는 호출부가 사전 확인)."""
    import chromadb
    client = chromadb.PersistentClient(path=INDEX_DIR, settings=chromadb.Settings(anonymized_telemetry=False))
    if create:
        return client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine", "embed_model": EMBED_MODEL},
                                           embedding_function=None)  # 임베딩은 직접 넘김(기본 ONNX 다운로드 방지)
    try:
        return client.get_collection(COLLECTION, embedding_function=None)
    except Exception:  # 예외 타입이 버전별로 달라 넓게 잡는다
        return None


class FilingQuery(BaseModel):
    query: str = Field(min_length=1, max_length=200, description="찾을 내용(사용자 질문 원문)")
    stock_code: str | None = Field(default=None, pattern=r"^[0-9A-Z]{6}$", description="종목코드 6자리, 없으면 색인 전체")
    top_k: int = Field(default=3, ge=1, le=5)


def _precheck(code: str | None) -> str | None:
    """NO_INDEX 사유 문구 또는 None."""
    col = open_collection()
    if col is None or col.count() == 0:
        return "공시 원문 색인이 아직 없습니다."
    if (col.metadata or {}).get("embed_model") != EMBED_MODEL:
        return "현재 임베딩 설정과 다른 색인입니다."
    if code and not col.get(where={"stock_code": code}, limit=1)["ids"]:
        return "해당 종목의 공시 원문은 아직 색인되지 않았습니다."
    return None


def _query(v: list[float], code: str | None, k: int) -> dict:
    return open_collection().query(query_embeddings=[v], n_results=k,
                                   where={"stock_code": code} if code else None,
                                   include=["documents", "metadatas", "distances"])


def _excerpt(text: str) -> str:
    t = re.sub(r"\s+", " ", _URL_RE.sub("", text)).strip()  # URL은 linkify 대상이 되지 않게 제거
    return t if len(t) <= EXCERPT_MAX else t[:EXCERPT_MAX] + "…"


@register(name="search_filing_text", kind="local",
          description="외부 API로 확인할 수 없는 공시 본문 서술(사업 내용·실적 전망·가이던스·위험 요인·경영진 분석 등)을 물을 때, "
                      "미리 색인한 공시 원문에서 근거 문단을 찾는다. 수치 지표 조회에는 쓰지 않는다.",
          input_model=FilingQuery,
          requires_keys=() if EMBED_MODEL == LOCAL_EMBED else ("GMS_API_KEY",),
          timeout_s=TOOL_TIMEOUT_S)
async def search_filing_text(inp: FilingQuery):
    if not os.path.isdir(INDEX_DIR):  # 클라이언트가 디렉터리를 만들지 않도록 사전 검사
        return fail("NO_INDEX", "공시 원문 색인이 아직 없습니다.")
    try:
        why = await asyncio.to_thread(_precheck, inp.stock_code)
        if why:
            return fail("NO_INDEX", why)
        try:
            v = (await embed_texts([inp.query]))[0]
        except llm.LlmError as e:
            raise ToolFailure(e.code, e.message) from None
        res = await asyncio.to_thread(_query, v, inp.stock_code, inp.top_k)
    except ImportError:
        logger.warning("filing_text chromadb 미설치")
        raise ToolFailure("API_ERROR", MSG["API_ERROR"]) from None
    floor = MIN_SCORE_LOCAL if EMBED_MODEL == LOCAL_EMBED else MIN_SCORE
    rows = []
    for doc, m, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        score = 1 - dist
        no = str(m.get("rcept_no", ""))
        if score < floor or not re.fullmatch(r"[0-9]{14}", no):
            continue
        rows.append({"text": _excerpt(doc), "score": round(score, 3), "stock_code": m.get("stock_code", ""),
                     "corp_name": m.get("corp_name", ""),
                     "title": re.sub(r"[\s\x00-\x1f\x7f]+", " ", str(m.get("report_nm", ""))).strip()[:100],
                     "rcept_no": no, "date": m.get("rcept_dt", ""), "section": m.get("section", ""),
                     "url": VIEW_URL.format(no)})
    rows.sort(key=lambda r: r["score"], reverse=True)
    logger.info("filing_text stock=%s hits=%d top=%s", inp.stock_code, len(rows), rows[0]["score"] if rows else "-")
    return ok(rows) if rows else fail("NO_RESULT", "관련 공시 문단을 찾지 못했습니다.")
