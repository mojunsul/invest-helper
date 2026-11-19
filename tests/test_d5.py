import asyncio
from datetime import date

import httpx
import pytest

from backend.config import Settings
from backend.identity.models import IdentityError
from backend.tools import base
from backend.tools import disclosures as d

KEY = "sekret"


def row(no="20260101000001", dt="20260101", nm="분기보고서", **kw):
    return {"corp_code": "00126380", "stock_code": "000001", "report_nm": nm, "rcept_no": no, "rcept_dt": dt, **kw}


def resp(rows, st="000"):
    return {"status": st, "list": rows}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(base, "settings", Settings(DART_API_KEY=KEY))
    monkeypatch.setattr(d, "settings", Settings(DART_API_KEY=KEY))
    monkeypatch.setattr(d, "_today", lambda: date(2026, 11, 19))
    monkeypatch.setattr(d, "get_corp_code", lambda c: "00126380")


def fetch(monkeypatch, r):
    seen = []

    async def f(corp, bgn, n):
        seen.append((corp, bgn, n))
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(d, "_fetch_list", f)
    return seen


def run(**kw):
    return asyncio.run(d.search_disclosures.run({"stock_code": "000001", **kw}))


def test_ok_sorted_limited_and_title(monkeypatch):
    seen = fetch(monkeypatch, resp([row("20260101000001", "20260101", " 가\n  나\t"),
                                    row("20260301000002", "20260301", "B"),
                                    row("20260201000003", "20260201", "C" * 150)]))
    r = run(limit=2)
    assert seen == [("00126380", "20260523", 2)]
    assert [x["date"] for x in r.data] == ["2026-03-01", "2026-02-01"]
    assert r.data[0] == {"title": "B", "date": "2026-03-01",
                         "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260301000002"}
    assert r.data[1]["title"] == "C" * 100 + "…"
    fetch(monkeypatch, resp([row(nm=" 가\n  나\t")]))
    assert run().data[0]["title"] == "가 나"


def test_bad_rows_excluded_and_no_result(monkeypatch):
    fetch(monkeypatch, resp([row(no="123"), row(dt="20261340"), row(dt="2026-1-1"), row(nm="  "),
                             row(corp_code="999"), row(stock_code="999999"), "x"]))
    r = run()
    assert r.error.code == "NO_RESULT" and base.is_cacheable(r)
    fetch(monkeypatch, resp([row(), row(dt="20260230")]))
    assert len(run().data) == 1


@pytest.mark.parametrize("js,code", [
    (resp([], "013"), "NO_RESULT"), (resp([]), "NO_RESULT"), ({"status": "000"}, "NO_RESULT"),
    (resp([], "010"), "MISSING_API_KEY"), (resp([], "011"), "MISSING_API_KEY"),
    (resp([], "020"), "RATE_LIMIT"), (resp([], "800"), "API_ERROR"),
    ({}, "API_ERROR"), ([], "API_ERROR"), ({"status": "000", "list": "x"}, "API_ERROR")])
def test_status_mapping(monkeypatch, js, code):
    fetch(monkeypatch, js)
    r = run()
    assert r.error.code == code
    assert base.is_cacheable(r) == (code == "NO_RESULT")


def test_http_errors(monkeypatch):
    fetch(monkeypatch, httpx.ReadTimeout("t"))
    assert run().error.code == "TIMEOUT"
    req = httpx.Request("GET", "http://x")
    fetch(monkeypatch, httpx.HTTPStatusError("e", request=req, response=httpx.Response(429, request=req)))
    assert run().error.code == "RATE_LIMIT"
    fetch(monkeypatch, httpx.HTTPStatusError("e", request=req, response=httpx.Response(500, request=req)))
    assert run().error.code == "API_ERROR"


def test_identity_errors(monkeypatch):
    fetch(monkeypatch, resp([row()]))

    def boom(code):
        def f(c):
            raise IdentityError(code)
        return f
    monkeypatch.setattr(d, "get_corp_code", boom("NO_RESULT"))
    assert run().error.code == "NO_RESULT"
    monkeypatch.setattr(d, "get_corp_code", boom("API_ERROR"))
    assert run().error.code == "API_ERROR"


def test_input_and_key(monkeypatch):
    fetch(monkeypatch, resp([row()]))
    for kw in ({"limit": 0}, {"limit": 11}, {"stock_code": "abc"}):
        assert asyncio.run(d.search_disclosures.run({"stock_code": "000001", **kw})).error.code == "INVALID_INPUT"
    monkeypatch.setattr(base, "settings", Settings(DART_API_KEY=""))
    assert run().error.code == "MISSING_API_KEY"


class _Stop(Exception):
    pass


def test_throttle_applied_before_http(monkeypatch):
    async def fake(url):
        raise _Stop(url)
    monkeypatch.setattr(d, "throttle", fake)
    with pytest.raises(_Stop) as e:
        asyncio.run(d._fetch_list("c", "20240101", 1))
    assert e.value.args[0] == d.URL
