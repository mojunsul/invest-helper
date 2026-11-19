import asyncio

import pytest

from backend.agent.nodes import execute as ex
from backend.agent.nodes import judge as jd
from backend.events import bus
from backend.identity.models import IdentityError, StockMatch, StockRef
from backend.tools import registry
from backend.tools.base import fail, ok

registry.load_all()


def run(c):
    return asyncio.run(c)


def fin(code, ni=100, eq=1000, cap=500, **kw):
    d = {"code": code, "fiscal_period": "FY2025", "revenue": 1000, "op_profit": 100, "net_income": ni,
         "assets": 2000, "equity": eq, "debt_ratio": 20.0, "mkt_cap": cap}
    d.update(kw)
    return ok(d, requested="bizYear=2025", actual="FY2025")


def cand(code, cap=700):
    return {"code": code, "name": code, "mkt_cap": cap}


COND = {"industry": "반도체", "market": None, "base_date": None, "want_disclosures": False,
        "pending_metrics": [], "constraints": [{"metric": "ROE", "op": ">=", "value": 5, "ref": None}]}


@pytest.fixture
def env(monkeypatch):
    s = {"calls": [], "events": [], "results": {},
         "cands": ok([cand("000001"), cand("000002")], actual="2026-01-02"), "disc": {}}
    bus._subscribers.clear()
    bus.subscribe(s["events"].append)

    async def fake(name, raw):
        s["calls"].append((name, dict(raw)))
        if name == "find_stock_candidates":
            return s["cands"]
        if name == "get_financials":
            return s["results"].get(raw["stock_code"], fin(raw["stock_code"]))
        return s["disc"].get(raw["stock_code"], fail("NO_RESULT", "없음"))

    monkeypatch.setattr(registry, "call", fake)
    yield s
    bus._subscribers.clear()


def st(**kw):
    b = {"session_id": "s", "intent": "screen", "conditions": COND, "tool_cache": {}, "candidates": [],
         "errors": [], "plan": [{"tool": "find_stock_candidates", "input": {"industry": "반도체"}}]}
    b.update(kw)
    return b


PLAN2 = [{"tool": "find_stock_candidates", "input": {"industry": "반도체", "market": "KOSPI"}}]  # 재계획: 입력이 달라 캐시 미적중


def n(env, name):
    return [c for c in env["calls"] if c[0] == name]


def test_screen_basic_and_events(env):
    o = run(ex.execute(st()))
    assert [c["code"] for c in o["candidates"]] == ["000001", "000002"]
    assert len(n(env, "get_financials")) == 2 and o["errors"] == []
    types = [e.type for e in env["events"]]
    assert types[0] == "stage_start" and types[-1] == "stage_end" and types.count("tool_call") == 3
    blob = str([e.payload for e in env["events"]])
    assert "revenue" not in blob and "sekret" not in blob
    j = run(jd.judge(st(**o)))
    assert set(j["metrics"]) == {"000001", "000002"} and j["verdicts"]["000001"]["status"] == "PASS"
    assert j["industry_avg"]["PER"]["n"] == 2


def test_cache_hit_and_union_merge(env):
    o = run(ex.execute(st()))
    env["calls"].clear()
    env["events"].clear()
    env["cands"] = ok([cand("000002"), cand("000003")], actual="x")
    o2 = run(ex.execute(st(**{**o, "plan": PLAN2})))
    assert [c["code"] for c in o2["candidates"]] == ["000001", "000002", "000003"]
    assert [c[1]["stock_code"] for c in n(env, "get_financials")] == ["000003"]
    hits = [e.payload for e in env["events"] if e.type == "tool_call" and e.payload["tool"] == "get_financials"]
    assert [h["cache_hit"] for h in hits] == [True, True, False]
    res = [e.payload for e in env["events"] if e.type == "tool_result" and e.payload["tool"] == "get_financials"]
    assert sorted(r["duration_ms"] for r in res)[:2] == [0, 0]


def test_cache_rules_and_errors_latest(env):
    env["results"]["000001"] = fail("API_ERROR", "x")
    env["results"]["000002"] = fail("NO_FINANCIALS", "nf")
    o = run(ex.execute(st()))
    assert len(o["tool_cache"]) == 2  # 후보 조회 + NO_FINANCIALS만
    assert {e["stock_code"]: e["code"] for e in o["errors"]} == {"000001": "API_ERROR", "000002": "NO_FINANCIALS"}
    env["results"]["000001"] = fin("000001")
    env["calls"].clear()
    o2 = run(ex.execute(st(**o)))
    assert [c[1]["stock_code"] for c in n(env, "get_financials")] == ["000001"]
    assert [e["stock_code"] for e in o2["errors"]] == ["000002"]
    j = run(jd.judge(st(**o2)))
    assert j["verdicts"]["000002"]["reasons"] == ["NO_FINANCIALS"]


def test_judge_failure_mapping(env):
    for code, tag in [("API_ERROR", "FETCH_FAILED"), ("RATE_LIMIT", "FETCH_FAILED"), ("TIMEOUT", "FETCH_FAILED"),
                      ("INVALID_INPUT", "FETCH_FAILED"), ("NO_INDEX", "FETCH_FAILED"),
                      ("NO_FINANCIALS", "NO_FINANCIALS")]:
        env["results"]["000001"] = fail(code, "m")
        o = run(ex.execute(st()))
        j = run(jd.judge(st(**o)))
        assert j["verdicts"]["000001"]["reasons"] == [tag]


def test_no_result_and_error_no_financials(env):
    env["cands"] = fail("NO_RESULT", "n")
    o = run(ex.execute(st()))
    assert n(env, "get_financials") == [] and o["errors"][0]["tool"] == "find_stock_candidates"
    assert ex.terminal_error(o["errors"]) is None
    env["cands"] = fail("API_ERROR", "a")
    o = run(ex.execute(st()))
    assert n(env, "get_financials") == [] and ex.terminal_error(o["errors"]) == "API_ERROR"


def test_replan_no_result_keeps_candidates(env):
    o = run(ex.execute(st()))
    env["cands"] = fail("NO_RESULT", "n")
    env["calls"].clear()
    o2 = run(ex.execute(st(**{**o, "plan": PLAN2})))
    assert len(o2["candidates"]) == 2 and ex.terminal_error(o2["errors"]) is None
    assert [e["code"] for e in o2["errors"]] == ["NO_RESULT"]
    assert n(env, "get_financials") == []  # 캐시 적중
    j = run(jd.judge(st(**o2)))
    assert len(j["metrics"]) == 2


def test_fatal_stops_unstarted(env, monkeypatch):
    monkeypatch.setattr(ex, "FIN_CONCURRENCY", 1)
    env["cands"] = ok([cand(f"00000{i}") for i in range(1, 5)], actual="x")
    for i in range(1, 5):
        env["results"][f"00000{i}"] = fail("MISSING_API_KEY", "k")
    o = run(ex.execute(st()))
    assert len(n(env, "get_financials")) == 1
    assert [e["code"] for e in o["errors"]] == ["MISSING_API_KEY"]
    assert ex.terminal_error(o["errors"]) == "MISSING_API_KEY"
    assert run(jd.judge(st(**o)))["verdicts"] == {}


def test_partial_failure_and_base_date(env):
    env["results"]["000001"] = fail("TIMEOUT", "t")
    c = {**COND, "base_date": "2025-06-30"}
    o = run(ex.execute(st(conditions=c)))
    assert n(env, "get_financials")[1][1] == {"stock_code": "000002", "base_date": "2025-06-30"}
    assert len(o["errors"]) == 1
    assert ex.fin_input("000001", COND) == {"stock_code": "000001"}
    assert registry.cache_key_for("get_financials", {"stock_code": "000001"}) == \
        registry.cache_key_for("get_financials", {"stock_code": "000001", "base_date": None})


def test_ordering_deterministic(env):
    env["cands"] = ok([cand(f"00000{i}") for i in range(1, 8)], actual="x")
    for i in range(1, 8):
        env["results"][f"00000{i}"] = fail("API_ERROR", "a")
    o = run(ex.execute(st()))
    assert [e["stock_code"] for e in o["errors"]] == [f"00000{i}" for i in range(1, 8)]


def test_lookup(env):
    s = st(intent="lookup", target_stock="삼성", plan=[{"tool": "get_financials", "input": {"stock_code": "005930"}}])
    o = run(ex.execute(s))
    assert n(env, "get_financials") == [("get_financials", {"stock_code": "005930"})]
    assert o["target_stock"] == "005930"
    j = run(jd.judge({**s, **o}))
    assert set(j["metrics"]) == {"005930"} and "verdicts" not in j
    assert j["metrics"]["005930"]["values"]["PER"] == 5.0
    env["results"]["005930"] = fail("NO_FINANCIALS", "nf")
    o = run(ex.execute(st(intent="lookup", target_stock="x", plan=s["plan"])))
    assert o["errors"][0]["code"] == "NO_FINANCIALS"
    j = run(jd.judge(st(intent="lookup", target_stock="005930", tool_cache=o["tool_cache"])))
    assert j["metrics"] == {}


def test_lookup_disclosures_not_called(env):
    s = st(intent="lookup", conditions={**COND, "want_disclosures": True}, target_stock="005930",
           plan=[{"tool": "get_financials", "input": {"stock_code": "005930"}}])
    o = run(ex.execute(s))
    run(jd.judge({**s, **o}))
    assert n(env, "search_disclosures") == []


def M(status, *names, total=None):
    return StockMatch(status, tuple(StockRef(f"00000{i}", "c", x) for i, x in enumerate(names)),
                      total if total is not None else len(names), "exact")


@pytest.mark.parametrize("intent", ["report_save", "lookup"])
def test_resolve_cases(env, monkeypatch, intent):
    def go(res, target="삼성"):
        def f(q):
            if isinstance(res, Exception):
                raise res
            return res
        monkeypatch.setattr(ex, "resolve_stock", f)
        env["calls"].clear()
        return run(ex.execute(st(intent=intent, plan=[], target_stock=target)))
    o = go(M("ok", "삼성전자"))
    assert o["target_stock"] == "000000" and o["errors"] == []
    assert len(n(env, "get_financials")) == (1 if intent == "lookup" else 0)
    o = go(M("none"))
    assert o["errors"][0]["code"] == "NO_RESULT" and "target_stock" not in o
    o = go(M("multiple", "a", "b", "c", "d", "e", "f", total=12))
    e = o["errors"][0]
    assert e["code"] == "INVALID_INPUT" and "a, b, c, d, e" in e["message"] and "외 7개" in e["message"]
    o = go(IdentityError("API_ERROR", "캐시 없음"))
    assert o["errors"][0]["code"] == "API_ERROR" and o["errors"][0]["message"] == "캐시 없음"
    o = go(RuntimeError("sqlite boom"))
    assert o["errors"][0]["code"] == "API_ERROR" and "boom" not in o["errors"][0]["message"]
    assert go(M("ok", "x"), target=None)["errors"][0]["code"] == "INVALID_INPUT"
    assert n(env, "get_financials") == [] and not [e for e in env["events"] if e.type == "tool_call" and
                                                   e.payload["tool"] == "resolve_stock"]


def test_noop_intents(env):
    for i in ("chitchat", None):
        o = run(ex.execute(st(intent=i)))
        assert env["calls"] == [] and o["errors"] == []
        assert "verdicts" not in run(jd.judge(st(intent=i)))


def test_judge_mkt_cap_fill_and_avg_recalc(env):
    env["results"]["000001"] = fin("000001", cap=None)
    env["results"]["000002"] = fin("000002", cap=1000, ni=50)
    c = {**COND, "constraints": [{"metric": "PER", "op": "<=", "value": None, "ref": "industry_avg"}]}
    o = run(ex.execute(st(conditions=c)))
    j = run(jd.judge(st(conditions=c, **o)))
    assert j["metrics"]["000001"]["values"]["mkt_cap"] == 700  # 후보 행 보충
    assert j["metrics"]["000002"]["values"]["mkt_cap"] == 1000  # None일 때만
    assert j["industry_avg"]["PER"]["n"] == 2
    assert j["verdicts"]["000001"]["status"] == "PASS" and j["verdicts"]["000002"]["status"] == "FAIL"
    env["results"]["000003"] = fin("000003", cap=100)
    env["cands"] = ok([cand("000003")], actual="x")
    o2 = run(ex.execute(st(conditions=c, **{**o, "plan": PLAN2})))
    j2 = run(jd.judge(st(conditions=c, **o2)))
    assert j2["industry_avg"]["PER"]["n"] == 3 and len(j2["verdicts"]) == 3
    assert j2["industry_avg"] != j["industry_avg"]  # 누적 후보로 재계산


def test_judge_empty_constraints_and_uncached_key(env):
    c = {**COND, "constraints": []}
    o = run(ex.execute(st(conditions=c)))
    assert run(jd.judge(st(conditions=c, **o)))["verdicts"] == {}
    # 캐시 키 None(입력 검증 실패) -> 캐시 없음 + 오류 레코드 -> FETCH_FAILED
    env["cands"] = ok([cand("bad")], actual="x")
    env["results"]["bad"] = fail("INVALID_INPUT", "i")
    o = run(ex.execute(st()))
    assert registry.cache_key_for("get_financials", {"stock_code": "bad"}) is None
    assert o["errors"][0]["code"] == "INVALID_INPUT"
    assert run(jd.judge(st(**o)))["verdicts"]["bad"]["reasons"] == ["FETCH_FAILED"]


def passing(env, want=True, **kw):
    s = st(conditions={**COND, "want_disclosures": want}, **kw)
    return {**s, **run(ex.execute(s))}


def test_disclosure_conditions_off(env):
    s = passing(env, want=False)
    run(jd.judge(s))
    assert n(env, "search_disclosures") == []
    s = passing(env)
    run(jd.judge({**s, "intent": "chitchat"}))
    strict = {**s["conditions"], "constraints": [{"metric": "ROE", "op": ">=", "value": 99, "ref": None}]}
    run(jd.judge({**s, "conditions": strict}))
    run(jd.judge({**s, "errors": [{"tool": "x", "stock_code": None, "code": "MISSING_API_KEY", "message": "k"}]}))
    assert n(env, "search_disclosures") == []


def test_disclosures_pass_only_and_outcomes(env):
    env["results"]["000002"] = fin("000002", ni=1)  # FAIL
    env["cands"] = ok([cand("000001"), cand("000002"), cand("000003"), cand("000004")], actual="x")
    env["disc"] = {"000001": ok([{"title": "SECRETTITLE", "url": "SECRETURL"}]), "000003": fail("API_ERROR", "a")}
    j = run(jd.judge(passing(env)))
    assert [c[1] for c in n(env, "search_disclosures")] == [{"stock_code": c} for c in ("000001", "000003", "000004")]
    assert list(j["disclosures"]) == ["000001"]
    assert [(e["tool"], e["stock_code"]) for e in j["errors"]] == [("search_disclosures", "000003")]
    blob = str([e.payload for e in env["events"]])
    assert "SECRET" not in blob and "공시 1건" in blob


def test_disclosure_fatal(env, monkeypatch):
    monkeypatch.setattr(ex, "FIN_CONCURRENCY", 1)
    env["disc"] = {f"00000{i}": fail("MISSING_API_KEY", "k") for i in (1, 2)}
    j = run(jd.judge(passing(env)))
    assert len(n(env, "search_disclosures")) == 1
    assert ex.terminal_error(j["errors"]) == "MISSING_API_KEY" and j["disclosures"] == {}


def test_terminal_error_order():
    def E(t, c):
        return {"tool": t, "stock_code": None, "code": c, "message": ""}
    assert ex.terminal_error([E("find_stock_candidates", "API_ERROR"), E("get_financials", "MISSING_API_KEY")]) \
        == "MISSING_API_KEY"
    assert ex.terminal_error([E("find_stock_candidates", "NO_RESULT")]) is None
    assert ex.terminal_error([E("get_financials", "API_ERROR")]) is None
