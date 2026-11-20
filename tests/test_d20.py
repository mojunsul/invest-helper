import asyncio
import json

from backend.agent import llm
from backend.agent.nodes import analyze as an
from backend.agent.nodes import respond as rs
from tests.test_d7 import OUT, env, go, st  # noqa: F401  (env fixture 재사용)
from tests.test_d9 import COND, S, ev, row  # noqa: F401


def run(c):
    return asyncio.run(c)


def _dec(events):
    return [e.payload for e in events if e.type == "llm_decision" and e.payload["reason"] == "unsupported_metric"]


def test_forward_per_alone_correct_llm(env):
    o = go(env, OUT(industry="반도체", unsupported_metrics=["Forward PER"]), msg="KOSPI 반도체 Forward PER 10 이하")
    assert o["unsupported_metrics"] == ["Forward PER"]
    assert o["status"] == "asked" and o["missing"] == ["threshold"]
    d = _dec(env[3])
    assert len(d) == 1 and d[0]["kind"] == "conditions" and "dropped=none" in d[0]["decision"]


def test_backstop_removes_substituted_per(env):
    o = go(env, OUT(industry="반도체", constraints=[{"metric": "PER", "op": "<=", "value": 10}]),
           msg="KOSPI 반도체 Forward PER 10 이하")
    assert o["conditions"]["constraints"] == [] and o["unsupported_metrics"] == ["Forward PER"]
    assert o["status"] == "asked"
    assert "dropped=PER(backstop)" in _dec(env[3])[0]["decision"]


def test_backstop_chitchat_becomes_screen(env):
    o = go(env, OUT("chitchat"), msg="Forward PER 10 이하 종목")
    assert o["intent"] == "screen" and o["unsupported_metrics"] == ["Forward PER"] and o["status"] == "asked"


def test_plain_per_preserved(env):
    o = go(env, OUT(industry="반도체", constraints=[{"metric": "PER", "op": "<=", "value": 10}],
                    unsupported_metrics=["Forward PER"]), msg="반도체 PER 10 이하 Forward PER 8 이하")
    assert [c["metric"] for c in o["conditions"]["constraints"]] == ["PER"]
    assert o["unsupported_metrics"] == ["Forward PER"] and "dropped=none" in _dec(env[3])[0]["decision"]


def test_mixed_supported_and_unsupported(env):
    o = go(env, OUT(industry="반도체", constraints=[{"metric": "ROE", "op": ">=", "value": 10}],
                    unsupported_metrics=["배당수익률"]), msg="반도체 ROE 10% 이상 배당수익률 3% 이상")
    assert o["missing"] == [] and o["unsupported_metrics"] == ["배당수익률"]


def test_sanitize():
    raw = ["a" * 21, "배당|수익\n률", "per", "ROE", "영업이익률", "`x`[y]‘z’※", "배당 수익률", "배당수익률", "k", "l", 5]
    out, _, _, _ = an._sanitize_unsupported(raw, "x", [], [])
    assert out == ["배당 수익 률", "x y z", "k"] and not any(c in "".join(out) for c in "|`[]‘’※\n")
    assert len(out) <= 3 and "per" not in out and "ROE" not in out


def test_lookup_target_price(env):
    o = go(env, OUT("lookup", target_stock="삼성전자", unsupported_metrics=["목표주가"]), msg="삼성전자 목표주가 알려줘")
    assert o["intent"] == "lookup" and o["unsupported_metrics"] == ["목표주가"]
    assert len(_dec(env[3])) == 1


def test_regression_no_unsupported(env):
    o = go(env, OUT(industry="반도체", constraints=[{"metric": "ROE", "op": ">=", "value": 10}]), msg="반도체 ROE 10% 이상")
    assert "unsupported_metrics" not in o and not _dec(env[3])


def test_false_positive_pattern():
    assert not an._VARIANT_RE.search("예상보다 PER 낮은 종목")
    assert an._VARIANT_RE.search("예상 PER 10 이하")


def test_respond_notice(ev):
    st_ = S(unsupported_metrics=["배당수익률", "EPS"])
    n = rs._unsupported_notice(st_)
    assert n.startswith("※ 요청하신 ‘배당수익률’, ‘EPS’은(는)") and "PER(Trailing, 확정 실적 기준)" in n and "영업이익률" in n
    assert rs._unsupported_notice(S()) is None


def test_respond_placement(ev):
    base = S(status="answered", unsupported_metrics=["배당수익률"], verdicts={"000001": {"status": "PASS", "reasons": [], "failed": []}},
             metrics={"000001": row()})
    a = run(rs._build(base))["answer"].split("\n")
    i = next(k for k, x in enumerate(a) if x.startswith("조건:"))
    assert a[i + 1].startswith("※ 요청하신 ‘배당수익률’")
    asked = run(rs._build(S(unsupported_metrics=["Forward PER"], status="asked", clarify_question="Q?")))["answer"]
    assert asked.startswith("※ 요청하신 ‘Forward PER’") and "\n\nQ?\n" in asked
    err = run(rs._build(S(unsupported_metrics=["EPS"], status="error")))["answer"].split("\n")
    assert err[1].startswith("※ 요청하신 ‘EPS’")
    lk = run(rs._build({"session_id": "s", "user_msg": "x", "intent": "lookup", "target_stock": "000001",
                        "unsupported_metrics": ["목표주가"], "errors": [], "metrics": {"000001": row()}}))["answer"]
    assert lk.startswith("※ 요청하신 ‘목표주가’")
    for it in ("chitchat",):
        assert "※" not in run(rs._build(S(intent=it, unsupported_metrics=["EPS"])))["answer"]
    assert "※ 요청" not in run(rs._build(S(intent="filing_qa", unsupported_metrics=["EPS"], filing_hits=[])))["answer"]
    plain = run(rs._build(base | {"unsupported_metrics": []}))["answer"]
    assert "※ 요청하신" not in plain
