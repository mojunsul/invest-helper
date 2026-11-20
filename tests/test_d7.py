import asyncio
import json
import logging

import httpx
import pytest

from backend.agent import graph, llm
from backend.agent.nodes import analyze as an
from backend.agent.nodes import plan as pl
from backend.config import Settings
from backend.events import bus
from backend.identity.models import StockMatch, StockRef
from backend.tools import base, registry
from backend.tools.financials import FinInput
from backend.tools.stock_candidates import MAX_LIMIT, CandidateInput

KEY = "sekret"


def OUT(intent="screen", **kw):
    return json.dumps({"intent": intent, "reason": "r", **kw}, ensure_ascii=False)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(monkeypatch):
    s = Settings(GMS_API_KEY=KEY)
    monkeypatch.setattr(base, "settings", s)
    monkeypatch.setattr(llm, "settings", s)
    calls = {"llm": 0, "msgs": []}
    queue = []

    async def fake(messages, *, json_mode, timeout_s=0):
        calls["llm"] += 1
        calls["msgs"].append(messages)
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(llm, "complete", fake)
    ind = {"calls": 0}

    def res(t):
        ind["calls"] += 1
        if t == "모호":
            return {"status": "ambiguous", "prefixes": [], "label": "", "options": [
                {"term": "a", "prefixes": ["1"], "n_listed": 1}, {"term": "b", "prefixes": ["2"], "n_listed": 1}]}
        if t == "???":
            return {"status": "unknown", "prefixes": [], "label": "", "options": []}
        if t == "boom":
            raise ValueError("bad terms")
        return {"status": "ok", "prefixes": ["261"], "label": t, "options": []}

    monkeypatch.setattr(an, "resolve_industry", res)
    events = []
    bus.subscribe(events.append)
    yield calls, queue, ind, events
    bus._subscribers.remove(events.append)


def st(msg="m", **kw):
    return {"session_id": "s1", "user_msg": msg, **kw}


def go(env, outs, **state):
    calls, queue, _, _ = env
    queue.extend(outs if isinstance(outs, list) else [outs])
    return run(an.analyze(st(**state)))


def cond(**kw):
    c = an.default_conditions()
    c.update(kw)
    return c


def con(m, op, v, ref=None):
    return {"metric": m, "op": op, "value": v, "ref": ref}


# ---------- analyze: 대표 요청 ----------
def test_rep1_screen_ok(env):
    r = go(env, OUT(industry="반도체", constraints=[{"metric": "ROE", "op": ">=", "value": 10},
                                                   {"metric": "PER", "op": "<=", "ref": "industry_avg"}]))
    assert r["intent"] == "screen" and r["missing"] == [] and "status" not in r
    c = r["conditions"]
    assert c["industry"] == "반도체" and c["constraints"] == [con("ROE", ">=", 10), con("PER", "<=", None, "industry_avg")]
    assert graph.route_after_analyze({**r}) == "plan"


def test_rep2_bank(env):
    r = go(env, OUT(industry="은행", constraints=[{"metric": "ROE", "value": 25}, {"metric": "DEBT_RATIO", "value": 30}]))
    assert r["conditions"]["constraints"] == [con("ROE", ">=", 25.0), con("DEBT_RATIO", "<=", 30.0)]


def test_rep4_chitchat_conditions_untouched(env):
    r = go(env, OUT("chitchat"), conditions=cond(industry="반도체"))
    assert r["intent"] == "chitchat" and "conditions" not in r and "status" not in r
    assert graph.route_after_analyze(r) == "respond"


def test_rep5_roe_high_asked_no_tool(env):
    r = go(env, OUT(pending_metrics=["ROE"]))
    assert r["status"] == "asked" and r["missing"] == ["industry", "threshold"]
    assert r["conditions"]["pending_metrics"] == ["ROE"]
    assert "ROE" in r["clarify_question"] and "산업" in r["clarify_question"]
    assert graph.route_after_analyze(r) == "respond"


# ---------- 되묻기 체인 ----------
def test_clarify_chain(env):
    r1 = go(env, OUT(pending_metrics=["ROE"]))
    r2 = go(env, OUT("chitchat", bare_number=10), conditions=r1["conditions"], prev_intent="screen", last_status="asked")
    assert r2["intent"] == "screen" and r2["missing"] == ["industry"]
    assert r2["conditions"]["constraints"] == [con("ROE", ">=", 10)] and r2["conditions"]["pending_metrics"] == []
    r3 = go(env, OUT(industry="반도체"), conditions=r2["conditions"], prev_intent="screen", last_status="asked")
    assert r3["missing"] == [] and "status" not in r3
    assert r3["conditions"]["industry"] == "반도체" and r3["conditions"]["constraints"] == [con("ROE", ">=", 10)]


def test_constraint_preserved_then_industry(env):
    r1 = go(env, OUT(constraints=[{"metric": "ROE", "op": ">=", "value": 10}]))
    assert r1["missing"] == ["industry"] and r1["status"] == "asked"
    r2 = go(env, OUT(industry="반도체"), conditions=r1["conditions"], prev_intent="screen", last_status="asked")
    assert r2["conditions"]["constraints"] == [con("ROE", ">=", 10)] and r2["missing"] == []


def test_bare_number_two_pending_asks(env):
    c = cond(industry="반도체", pending_metrics=["ROE", "PER"])
    r = go(env, OUT("screen", bare_number=10), conditions=c, prev_intent="screen", last_status="asked")
    assert r["status"] == "asked" and r["missing"] == ["threshold"]
    assert r["conditions"]["pending_metrics"] == ["ROE", "PER"] and r["conditions"]["constraints"] == []


def test_bare_number_no_pending_ignored(env):
    c = cond(industry="반도체", constraints=[con("ROE", ">=", 10)])
    r = go(env, OUT("refine", bare_number=5), conditions=c, prev_intent="screen", last_status="answered")
    assert r["conditions"]["constraints"] == [con("ROE", ">=", 10)] and r["missing"] == []


def test_pending_promote_in_refine(env):
    c = cond(industry="반도체", pending_metrics=["PER"])
    r = go(env, OUT("refine", bare_number=12), conditions=c, prev_intent="screen", last_status="answered")
    assert r["conditions"]["constraints"] == [con("PER", "<=", 12)] and r["missing"] == []


# ---------- limit 후 숫자만 ----------
def test_limit_number_one_constraint(env):
    c = cond(industry="반도체", constraints=[con("ROE", ">=", 25), ])
    r = go(env, OUT("chitchat", bare_number=15), conditions=c, prev_intent="screen", last_status="limit")
    assert r["intent"] == "refine" and r["missing"] == []
    assert r["conditions"]["constraints"] == [con("ROE", ">=", 15)]


def test_limit_number_replaces_ref(env):
    c = cond(industry="반도체", constraints=[con("PER", "<=", None, "industry_avg")])
    r = go(env, OUT("chitchat", bare_number=15), conditions=c, prev_intent="screen", last_status="limit")
    assert r["conditions"]["constraints"] == [con("PER", "<=", 15)]


def test_limit_number_two_constraints_asks(env):
    c = cond(industry="반도체", constraints=[con("ROE", ">=", 25), con("PER", "<=", 5)])
    r = go(env, OUT("chitchat", bare_number=15), conditions=c, prev_intent="screen", last_status="limit")
    assert r["intent"] == "refine" and r["status"] == "asked" and r["missing"] == ["threshold"]
    assert r["conditions"]["constraints"] == c["constraints"]


# ---------- refine / 후속 vs 새 screen ----------
def test_refine_per_only_shows_current(env):
    c = cond(industry="반도체", constraints=[con("ROE", ">=", 10), con("PER", "<=", 15)])
    r = go(env, OUT("refine", pending_metrics=["PER"]), conditions=c, prev_intent="screen", last_status="answered")
    assert r["status"] == "asked" and r["missing"] == ["threshold"]
    assert "현재 PER 상한 15" in r["clarify_question"]
    assert r["conditions"]["industry"] == "반도체"


def test_new_screen_does_not_reuse_industry(env):
    c = cond(industry="반도체", constraints=[con("ROE", ">=", 10)])
    r = go(env, OUT("screen", constraints=[{"metric": "PER", "value": 8}]), conditions=c,
           prev_intent="screen", last_status="answered")
    assert r["conditions"]["industry"] is None and r["missing"] == ["industry"]
    assert r["conditions"]["constraints"] == [con("PER", "<=", 8)]


def test_asked_then_lookup_then_new_screen_no_pollution(env):
    c = cond(industry="반도체", constraints=[con("ROE", ">=", 10)])
    r = go(env, OUT("screen", industry="은행", constraints=[{"metric": "PER", "value": 8}]),
           conditions=c, prev_intent="lookup", last_status="asked")
    assert r["conditions"]["constraints"] == [con("PER", "<=", 8)] and r["conditions"]["industry"] == "은행"


# ---------- lookup / report_save ----------
@pytest.mark.parametrize("intent", ["lookup", "report_save"])
def test_lookup_conditions_untouched(env, intent):
    r = go(env, OUT(intent, target_stock="삼성전자", industry="x"), conditions=cond(industry="반도체"))
    assert "conditions" not in r and r["target_stock"] == "삼성전자" and r["missing"] == []
    assert graph.route_after_analyze(r) == "plan"


def test_lookup_missing_target(env):
    r = go(env, OUT("lookup"))
    assert r["status"] == "asked" and r["missing"] == ["target_stock"] and r["clarify_question"] == "어느 종목인가요?"


@pytest.mark.parametrize("intent", ["lookup", "report_save"])
def test_stock_name_only_continues(env, intent):
    r = go(env, OUT("chitchat", target_stock="삼성전자"), prev_intent=intent, last_status="asked")
    assert r["intent"] == intent and r["target_stock"] == "삼성전자" and r["missing"] == []


# ---------- 산업 해석 ----------
@pytest.mark.parametrize("t,frag", [("모호", "a, b"), ("???", "인식하지 못했습니다")])
def test_industry_unresolved_asks(env, t, frag):
    r = go(env, OUT(industry=t, constraints=[{"metric": "ROE", "value": 10}]),
           conditions=cond(industry="반도체"), prev_intent="screen", last_status="asked")
    assert r["conditions"]["industry"] is None and r["missing"] == ["industry"]
    assert frag in r["clarify_question"] and r["status"] == "asked"
    assert env[0]["llm"] == 1


def test_industry_value_error(env):
    r = go(env, OUT(industry="boom"))
    assert r["status"] == "error" and r["errors"][0]["tool"] == "resolve_industry" and r["errors"][0]["code"] == "API_ERROR"


# ---------- 불량 출력 / LlmError ----------
BAD = [OUT(constraints=[{"metric": "ROE", "value": 1, "ref": "industry_avg"}]),
       OUT(constraints=[{"metric": "XXX", "value": 1}]),
       OUT(market="NASDAQ"), OUT(base_date="2024/01/01"), OUT(constraints=[{"metric": "ROE", "value": True}]),
       OUT("nonsense"), "not json", "{broken"]


@pytest.mark.parametrize("bad", BAD)
def test_invalid_twice_errors(env, bad):
    r = go(env, [bad, bad])
    assert env[0]["llm"] == 2 and r["status"] == "error"
    e = r["errors"][0]
    assert (e["tool"], e["code"], e["message"]) == ("llm", "API_ERROR", base.MSG["API_ERROR"])
    assert env[3][-1].payload["intent"] is None


def test_invalid_then_valid(env):
    r = go(env, ["garbage", "text before " + OUT("chitchat") + " after"])
    assert env[0]["llm"] == 2 and r["intent"] == "chitchat"


@pytest.mark.parametrize("code", ["MISSING_API_KEY", "TIMEOUT", "RATE_LIMIT", "API_ERROR"])
def test_llm_error_codes(env, code):
    r = go(env, [llm.LlmError(code)])
    assert env[0]["llm"] == 1 and r["status"] == "error"
    assert r["errors"][0]["code"] == code and r["errors"][0]["message"] == base.MSG[code]
    assert graph.route_after_analyze(r) == "respond"


def test_missing_key_real_complete(env, monkeypatch):
    monkeypatch.undo()  # fake 제거 후 키 없는 설정으로 실제 complete 호출(네트워크 전 차단)
    monkeypatch.setattr(llm, "settings", Settings(GMS_API_KEY=""))
    monkeypatch.setattr(base, "settings", Settings(GMS_API_KEY=""))
    r = run(an.analyze(st()))
    assert r["status"] == "error" and r["errors"][0]["code"] == "MISSING_API_KEY"


# ---------- 이벤트 / 로그 ----------
def test_events_order_payload(env, caplog):
    caplog.set_level(logging.DEBUG)
    env[1].append(OUT(industry="반도체", constraints=[{"metric": "ROE", "op": ">=", "value": 10},
                                                   {"metric": "PER", "ref": "industry_avg"}], reason="x" * 300))
    ev = env[3]
    ev.clear()
    run(an.analyze(st("비밀원문질문")))
    assert [(e.type, e.payload.get("kind") or e.payload.get("stage")) for e in ev] == [
        ("stage_start", "analyze"), ("llm_decision", "intent"), ("llm_decision", "conditions"), ("stage_end", "analyze")]
    assert ev[1].payload["decision"] == "screen" and len(ev[1].payload["reason"]) == 200
    assert ev[2].payload["decision"] == ("industry=반도체; ROE>=10; PER<=industry_avg; pending=[]; missing=[]")
    assert ev[2].payload["reason"] == "new_screen"
    assert ev[3].payload["intent"] == "screen"
    blob = json.dumps([e.payload for e in ev], ensure_ascii=False) + caplog.text
    assert KEY not in blob and "비밀원문질문" not in blob


def test_chitchat_has_no_conditions_event(env):
    go(env, OUT("chitchat"))
    assert [e.payload.get("kind") for e in env[3] if e.type == "llm_decision"] == ["intent"]


def test_prompt_has_no_prev_dialog_and_key(env):
    go(env, OUT("chitchat"), conditions=cond(industry="반도체"), prev_intent="screen", last_status="asked")
    msgs = env[0]["msgs"][0]
    assert KEY not in json.dumps(msgs)
    u = json.loads(msgs[1]["content"])
    assert set(u) == {"message", "prev_conditions", "prev_intent", "last_status"}


# ---------- plan ----------
@pytest.fixture
def penv(monkeypatch):
    monkeypatch.setattr(pl, "similar_industries", lambda t: ["262", "263"])
    monkeypatch.setattr(pl, "resolve_stock", lambda q: (
        StockMatch("ok", (StockRef("005930", "c", "삼성전자"),), 1, "exact") if q == "삼성전자"
        else StockMatch("multiple" if q == "삼성" else "none", (), 0, None)))
    registry.load_all()


def pstate(intent="screen", log=(), **c):
    return st(intent=intent, conditions=cond(industry="반도체", **c), replan_log=list(log), target_stock=None)


def rec(ch):
    return {"count": 1, "trigger": "no_pass", "change": ch}


def test_plan_initial_scope(penv):
    r = run(pl.plan(pstate(market="KOSPI")))
    assert r["search_scope"] == {"market": "KOSPI", "extra_industries": [], "candidate_limit": None}
    assert r["plan"] == [{"tool": "find_stock_candidates", "input": {"industry": "반도체", "market": "KOSPI"}}]


@pytest.mark.parametrize("log,exp", [
    (["market_expand"], (None, [], None)),
    (["similar_industry"], ("KOSPI", ["262", "263"], None)),
    (["limit_up"], ("KOSPI", [], MAX_LIMIT)),
    (["limit_up", "market_expand", "similar_industry"], (None, ["262", "263"], MAX_LIMIT)),
    (["similar_industry", "limit_up", "market_expand"], (None, ["262", "263"], MAX_LIMIT)),
])
def test_plan_scope_combos(penv, log, exp):
    r = run(pl.plan(pstate(log=[rec(x) for x in log], market="KOSPI")))
    sc = r["search_scope"]
    assert (sc["market"], sc["extra_industries"], sc["candidate_limit"]) == exp
    assert len(r["plan"]) == 1
    CandidateInput.model_validate(r["plan"][0]["input"])


def test_plan_key_omission_and_cache_key(penv):
    a = run(pl.plan(pstate()))["plan"][0]
    assert a["input"] == {"industry": "반도체"}
    assert registry.cache_key_for(a["tool"], a["input"]) == registry.cache_key_for(
        "find_stock_candidates", {"industry": "반도체", "extra_industries": None, "limit": None})
    b = run(pl.plan(pstate(log=[rec("market_expand")], market="KOSPI", base_date="2024-01-02")))["plan"][0]
    assert b["input"] == {"industry": "반도체", "base_date": "2024-01-02"}


@pytest.mark.parametrize("intent", ["screen", "refine"])
@pytest.mark.parametrize("wd", [True, False])
def test_plan_one_step_regardless_disclosures(penv, intent, wd):
    r = run(pl.plan(pstate(intent, want_disclosures=wd)))
    assert [s["tool"] for s in r["plan"]] == ["find_stock_candidates"]


def test_plan_lookup(penv):
    s = pstate("lookup", base_date="2024-01-02")
    s["target_stock"] = "삼성전자"
    r = run(pl.plan(s))
    assert r["plan"] == [{"tool": "get_financials", "input": {"stock_code": "005930", "base_date": "2024-01-02"}}]
    FinInput.model_validate(r["plan"][0]["input"])
    s["conditions"]["base_date"] = None
    assert run(pl.plan(s))["plan"][0]["input"] == {"stock_code": "005930"}
    for q in ("삼성", "없는종목"):
        s["target_stock"] = q
        assert run(pl.plan(s))["plan"] == []


def test_plan_report_save_empty_and_events(penv):
    s = pstate("report_save")
    s["target_stock"] = "삼성전자"
    bus_events = []
    bus.subscribe(bus_events.append)
    try:
        assert run(pl.plan(s))["plan"] == []
    finally:
        bus._subscribers.remove(bus_events.append)
    assert [(e.type, e.payload["stage"]) for e in bus_events] == [("stage_start", "plan"), ("stage_end", "plan")]


def test_plan_tools_registered(penv):
    for n in ("find_stock_candidates", "get_financials"):
        assert registry.get(n) is not None


# ---------- 라우팅 ----------
@pytest.mark.parametrize("status,intent,exp", [
    ("asked", "screen", "respond"), ("error", None, "respond"), ("error", "screen", "respond"),
    ("asked", "lookup", "respond"), (None, "chitchat", "respond"), (None, "screen", "plan"),
    (None, "refine", "plan"), (None, "lookup", "plan"), (None, "report_save", "plan"),
    ("answered", "screen", "plan"), ("limit", "refine", "plan"), ("asked", "chitchat", "respond")])
def test_routing(status, intent, exp):
    assert graph.route_after_analyze({"status": status, "intent": intent}) == exp


# ---------- llm.complete (httpx MockTransport) ----------
def mock_http(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


@pytest.fixture
def lenv(monkeypatch):
    s = Settings(GMS_API_KEY=KEY, GMS_BASE_URL="https://gms.test/v1")
    monkeypatch.setattr(llm, "settings", s)
    monkeypatch.setattr(base, "settings", s)


def test_complete_ok_and_request(lenv, monkeypatch):
    seen = {}

    def h(req):
        seen["url"], seen["h"], seen["body"] = str(req.url), req.headers, json.loads(req.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}]})

    mock_http(monkeypatch, h)
    assert run(llm.complete([{"role": "user", "content": "x"}], json_mode=True)) == "hi"
    assert seen["url"] == "https://gms.test/v1/messages"
    assert seen["h"]["x-api-key"] == KEY and seen["h"]["anthropic-version"] == "2023-06-01" and "authorization" not in seen["h"]
    assert seen["body"]["model"] == Settings().GMS_MODEL and seen["body"]["max_tokens"] == llm.LLM_MAX_TOKENS
    assert "response_format" not in seen["body"] and seen["body"]["system"] == llm.JSON_HINT
    run(llm.complete([], json_mode=False))
    assert "system" not in seen["body"]


def test_body_messages():
    ms = [{"role": "system", "content": "A"}, {"role": "user", "content": "u"},
          {"role": "system", "content": "B"}, {"role": "assistant", "content": "a"}]
    b = llm._body(ms, False)
    assert b["system"] == "A\n\nB" and [m["role"] for m in b["messages"]] == ["user", "assistant"]
    assert llm._body(ms, True)["system"] == "A\n\nB\n\n" + llm.JSON_HINT
    assert "system" not in llm._body([{"role": "user", "content": "u"}], False)


def test_text_extraction(lenv, monkeypatch):
    out = {"content": [{"type": "text", "text": "a"}, {"type": "tool_use", "id": "x"}, {"type": "text", "text": "b"}]}
    mock_http(monkeypatch, lambda req: httpx.Response(200, json=out))
    assert run(llm.complete([], json_mode=False)) == "ab"
    mock_http(monkeypatch, lambda req: httpx.Response(200, json={"content": []}))
    with pytest.raises(llm.LlmError) as ei:
        run(llm.complete([], json_mode=False))
    assert ei.value.code == "API_ERROR"


@pytest.mark.parametrize("status,code", [(429, "RATE_LIMIT"), (529, "API_ERROR")])
def test_anthropic_error_body(lenv, monkeypatch, caplog, status, code):
    et = "rate_limit_error" if status == 429 else "overloaded_error"
    mock_http(monkeypatch, lambda req: httpx.Response(
        status, json={"type": "error", "error": {"type": et, "message": f"m {KEY}"}}))
    with caplog.at_level(logging.WARNING), pytest.raises(llm.LlmError) as ei:
        run(llm.complete([], json_mode=True))
    assert ei.value.code == code and f"err_type={et}" in caplog.text and "err_code=None" in caplog.text
    assert KEY not in caplog.text


def test_parse_code_fence():
    o = an._parse('```json\n{"intent": "screen", "industry": "반도체"}\n```')
    assert o is not None and o.intent == "screen" and o.industry == "반도체"


@pytest.mark.parametrize("status,code", [(429, "RATE_LIMIT"), (500, "API_ERROR"), (401, "API_ERROR")])
def test_complete_http_errors(lenv, monkeypatch, caplog, status, code):
    mock_http(monkeypatch, lambda req: httpx.Response(status, text=f"body {KEY}"))
    with pytest.raises(llm.LlmError) as ei:
        run(llm.complete([], json_mode=True))
    assert ei.value.code == code and ei.value.message == base.MSG[code]
    assert KEY not in caplog.text and KEY not in str(ei.value) and ei.value.__cause__ is None


def test_complete_timeout_and_bad_body(lenv, monkeypatch, caplog):
    def boom(req):
        raise httpx.ReadTimeout(f"t {KEY}")

    mock_http(monkeypatch, boom)
    with pytest.raises(llm.LlmError) as ei:
        run(llm.complete([], json_mode=True))
    assert ei.value.code == "TIMEOUT"
    mock_http(monkeypatch, lambda req: httpx.Response(200, json={"nope": 1}))
    with pytest.raises(llm.LlmError) as ei:
        run(llm.complete([], json_mode=True))
    assert ei.value.code == "API_ERROR" and KEY not in caplog.text


def test_complete_empty_key_no_network(monkeypatch):
    monkeypatch.setattr(llm, "settings", Settings(GMS_API_KEY=""))
    mock_http(monkeypatch, lambda req: pytest.fail("network"))
    with pytest.raises(llm.LlmError) as ei:
        run(llm.complete([], json_mode=True))
    assert ei.value.code == "MISSING_API_KEY"


GMS429 = {"message": "[OpenAI 에러] Request failed with status code 429", "statusCode": 429,
          "error": {"error": {"message": f"Your organization has reached its limit {KEY}", "type": "insufficient_quota",
                              "param": None, "code": "organization_spend_limit_exceeded"}}}


def _fail(monkeypatch, status, **kw):
    mock_http(monkeypatch, lambda req: httpx.Response(status, **kw))
    with pytest.raises(llm.LlmError) as ei:
        run(llm.complete([], json_mode=True))
    return ei.value.code


def test_diag_gms_wrapped_429(lenv, monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    assert _fail(monkeypatch, 429, json=GMS429) == "RATE_LIMIT"
    for t in ("[OpenAI 에러]", "err_code=organization_spend_limit_exceeded", "err_type=insufficient_quota", "***"):
        assert t in caplog.text
    assert KEY not in caplog.text


def test_diag_gms_own_400(lenv, monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    body = {"message": "[GMS 에러] Model x is not available in Model", "statusCode": 400}
    assert _fail(monkeypatch, 400, json=body) == "API_ERROR"
    assert "[GMS 에러]" in caplog.text and "err_code=None" in caplog.text and KEY not in caplog.text


def test_diag_flat_openai_401(lenv, monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    body = {"error": {"message": "bad", "type": "invalid_request_error", "code": "invalid_api_key"}}
    assert _fail(monkeypatch, 401, json=body) == "API_ERROR"
    assert "err_code=invalid_api_key" in caplog.text and "msg=None" in caplog.text and KEY not in caplog.text


def test_diag_non_json_long_body(lenv, monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    text = "<html>" + "x" * 190 + KEY + "y" * 500
    assert _fail(monkeypatch, 502, text=text) == "API_ERROR"
    assert "raw=<html>" in caplog.text and KEY not in caplog.text
    assert len(llm._err_diag(httpx.Response(502, text=text))) <= len("raw=") + llm.DIAG_MAX
    assert len(llm._clip(text)) <= 200


def test_diag_non_dict_json(lenv, monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    assert _fail(monkeypatch, 500, json=[1, 2]) == "API_ERROR"
    assert "raw=" in caplog.text and KEY not in caplog.text


def test_diag_via_root_handler_with_mask_filter(lenv, monkeypatch):
    import io
    buf, root = io.StringIO(), logging.getLogger()
    h, lvl = logging.StreamHandler(buf), root.level
    h.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))  # basicConfig 기본 포맷
    root.addHandler(h)
    root.setLevel(logging.INFO)
    try:
        base.install_log_mask()  # 기존 모든 핸들러에 _MaskFilter 부착(무해)
        assert _fail(monkeypatch, 429, json=GMS429) == "RATE_LIMIT"
    finally:
        root.removeHandler(h)
        root.setLevel(lvl)
    out = buf.getvalue()
    for t in ("WARNING", "backend.agent.llm", "err_code=organization_spend_limit_exceeded"):
        assert t in out
    assert KEY not in out
