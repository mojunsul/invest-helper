import asyncio
from datetime import date

import httpx
import pytest

from backend.config import Settings
from backend.identity.models import IdentityError
from backend.tools import base
from backend.tools import stock_candidates as sc

KEY = "sekret"
STOCKS = [{"stock_code": c, "name": f"n{c}", "induty_code": "26110"} for c in ("000001", "000002", "000003", "000004")]


def row(code, cap, mk="KOSPI", price="100"):
    return {"srtnCd": code, "mrktCtg": mk, "clpr": price, "mrktTotAmt": str(cap)}


def page(items, total=None):
    return {"response": {"header": {"resultCode": "00"},
                         "body": {"totalCount": len(items) if total is None else total,
                                  "items": {"item": items} if items else ""}}}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(base, "settings", Settings(DATA_GO_KR_KEY=KEY))
    monkeypatch.setattr(sc, "settings", Settings(DATA_GO_KR_KEY=KEY))
    monkeypatch.setattr(sc, "_today", lambda: date(2025, 1, 6))
    monkeypatch.setattr(sc, "resolve_industry", lambda t: {"status": "ok", "prefixes": ["261"], "label": t, "options": []}
                        if t != "?" else {"status": "unknown", "prefixes": [], "label": "", "options": []})
    monkeypatch.setattr(sc, "select_by_industry", lambda p: list(STOCKS))


def fetch(monkeypatch, pages):
    calls = []

    async def f(d, p, rows):
        calls.append((d, p))
        r = pages(d, p) if callable(pages) else pages
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(sc, "_fetch_page", f)
    return calls


def run(**kw):
    return asyncio.run(sc.find_stock_candidates.run({"industry": "반도체", **kw}))


def test_ok_filter_sort_limit(monkeypatch):
    fetch(monkeypatch, page([row("000001", 5), row("000002", 9), row("000003", 9, "KOSDAQ"), row("999999", 99),
                             row("000002", 1), row("000004", 0)]))
    r = run()
    assert [c["code"] for c in r.data] == ["000002", "000003", "000001"]  # 동률 code순, 0·타산업 제외, dedupe
    assert r.data[0] == {"code": "000002", "name": "n000002", "market": "KOSPI", "industry": "26110",
                         "mkt_cap": 9, "price": 100}
    assert r.meta.actual_basis == "2025-01-06" and not r.meta.estimated
    assert [c["code"] for c in run(market="KOSDAQ").data] == ["000003"]
    assert len(run(limit=1).data) == 1


def test_lookback_and_all_fail(monkeypatch):
    calls = fetch(monkeypatch, lambda d, p: page([row("000001", 5)]) if d == "20250103" else page([]))
    r = run()
    assert r.meta.actual_basis == "2025-01-03" and r.meta.requested_basis is None and len(calls) == 4
    fetch(monkeypatch, page([]))
    assert run().error.code == "API_ERROR"


def test_multipage_single_dict(monkeypatch):
    monkeypatch.setattr(sc, "PAGE", 1)
    fetch(monkeypatch, lambda d, p: page([row("00000%d" % p, p)], total=2))
    assert len(run().data) == 2
    fetch(monkeypatch, {"response": {"header": {"resultCode": "00"},
                                     "body": {"totalCount": 1, "items": {"item": row("000001", 5)}}}})
    assert len(run().data) == 1


def test_errors(monkeypatch):
    fetch(monkeypatch, page([row("000001", 5)]))
    assert run(industry="?").error.code == "INVALID_INPUT"
    assert run(extra_industries=["?"]).error.code == "INVALID_INPUT"
    for kw in ({"limit": 0}, {"limit": 101}, {"base_date": "2025/01/01"}):
        assert run(**kw).error.code == "INVALID_INPUT"
    assert run(market="KOSDAQ").error.code == "NO_RESULT"
    monkeypatch.setattr(sc, "select_by_industry", lambda p: [])
    assert run().error.code == "NO_RESULT"

    def boom(p):
        raise IdentityError("API_ERROR", "캐시 없음")
    monkeypatch.setattr(sc, "select_by_industry", boom)
    assert run().error.code == "API_ERROR"


def test_api_codes(monkeypatch):
    def rc(c):
        return {"response": {"header": {"resultCode": c}, "body": {}}}
    for c, e in (("30", "MISSING_API_KEY"), ("22", "RATE_LIMIT"), ("99", "API_ERROR")):
        fetch(monkeypatch, rc(c))
        assert run().error.code == e
    req = httpx.Request("GET", "http://x")
    fetch(monkeypatch, httpx.HTTPStatusError("x", request=req, response=httpx.Response(429, request=req)))
    assert run().error.code == "RATE_LIMIT"
    fetch(monkeypatch, httpx.ReadTimeout("x"))
    assert run().error.code == "TIMEOUT"
    fetch(monkeypatch, {"bad": 1})
    r = run()
    assert r.error.code == "API_ERROR" and KEY not in r.error.message
