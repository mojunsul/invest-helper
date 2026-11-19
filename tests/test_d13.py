import asyncio
import logging
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from backend import config, main
from backend.agent import memory
from backend.agent.nodes import respond
from backend.api import chat, logs
from backend.events import bus, logger_agent as la, stream
from backend.events.bus import Event, Final, Replan, StageEnd, StageStart, ToolCall, ToolResultPayload, publish

SID = "123e4567-e89b-42d3-a456-426614174000"
SID2 = "223e4567-e89b-42d3-a456-426614174000"


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(bus, "_subscribers", [])
    monkeypatch.setattr(bus, "_seq", {})
    monkeypatch.setattr(stream, "_sinks", {})
    monkeypatch.setattr(stream, "_installed", False)
    monkeypatch.setattr(la, "_installed", False)
    monkeypatch.setattr(main, "FRONTEND", tmp_path / "nofront")
    memory.clear_all()
    yield
    memory.clear_all()


@pytest.fixture
def client():
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c


def ev(type_, payload, seq=1, sid=SID):
    return Event(session_id=sid, seq=seq, ts=f"2024-01-01T00:00:{seq:02d}Z", type=type_, payload=payload)


def run_logs(sid=SID):
    return [r[0] for r in sqlite3.connect(config.DB_PATH).execute(
        "SELECT type FROM run_logs WHERE session_id=? ORDER BY id", (sid,))]


def full_run(sid, status="answered"):
    publish(sid, "stage_start", StageStart(stage="analyze"))
    publish(sid, "stage_end", StageEnd(stage="analyze", intent="screen", reason="r"))
    publish(sid, "final", Final(status=status))


STATE = {"status": "answered", "answer": "본문\n\n" + respond.DISCLAIMER,
         "candidates": [{"code": "005930", "name": "삼성전자"}],
         "verdicts": {"005930": {"status": "PASS", "reasons": [], "failed": []},
                      "000660": {"status": "FAIL", "reasons": ["BELOW_MIN"], "failed": ["PER"]}},
         "industry_avg": {"PER": {"value": 9.0, "n": 3}},
         "replan_log": [{"count": 1, "trigger": "no_pass", "change": "market_expand"}],
         "errors": [], "report_draft": {"005930": {"stock_code": "005930", "stock_name": "삼성전자", "metrics": {}}}}


def mock_turn(monkeypatch, state=STATE, emit_final=True, exc=None, gate=None):
    calls = []

    async def run_turn(sid, msg):
        calls.append((sid, msg))
        publish(sid, "stage_start", StageStart(stage="analyze"))
        if gate:
            await gate.wait()
        if exc:
            raise exc
        if emit_final:
            publish(sid, "final", Final(status=state["status"]))
        return state
    monkeypatch.setattr(memory, "run_turn", run_turn)
    return calls


def post(c, sid=SID, msg="안녕"):
    return c.post("/api/chat", json={"session_id": sid, "message": msg})


# ---- 기동 ----
def test_startup_order(monkeypatch):
    order = []
    for mod, name, label in [(main, "install_log_mask", "mask"), (main.registry, "load_all", "load"),
                             (main.sqlite, "init_table", "reports"), (main.logger_agent, "install", "logger"),
                             (main.stream, "install", "stream")]:
        monkeypatch.setattr(mod, name, lambda l=label: order.append(l))
    with TestClient(main.app):
        pass
    assert order == ["mask", "load", "reports", "logger", "stream"]


def test_double_startup_single_record(monkeypatch):
    mock_turn(monkeypatch)
    with TestClient(main.app):
        pass
    with TestClient(main.app) as c:
        post(c)
    assert run_logs().count("final") == 1


def test_reports_mounted_and_format(client):
    r = client.get("/api/reports/999")
    assert r.status_code == 404 and r.json()["code"] == "REPORT_NOT_FOUND"
    assert client.get("/api/reports").json() == []


# ---- chat ----
def test_chat_ok(client, monkeypatch):
    calls = mock_turn(monkeypatch)
    r = post(client, SID.upper())
    assert r.status_code == 200 and calls == [(SID, "안녕")]
    b = r.json()
    assert set(b) == {"answer", "status", "verdicts", "industry_avg", "steps", "tool_calls", "replans",
                      "disclaimer", "missing_api_key", "saveable"}
    assert b["disclaimer"] == respond.DISCLAIMER and b["status"] == "answered"
    assert b["verdicts"][0] == {"stock_code": "005930", "stock_name": "삼성전자", "status": "PASS",
                                "reasons": [], "failed": []}
    assert b["verdicts"][1]["stock_name"] == "000660"
    assert b["replans"] == STATE["replan_log"] and b["industry_avg"] == STATE["industry_avg"]
    assert b["saveable"] == [{"stock_code": "005930", "stock_name": "삼성전자"}]
    assert b["missing_api_key"] is False
    assert run_logs() == ["stage_start", "final"]  # final 정확히 1건


def test_missing_api_key_flag(client, monkeypatch):
    mock_turn(monkeypatch, {**STATE, "errors": [{"tool": "x", "stock_code": None, "code": "MISSING_API_KEY",
                                                  "message": "m"}]})
    assert post(client).json()["missing_api_key"] is True


def test_chat_exception(client, monkeypatch):
    mock_turn(monkeypatch, exc=RuntimeError("SECRET-boom"), emit_final=False)
    r = post(client)
    b = r.json()
    assert r.status_code == 200 and b["status"] == "error" and "SECRET" not in r.text
    assert b["answer"] == "요청을 처리하지 못했습니다.\n\n" + respond.DISCLAIMER
    assert b["verdicts"] == [] and b["saveable"] == [] and b["steps"][0]["stage"] == "analyze"
    assert run_logs().count("final") == 1
    assert memory.get_report_draft(SID, "005930") is None
    mock_turn(monkeypatch)
    assert post(client).status_code == 200  # 락 해제


def test_final_synthesized_from_state_status(client, monkeypatch):
    mock_turn(monkeypatch, {**STATE, "status": "limit"}, emit_final=False)
    assert post(client).json()["status"] == "limit"
    assert run_logs().count("final") == 1


def test_cancel_still_publishes_final(monkeypatch):
    with TestClient(main.app):
        async def run_turn(sid, msg):
            await asyncio.sleep(10)
        monkeypatch.setattr(memory, "run_turn", run_turn)

        async def go():
            t = asyncio.create_task(chat.chat(chat.ChatRequest(session_id=SID, message="x")))
            await asyncio.sleep(0.05)
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
        asyncio.run(go())
        assert run_logs() == ["final"]
        lk = chat._locks.get(SID)
        assert lk is None or not lk.locked()  # 취소 후 락 해제


def test_concurrency(client, monkeypatch):
    # 지연 요청을 이벤트 루프 안에서 직접 구동(TestClient 스레드는 서로 다른 루프)
    async def go():
        gate = asyncio.Event()
        mock_turn(monkeypatch, gate=gate)
        t1 = asyncio.create_task(chat.chat(chat.ChatRequest(session_id=SID, message="a")))
        await asyncio.sleep(0.05)
        r2 = await chat.chat(chat.ChatRequest(session_id=SID.upper(), message="b"))
        assert r2.status_code == 409 and r2.body == '{"code":"SESSION_BUSY","message":"이전 요청을 처리 중입니다."}'.encode()
        r3 = await asyncio.wait_for(chat.chat(chat.ChatRequest(session_id=SID2, message="c")), 0.2) \
            if False else None  # 다른 세션은 gate 공유라 아래에서 별도 확인
        gate.set()
        assert (await t1)["status"] == "answered"
        assert (await chat.chat(chat.ChatRequest(session_id=SID, message="d")))["status"] == "answered"
    with client:
        asyncio.run(go())


def test_different_sessions_parallel(client, monkeypatch):
    async def go():
        gate = asyncio.Event()
        mock_turn(monkeypatch, gate=gate)
        a = asyncio.create_task(chat.chat(chat.ChatRequest(session_id=SID, message="a")))
        b = asyncio.create_task(chat.chat(chat.ChatRequest(session_id=SID2, message="b")))
        await asyncio.sleep(0.05)
        gate.set()
        ra, rb = await asyncio.gather(a, b)
        assert ra["status"] == rb["status"] == "answered"
    asyncio.run(go())


@pytest.mark.parametrize("body", [
    {"message": "x"}, {"session_id": "abc", "message": "x"}, {"session_id": "", "message": "x"},
    {"session_id": SID}, {"session_id": SID, "message": ""}, {"session_id": SID, "message": "   "},
    {"session_id": SID, "message": "a" * 2001}, {"session_id": SID, "message": "x", "evil_field": 1}])
def test_validation_422(client, monkeypatch, body):
    calls = mock_turn(monkeypatch)
    r = client.post("/api/chat", json=body)
    assert r.status_code == 422 and set(r.json()) == {"code", "message"} and r.json()["code"] == "INVALID_REQUEST"
    assert "evil_field" not in r.text and "abc" not in r.text and calls == []


# ---- 변환 함수 ----
def test_to_steps_rounds_and_missing_end():
    evs = [ev("stage_start", {"stage": "analyze"}, 1), ev("stage_end", {"stage": "analyze", "intent": "screen", "reason": "r"}, 2),
           ev("stage_start", {"stage": "execute"}, 3), ev("stage_end", {"stage": "execute"}, 4),
           ev("replan", {"count": 1, "trigger": "no_pass", "change": "limit_up"}, 5),
           ev("stage_start", {"stage": "execute"}, 6)]
    s = chat.to_steps(evs)
    assert [(x["stage"], x["round"]) for x in s] == [("analyze", 0), ("execute", 0), ("execute", 1)]
    assert s[0]["intent"] == "screen" and s[0]["reason"] == "r" and s[0]["ended_at"] == evs[1].ts
    assert s[2]["ended_at"] is None and s[2]["seq"] == 6


def test_to_tool_calls_flatten_interleaved():
    evs = [ev("tool_call", {"tool": "a", "input": {"x": 1}, "cache_hit": False}, 1),
           ev("tool_call", {"tool": "b", "input": {}, "cache_hit": True}, 2),
           ev("replan", {}, 3),
           ev("tool_result", {"tool": "b", "ok": True, "error_code": None, "duration_ms": 5, "summary": "s"}, 4),
           ev("tool_result", {"tool": "a", "ok": False, "error_code": "TIMEOUT", "duration_ms": 9, "summary": "t"}, 5),
           ev("llm_decision", {}, 6)]
    t = chat.to_tool_calls(evs)
    assert [(x["type"], x["tool"], x["round"]) for x in t] == [("call", "a", 0), ("call", "b", 0), ("result", "b", 1), ("result", "a", 1)]
    assert t[3]["error_code"] == "TIMEOUT" and t[1]["cache_hit"] is True


# ---- logs ----
def seed(sid, n_runs, base_seq=1):
    for _ in range(n_runs):
        full_run(sid)


def test_logs_list_and_detail_runs(client):
    seed(SID, 2)
    seed(SID2, 1)
    lst = client.get("/api/logs").json()
    assert {x["session_id"]: x["event_count"] for x in lst} == {SID: 6, SID2: 3}
    d = client.get(f"/api/logs/{SID.upper()}").json()
    assert [e["run"] for e in d] == [1, 1, 1, 2, 2, 2] and set(d[0]) == {"session_id", "seq", "ts", "type", "payload", "run"}


def test_run_split_cases():
    E = lambda t, p=None: ev(t, p or {})
    S = lambda st: E("stage_start", {"stage": st})
    # final 없이 analyze 재시작
    assert [e["run"] for e in logs.split_runs([S("analyze"), S("plan"), S("analyze"), E("final")])] == [1, 1, 2, 2]
    # 합성 final 단독 + 다음 run
    assert [e["run"] for e in logs.split_runs([E("final"), S("analyze"), E("final")])] == [1, 2, 2]
    # asked: plan 이후 미실행
    assert [e["run"] for e in logs.split_runs([S("analyze"), S("plan"), E("final"), S("analyze"), E("final")])] == [1, 1, 1, 2, 2]


def test_logs_seq_reset_keeps_id_order(client):
    full_run(SID)
    bus._seq.clear()  # 재시작 시뮬레이션
    full_run(SID)
    d = client.get(f"/api/logs/{SID}").json()
    assert [e["seq"] for e in d] == [1, 2, 3, 1, 2, 3] and [e["run"] for e in d] == [1, 1, 1, 2, 2, 2]


def test_logs_404_422_and_delete_idempotent(client, monkeypatch):
    r = client.get(f"/api/logs/{SID}")
    assert r.status_code == 404 and r.json()["code"] == "SESSION_NOT_FOUND"
    assert client.get("/api/logs/not-a-uuid").status_code == 422
    seed(SID, 1)
    seed(SID2, 1)
    called = []
    monkeypatch.setattr(memory, "clear_session", lambda s: called.append(s))
    assert client.delete(f"/api/logs/{SID}").status_code == 204
    assert client.delete(f"/api/logs/{SID}").status_code == 204
    assert client.get(f"/api/logs/{SID}").status_code == 404 and len(client.get(f"/api/logs/{SID2}").json()) == 3
    assert called == []


@pytest.mark.parametrize("m", ["put", "patch", "post"])
def test_logs_modify_405(client, m):
    r = getattr(client, m)(f"/api/logs/{SID}")
    assert r.status_code == 405 and r.json()["code"] == "METHOD_NOT_ALLOWED" and set(r.json()) == {"code", "message"}


# ---- 오류·보안 ----
def test_unknown_route_json_404(client):
    r = client.get("/api/x")
    assert r.status_code == 404 and r.json() == {"code": "NOT_FOUND", "message": "요청한 경로를 찾을 수 없습니다."}


def test_internal_error_500(client, monkeypatch):
    def boom():
        raise RuntimeError("C:\\secret\\path KEY123")
    monkeypatch.setattr(la, "list_sessions", boom)
    r = client.get("/api/logs")
    assert r.status_code == 500 and r.json() == {"code": "INTERNAL_ERROR", "message": "요청을 처리하지 못했습니다."}
    assert "secret" not in r.text and "nosniff" == r.headers.get("x-content-type-options")


def test_headers_and_no_cors(client):
    r = client.get("/api/logs", headers={"Origin": "http://evil.example"})
    assert r.headers["x-content-type-options"] == "nosniff"
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "access-control-allow-origin" not in r.headers


def test_reports_error_format_unchanged(client):
    r = client.post("/api/reports", json={"session_id": SID, "stock_code": "005930"})
    assert r.status_code == 404 and r.json()["code"] == "DRAFT_NOT_FOUND"
    assert client.post("/api/reports", json={}).json()["code"] == "INVALID_REQUEST"


# ---- 정적 ----
def test_static(monkeypatch, tmp_path):
    fe = tmp_path / "fe"
    fe.mkdir()
    (fe / "index.html").write_text("<h1>i</h1>")
    (fe / "reports.html").write_text("r")
    (fe / "app.js").write_text("1")
    (tmp_path / ".env").write_text("SECRET")
    monkeypatch.setattr(main, "FRONTEND", fe)
    # mount는 import 시점이므로 새 앱을 같은 방식으로 조립
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    app = FastAPI(lifespan=main.lifespan)
    app.exception_handler(main.StarletteHTTPException)(main._http)
    app.include_router(logs.router)
    app.mount("/", StaticFiles(directory=fe, html=True))
    with TestClient(app) as c:
        assert c.get("/").status_code == 200 and c.get("/reports.html").status_code == 200
        assert c.get("/app.js").status_code == 200
        assert c.get("/api/logs").json() == []  # /api가 정적보다 우선
        for p in ["/../.env", "/%2e%2e/.env", "/..%2f.env", "/.env"]:
            r = c.get(p)
            assert r.status_code == 404 and "SECRET" not in r.text
        assert c.get("/nope.html").json()["code"] == "NOT_FOUND"


def test_startup_log_names_only(monkeypatch, caplog):
    monkeypatch.setattr(main.config, "settings", main.config.Settings(GMS_API_KEY="TOPSECRETVAL"))
    with caplog.at_level(logging.INFO):
        with TestClient(main.app):
            pass
    t = caplog.text
    assert "DATA_GO_KR_KEY" in t and "TOPSECRETVAL" not in t


# ---- 통합: 대표 요청 흐름 ----
def test_integration_multi_turn_and_save(client, monkeypatch):
    seq = iter(["answered", "answered", "answered", "asked"])

    async def run_turn(sid, msg):
        st = next(seq)
        publish(sid, "stage_start", StageStart(stage="analyze"))
        publish(sid, "stage_end", StageEnd(stage="analyze", intent="screen", reason="r"))
        publish(sid, "tool_call", ToolCall(tool="t", input={}, cache_hit=False))
        publish(sid, "tool_result", ToolResultPayload(tool="t", ok=True, duration_ms=1, summary="s"))
        if len(msg) == 1:
            publish(sid, "replan", Replan(count=1, trigger="no_pass", change="market_expand"))
        publish(sid, "final", Final(status=st))
        d = {"stock_code": "005930", "stock_name": "삼성전자", "base_date": "2024-12-31",
             "metrics": {"values": {}}, "industry_avg": {}, "disclosures": None}
        s = {"status": st, "answer": "a", "intent": "screen", "report_draft": {"005930": d}}
        memory.update_memory(sid, s)
        return s
    monkeypatch.setattr(memory, "run_turn", run_turn)
    rs = [post(client, SID, m).json() for m in ["a", "bb", "cc", "dd"]]
    assert [r["status"] for r in rs] == ["answered", "answered", "answered", "asked"]
    assert [s["round"] for s in rs[0]["steps"]] == [0] and rs[0]["tool_calls"][0]["round"] == 0
    d = client.get(f"/api/logs/{SID}").json()
    assert max(e["run"] for e in d) == 4 and [e["type"] for e in d].count("final") == 4
    for run in range(1, 5):
        assert [e["type"] for e in d if e["run"] == run].count("final") == 1
    sv = rs[0]["saveable"][0]["stock_code"]
    r = client.post("/api/reports", json={"session_id": SID, "stock_code": sv})
    assert r.status_code == 201
