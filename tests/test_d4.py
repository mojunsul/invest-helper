import asyncio
from datetime import date

import httpx
import pytest

from backend.config import Settings
from backend.identity.models import IdentityError
from backend.tools import base
from backend.tools import financials as fin

KEY = "sekret"
CRNO = "1301110006246"


def frow(dcd="110", **kw):
    r = {"crno": CRNO, "bizYear": "2025", "fnclDcd": dcd, "enpSaleAmt": "100", "enpBzopPft": "10",
         "enpCrtmNpf": "-5", "enpTastAmt": "500", "enpTcptAmt": "200", "fnclDebtRto": "150.5"}
    r.update(kw)
    return r


def resp(items, rc="00"):
    return {"response": {"header": {"resultCode": rc}, "body": {"items": {"item": items} if items else ""}}}


def price(code="000001", cap="999"):
    return {"response": {"header": {"resultCode": "00"}, "body": {"totalCount": 1,
            "items": {"item": [{"srtnCd": code, "mrktTotAmt": cap}]}}}}


EMPTY_PRICE = {"response": {"header": {"resultCode": "00"}, "body": {"totalCount": 0, "items": ""}}}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(base, "settings", Settings(DATA_GO_KR_KEY=KEY))
    monkeypatch.setattr(fin, "settings", Settings(DATA_GO_KR_KEY=KEY))
    monkeypatch.setattr(fin, "_today", lambda: date(2026, 11, 19))
    monkeypatch.setattr(fin, "get_crno", lambda c: CRNO)

    async def p(d, c):
        return price(c)
    monkeypatch.setattr(fin, "_fetch_price", p)


def fetch(monkeypatch, by_year):
    calls = []

    async def f(crno, y):
        calls.append(y)
        r = by_year(y) if callable(by_year) else by_year.get(y, resp(None))
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(fin, "_fetch_fin", f)
    return calls


def run(**kw):
    return asyncio.run(fin.get_financials.run({"stock_code": "000001", **kw}))


def test_ok_consolidated_first(monkeypatch):
    fetch(monkeypatch, {2025: resp([frow("120", enpSaleAmt="1"), frow("110")])})
    r = run()
    assert r.ok and not r.meta.estimated and r.meta.actual_basis == "FY2025" and r.meta.requested_basis == "bizYear=2025"
    assert r.data == {"code": "000001", "fiscal_period": "FY2025", "revenue": 100, "op_profit": 10,
                      "net_income": -5, "assets": 500, "equity": 200, "debt_ratio": 150.5, "mkt_cap": 999}


def test_separate_fallback_single_dict(monkeypatch):
    fetch(monkeypatch, {2025: resp(frow("120"))})
    assert run().ok


def test_fallback_prev_year_estimated(monkeypatch):
    calls = fetch(monkeypatch, {2024: resp([frow(bizYear="2024")])})
    r = run()
    assert calls == [2025, 2024] and r.meta.estimated and r.data["fiscal_period"] == "FY2024"


def test_base_date_year_and_both_empty(monkeypatch):
    calls = fetch(monkeypatch, {})
    r = run(base_date="2025-06-30")
    assert calls == [2024, 2023] and r.error.code == "NO_FINANCIALS" and base.is_cacheable(r)


def test_all_core_none_and_partial(monkeypatch):
    fetch(monkeypatch, {2025: resp([frow(**{k: "" for k in fin._CORE})])})
    assert run().error.code == "NO_FINANCIALS"
    fetch(monkeypatch, {2025: resp([frow(enpSaleAmt="", fnclDebtRto=None)])})
    r = run()
    assert r.ok and r.data["revenue"] is None and r.data["debt_ratio"] is None


def test_ignores_other_crno(monkeypatch):
    fetch(monkeypatch, {2025: resp([frow(crno="999")])})
    assert run().error.code == "NO_FINANCIALS"


def test_mkt_cap_lookback_and_mismatch(monkeypatch):
    fetch(monkeypatch, {2025: resp([frow()])})
    seen = []

    async def mismatch(d, c):
        seen.append(d)
        return price("999999")
    monkeypatch.setattr(fin, "_fetch_price", mismatch)
    assert run().data["mkt_cap"] is None and len(seen) == fin.sc.LOOKBACK_DAYS
    seen.clear()

    async def holiday(d, c):
        seen.append(d)
        return EMPTY_PRICE if len(seen) < 3 else price(c, "77")
    monkeypatch.setattr(fin, "_fetch_price", holiday)
    assert run().data["mkt_cap"] == 77 and seen[:2] == ["20261119", "20261118"]


def test_price_failure_swallowed_but_timeout_propagates(monkeypatch):
    fetch(monkeypatch, {2025: resp([frow()])})

    async def bad(d, c):
        raise httpx.ConnectError("x")
    monkeypatch.setattr(fin, "_fetch_price", bad)
    r = run()
    assert r.ok and r.data["mkt_cap"] is None

    async def to(d, c):
        raise httpx.ReadTimeout("x")
    monkeypatch.setattr(fin, "_fetch_price", to)
    assert run().error.code == "TIMEOUT"


def test_identity(monkeypatch):
    fetch(monkeypatch, {})

    def g(code):
        raise IdentityError("NO_RESULT")
    monkeypatch.setattr(fin, "get_crno", g)
    assert run().error.code == "NO_FINANCIALS"

    def g2(code):
        raise IdentityError("CACHE_NOT_READY")
    monkeypatch.setattr(fin, "get_crno", g2)
    assert run().error.code == "API_ERROR"


@pytest.mark.parametrize("rc,code", [("30", "MISSING_API_KEY"), ("22", "RATE_LIMIT"), ("99", "API_ERROR")])
def test_result_codes(monkeypatch, rc, code):
    fetch(monkeypatch, {2025: resp(None, rc)})
    r = run()
    assert r.error.code == code and KEY not in r.error.message


def test_http_and_timeout(monkeypatch):
    fetch(monkeypatch, lambda y: httpx.ReadTimeout("t"))
    assert run().error.code == "TIMEOUT"
    req = httpx.Request("GET", "http://x")
    fetch(monkeypatch, lambda y: httpx.HTTPStatusError("e", request=req, response=httpx.Response(429, request=req)))
    assert run().error.code == "RATE_LIMIT"
    fetch(monkeypatch, {2025: {"bad": 1}})
    assert run().error.code == "API_ERROR"


def test_invalid_input(monkeypatch):
    fetch(monkeypatch, {})
    assert run(stock_code="abc").error.code == "INVALID_INPUT"
    assert run(base_date="2025/01/01").error.code == "INVALID_INPUT"
    assert run(base_date="2025-02-30").error.code == "INVALID_INPUT"
