"""D18 직전 응답 반영: last_result_summary.top → analyze prev_result (LLM 목킹)."""
import json

from backend.agent import memory as mm
from backend.agent.nodes import respond as rs
import importlib

d7, d9 = importlib.import_module("test_d7"), importlib.import_module("test_d9")  # 기존 테스트 헬퍼 재사용
KEY, OUT, an, cond, con, env, go, run = d7.KEY, d7.OUT, d7.an, d7.cond, d7.con, d7.env, d7.go, d7.run
S, V, row, ev = d9.S, d9.V, d9.row, d9.ev

TOP = [{"code": "000001", "name": "A전자", "values": {"ROE": 15.2, "PER": 8.1}},
       {"code": "000002", "name": "B반도", "values": {"ROE": 12.0, "PER": 5.5}}]
SUM = {"top_codes": ["000001", "000002"], "pass_count": 2, "top": TOP}


def test_respond_top_values_and_limit(ev):
    n = 7
    cands = [{"code": f"{i:06d}", "name": "N" * 40, "mkt_cap": 1} for i in range(1, n + 1)]
    r = row(roe=12.3456, per=None)
    s = S(candidates=cands, verdicts={c["code"]: V("PASS") for c in cands},
          metrics={c["code"]: r for c in cands}, status="answered")
    top = run(rs.respond(s))["last_result_summary"]["top"]
    assert len(top) == 5 and [t["code"] for t in top] == [c["code"] for c in cands[:5]]
    assert top[0]["name"] == "N" * 30 and top[0]["values"] == {"ROE": 12.35}  # None 제외, 2자리
    o = run(rs.respond(S(verdicts={}, status="limit")))
    assert o["last_result_summary"] == {"top_codes": [], "pass_count": 0, "top": []}


def test_analyze_payload_prev_result(env):
    go(env, OUT("chitchat"), last_result_summary=SUM)
    u = json.loads(env[0]["msgs"][-1][1]["content"])
    assert u["prev_result"] == {"pass_count": 2, "stocks": TOP}
    assert "answer" not in u and KEY not in json.dumps(env[0]["msgs"][-1])
    go(env, OUT("chitchat"))
    assert json.loads(env[0]["msgs"][-1][1]["content"])["prev_result"] is None


def test_deictic_lookup_keeps_conditions(env):
    c = cond(industry="반도체", constraints=[con("ROE", ">=", 10)])
    r = go(env, OUT("lookup", target_stock="000002", tools=["get_financials"]),
           conditions=c, prev_intent="screen", last_status="answered", last_result_summary=SUM)
    assert r["intent"] == "lookup" and r["target_stock"] == "000002" and "conditions" not in r


def test_deictic_null_asks(env):
    r = go(env, OUT("lookup", target_stock=None), last_result_summary=SUM, last_status="answered")
    assert r["status"] == "asked" and r["missing"] == ["target_stock"]


def test_two_turn_diff_and_report_save(env):
    mm.clear_all()
    sid = "s1"

    def turn(out, **extra):
        env[1].append(out)
        st = mm.build_state(sid, "m")
        st["session_id"] = sid
        o = run(an.analyze(st))
        return st, {**st, **o, **extra}

    st1, f1 = turn(OUT("screen", industry="반도체", constraints=[
        {"metric": "ROE", "op": ">=", "value": 10}, {"metric": "PER", "op": "<=", "value": 20}]),
        last_result_summary=SUM, report_draft={"000001": {}, "000002": {}})
    mm.update_memory(sid, {**f1, "status": "answered"})
    c1 = json.loads(json.dumps(f1["conditions"]))
    # T2 지시어 lookup: summary·conditions 불변
    st2, f2 = turn(OUT("lookup", target_stock="000002"))
    assert st2["last_result_summary"] == SUM
    mm.update_memory(sid, {**f2, "status": "answered"})
    assert mm._store[sid]["last_result_summary"] == SUM and mm._store[sid]["conditions"] == c1
    # T4 refine PER만 변경
    st4, f4 = turn(OUT("refine", constraints=[{"metric": "PER", "op": "<=", "value": 10}]),
                   last_result_summary={**SUM, "top": TOP[1:], "top_codes": ["000002"], "pass_count": 1},
                   report_draft={"000002": {"stock_code": "000002", "stock_name": "B반도"}})
    mm.update_memory(sid, {**f4, "status": "answered"})
    c4 = f4["conditions"]
    diff = [(a["metric"], a["value"], b["value"]) for a, b in zip(c1["constraints"], c4["constraints"]) if a != b]
    assert diff == [("PER", 20, 10)] and c4["industry"] == c1["industry"] and len(c4["constraints"]) == 2
    # T5 report_save: draft의 올바른 종목
    st5, f5 = turn(OUT("report_save", target_stock="000002"))
    assert f5["target_stock"] == "000002"
    a = run(rs.respond({**f5, "status": None}))["answer"]
    assert "B반도(000002)" in a and "A전자" not in a
    mm.clear_all()
