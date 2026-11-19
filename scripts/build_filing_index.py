"""D17 공시 원문 색인 스크립트(오프라인, 수동 실행).
python -m scripts.build_filing_index [--codes 005930 ...] [--force] [--clear]"""
import argparse
import asyncio
import html
import io
import json
import logging
import re
import zipfile
from datetime import date, timedelta

from backend.agent.llm import LlmError
from backend.config import settings
from backend.identity.dart import DartNetworkError, HttpDartSource
from backend.identity.models import IdentityError
from backend.identity.resolve import get_corp_code
from backend.tools import filing_text as ft
from backend.tools.base import install_log_mask, mask, throttle

logger = logging.getLogger("build_filing_index")

DEFAULT_CODES = ["005930", "000660", "005380", "373220", "035420", "035720", "105560", "068270"]
CHUNK_MAX, CHUNK_MIN, MAX_CHUNKS = 800, 50, 600
EMBED_BATCH, EMBED_INTERVAL_S, DART_INTERVAL_S = 64, 0.5, 2.0
LOOKBACK_DAYS = 450
# 사업보고서 ZIP은 첨부 포함 시 corpCode ZIP(20MB)보다 클 수 있어 별도 상수(임의, 실측 후 조정)
ZIP_MAX_BYTES = 60 * 1024 * 1024
XML_MAX_BYTES = 200 * 1024 * 1024
LIST_MAX_BYTES = 5 * 1024 * 1024
DART_TIMEOUT_S = 60.0

_sleep = asyncio.sleep  # 테스트에서 patch


# ---------- 청크 ----------
def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _units(p: str) -> list[str]:
    if len(p) <= CHUNK_MAX:
        return [p]
    out = []
    for s in re.split(r"(?<=\.)\s+", p):  # "다. "·". " 문장 끝
        out += [s[i:i + CHUNK_MAX] for i in range(0, len(s), CHUNK_MAX)]
    return out


def chunk_document(xml_text: str) -> list[tuple[str, str]]:
    xml_text = re.sub(r"<TABLE\b.*?</TABLE>", " ", xml_text, flags=re.I | re.S)
    toks = list(re.finditer(r"<TITLE\b[^>]*>(.*?)</TITLE>|<P\b[^>]*>(.*?)</P>", xml_text, flags=re.I | re.S))
    if not any(m.group(2) is not None for m in toks):  # <P> 없으면 빈 줄 경계
        paras = [(None, x) for x in re.split(r"\n\s*\n", re.sub(r"<TITLE\b[^>]*>.*?</TITLE>", "", xml_text, flags=re.I | re.S))]
    else:
        paras = [(m.group(1), m.group(2)) for m in toks]
    chunks: list[tuple[str, str]] = []
    section, buf = "", ""

    def flush():
        nonlocal buf
        if len(buf) >= CHUNK_MIN:
            chunks.append((section, buf))
        buf = ""

    for title, para in paras:
        if title is not None:
            flush()
            section = _clean(title)[:100]
            continue
        p = _clean(para)
        if not p:
            continue
        for u in _units(p):
            if buf and len(buf) + 1 + len(u) > CHUNK_MAX:
                flush()
            buf = f"{buf} {u}" if buf else u
    flush()
    if len(chunks) > MAX_CHUNKS:
        logger.warning("chunks truncated %d -> %d", len(chunks), MAX_CHUNKS)
    return chunks[:MAX_CHUNKS]


# ---------- DART ----------
class _Abort(Exception):
    pass


class _Skip(Exception):
    pass


class _Fail(Exception):
    pass


async def _src_get(path: str, params: dict, max_bytes: int) -> bytes:
    # dart.py의 _get 재사용(스트리밍 상한·DartNetworkError 변환·예외 체인 차단)
    return await HttpDartSource()._get(path, params, DART_TIMEOUT_S, max_bytes)


async def _dart(path: str, params: dict, max_bytes: int, state: dict) -> bytes:
    await throttle(ft_url(path))
    await _sleep(DART_INTERVAL_S)
    try:
        data = await _src_get(path, params, max_bytes)
    except DartNetworkError:
        state["net"] += 1
        if state["net"] >= 2:
            raise _Abort("IP 차단 의심: 다른 네트워크에서 opendart 사이트 접속으로 확인") from None
        raise _Fail("API_ERROR") from None
    except IdentityError as e:
        if e.code in ("MISSING_API_KEY", "RATE_LIMIT"):
            raise _Abort(e.code) from None
        raise _Fail(e.code) from None
    state["net"] = 0
    return data


def ft_url(path: str) -> str:
    return f"https://opendart.fss.or.kr/api/{path}"


def _status(st: str) -> None:
    if st == "000":
        return
    if st == "013":
        raise _Skip()
    if st in ("010", "011"):
        raise _Abort("MISSING_API_KEY")
    if st == "020":
        raise _Abort("RATE_LIMIT")
    raise _Fail("API_ERROR")


def pick_report(body: dict) -> dict | None:
    rows = [r for r in body.get("list") or []
            if isinstance(r, dict) and "사업보고서" in str(r.get("report_nm", ""))
            and re.fullmatch(r"[0-9]{14}", str(r.get("rcept_no", ""))) and re.fullmatch(r"[0-9]{8}", str(r.get("rcept_dt", "")))]
    return max(rows, key=lambda r: (r["rcept_dt"], r["rcept_no"])) if rows else None


def extract_xml(data: bytes, rcept_no: str) -> str:
    """ZIP에서 본문 xml 1개 선택·디코딩. ZIP이 아니면 오류 본문으로 보고 status 해석."""
    if not zipfile.is_zipfile(io.BytesIO(data)):
        m = re.search(rb"<status>\s*(\d+)\s*</status>", data) or re.search(rb'"status"\s*:\s*"(\d+)"', data)
        _status(m.group(1).decode() if m else "?")
        raise _Fail("API_ERROR")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xmls = [i for i in z.infolist() if i.filename.lower().endswith(".xml")]
        if not xmls:
            raise _Fail("API_ERROR")
        pick = next((i for i in xmls if i.filename == f"{rcept_no}.xml"), None) or max(xmls, key=lambda i: i.file_size)
        if pick.file_size > XML_MAX_BYTES:
            raise _Fail("API_ERROR")
        raw = z.read(pick)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp949", errors="replace")


# ---------- 색인 ----------
async def _index_one(code: str, col, state: dict, force: bool) -> str:
    if not force and await asyncio.to_thread(lambda: col.get(where={"stock_code": code}, limit=1)["ids"]):
        return "skipped"
    try:
        corp = await asyncio.to_thread(get_corp_code, code)
    except IdentityError as e:
        raise _Fail(e.code) from None
    bgn = (date.today() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    try:
        body = json.loads(await _dart("list.json", {"corp_code": corp, "bgn_de": bgn, "pblntf_ty": "A", "page_count": 10}, LIST_MAX_BYTES, state))
    except ValueError:
        raise _Fail("API_ERROR") from None
    _status(str(body.get("status")))
    rep = pick_report(body)
    if rep is None:
        raise _Skip()
    no = rep["rcept_no"]
    xml = extract_xml(await _dart("document.xml", {"rcept_no": no}, ZIP_MAX_BYTES, state), no)
    chunks = chunk_document(xml)
    if not chunks:
        raise _Skip()
    embs: list[list[float]] = []
    try:
        for i in range(0, len(chunks), EMBED_BATCH):
            if i:
                await _sleep(EMBED_INTERVAL_S)
            embs += await ft.embed_texts([t for _, t in chunks[i:i + EMBED_BATCH]])
    except LlmError as e:
        raise (_Abort(e.code) if e.code == "MISSING_API_KEY" else _Fail(e.code)) from None
    d = rep["rcept_dt"]
    meta = [{"stock_code": code, "corp_name": str(rep.get("corp_name") or code), "report_nm": rep["report_nm"],
             "rcept_no": no, "rcept_dt": f"{d[:4]}-{d[4:6]}-{d[6:]}", "section": s, "chunk_no": i}
            for i, (s, _) in enumerate(chunks)]

    def store():  # 임베딩 성공 뒤에만 교체(실패 시 기존 색인 보존). 새 공시일 수 있어 종목 단위로 지움
        col.delete(where={"stock_code": code})
        col.add(ids=[f"{no}-{i:04d}" for i in range(len(chunks))], documents=[t for _, t in chunks],
                embeddings=embs, metadatas=meta)
    await asyncio.to_thread(store)
    return f"indexed {len(chunks)}"


async def run(codes: list[str], *, force: bool = False, clear: bool = False) -> dict[str, str]:
    import os
    if clear:
        if os.path.isdir(ft.INDEX_DIR):
            import chromadb
            c = chromadb.PersistentClient(path=ft.INDEX_DIR, settings=chromadb.Settings(anonymized_telemetry=False))
            try:
                c.delete_collection(ft.COLLECTION)
            except Exception:
                pass
        logger.info("collection cleared")
        return {}
    if not settings.DART_API_KEY:
        raise SystemExit("DART_API_KEY가 설정되지 않았습니다.")
    col = await asyncio.to_thread(ft.open_collection, True)
    state = {"net": 0}
    result: dict[str, str] = {}
    for code in codes:
        try:
            result[code] = await _index_one(code, col, state, force)
        except _Skip:
            result[code] = "skipped(no report)"
        except _Fail as e:
            result[code] = f"failed({e})"
        except _Abort as e:
            result[code] = f"aborted({e})"
            logger.error(mask(f"중단: {e}"))
            break
    for c, r in result.items():
        logger.info(mask(f"{c} {r}"))
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", nargs="+", default=DEFAULT_CODES)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--clear", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    install_log_mask()
    asyncio.run(run(a.codes, force=a.force, clear=a.clear))


if __name__ == "__main__":
    main()
