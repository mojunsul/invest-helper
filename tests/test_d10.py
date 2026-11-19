import asyncio

import pytest

from backend.agent import memory as mm
from backend.agent.nodes import analyze as an
from backend.agent.nodes import plan as pl


@pytest.fixture(autouse=True)
def _clean():
    mm.clear_all()
    yield
    mm.clear_all()


def C(**kw):
    c = an.default_conditions()
    c.update(kw)
    return c


def fin(intent, status="answered", cond=None, **kw):
    f = {"intent": intent, "status": status, "conditions": cond if cond is not None else an.default_conditions()}
    f.update(kw)
    return f


def mem(sid="s"):
    return mm._store[sid]


def test_build_state_empty():
    st = mm.build_state("s", "hi")
    assert st["conditions"] == an.default_conditions()
    assert set(st) == {"session_id", "user_msg", "conditions", "prev_intent", "last_status",
                       "last_result_summary", "report_draft"}
    assert st["prev_intent"] is st["last_status"] is st["last_result_summary"] is st["report_draft"] is None
    assert "s" not in mm._store


def test_build_state_deepcopy():
    mm.update_memory("s", fin("screen", cond=C(industry="반도체")))
    st = mm.build_state("s", "x")
    st["conditions"]["industry"] = "X"
    assert mem()["conditions"]["industry"] == "반도체"


def test_screen_variants():
    for status in ("answered", "asked", "error", "limit"):
        mm.clear_all()
        mm.update_memory("s", fin("screen", status, C(industry="반도체", pending_metrics=["ROE"]),
                                  last_result_summary={"top_codes": ["1"], "pass_count": 1},
                                  report_draft={"000001": {"a": 1}}))
        m = mem()
        assert m["last_intent"] == "screen" and m["last_status"] == status
        assert m["conditions"]["pending_metrics"] == ["ROE"]
        assert m["last_result_summary"]["pass_count"] == 1 and "000001" in m["report_draft"]


def test_refine():
    mm.update_memory("s", fin("refine", cond=C(industry="a")))
    assert mem()["last_intent"] == "refine" and mem()["conditions"]["industry"] == "a"


def test_lookup_report_save_keep_conditions():
    mm.update_memory("s", fin("screen", "asked", C(pending_metrics=["PER"])))
    for it in ("lookup", "report_save"):
        mm.update_memory("s", fin(it, "answered", C(industry="바뀜"), report_draft={"1": {}}))
        assert mem()["last_intent"] == it and mem()["last_status"] == "answered"
        assert mem()["conditions"]["pending_metrics"] == ["PER"] and mem()["conditions"]["industry"] is None
        assert mem()["report_draft"] == {"1": {}}


def test_chitchat_none_and_status_none_noop():
    mm.update_memory("s", fin("screen", "asked", C(industry="a")))
    before = repr(mem())
    mm.update_memory("s", fin("chitchat"))
    mm.update_memory("s", {"status": "error"})
    mm.update_memory("s", fin("lookup", None))
    assert repr(mem()) == before
    mm.update_memory("new", fin("chitchat"))
    assert "new" not in mm._store


def test_summary_draft_kept_and_replaced():
    mm.update_memory("s", fin("screen", last_result_summary={"top_codes": [], "pass_count": 1}, report_draft={"1": {}}))
    st = mm.build_state("s", "x")
    mm.update_memory("s", {**fin("screen", "asked"), **{k: st[k] for k in ("last_result_summary", "report_draft")}})
    assert mem()["report_draft"] == {"1": {}} and mem()["last_result_summary"]["pass_count"] == 1
    mm.update_memory("s", fin("screen", last_result_summary={"top_codes": [], "pass_count": 2}, report_draft={"2": {}}))
    assert mem()["report_draft"] == {"2": {}} and mem()["last_result_summary"]["pass_count"] == 2


def test_scenarios_pending_preserved():
    for mid in (("lookup",), ("lookup", "chitchat")):
        mm.clear_all()
        mm.update_memory("s", fin("screen", "asked", C(pending_metrics=["ROE"])))
        for it in mid:
            mm.update_memory("s", fin(it, cond=mm.build_state("s", "x")["conditions"]))
        st = mm.build_state("s", "x")
        assert st["conditions"]["pending_metrics"] == ["ROE"]
        mm.update_memory("s", fin("refine", cond=st["conditions"]))
        assert mem()["conditions"]["pending_metrics"] == ["ROE"]


def test_first_turn_plan_no_keyerror():
    for intent in ("lookup", "report_save", "chitchat"):
        st = mm.build_state("s", "x")
        st.update(intent=intent, target_stock=None)
        try:
            asyncio.run(pl.plan(st))
        except KeyError as e:
            pytest.fail(f"KeyError {e}")
        except Exception:
            pass  # conditions 이외 사유는 본 테스트 범위 밖


def test_run_turn(monkeypatch):
    seen = []

    async def ok(state):
        seen.append(state)
        return {**state, **fin("screen", cond=C(industry="반도체"), report_draft={"1": {}})}

    monkeypatch.setattr(mm, "run_agent", ok)
    f = asyncio.run(mm.run_turn("s", "a"))
    assert f["intent"] == "screen" and mem()["last_status"] == "answered"
    asyncio.run(mm.run_turn("s", "b"))
    assert seen[1]["prev_intent"] == "screen" and seen[1]["conditions"]["industry"] == "반도체"

    async def boom(state):
        raise RuntimeError("x")

    monkeypatch.setattr(mm, "run_agent", boom)
    before = repr(mem())
    with pytest.raises(RuntimeError):
        asyncio.run(mm.run_turn("s", "c"))
    assert repr(mem()) == before


def test_get_report_draft_and_clear():
    assert mm.get_report_draft("s", "1") is None
    mm.update_memory("s", fin("screen", report_draft={"1": {"a": [1]}}))
    d = mm.get_report_draft("s", "1")
    d["a"].append(2)
    assert mm.get_report_draft("s", "1") == {"a": [1]}
    assert mm.get_report_draft("s", "2") is None and mm.get_report_draft("x", "1") is None
    mm.clear_session("s")
    mm.clear_session("none")
    assert mm.get_report_draft("s", "1") is None


def test_lru(monkeypatch):
    monkeypatch.setattr(mm, "MAX_SESSIONS", 2)
    mm.update_memory("a", fin("screen"))
    mm.update_memory("b", fin("screen"))
    mm.build_state("a", "x")  # 읽기도 최근 사용
    mm.update_memory("c", fin("screen"))
    assert set(mm._store) == {"a", "c"}
