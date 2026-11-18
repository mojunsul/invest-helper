import pytest
from pydantic import ValidationError

from backend.agent.state import AgentState  # noqa: F401  (import check)
from backend.config import Settings, _replan_max, settings
from backend.events import bus
from backend.tools.base import ToolError, ToolResult, cache_key, is_cacheable


def err(code):
    return ToolError(code=code, message="x")


def test_toolresult_invariants():
    ToolResult(ok=True, data=[1])
    ToolResult(ok=False, error=err("NO_RESULT"))
    for bad in (dict(ok=True), dict(ok=True, data=1, error=err("API_ERROR")),
                dict(ok=False), dict(ok=False, data=[], error=err("API_ERROR"))):
        with pytest.raises(ValidationError):
            ToolResult(**bad)
    assert ToolResult(ok=True, data=1).meta.estimated is False


def test_cache():
    assert cache_key("t", {"b": 1, "a": "한"}) == 't:{"a": "한", "b": 1}'
    assert is_cacheable(ToolResult(ok=True, data=1))
    assert is_cacheable(ToolResult(ok=False, error=err("NO_FINANCIALS")))
    assert is_cacheable(ToolResult(ok=False, error=err("NO_RESULT")))
    assert not is_cacheable(ToolResult(ok=False, error=err("TIMEOUT")))


def test_bus():
    got = []
    bus.subscribe(lambda e: 1 / 0)  # 예외 삼킴 확인
    bus.subscribe(got.append)
    bus.publish("s1", "stage_start", bus.StageStart(stage="analyze"))
    bus.publish("s1", "final", bus.Final(status="answered"))
    bus.publish("s2", "final", bus.Final(status="error"))
    assert [(e.session_id, e.seq) for e in got] == [("s1", 1), ("s1", 2), ("s2", 1)]
    assert got[0].ts.endswith("Z") and got[0].payload == {"stage": "analyze"}


def test_config():
    assert [_replan_max(x) for x in ("0", "9", "2", "abc", None)] == [1, 3, 2, 3, 3]
    s = Settings(DATA_GO_KR_KEY="SECRETKEY")
    assert "SECRETKEY" not in repr(s) and "SECRETKEY" not in str(s)
    assert 1 <= settings.REPLAN_MAX <= 3
