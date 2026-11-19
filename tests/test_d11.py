import asyncio
import inspect
import sqlite3
import threading
from urllib.parse import quote

import pytest

from backend import config
from backend.events import bus, logger_agent as la, stream
from backend.events.bus import Final, LlmDecision, StageStart, ToolCall
from backend.tools import base
from backend.tools.base import Settings

KEY = "abc/def+ghi=="


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(base, "settings", Settings(DATA_GO_KR_KEY=KEY))
    monkeypatch.setattr(bus, "_subscribers", [])
    monkeypatch.setattr(bus, "_seq", {})
    monkeypatch.setattr(stream, "_sinks", {})
    monkeypatch.setattr(stream, "_installed", False)
    monkeypatch.setattr(la, "_installed", False)
    la.install()
    stream.install()


def rows():
    return sqlite3.connect(config.DB_PATH).execute(
        "SELECT session_id, seq, ts, type, payload_json FROM run_logs ORDER BY id").fetchall()


# ---- stream ----
def test_collect_order_isolation_and_removal():
    with stream.collect("a") as evs:
        bus.publish("a", "stage_start", StageStart(stage="analyze"))
        bus.publish("b", "stage_start", StageStart(stage="plan"))
        bus.publish("a", "final", Final(status="answered"))
    bus.publish("a", "final", Final(status="answered"))
    assert [e.type for e in evs] == ["stage_start", "final"] and [e.seq for e in evs] == [1, 2]
    assert "a" not in stream._sinks


def test_sink_exception_isolated():
    def boom(ev):
        raise RuntimeError

    stream._add("a", boom)
    with stream.collect("a") as evs:
        bus.publish("a", "final", Final(status="answered"))
    assert len(evs) == 1


def test_install_idempotent():
    stream.install()
    la.install()
    assert bus._subscribers.count(stream._dispatch) == 1 and bus._subscribers.count(la.record) == 1
    bus.publish("a", "final", Final(status="answered"))
    assert len(rows()) == 1


def test_sse_format():
    ev = bus.publish("a", "final", Final(status="answered"))
    s = stream.sse_format(ev)
    assert s == f"id: 1\nevent: final\ndata: {ev.model_dump_json()}\n\n"


def test_stream_after_open_final_closes_and_thread_publish():
    async def go():
        bus.publish("a", "stage_start", StageStart(stage="analyze"))  # 열기 전
        got = []
        with stream.open_stream("a") as s:
            t = threading.Thread(target=lambda: (
                bus.publish("a", "stage_start", StageStart(stage="plan")),
                bus.publish("a", "final", Final(status="answered"))))
            t.start()
            async for ev in s:
                got.append(ev.type)
            t.join()
        return got

    assert asyncio.run(go()) == ["stage_start", "final"]
    assert "a" not in stream._sinks


def test_stream_closed_loop_swallowed():
    loop = asyncio.new_event_loop()

    async def mk():
        return stream.open_stream("a")

    s = loop.run_until_complete(mk())
    loop.close()
    with stream.collect("a") as evs:
        bus.publish("a", "final", Final(status="answered"))
    assert len(evs) == 1
    s.close()
    s.close()


# ---- logger ----
@pytest.mark.parametrize("form", [KEY, quote(KEY, safe=""), KEY.upper(), quote(KEY, safe="").lower()])
def test_mask_key_forms(form):
    bus.publish("a", "tool_call", ToolCall(tool="t", input={"q": f"x{form}y", "n": [form]}, cache_hit=False))
    bus.publish("a", "llm_decision", LlmDecision(kind="intent", decision=form, reason=f"https://x?k={form}"))
    for r in rows():
        assert form not in r[4] and "***" in r[4]


def test_record_mapping_korean_and_get_session():
    ev = bus.publish("a", "llm_decision", LlmDecision(kind="intent", decision="추천", reason="이유"))
    r = rows()[0]
    assert r[:4] == ("a", 1, ev.ts, "llm_decision") and "추천" in r[4]
    assert la.get_session("a") == [ev] and la.get_session("none") == []


def test_seq_reset_and_order():
    bus.publish("a", "stage_start", StageStart(stage="analyze"))
    bus.publish("a", "final", Final(status="answered"))
    bus._seq.clear()
    bus.publish("a", "stage_start", StageStart(stage="plan"))
    assert [e.seq for e in la.get_session("a")] == [1, 2, 1]


def test_list_sessions_and_delete():
    bus.publish("a", "stage_start", StageStart(stage="analyze"))
    bus.publish("b", "stage_start", StageStart(stage="analyze"))
    bus.publish("b", "final", Final(status="answered"))
    ls = la.list_sessions()
    assert [x["session_id"] for x in ls] == ["b", "a"] and ls[0]["event_count"] == 2
    assert la.delete_session("b") == 2 and la.delete_session("b") == 0
    assert len(la.get_session("a")) == 1


def test_record_db_failure_swallowed(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", "/nonexistent_dir/x/y.db")
    bus.publish("a", "final", Final(status="answered"))  # 예외 없이 반환


def test_no_llm_import_no_update_fn():
    src = inspect.getsource(la)
    assert "openai" not in src.lower() and "llm" not in src.lower().replace("llm_decision", "")
    assert not [n for n in dir(la) if n.startswith(("update", "edit", "modify"))]
    assert "UPDATE " not in src


def test_integration_collect_equals_get_session():
    with stream.collect("a") as evs:
        bus.publish("a", "stage_start", StageStart(stage="analyze"))
        bus.publish("a", "tool_call", ToolCall(tool="t", input={}, cache_hit=True))
        bus.publish("a", "final", Final(status="answered"))
    assert la.get_session("a") == evs
    assert sum(e.type == "final" for e in evs) == 1
