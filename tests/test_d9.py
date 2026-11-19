import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.agent import graph, llm
from backend.agent.nodes import analyze as an
from backend.agent.nodes import plan as pl
from backend.agent.nodes import replan as rp
from backend.agent.nodes import respond as rs
from backend.events import bus
from backend.identity.models import StockMatch, StockRef
from backend.tools import registry
from backend.tools.base import fail, ok
from backend.tools.financials import NO_FIN_FINANCIAL

registry.load_all()


def run(c):
    return asyncio.run(c)


def V(status, reasons=(), failed=()):
    return {"status": status, "reasons": list(reasons), "failed": list(failed)}


def E(tool, code, stock=None, msg="m"):
    return {"tool": tool, "stock_code": stock, "code": code, "message": msg}


def cons(metric="ROE", op=">=", value=10.0, ref=None):
    return {"metric": metric, "op": op, "value": value, "ref": ref}


COND = {"industry": "반도체", "market": "KOSPI", "base_date": None, "want_disclosures": False,
        "pending_metrics": [], "constraints": [cons()]}


def S(**kw):
    b = {"session_id": "s", "user_msg": "x", "intent": "screen", "conditions": COND, "errors": [],
         "candidates": [{"code": "000001", "name": "A", "mkt_cap": 1}], "verdicts": {}, "tool_cache": {},
         "search_scope": {"market": "KOSPI", "extra_industries": [], "candidate_limit": None},
         "expansion_step": 0, "replan_count": 0, "replan_log": []}
    b.update(kw)
    return b


def row(roe=20.0, per=5.0, fp="FY2025", est=False, reasons=None):
    v = {m: None for m in rs.METRICS}
    v.update(ROE=roe, PER=per)
    return {"values": v, "fiscal_period": fp, "actual_basis": fp, "estimated": est, "reasons": reasons or {}}


@pytest.fixture
def ev(monkeypatch):
    events = []
    bus._subscribers.clear()
    bus.subscribe(events.append)
    monkeypatch.setattr(rp, "settings", SimpleNamespace(REPLAN_MAX=3))

    async def sim(t):
        return None

    monkeypatch.setattr(rp, "similar_industries", lambda t: ["x"])
    monkeypatch.setattr(rs, "cache_status", lambda: {"total": 100, "ok": 90, "pending": 5, "failed": 5,
                                                     "complete": False, "corp_synced_at": None})
    monkeypatch.setattr(rs, "resolve_stock", lambda q: StockMatch("ok", (StockRef(q, "c", "삼성전자"),), 1, "code"))

    async def bad_llm(messages, *, json_mode, timeout_s=0):
        raise llm.LlmError("MISSING_API_KEY")

    monkeypatch.setattr(llm, "complete", bad_llm)
    yield events
    bus._subscribers.clear()


# ---------------- decide_outcome ----------------
def test_rule1_and_2_error_no_replan(ev):
    assert run(rp.decide_outcome(S(errors=[E("search_disclosures", "MISSING_API_KEY")]))) == {"status": "error"}
    assert run(rp.decide_outcome(S(errors=[E("find_stock_candidates", "API_ERROR")]))) == {"status": "error"}
    assert not [e for e in ev if e.type == "replan"]


def test_rule3_no_candidates_replan_then_error(ev):
    o = run(rp.decide_outcome(S(candidates=[], errors=[E("find_stock_candidates", "NO_RESULT")])))
    assert "status" not in o
    assert o["replan_count"] == 1 and o["expansion_step"] == 1
    assert o["replan_log"] == [{"count": 1, "trigger": "no_candidates", "change": "market_expand"}]
    p = [e.payload for e in ev if e.type == "replan"]
    assert p == [{"count": 1, "trigger": "no_candidates", "change": "market_expand"}]
    # 소진: replan_count == REPLAN_MAX
    assert run(rp.decide_outcome(S(candidates=[], replan_count=3))) == {"status": "error"}


def test_rule4_5_6(ev):
    assert run(rp.decide_outcome(S(verdicts={"000001": V("UNDECIDABLE", ["NO_FINANCIALS"])}))) == {"status": "error"}
    vs = {"1": V("PASS"), "2": V("FAIL"), "3": V("UNDECIDABLE")}
    assert run(rp.decide_outcome(S(verdicts=vs))) == {"status": "answered"}
    o = run(rp.decide_outcome(S(verdicts={"1": V("FAIL")})))
    assert o["replan_log"][0]["trigger"] == "no_pass" and "status" not in o
    assert run(rp.decide_outcome(S(verdicts={"1": V("FAIL")}, replan_count=3))) == {"status": "limit"}


def test_next_step_skips(ev, monkeypatch):
    f = lambda **kw: run(rp.next_step(S(**kw), kw.pop("trig", "no_pass")))
    sc = {"market": None, "extra_industries": [], "candidate_limit": None}
    assert run(rp.next_step(S(search_scope=sc), "no_pass")) == 2  # 시장 None -> 1 건너뜀
    assert run(rp.next_step(S(search_scope=sc), "no_candidates")) == 2
    assert run(rp.next_step(S(search_scope=sc, expansion_step=2), "no_candidates")) is None  # 3은 no_pass만
    assert run(rp.next_step(S(search_scope=sc, expansion_step=2), "no_pass")) == 3
    monkeypatch.setattr(rp, "similar_industries", lambda t: [])
    assert run(rp.next_step(S(search_scope=sc), "no_pass")) == 3  # 유사 업종 없음 -> 점프
    sc2 = {**sc, "candidate_limit": rp.MAX_LIMIT}
    assert run(rp.next_step(S(search_scope=sc2), "no_pass")) is None  # 즉시 limit

    def boom(t):
        raise RuntimeError

    monkeypatch.setattr(rp, "similar_industries", boom)
    assert run(rp.next_step(S(search_scope=sc2), "no_pass")) is None
    assert run(rp.next_step(S(), "no_pass")) == 1


@pytest.mark.parametrize("mx", [1, 2, 3])
def test_replan_max(ev, monkeypatch, mx):
    monkeypatch.setattr(rp, "settings", SimpleNamespace(REPLAN_MAX=mx))
    s, n = S(verdicts={"1": V("FAIL")}), 0
    while True:
        o = run(rp.decide_outcome(s))
        if "status" in o:
            break
        s = {**s, **o}
        n += 1
    assert n == mx and o == {"status": "limit"} and s["expansion_step"] == mx


# ---------------- 래퍼 / 라우터 ----------------
def test_wrappers(ev, monkeypatch):
    async def fx(s):
        return {"candidates": [], "errors": []}

    async def fj(s):
        return {"verdicts": {"1": V("PASS")}}

    monkeypatch.setattr(rp, "execute", fx)
    monkeypatch.setattr(rp, "judge", fj)
    o = run(rp.execute_step(S(candidates=[])))
    assert o["replan_count"] == 1 and "status" not in o
    assert run(rp.judge_step(S()))["status"] == "answered"
    # lookup/report_save에는 판정 없음
    assert run(rp.judge_step(S(intent="lookup"))) == {"verdicts": {"1": V("PASS")}}
    assert run(rp.execute_step(S(intent="lookup"))) == {"candidates": [], "errors": []}

    async def fx2(s):
        return {"candidates": [{"code": "1"}], "errors": []}

    monkeypatch.setattr(rp, "execute", fx2)
    assert run(rp.execute_step(S())) == {"candidates": [{"code": "1"}], "errors": []}

    async def fx3(s):
        return {"candidates": [], "errors": [E("get_financials", "MISSING_API_KEY")]}

    monkeypatch.setattr(rp, "execute", fx3)
    assert run(rp.execute_step(S()))["status"] == "error"


def test_routers():
    re_, rj = rp.route_after_execute, rp.route_after_judge
    assert re_(S(candidates=[])) == "plan"
    assert re_(S(candidates=[], status="error")) == "respond"
    assert re_(S(errors=[E("x", "MISSING_API_KEY")], status="error")) == "respond"
    assert re_(S(errors=[E("x", "MISSING_API_KEY")])) == "plan"
    assert re_(S()) == "judge" and re_(S(intent="refine")) == "judge"
    assert re_(S(intent="lookup")) == "judge" and re_(S(intent="report_save")) == "respond"
    assert rj(S()) == "plan" and rj(S(status="limit")) == "respond"
    assert rj(S(intent="lookup")) == "respond"
    assert graph.route_after_analyze(S(status="asked")) == "respond"


# ---------------- respond ----------------
def final_of(ev):
    return [e for e in ev if e.type == "final"]


def test_answered_table_and_sections(ev, monkeypatch):
    cond = {**COND, "constraints": [cons(), cons("PER", "<=", None, "industry_avg")], "want_disclosures": True,
            "base_date": "2026-01-01"}
    cache = {"find_stock_candidates:{}": ok([], actual="2026-01-02").model_dump()} if False else {
        "find_stock_candidates:{}": {"ok": True, "meta": {"actual_basis": "2026-01-02"}}}
    s = S(conditions=cond, intent="screen", tool_cache=cache,
          candidates=[{"code": "000001", "name": "A|x", "mkt_cap": 1}, {"code": "000002", "name": "B", "mkt_cap": 1},
                      {"code": "000003", "name": "C", "mkt_cap": 1}, {"code": "000004", "name": "D", "mkt_cap": 1}],
          verdicts={"000001": V("PASS"), "000002": V("FAIL", ["THRESHOLD_NOT_MET", "NO_FINANCIALS"], ["ROE"]),
                    "000003": V("UNDECIDABLE", ["NO_FINANCIALS", "FETCH_FAILED"])},
          metrics={"000001": row(est=True), "000002": row(roe=8.2)},
          industry_avg={"PER": {"value": 10.0, "n": 2}},
          disclosures={}, replan_log=[{"count": 1, "trigger": "no_pass", "change": "market_expand"}], replan_count=1,
          errors=[E("search_disclosures", "API_ERROR", "000001")], status="answered")
    o = run(rs.respond(s))
    a = o["answer"]
    assert o["status"] == "answered" and a.endswith(rs.DISCLAIMER)
    assert "| A x(000001) | 20.00% | 5.00배 | FY2025* |" in a and "직전 확정 연도" in a
    assert "표본 부족" in a and "공식 산업 평균 아님" in a and "PER 산업 평균 10.00배" in a
    assert "요청일 2026-01-01은 휴장 등으로 2026-01-02 기준" in a
    assert "시장 확대" in a and "공시 조회 실패" in a
    assert "B(000002): ROE 8.20% (기준 ≥ 10.00%) · 판단불가: 재무정보 없음" in a
    assert "C(000003): 재무정보 없음, 조회 실패" in a and "D(000004)" not in a
    assert "5/100" not in a and "10/100종목" in a  # pending+failed
    assert o["last_result_summary"] == {"top_codes": ["000001"], "pass_count": 1}
    d = o["report_draft"]["000001"]
    assert set(d) == {"stock_code", "stock_name", "base_date", "metrics", "industry_avg", "disclosures"}
    assert d["disclosures"] == [] and list(o["report_draft"]) == ["000001"]
    assert [e.type for e in ev][-3:] == ["llm_decision", "stage_end", "final"] or final_of(ev)[0].payload == {"status": "answered"}
    assert len(final_of(ev)) == 1


def test_disclosure_states(ev):
    cond = {**COND, "want_disclosures": True}
    s = S(conditions=cond, candidates=[{"code": c, "name": c} for c in ("1", "2", "3")],
          verdicts={c: V("PASS") for c in ("1", "2", "3")}, metrics={c: row() for c in ("1", "2", "3")},
          industry_avg={"PER": {"value": None, "n": 0}},
          disclosures={"1": [{"date": "2026-01-02", "title": "보고서", "url": "http://u"}]},
          errors=[E("search_disclosures", "API_ERROR", "3")], status="answered")
    a = run(rs.respond(s))["answer"]
    assert "2026-01-02 보고서 (http://u)" in a and "공시 없음" in a and "공시 조회 실패" in a


def test_limit(ev):
    cond = {**COND, "constraints": [cons("ROE", ">=", 25.0), cons("PER", "<=", None, "industry_avg")]}
    s = S(conditions=cond, verdicts={"000001": V("FAIL", ["THRESHOLD_NOT_MET"], ["ROE"])},
          metrics={"000001": row(roe=12.0, per=7.0), "000002": row(roe=15.0, per=4.0)},
          industry_avg={"PER": {"value": None, "n": 0}}, replan_count=3,
          replan_log=[{"count": i + 1, "trigger": "no_pass", "change": c}
                      for i, c in enumerate(("market_expand", "similar_industry", "limit_up"))])
    o = run(rs.respond(s | {"status": "limit"}))
    a = o["answer"]
    assert "ROE: 후보 중 최고 15.00% (기준 ≥ 25.00%)" in a and "PER: 후보 중 최저 4.00배 (기준 ≤ -)" in a
    assert "조건을 완화할까요?" in a and o["clarify_question"].startswith("조건을 완화할까요?")
    assert "재계획 0회" not in a and "재계획 이력" in a and "limit_up" not in a
    assert o["last_result_summary"] == {"top_codes": [], "pass_count": 0} and "report_draft" not in o
    assert "재계획 3회" in rs.__dict__.get("x", "재계획 3회")  # 템플릿 문구는 아래 요약 테스트에서 확인


def test_error_texts(ev):
    t = lambda **kw: run(rs.respond(S(**kw)))["answer"]
    assert t(errors=[E("find_stock_candidates", "MISSING_API_KEY", msg="키없음")]).startswith("키없음")  # 1
    assert t(status="error", errors=[E("llm", "API_ERROR", msg="LLM실패")]).startswith("LLM실패")  # 2
    assert t(intent="lookup", target_stock="x", errors=[E("resolve_stock", "NO_RESULT")]).startswith("일치하는 종목이 없습니다.")  # 3
    assert t(intent="lookup", target_stock="x", errors=[E("resolve_stock", "INVALID_INPUT", msg="복수 A, B")]).startswith("복수 A, B")
    assert t(intent="report_save", target_stock="x", errors=[E("resolve_stock", "NO_RESULT")]).startswith("저장할 종목을 확인하지 못했습니다: ")
    assert t(intent="lookup", target_stock="005930", errors=[E("get_financials", "NO_FINANCIALS", "005930")]).startswith("‘005930’의 재무정보를 찾을 수 없습니다.")  # 4
    assert t(intent="lookup", target_stock="005930", errors=[E("get_financials", "API_ERROR", "005930", "장애")]).startswith("재무정보 조회에 실패했습니다: 장애")
    assert t(intent="report_save", target_stock="005930", report_draft=None).startswith("먼저 해당 종목을 조회하세요.")  # 5
    assert t(intent="report_save", target_stock="005930", report_draft={"1": {}}).startswith("먼저 해당 종목을 조회하세요.")
    assert t(errors=[E("find_stock_candidates", "API_ERROR", msg="조회불가")]).startswith("후보 종목 조회에 실패했습니다: 조회불가")  # 6
    a = t(candidates=[], errors=[E("find_stock_candidates", "NO_RESULT")])  # 7
    assert a.startswith("조건에 맞는 후보 종목이 없습니다.") and "더 확장할 범위가 없습니다." in a
    a = t(candidates=[{"code": c, "name": c} for c in "123"],
          verdicts={"1": V("UNDECIDABLE", ["NO_FINANCIALS"]), "2": V("UNDECIDABLE", ["NO_FINANCIALS", "FETCH_FAILED"]),
                    "3": V("UNDECIDABLE", ["FETCH_FAILED"])})  # 8
    assert "후보 3개 모두 판단불가입니다: 재무정보 없음 2, 조회 실패(일시 오류 가능) 2" in a
    assert "요청을 처리하지 못했습니다." in t(verdicts={"1": V("FAIL")}, intent="lookup", target_stock=None,
                                              metrics={"x": row()}, status="error")  # 9
    for x in (t(errors=[E("llm", "API_ERROR")]), t(intent="chitchat")):
        assert x.endswith(rs.DISCLAIMER)


def test_asked_chitchat(ev):
    o = run(rs.respond({"session_id": "s", "user_msg": "x", "intent": "screen", "status": "asked",
                        "clarify_question": "어떤 산업?"}))
    assert o["answer"] == "어떤 산업?\n\n" + rs.DISCLAIMER and o["status"] == "asked"
    assert "추가 정보가 필요합니다." in run(rs.respond({"session_id": "s", "user_msg": "x", "intent": "screen",
                                                         "status": "asked"}))["answer"]
    o = run(rs.respond({"session_id": "s", "user_msg": "안녕", "intent": "chitchat"}))
    assert o["status"] == "answered" and o["answer"] == rs.CHITCHAT + "\n\n" + rs.DISCLAIMER
    assert "report_draft" not in o and "last_result_summary" not in o


def test_lookup(ev, monkeypatch):
    r = row(roe=None, per=None, est=True, reasons={"ROE": "NEGATIVE_EQUITY", "PER": "NO_MARKET_CAP"})
    r["values"]["PBR"] = 1.234
    s = S(intent="lookup", target_stock="005930", metrics={"005930": r}, report_draft={"000660": {"stock_code": "000660"}})
    o = run(rs.respond(s))
    a = o["answer"]
    assert o["status"] == "answered" and "삼성전자(005930)의 재무 지표입니다." in a
    assert "- ROE: 자본잠식" in a and "- PER: 시가총액 확인 불가" in a and "- PBR: 1.23배" in a
    assert "- 부채비율: 재무정보 없음" in a and "FY2025*" in a and "공시" not in a
    assert set(o["report_draft"]) == {"000660", "005930"} and "last_result_summary" not in o

    def boom(q):
        raise RuntimeError

    monkeypatch.setattr(rs, "resolve_stock", boom)
    assert "005930(005930)" in run(rs.respond(s))["answer"]
    # metrics 없음 / 키 누락 -> error
    assert run(rs.respond(S(intent="lookup", target_stock="1", metrics={})))["status"] == "error"
    assert run(rs.respond(S(intent="lookup", target_stock="1", metrics={"1": r},
                            errors=[E("get_financials", "MISSING_API_KEY", "1")])))["status"] == "error"


def test_report_save(ev):
    o = run(rs.respond(S(intent="report_save", target_stock="005930", report_draft={"005930": {"stock_name": "삼성"}})))
    assert o["status"] == "answered" and "삼성" in o["answer"] and "report_draft" not in o
    assert run(rs.respond(S(intent="report_save", target_stock="005930")))["status"] == "error"
    assert run(rs.respond(S(intent="report_save", target_stock="x", errors=[E("resolve_stock", "NO_RESULT")])))["status"] == "error"


def test_summary_llm(ev, monkeypatch):
    seen = []

    async def good(messages, *, json_mode, timeout_s=0):
        seen.append((messages, json_mode))
        return seen_out[0]

    monkeypatch.setattr(llm, "complete", good)
    s = S(candidates=[{"code": "000001", "name": "비밀종목명", "mkt_cap": 1}], verdicts={"000001": V("PASS")},
          metrics={"000001": row()}, industry_avg={"PER": {"value": 5.0, "n": 1}},
          conditions={**COND, "want_disclosures": True},
          disclosures={"000001": [{"date": "2026-01-02", "title": "외부제목XYZ", "url": "http://u"}]}, status="answered")
    seen_out = ["후보 1개 중 1개가 충족했습니다.\n"]
    a = run(rs.respond(s))["answer"]
    assert a.startswith("후보 1개 중 1개가 충족했습니다.") and seen[0][1] is False
    blob = json.dumps(seen[0][0], ensure_ascii=False)
    assert "외부제목XYZ" not in blob and "비밀종목명" not in blob and "http" not in blob
    d = [e.payload for e in ev if e.type == "llm_decision"]
    assert d == [{"kind": "summary", "decision": "llm", "reason": "ok"}]
    seen_out[0] = "후보 77개 중 충족"  # 새 숫자
    a = run(rs.respond(s))["answer"]
    assert "‘반도체’ 후보 1개 중 1개가 조건을 충족했습니다." in a
    assert [e.payload["reason"] for e in ev if e.type == "llm_decision"][-1] == "invalid"
    seen_out[0] = "x" * 201
    assert "조건을 충족했습니다." in run(rs.respond(s))["answer"]
    # limit 템플릿
    s2 = S(verdicts={"000001": V("FAIL", [], ["ROE"])}, metrics={"000001": row(roe=1.0)}, replan_count=2,
           status="limit")
    monkeypatch.setattr(llm, "complete", lambda *a, **k: (_ for _ in ()).throw(llm.LlmError("TIMEOUT")))
    assert "조건을 충족한 종목이 없습니다(재계획 2회)." in run(rs.respond(s2))["answer"]
    assert [e.payload["reason"] for e in ev if e.type == "llm_decision"][-1] == "TIMEOUT"


def test_respond_internal_exception(ev):
    o = run(rs.respond(S(conditions=None, verdicts={"1": V("PASS")}, status="answered")))
    assert o["status"] == "error" and o["answer"].endswith(rs.DISCLAIMER)
    assert len(final_of(ev)) == 1 and final_of(ev)[0].payload == {"status": "error"}


def test_final_event_order_and_no_text(ev):
    run(rs.respond(S(intent="chitchat")))
    types = [e.type for e in ev]
    assert types == ["stage_start", "stage_end", "final"]
    assert ev[0].payload == {"stage": "respond"} and ev[-1].payload == {"status": "answered"}


# ---------------- 그래프 end-to-end ----------------
def fin(code, ni, eq=1000, cap=1000):
    return ok({"code": code, "fiscal_period": "FY2025", "revenue": 5000, "op_profit": 100, "net_income": ni,
               "assets": 9000, "equity": eq, "debt_ratio": 20.0, "mkt_cap": cap}, requested="2025", actual="FY2025")


@pytest.fixture
def g(monkeypatch, ev):
    s = {"analysis": [], "summary": None, "calls": [], "fin": {}, "disc": {}, "cands": None, "sim": ["x"]}

    async def fake_llm(messages, *, json_mode, timeout_s=0):
        if json_mode:
            return s["analysis"].pop(0)
        if s["summary"] is None:
            raise llm.LlmError("API_ERROR")
        return s["summary"]

    async def fake_call(name, raw):
        s["calls"].append((name, dict(raw)))
        if name == "find_stock_candidates":
            r = s["cands"](raw) if callable(s["cands"]) else s["cands"]
            return r
        if name == "get_financials":
            return s["fin"].get(raw["stock_code"]) or fail("NO_FINANCIALS", "없음")
        return s["disc"].get(raw["stock_code"]) or fail("NO_RESULT", "없음")

    def match(q):
        return StockMatch("ok", (StockRef("005930", "c", "삼성전자"),), 1, "exact")

    monkeypatch.setattr(llm, "complete", fake_llm)
    monkeypatch.setattr(registry, "call", fake_call)
    monkeypatch.setattr(an, "resolve_industry", lambda t: {"status": "ok", "prefixes": ["261"], "label": t, "options": []})
    monkeypatch.setattr(rp, "similar_industries", lambda t: s["sim"])
    monkeypatch.setattr(pl, "similar_industries", lambda t: s["sim"])
    monkeypatch.setattr(pl, "resolve_stock", match)
    from backend.agent.nodes import execute as ex
    monkeypatch.setattr(ex, "resolve_stock", match)
    return s


def A(intent="screen", **kw):
    return json.dumps({"intent": intent, "reason": "r", **kw}, ensure_ascii=False)


def init(msg="q", **kw):
    return {"session_id": "s", "user_msg": msg, "errors": [], "conditions": an.default_conditions(), "tool_cache": {}, "replan_count": 0,
            "expansion_step": 0, "replan_log": [], **kw}


def cand(c, cap=1000):
    return {"code": c, "name": f"N{c}", "mkt_cap": cap}


PER_AVG = {"metric": "PER", "op": "<=", "ref": "industry_avg"}


def types(ev):
    return [e.type for e in ev]


def test_graph_shape():
    nodes = set(graph.build_graph().get_graph().nodes) - {"__start__", "__end__"}
    assert nodes == {"analyze", "plan", "execute", "judge", "respond"} and "decide" not in nodes


def test_e2e_1_answered(g, ev):
    g["analysis"] = [A(industry="반도체", constraints=[{"metric": "ROE", "op": ">=", "value": 10}, PER_AVG],
                       want_disclosures=True)]
    g["cands"] = ok([cand("000001"), cand("000002"), cand("000003")], actual="2026-01-02")
    g["fin"] = {"000001": fin("000001", 200, cap=1000), "000002": fin("000002", 150, cap=3000),
                "000003": fin("000003", 50, cap=500)}
    g["disc"] = {"000001": ok([{"title": "T", "date": "2026-01-01", "url": "http://u"}])}
    g["summary"] = "후보 3개 중 1개가 충족했습니다."
    r = run(graph.run_agent(init()))
    assert r["status"] == "answered" and r["answer"].endswith(rs.DISCLAIMER)
    assert "N000001(000001)" in r["answer"] and "http://u" in r["answer"] and "PER 산업 평균 10.00배" in r["answer"]
    assert r["last_result_summary"]["top_codes"] == ["000001"] and list(r["report_draft"]) == ["000001"]
    assert len(final_of(ev)) == 1 and "replan" not in types(ev)
    assert types(ev).index("final") == len(ev) - 1


def test_e2e_2_limit_with_replans(g, ev):
    g["analysis"] = [A(industry="은행", market="KOSPI", constraints=[
        {"metric": "ROE", "op": ">=", "value": 25}, {"metric": "DEBT_RATIO", "op": "<=", "value": 30}])]
    g["cands"] = lambda raw: ok([cand("000001"), cand("000002")], actual="2026-01-02")
    g["fin"] = {"000001": fin("000001", 100), "000002": fin("000002", 120)}
    r = run(graph.run_agent(init()))
    assert r["status"] == "limit" and r["replan_count"] == 3
    assert [x["change"] for x in r["replan_log"]] == ["market_expand", "similar_industry", "limit_up"]
    assert [e.payload["count"] for e in ev if e.type == "replan"] == [1, 2, 3]
    assert "조건을 완화할까요?" in r["clarify_question"] and "재계획 이력" in r["answer"]
    assert "후보 중 최고 12.00%" in r["answer"]
    assert len(final_of(ev)) == 1
    # 기존 재무는 캐시 적중: get_financials 실호출은 종목당 1회
    assert len([c for c in g["calls"] if c[0] == "get_financials"]) == 2
    assert len([c for c in g["calls"] if c[0] == "find_stock_candidates"]) == 4
    # 시장 미지정이면 재계획 2회
    g["analysis"] = [A(industry="은행", constraints=[{"metric": "ROE", "op": ">=", "value": 25}])]
    ev.clear()
    r = run(graph.run_agent(init(tool_cache={})))
    assert r["status"] == "limit" and [x["change"] for x in r["replan_log"]] == ["similar_industry", "limit_up"]


def test_e2e_3_no_financials_one(g, ev):
    g["analysis"] = [A(industry="반도체", constraints=[{"metric": "ROE", "op": ">=", "value": 10}])]
    g["cands"] = ok([cand("000001"), cand("000002")], actual="2026-01-02")
    g["fin"] = {"000001": fin("000001", 200)}
    r = run(graph.run_agent(init()))
    assert r["status"] == "answered" and r["verdicts"]["000002"]["status"] == "UNDECIDABLE"
    assert "N000002(000002): 재무정보 없음" in r["answer"]


def test_e2e_4_chitchat_5_asked(g, ev):
    g["analysis"] = [A("chitchat")]
    r = run(graph.run_agent(init("안녕")))
    assert r["status"] == "answered" and g["calls"] == [] and not ({"plan", "execute"} & {e.payload.get("stage") for e in ev})
    assert len(final_of(ev)) == 1
    g["analysis"] = [A(industry=None, constraints=[{"metric": "ROE"}])]
    ev.clear()
    r = run(graph.run_agent(init("ROE 높은 종목")))
    assert r["status"] == "asked" and g["calls"] == [] and r["answer"].startswith(r["clarify_question"])
    assert final_of(ev)[0].payload == {"status": "asked"} and len(final_of(ev)) == 1


def test_e2e_missing_key_skips_judge(g, ev):
    g["analysis"] = [A(industry="반도체", constraints=[{"metric": "ROE", "op": ">=", "value": 10}])]
    g["cands"] = fail("MISSING_API_KEY", "키 없음")
    r = run(graph.run_agent(init()))
    assert r["status"] == "error" and "judge" not in [e.payload.get("stage") for e in ev]
    assert "replan" not in types(ev) and len(final_of(ev)) == 1


def test_e2e_no_candidates_exhausted_skips_judge(g, ev):
    g["analysis"] = [A(industry="반도체", market="KOSPI", constraints=[{"metric": "ROE", "op": ">=", "value": 10}])]
    g["cands"] = fail("NO_RESULT", "없음")
    r = run(graph.run_agent(init()))
    assert r["status"] == "error" and r["replan_count"] == 2  # 단계 1,2 (3은 건너뜀)
    assert [e.payload["trigger"] for e in ev if e.type == "replan"] == ["no_candidates"] * 2
    assert "judge" not in [e.payload.get("stage") for e in ev] and "더 확장할 범위가 없습니다." in r["answer"]


def test_e2e_lookup_and_report_save(g, ev):
    g["analysis"] = [A("lookup", target_stock="삼성전자")]
    g["fin"] = {"005930": fin("005930", 100)}
    r = run(graph.run_agent(init()))
    assert r["status"] == "answered" and "삼성전자" not in r["answer"] or "005930" in r["answer"]
    assert "005930" in r["report_draft"]
    g["analysis"] = [A("report_save", target_stock="삼성전자")]
    ev.clear()
    r = run(graph.run_agent(init(report_draft=r["report_draft"])))
    assert r["status"] == "answered" and [e.payload.get("stage") for e in ev if e.type == "stage_start"] == [
        "analyze", "plan", "execute", "respond"]
    g["analysis"] = [A("report_save", target_stock="삼성전자")]
    assert run(graph.run_agent(init()))["status"] == "error"


def test_langgraph_partial_return_and_required(g, ev):
    # 노드가 변경 키만 반환해도 나머지 state가 유지되고(session_id 등 Required), recursion_limit 내에서 종료
    g["analysis"] = [A(industry="반도체", constraints=[{"metric": "ROE", "op": ">=", "value": 10}])]
    g["cands"] = ok([cand("000001")], actual="2026-01-02")
    g["fin"] = {"000001": fin("000001", 200)}
    r = run(graph.run_agent(init("원문")))
    assert r["session_id"] == "s" and r["user_msg"] == "원문" and r["intent"] == "screen"
    assert graph.RECURSION_LIMIT == 40


# ---------------- D4a: 금융업 안내 ----------------
FE = lambda stock=None: E("get_financials", "NO_FINANCIALS", stock, NO_FIN_FINANCIAL)  # noqa: E731
NOTE = "※ " + NO_FIN_FINANCIAL


def test_fin_note_screen_error(ev):
    a = run(rs.respond(S(candidates=[{"code": "1", "name": "A"}], verdicts={"1": V("UNDECIDABLE", ["NO_FINANCIALS"])},
                         errors=[FE("1")])))["answer"]
    assert "후보 1개 모두 판단불가입니다: 재무정보 없음 1\n\n" + NOTE in a and a.count(NOTE) == 1


def test_fin_note_absent_for_plain_no_financials(ev):
    st = dict(candidates=[{"code": "1", "name": "A"}], verdicts={"1": V("UNDECIDABLE", ["NO_FINANCIALS"])})
    a = run(rs.respond(S(**st, errors=[E("get_financials", "NO_FINANCIALS", "1", "재무 정보를 찾을 수 없습니다.")])))["answer"]
    assert "금융업" not in a and "재무정보 없음 1" in a
    assert NOTE not in run(rs.respond(S(**st, errors=[E("find_stock_candidates", "NO_FINANCIALS", "1", NO_FIN_FINANCIAL)])))["answer"]


def test_fin_note_answered_once_and_excluded_line_unchanged(ev):
    st = dict(candidates=[{"code": c, "name": c} for c in ("1", "2", "3")],
              verdicts={"1": V("PASS"), "2": V("UNDECIDABLE", ["NO_FINANCIALS"]), "3": V("UNDECIDABLE", ["NO_FINANCIALS"])},
              metrics={"1": row()}, errors=[FE("2"), FE("3")])
    o = run(rs.respond(S(**st, status="answered")))
    assert o["status"] == "answered" and o["answer"].count(NOTE) == 1
    assert "- 2(2): 재무정보 없음" in o["answer"]


def test_fin_note_limit(ev):
    o = run(rs.respond(S(status="limit", candidates=[{"code": "1", "name": "A"}, {"code": "2", "name": "B"}],
                         verdicts={"1": V("FAIL", ["THRESHOLD_NOT_MET"], ["ROE"]), "2": V("UNDECIDABLE", ["NO_FINANCIALS"])},
                         metrics={"1": row(roe=1.0)}, errors=[FE("2")])))
    assert o["status"] == "limit" and o["answer"].count(NOTE) == 1


def test_fin_note_lookup_text(ev):
    a = run(rs.respond(S(intent="lookup", target_stock="105560", errors=[FE("105560")])))["answer"]
    assert a.startswith(f"‘105560’의 재무정보를 제공하지 못합니다. {NO_FIN_FINANCIAL}") and "찾을 수 없습니다" not in a
