"""D21: 응답 스키마 문서화(openapi) 대조 + 모델↔실제 응답 적합성. 런타임 불변은 기존 테스트가 보증."""
import pytest
from fastapi.testclient import TestClient

from backend import config, main
from backend.agent import memory
from backend.events.bus import Final, StageEnd, StageStart, ToolCall, ToolResultPayload, publish
from backend import schemas as S

SID = "123e4567-e89b-42d3-a456-426614174000"
SUMMARY = {"id", "name", "stock_code", "stock_name", "base_date", "created_at", "updated_at", "review_date",
           "target_per", "target_roe"}
KEYS = {
    "ChatResponse": {"answer", "status", "verdicts", "industry_avg", "steps", "tool_calls", "replans", "disclaimer",
                     "missing_api_key", "saveable", "intent", "conditions", "metrics", "disclosures", "evidence"},
    "ReportSummary": SUMMARY, "Report": SUMMARY | {"metrics", "industry_avg", "disclosures", "disclaimer", "memo"},
    "LogEvent": {"session_id", "seq", "ts", "type", "payload", "run"},
    "LogSession": {"session_id", "started_at", "event_count"}, "SaveResult": {"id", "duplicate_of"},
    "ErrorBody": {"code", "message"},
    "ChatRequest": {"session_id", "message"}, "CreateBody": {"name", "memo", "review_date", "session_id", "stock_code"},
    "PatchBody": {"name", "memo", "review_date", "target_per", "target_roe"},
}
# (경로, 메서드): (성공코드, 성공 모델 or None, 오류코드)
ROUTES = {
    ("/api/chat", "post"): (200, "ChatResponse", {409, 422}),
    ("/api/reports", "get"): (200, "ReportSummary[]", set()),
    ("/api/reports/{report_id}", "get"): (200, "Report", {404, 422}),
    ("/api/reports", "post"): (201, "SaveResult", {404, 422}),
    ("/api/reports/{report_id}", "patch"): (200, "Report", {404, 422}),
    ("/api/reports/{report_id}", "delete"): (204, None, {404, 422}),
    ("/api/logs", "get"): (200, "LogSession[]", set()),
    ("/api/logs/{session_id}", "get"): (200, "LogEvent[]", {404, 422}),
    ("/api/logs/{session_id}", "delete"): (204, None, {422}),
}


def _ref(s):
    s = s.get("items", s)
    return s["$ref"].rsplit("/", 1)[1]


def test_openapi_matches_spec():
    o = main.app.openapi()
    sch = o["components"]["schemas"]
    for (path, m), (ok, model, errs) in ROUTES.items():
        rs = o["paths"][path][m]["responses"]
        assert {str(ok)} | {str(e) for e in errs} <= set(rs), (path, m)
        if model is None:
            assert "content" not in rs[str(ok)]
        else:
            s = rs[str(ok)]["content"]["application/json"]["schema"]
            assert _ref(s) + ("[]" if "items" in s else "") == model and s != {}
        for e in errs:
            assert _ref(rs[str(e)]["content"]["application/json"]["schema"]) == "ErrorBody"
    for name, keys in KEYS.items():
        assert set(sch[name]["properties"]) == keys, name


def _check(model, body):
    m = model.model_validate(body)
    assert set(body) == set(type(m).model_fields), model.__name__


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(main, "FRONTEND", tmp_path / "nofront")
    memory.clear_all()

    async def run_turn(sid, msg):
        publish(sid, "stage_start", StageStart(stage="analyze"))
        publish(sid, "stage_end", StageEnd(stage="analyze", intent="screen", reason="r"))
        publish(sid, "tool_call", ToolCall(tool="t", input={"a": 1}, cache_hit=False))
        publish(sid, "tool_result", ToolResultPayload(tool="t", ok=True, error_code=None, duration_ms=5, summary="s"))
        publish(sid, "final", Final(status="answered"))
        return {"status": "answered", "answer": "a", "candidates": [{"code": "005930", "name": "삼성전자"}],
                "verdicts": {"005930": {"status": "PASS"}}, "replan_log": [{"count": 1, "trigger": "t", "change": "c"}],
                "report_draft": {}}
    monkeypatch.setattr(memory, "run_turn", run_turn)
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c
    memory.clear_all()


def test_models_conform_to_real_responses(client):
    b = client.post("/api/chat", json={"session_id": SID, "message": "hi"}).json()
    _check(S.ChatResponse, b)
    for k, m in (("verdicts", S.ChatVerdict), ("steps", S.Step), ("tool_calls", S.ToolCall), ("replans", S.Replan)):
        assert b[k]
        for x in b[k]:
            m.model_validate(x)
    memory.update_memory("s1", {"intent": "screen", "status": "answered", "report_draft": {"005930": {
        "stock_code": "005930", "stock_name": "삼성전자", "base_date": "2024-12-31", "extra": 1,
        "metrics": {"values": {"PER": 1.5}, "reasons": {"X": "Y"}}, "industry_avg": {}, "disclosures": None}}})
    r = client.post("/api/reports", json={"session_id": "s1", "stock_code": "005930"})
    assert r.status_code == 201
    _check(S.SaveResult, r.json())
    rid = r.json()["id"]
    _check(S.ReportSummary, client.get("/api/reports").json()[0])
    g = client.get(f"/api/reports/{rid}").json()
    _check(S.Report, g)
    assert g["metrics"]["reasons"] == {"X": "Y"}  # 동적 구조 비필터링
    _check(S.Report, client.patch(f"/api/reports/{rid}", json={"target_per": 12}).json())
    _check(S.LogSession, client.get("/api/logs").json()[0])
    ev = client.get(f"/api/logs/{SID}").json()
    assert ev
    for e in ev:
        _check(S.LogEvent, e)
    e = client.get("/api/reports/999")
    _check(S.ErrorBody, e.json())
    _check(S.ErrorBody, client.get("/api/reports/0").json())
