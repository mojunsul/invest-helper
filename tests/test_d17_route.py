"""D17 3단계: filing_qa 라우팅·응답. LLM·Tool·resolve 전부 mock."""
import asyncio
import json

import pytest

from backend.agent import llm, memory
from backend.agent.nodes import analyze as an
from backend.agent.nodes import execute as ex
from backend.agent.nodes import plan as pl
from backend.agent.nodes import replan as rp
from backend.agent.nodes import respond as rs
from backend.events import bus
from backend.identity.models import StockMatch, StockRef
from backend.tools import registry
from backend.tools.base import fail, ok

registry.load_all()


def run(c):
    return asyncio.run(c)


def hit(n=1, corp="삼성전자", code="005930", text="내년 실적 가이던스는 매출 300조원 수준이다."):
    return {"text": text, "score": 0.8, "stock_code": code, "corp_name": corp, "title": "사업보고서 (2025.12)",
            "rcept_no": f"2026031000012{n}", "date": "2026-03-10", "section": "IV. 경영진단",
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo=2026031000012{n}"}


def E(tool, code, stock=None, msg="m"):
    return {"tool": tool, "stock_code": stock, "code": code, "message": msg}


@pytest.fixture
def env(monkeypatch):
    events, calls = [], {"llm": 0, "tool": []}
    bus._subscribers.clear()
    bus.subscribe(events.append)
    q = {"llm": [], "tool": None}

    async def fake_llm(messages, *, json_mode, timeout_s=0):
        calls["llm"] += 1
        r = q["llm"].pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    async def fake_call(name, raw):
        calls["tool"].append((name, raw))
        return q["tool"]

    def match(s):
        if s == "없음":
            return StockMatch("none", (), 0, "x")
        return StockMatch("ok", (StockRef("005930", "c", "삼성전자"),), 1, "exact")

    monkeypatch.setattr(llm, "complete", fake_llm)
    monkeypatch.setattr(registry, "call", fake_call)
    for m in (pl, ex, rs):
        monkeypatch.setattr(m, "resolve_stock", match)
    monkeypatch.setattr(an, "resolve_industry", lambda t: {"status": "ok", "prefixes": [], "label": t, "options": []})
    yield q, calls, events
    bus._subscribers.clear()


def A(intent="filing_qa", **kw):
    return json.dumps({"intent": intent, "reason": "r", **kw}, ensure_ascii=False)


def st(**kw):
    b = {"session_id": "s", "user_msg": "삼성전자 실적 가이던스 어땠어?", "intent": "filing_qa", "errors": [],
         "conditions": an.default_conditions(), "tool_cache": {}}
    b.update(kw)
    return b


# ---------- analyze ----------
def test_analyze_filing_qa(env):
    q, _, _ = env
    q["llm"].append(A(target_stock="삼성전자", industry="반도체"))
    o = run(an.analyze({"session_id": "s", "user_msg": "m"}))
    assert o["intent"] == "filing_qa" and o["missing"] == [] and "conditions" not in o and o["target_stock"] == "삼성전자"
    q["llm"].append(A())
    o = run(an.analyze({"session_id": "s", "user_msg": "m"}))
    assert o["missing"] == [] and "status" not in o and o["target_stock"] is None  # 되묻지 않음


def test_analyze_keeps_filing_qa_after_asked_lookup(env):
    q, _, _ = env
    q["llm"].append(A(target_stock="삼성전자"))
    o = run(an.analyze({"session_id": "s", "user_msg": "m", "prev_intent": "lookup", "last_status": "asked"}))
    assert o["intent"] == "filing_qa"
    q["llm"].append(A("lookup", target_stock="삼성전자"))  # 가드가 lookup 승격을 막지 않는지(기존 동작)
    o = run(an.analyze({"session_id": "s", "user_msg": "m", "prev_intent": "report_save", "last_status": "asked"}))
    assert o["intent"] == "report_save"


def test_prompt_mentions_filing_qa():
    assert "filing_qa" in an.SYSTEM_PROMPT and "삼성전자 PER 알려줘" in an.SYSTEM_PROMPT


# ---------- plan ----------
def test_plan_steps(env):
    o = run(pl.plan(st(target_stock="삼성전자")))
    assert o["plan"] == [{"tool": "search_filing_text", "input": {"query": "삼성전자 실적 가이던스 어땠어?", "stock_code": "005930"}}]
    o = run(pl.plan(st()))
    assert o["plan"] == [{"tool": "search_filing_text", "input": {"query": "삼성전자 실적 가이던스 어땠어?"}}]
    assert run(pl.plan(st(target_stock="없음")))["plan"] == []
    assert len(run(pl.plan(st(user_msg="가" * 300)))["plan"][0]["input"]["query"]) == 200


# ---------- execute / route ----------
def test_execute_stores_hits_and_summary(env):
    q, calls, events = env
    q["tool"] = ok([hit()])
    plan = [{"tool": "search_filing_text", "input": {"query": "q", "stock_code": "005930"}}]
    o = run(ex.execute(st(plan=plan)))
    assert o["filing_hits"][0]["corp_name"] == "삼성전자" and o["errors"] == []
    tr = [e for e in events if e.type == "tool_result"][0]
    assert "근거 1건" in tr.payload["summary"] and "0.80" in tr.payload["summary"] and "가이던스" not in tr.payload["summary"]


def test_execute_error_recorded(env):
    q, _, _ = env
    q["tool"] = fail("NO_INDEX", "없음")
    plan = [{"tool": "search_filing_text", "input": {"query": "q", "stock_code": "005930"}}]
    o = run(ex.execute(st(plan=plan)))
    assert o["errors"] == [E("search_filing_text", "NO_INDEX", "005930", "없음")] and "filing_hits" not in o


def test_execute_no_step_resolve_error(env):
    o = run(ex.execute(st(target_stock="없음", plan=[])))
    assert o["errors"][0]["tool"] == "resolve_stock"


def test_route_to_respond_no_judge():
    assert rp.route_after_execute(st()) == "respond"


# ---------- respond ----------
def test_finalize_status_rows():
    f = rs.finalize_status
    assert f(st(errors=[E("search_filing_text", "MISSING_API_KEY")])) == "error"
    assert f(st(errors=[E("resolve_stock", "NO_RESULT")])) == "error"
    assert f(st(errors=[E("search_filing_text", "NO_INDEX")])) == "error"
    assert f(st(errors=[E("search_filing_text", "NO_RESULT")])) == "answered"
    assert f(st(errors=[E("search_filing_text", "API_ERROR")])) == "error"
    assert f(st(filing_hits=[hit()])) == "answered"


def test_respond_no_index_target_names(env):
    plan = [{"tool": "search_filing_text", "input": {"query": "q", "stock_code": "005930"}}]
    e = [E("search_filing_text", "NO_INDEX", "005930")]
    o = run(rs.respond(st(errors=e, plan=plan)))
    assert o["status"] == "error" and o["answer"].startswith("[NO_INDEX] ‘삼성전자(005930)’의 공시 원문을 아직 모으지 못했습니다.")
    assert "python -m scripts.build_filing_index" in o["answer"] and o["answer"].endswith(rs.DISCLAIMER)
    o = run(rs.respond(st(errors=e, plan=[], target_stock="LG")))
    assert "[NO_INDEX] ‘LG’의" in o["answer"]
    o = run(rs.respond(st(errors=e, plan=[{"tool": "search_filing_text", "input": {"query": "q"}}])))
    assert "[NO_INDEX] 요청하신 내용의" in o["answer"]


def test_respond_no_result_and_other_error(env):
    o = run(rs.respond(st(errors=[E("search_filing_text", "NO_RESULT")])))
    assert o["status"] == "answered" and "찾지 못했습니다. 다른 표현으로" in o["answer"]
    o = run(rs.respond(st(errors=[E("search_filing_text", "API_ERROR", msg="장애")])))
    assert o["status"] == "error" and "공시 원문 검색에 실패했습니다: 장애" in o["answer"]


PLAN1 = [{"tool": "search_filing_text", "input": {"query": "q", "stock_code": "005930"}}]


def go(env, summary, hits=None, plan=PLAN1):
    q, calls, events = env
    if summary is not None:
        q["llm"].append(summary)
    o = run(rs.respond(st(filing_hits=hits or [hit()], plan=plan)))
    d = [e for e in events if e.type == "llm_decision"][-1].payload
    return o, d


def test_respond_answered_block_and_llm(env):
    o, d = go(env, "삼성전자는 내년 매출 300조원 수준을 제시했습니다 [1].")
    a = o["answer"]
    assert o["status"] == "answered" and d["decision"] == "llm"
    assert a.startswith("삼성전자는 내년 매출 300조원") and "근거 공시\n[1] 삼성전자 · 사업보고서 (2025.12) · 2026-03-10 · 접수번호 20260310000121" in a
    assert "섹션: IV. 경영진단" in a and "“내년 실적 가이던스는" in a
    assert "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260310000121" in a and a.endswith(rs.DISCLAIMER)
    assert "report_draft" not in o and "last_result_summary" not in o


@pytest.mark.parametrize("bad", [
    "삼성전자는 좋습니다 [2].",                    # 범위 밖 [n]
    "삼성전자는 좋습니다.",                         # 인용 없음
    "삼성전자는 매출 999조원입니다 [1].",           # 새 숫자
    "삼성전자 https://x.com [1].",                  # URL
    "회사는 좋습니다 [1].",                         # 회사명 없음
    "삼성전자 " + "가" * 400 + " [1]",              # 길이
])
def test_respond_invalid_summary_template(env, bad):
    o, d = go(env, bad)
    assert d["decision"] == "template" and d["reason"] == "invalid"
    assert o["answer"].startswith("질문과 관련된 공시 문단 1건을 찾았습니다. 아래 근거를 확인하세요.")
    assert "근거 공시" in o["answer"]


def test_respond_llm_error_template(env):
    o, d = go(env, llm.LlmError("TIMEOUT"))
    assert d["decision"] == "template" and d["reason"] == "TIMEOUT" and "근거 공시" in o["answer"]


def test_respond_multi_corp_no_llm(env):
    _, calls, _ = env
    hits = [hit(1), hit(2, "SK하이닉스", "000660"), hit(3)]
    o, d = go(env, None, hits, plan=[{"tool": "search_filing_text", "input": {"query": "q"}}])
    assert calls["llm"] == 0 and d["reason"] == "multi_corp"
    assert "3건을 찾았습니다(삼성전자, SK하이닉스)." in o["answer"]


def test_respond_corp_name_in_llm_payload_and_final_once(env, monkeypatch):
    q, calls, events = env
    seen = []

    async def spy(messages, *, json_mode, timeout_s=0):
        seen.append(json.loads(messages[1]["content"]))
        assert "각 문장에 회사명" in messages[0]["content"]
        return "삼성전자는 300조원 [1]"

    monkeypatch.setattr(llm, "complete", spy)
    run(rs.respond(st(filing_hits=[hit()], plan=PLAN1)))
    assert seen[0]["evidence"][0]["corp_name"] == "삼성전자" and seen[0]["question"]
    assert len([e for e in events if e.type == "final"]) == 1


# ---------- memory ----------
def test_memory_unchanged_for_filing_qa():
    memory.clear_all()
    memory._put("m", {**memory._default_entry(), "last_intent": "screen", "last_status": "limit"})
    memory.update_memory("m", {"intent": "filing_qa", "status": "answered", "report_draft": {"x": 1}})
    e = memory._get("m")
    assert e["last_intent"] == "screen" and e["last_status"] == "limit" and e["report_draft"] is None
    memory.clear_all()
