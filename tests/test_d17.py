"""D17 1~2단계: 색인 스크립트·저장소·임베딩·search_filing_text. 네트워크 없음."""
import asyncio
import io
import json
import logging
import zipfile

import pytest

from backend.agent import llm
from backend.config import Settings
from backend.identity.dart import DartNetworkError
from backend.identity.models import IdentityError
from backend.tools import base, registry
from backend.tools import filing_text as ft
from scripts import build_filing_index as bld

KEY = "sekret-key"
RNO = "20260310000123"


def p(text):
    return f"<P>{text}</P>"


# ---------- 청크 ----------
def test_chunk_table_title_entity():
    long = "가" * 60
    xml = (f"<TITLE>I. 회사&amp;개요</TITLE>{p(long + ' &lt;A&gt;')}<TABLE><TR><TD>{'수치' * 40}</TD></TR></TABLE>"
           f"<TITLE>II. 위험</TITLE>{p('나' * 60)}")
    ch = bld.chunk_document(xml)
    assert [s for s, _ in ch] == ["I. 회사&개요", "II. 위험"]
    assert "<A>" in ch[0][1] and "수치" not in ch[0][1]


def test_chunk_split_sentence_and_min():
    sent = "이것은 문장입니다 " + "다" * 280 + "다."
    ch = bld.chunk_document("<TITLE>T</TITLE>" + p(" ".join([sent] * 5)))
    assert all(len(t) <= bld.CHUNK_MAX for _, t in ch) and len(ch) >= 2
    assert all(t.endswith("다.") for _, t in ch)  # 문장 경계
    hard = bld.chunk_document(p("가" * 2000))
    assert [len(t) for _, t in hard] == [800, 800, 400]


def test_chunk_min_and_max(monkeypatch):
    assert bld.chunk_document(p("가" * 49)) == []
    monkeypatch.setattr(bld, "MAX_CHUNKS", 3)
    xml = "".join(f"<TITLE>S{i}</TITLE>{p('가' * 60)}" for i in range(10))
    assert len(bld.chunk_document(xml)) == 3


def test_chunk_no_p_fallback():
    ch = bld.chunk_document("가" * 60 + "\n\n" + "나" * 60)
    assert len(ch) == 1


# ---------- 임베딩 ----------
def test_hash_embed_normalized():
    v = ft._hash_embed("삼성전자 실적 전망")
    assert len(v) == ft.HASH_DIM and abs(sum(x * x for x in v) - 1) < 1e-9
    assert ft._hash_embed("") == [0.0] * ft.HASH_DIM


def test_embed_timeout_below_tool_timeout():
    assert ft.EMBED_TIMEOUT_S < ft.TOOL_TIMEOUT_S
    assert base._TOOLS["search_filing_text"].timeout_s == ft.TOOL_TIMEOUT_S


def test_llm_embed_errors(monkeypatch):
    monkeypatch.setattr(llm, "settings", Settings(GMS_API_KEY=""))
    with pytest.raises(llm.LlmError) as e:
        asyncio.run(llm.embed(["a"]))
    assert e.value.code == "MISSING_API_KEY"


# ---------- 공통 fixture ----------
def fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0] * 8
        v[0 if "가이던스" in t else 1 if "위험" in t else 2] = 1.0
        out.append(v)
    return out


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(base, "settings", Settings(GMS_API_KEY=KEY, DART_API_KEY=KEY))
    monkeypatch.setattr(bld, "settings", Settings(DART_API_KEY=KEY))
    monkeypatch.setattr(ft, "INDEX_DIR", str(tmp_path / "idx"))
    monkeypatch.setattr(ft, "EMBED_MODEL", "fake-v1")
    monkeypatch.setattr(ft, "MIN_SCORE", 0.3)

    async def emb(texts):
        return fake_embed(texts)
    monkeypatch.setattr(ft, "embed_texts", emb)
    registry.load_all()


def make_zip(xml: str, name=RNO + ".xml", extra=None) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(name, xml.encode("utf-8"))
        for n, c in (extra or {}).items():
            z.writestr(n, c)
    return b.getvalue()


DOC = ("<TITLE>IV. 경영진단</TITLE>" + p("내년 실적 가이던스는 " + "가" * 80)
       + "<TITLE>V. 위험</TITLE>" + p("주요 위험 요인은 " + "나" * 80))


class FakeDart:
    def __init__(self, listing=None, doc=None, errs=None):
        self.calls, self.sleeps, self.throttled = [], [], []
        self.listing = listing if listing is not None else {"status": "000", "list": [
            {"corp_name": "삼성전자", "report_nm": "분기보고서 (2025.09)", "rcept_no": "20251114000001", "rcept_dt": "20251114"},
            {"corp_name": "삼성전자", "report_nm": "사업보고서 (2024.12)", "rcept_no": "20250310000001", "rcept_dt": "20250310"},
            {"corp_name": "삼성전자", "report_nm": "사업보고서 (2025.12)", "rcept_no": RNO, "rcept_dt": "20260310"}]}
        self.doc = doc if doc is not None else make_zip(DOC)
        self.errs = list(errs or [])

    async def get(self, path, params, max_bytes):
        self.calls.append((path, params))
        if self.errs:
            e = self.errs.pop(0)
            if e:
                raise e
        if path == "list.json":
            return self.listing if isinstance(self.listing, bytes) else json.dumps(self.listing).encode()
        return self.doc


@pytest.fixture
def dart(monkeypatch):
    d = FakeDart()
    install(monkeypatch, d)
    return d


def install(monkeypatch, d):
    async def sleep(s):
        d.sleeps.append(s)

    async def thr(url):
        d.throttled.append(url)
    monkeypatch.setattr(bld, "_src_get", d.get)
    monkeypatch.setattr(bld, "_sleep", sleep)
    monkeypatch.setattr(bld, "throttle", thr)
    monkeypatch.setattr(bld, "get_corp_code", lambda c: "0012")


def count():
    col = ft.open_collection()
    return col.count() if col else 0


# ---------- 스크립트 ----------
def test_index_basic(dart):
    r = asyncio.run(bld.run(["005930"]))
    assert r["005930"] == "indexed 2"
    assert [c[0] for c in dart.calls] == ["list.json", "document.xml"]
    assert dart.calls[1][1]["rcept_no"] == RNO  # 사업보고서 최신 1건
    assert dart.calls[0][1]["pblntf_ty"] == "A"
    col = ft.open_collection()
    assert col.metadata["embed_model"] == "fake-v1"
    m = col.get()["metadatas"][0]
    assert m["rcept_dt"] == "2026-03-10" and m["corp_name"] == "삼성전자" and m["stock_code"] == "005930"
    # 모든 DART 요청 전 throttle + 고정 대기
    assert len(dart.throttled) == 2 and dart.sleeps.count(bld.DART_INTERVAL_S) == 2


def test_skip_indexed_and_force(dart):
    asyncio.run(bld.run(["005930"]))
    dart.calls.clear()
    assert asyncio.run(bld.run(["005930"]))["005930"] == "skipped"
    assert dart.calls == []
    asyncio.run(bld.run(["005930"], force=True))
    assert len(dart.calls) == 2 and count() == 2  # 중복 없이 교체


def test_zip_file_selection(monkeypatch):
    d = FakeDart(doc=make_zip("<P>" + "x" * 5 + "</P>", name="other.xml",
                              extra={"big.xml": DOC, "a.txt": "zzz"}))
    install(monkeypatch, d)
    assert asyncio.run(bld.run(["005930"]))["005930"] == "indexed 2"  # {rcept}.xml 없으면 가장 큰 xml


def test_cp949_decode(monkeypatch):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(RNO + ".xml", DOC.encode("cp949"))
    install(monkeypatch, FakeDart(doc=b.getvalue()))
    assert asyncio.run(bld.run(["005930"]))["005930"] == "indexed 2"


@pytest.mark.parametrize("st,expect", [("010", "aborted"), ("011", "aborted"), ("020", "aborted"), ("013", "skipped")])
def test_document_status(monkeypatch, st, expect):
    d = FakeDart(doc=f"<result><status>{st}</status></result>".encode())
    install(monkeypatch, d)
    r = asyncio.run(bld.run(["005930", "000660"]))
    assert r["005930"].startswith(expect)
    if expect == "aborted":
        assert "000660" not in r  # 전체 중단
    else:
        assert "000660" in r


def test_list_status_abort_and_013(monkeypatch):
    d = FakeDart(listing={"status": "020", "message": "x"})
    install(monkeypatch, d)
    assert asyncio.run(bld.run(["005930", "000660"])) == {"005930": "aborted(RATE_LIMIT)"}
    d = FakeDart(listing={"status": "013"})
    install(monkeypatch, d)
    assert asyncio.run(bld.run(["005930"]))["005930"].startswith("skipped")


def test_two_transport_errors_abort(monkeypatch):
    d = FakeDart(errs=[DartNetworkError(), DartNetworkError()])
    install(monkeypatch, d)
    r = asyncio.run(bld.run(["005930", "000660", "005380"]))
    assert r["005930"] == "failed(API_ERROR)" and r["000660"].startswith("aborted")
    assert "005380" not in r and len(d.calls) == 2


def test_transport_error_not_consecutive(monkeypatch):
    d = FakeDart(errs=[DartNetworkError(), None])
    install(monkeypatch, d)
    r = asyncio.run(bld.run(["005930", "000660"]))
    assert r["005930"].startswith("failed") and r["000660"] == "indexed 2"


def test_no_report_skipped(monkeypatch):
    install(monkeypatch, FakeDart(listing={"status": "000", "list": [
        {"report_nm": "분기보고서", "rcept_no": RNO, "rcept_dt": "20260310"}]}))
    assert asyncio.run(bld.run(["005930"]))["005930"].startswith("skipped")


def test_clear(dart):
    asyncio.run(bld.run(["005930"]))
    asyncio.run(bld.run([], clear=True))
    assert ft.open_collection() is None


def test_corp_code_failure(monkeypatch):
    d = FakeDart()
    install(monkeypatch, d)

    def boom(c):
        raise IdentityError("NO_RESULT")
    monkeypatch.setattr(bld, "get_corp_code", boom)
    r = asyncio.run(bld.run(["005930"]))
    assert r["005930"].startswith("failed") and d.calls == []


def test_embed_batches(monkeypatch, dart):
    monkeypatch.setattr(bld, "EMBED_BATCH", 1)
    asyncio.run(bld.run(["005930"]))
    assert bld.EMBED_INTERVAL_S in dart.sleeps


def test_log_has_no_key(monkeypatch, dart, caplog):
    monkeypatch.setattr(bld, "get_corp_code", lambda c: "0012")
    caplog.set_level(logging.INFO)
    asyncio.run(bld.run(["005930"]))
    assert KEY not in caplog.text and "가이던스" not in caplog.text


# ---------- Tool ----------
def run_tool(**kw):
    return asyncio.run(registry.call("search_filing_text", {"query": "가이던스", **kw}))


def test_registered_local():
    t = registry.get("search_filing_text")
    assert t.kind == "local"
    assert any(d["name"] == "search_filing_text" and d["kind"] == "local" for d in registry.describe())


def test_no_index_dir_not_created(tmp_path):
    r = run_tool()
    assert r.error.code == "NO_INDEX" and not (tmp_path / "idx").exists()


def test_no_index_empty_and_mismatch(dart, monkeypatch):
    ft.open_collection(True)  # 빈 컬렉션
    assert run_tool().error.code == "NO_INDEX"
    asyncio.run(bld.run(["005930"]))
    monkeypatch.setattr(ft, "EMBED_MODEL", "other")
    r = run_tool()
    assert r.error.code == "NO_INDEX" and "다른 색인" in r.error.message


def test_stock_not_indexed(dart):
    asyncio.run(bld.run(["005930"]))
    r = run_tool(stock_code="000660")
    assert r.error.code == "NO_INDEX" and "해당 종목" in r.error.message
    assert not base.is_cacheable(r)


def test_search_ok(dart):
    asyncio.run(bld.run(["005930"]))
    r = run_tool(stock_code="005930", top_k=5)
    assert r.ok and len(r.data) == 1  # 질의 벡터와 유사한 청크만(MIN_SCORE 통과)
    h = r.data[0]
    assert "가이던스" in h["text"] and h["score"] == 1.0
    assert h["url"] == f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={RNO}"
    assert h["date"] == "2026-03-10" and h["section"] == "IV. 경영진단" and h["title"].startswith("사업보고서")
    assert r.meta.estimated is False


def test_search_sorted_and_no_stock_filter(monkeypatch, dart):
    asyncio.run(bld.run(["005930"]))
    monkeypatch.setattr(ft, "MIN_SCORE", -2)  # 전부 통과
    r = run_tool(top_k=5)
    assert [x["score"] for x in r.data] == sorted([x["score"] for x in r.data], reverse=True) and len(r.data) == 2


def test_no_result(dart):
    asyncio.run(bld.run(["005930"]))
    r = run_tool(query="전혀 무관한 질문", stock_code="005930")  # fake 임베딩 축 2 → 유사도 0
    assert r.error.code == "NO_RESULT" and base.is_cacheable(r)


def test_excerpt_len_and_url_removed(monkeypatch, dart):
    asyncio.run(bld.run(["005930"]))
    col = ft.open_collection()
    col.update(ids=[col.get()["ids"][0]], documents=["가이던스 https://evil.example/x www.a.com " + "가" * 500],
               embeddings=[fake_embed(["가이던스"])[0]])
    monkeypatch.setattr(ft, "MIN_SCORE", -2)
    for h in run_tool(top_k=5).data:
        assert len(h["text"]) <= ft.EXCERPT_MAX + 1 and "http" not in h["text"] and "www." not in h["text"]


@pytest.mark.parametrize("code", ["TIMEOUT", "RATE_LIMIT", "API_ERROR", "MISSING_API_KEY"])
def test_llm_error_mapping(monkeypatch, dart, code):
    asyncio.run(bld.run(["005930"]))

    async def boom(texts):
        raise llm.LlmError(code)
    monkeypatch.setattr(ft, "embed_texts", boom)
    assert run_tool().error.code == code


def test_no_gms_key_needed_for_local_embed(monkeypatch, dart):
    asyncio.run(bld.run(["005930"]))
    monkeypatch.setattr(base, "settings", Settings())
    assert base._TOOLS["search_filing_text"].requires_keys == ()
    r = run_tool()
    assert not (r.error and r.error.code == "MISSING_API_KEY")


def test_chromadb_missing(monkeypatch, dart):
    asyncio.run(bld.run(["005930"]))

    def imp(*a, **k):
        raise ImportError("x")
    monkeypatch.setattr(ft, "open_collection", imp)
    assert run_tool().error.code == "API_ERROR"


@pytest.mark.parametrize("raw", [{"query": ""}, {"query": "a" * 201}, {"query": "a", "stock_code": "abc"},
                                 {"query": "a", "top_k": 0}, {"query": "a", "top_k": 6}])
def test_input_validation(raw):
    assert asyncio.run(registry.call("search_filing_text", raw)).error.code == "INVALID_INPUT"


def test_cache_key_stable():
    a = registry.cache_key_for("search_filing_text", {"query": "q"})
    assert a == registry.cache_key_for("search_filing_text", {"query": "q", "top_k": 3}) and a
