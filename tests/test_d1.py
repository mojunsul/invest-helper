import asyncio
import logging
from urllib.parse import quote

import httpx
import pytest
from pydantic import BaseModel

from backend.config import Settings
from backend.tools import base, registry
from backend.tools.base import (ToolError, ToolFailure, ToolResult, fail, is_cacheable, mask, ok,
                                register)

KEY = "ab/c+d=="


class In(BaseModel):
    industry: str
    market: str | None = None


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(base, "settings", Settings(DATA_GO_KR_KEY=KEY))
    monkeypatch.setattr(base, "_TOOLS", {})


def mk(fn, **kw):
    base._TOOLS.clear()
    return register(name="t", description="d", input_model=In, **kw)(fn)


def run(t, raw=None, **kw):
    return asyncio.run(t.run({"industry": "x"} if raw is None else raw, **kw))


def code(r):
    return r.error.code


def test_exception_mapping():
    def raiser(e):
        async def f(inp):
            raise e
        return f
    req = httpx.Request("GET", "http://x")
    st = lambda n: httpx.HTTPStatusError("x", request=req, response=httpx.Response(n, request=req))
    cases = [(asyncio.TimeoutError(), "TIMEOUT"), (httpx.ReadTimeout("x"), "TIMEOUT"),
             (st(429), "RATE_LIMIT"), (st(500), "API_ERROR"),
             (httpx.ConnectError("x"), "API_ERROR"), (ValueError("x"), "API_ERROR"),
             (ToolFailure("NO_RESULT", "없음"), "NO_RESULT"), (ToolFailure("BOGUS", "m"), "API_ERROR")]
    for e, c in cases:
        assert code(run(mk(raiser(e)))) == c


def test_validation_and_keys():
    async def f(inp):
        return ok([1])
    assert code(run(mk(f), {})) == "INVALID_INPUT"
    t = mk(f, requires_keys=("DART_API_KEY",))
    assert code(run(t, {})) == "MISSING_API_KEY"  # 키 검사가 입력 검증보다 앞
    with pytest.raises(ValueError):
        register(name="u", description="d", input_model=In, requires_keys=("NOPE",))(f)
    mk(f)
    with pytest.raises(ValueError):  # 중복
        register(name="t", description="d", input_model=In)(f)


def test_timeout_priority_and_invalid_return():
    async def slow(inp):
        await asyncio.sleep(0.5)
        return ok([1])
    assert code(run(mk(slow), timeout_s=0.01)) == "TIMEOUT"
    assert code(run(mk(slow, timeout_s=0.01))) == "TIMEOUT"

    async def bad(inp):
        return ToolResult.model_construct(ok=True, data=None, error=None)
    assert code(run(mk(bad))) == "API_ERROR"

    async def notres(inp):
        return {"ok": True}
    assert code(run(mk(notres))) == "API_ERROR"


def test_cancel_propagates():
    async def f(inp):
        await asyncio.sleep(5)

    async def main():
        task = asyncio.create_task(mk(f).run({"industry": "x"}))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(main())


def test_mask_and_fail():
    for form in (KEY, quote(KEY, safe=""), quote(KEY, safe="").lower()):
        assert KEY not in mask(f"u?k={form}") and "***" in mask(f"u?k={form}")
    assert mask("abc") == "abc"
    assert fail("API_ERROR", f"bad {KEY}").error.message == base.MSG["API_ERROR"]
    assert fail("NO_RESULT", f"bad {KEY}").error.code == "NO_RESULT"

    async def direct(inp):
        return ToolResult(ok=False, error=ToolError(code="NO_RESULT", message=KEY))

    async def tf(inp):
        raise ToolFailure("NO_RESULT", KEY)
    for f in (direct, tf):
        r = run(mk(f))
        assert code(r) == "NO_RESULT" and KEY not in r.error.message


def test_log_mask_idempotent():
    lg = logging.getLogger("zz.child")
    lg.propagate = False
    seen = []
    h = logging.Handler()
    h.emit = lambda rec: seen.append(h.format(rec))
    lg.addHandler(h)
    base.install_log_mask()
    base.install_log_mask()
    assert sum(isinstance(f, base._MaskFilter) for f in h.filters) == 1
    try:
        raise ValueError(KEY)
    except ValueError:
        lg.error("k=%s", KEY, exc_info=True)
    assert seen and all(KEY not in s for s in seen)
    lg.removeHandler(h)


def test_registry_and_cache():
    async def f(inp):
        return ok([1])
    mk(f)
    d = registry.describe()
    assert set(d[0]) == {"name", "description", "input_schema"}
    assert registry.get("t").fn is f
    assert registry.cache_key_for("t", {"industry": "a"}) == registry.cache_key_for(
        "t", {"industry": "a", "market": None, "extra": 1})
    assert registry.cache_key_for("t", {}) is None and registry.cache_key_for("nope", {}) is None
    assert code(asyncio.run(registry.call("nope", {}))) == "INVALID_INPUT"
    assert asyncio.run(registry.call("t", {"industry": "a"})).ok
    assert is_cacheable(ok([1])) and is_cacheable(fail("NO_RESULT", "x")) and not is_cacheable(fail("TIMEOUT", "x"))
    registry.load_all()
