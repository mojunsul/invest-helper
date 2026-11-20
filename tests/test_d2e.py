import asyncio
import json

import pytest

from backend.agent import llm
from backend.identity import resolve, theme
from backend.identity.models import IdentityError
from backend.tools import base
from backend.tools import stock_candidates as sc
from tests import test_d2 as t2
from tests import test_d7 as t7
from tests.test_d7 import OUT, an, cond, env, go  # noqa: F401  (env 픽스처)
from tests.test_d3 import fetch, page, row


def run(c):
    return asyncio.run(c)


@pytest.fixture(autouse=True)
def clean():
    theme._cache.clear()
    theme._unresolved.clear()


@pytest.fixture
def idb(tmp_path, monkeypatch):
    from backend import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    t2.run()
    return tmp_path


def fake_llm(monkeypatch, raw):
    n = {"n": 0}

    async def f(messages, *, json_mode, timeout_s=0):
        n["n"] += 1
        if isinstance(raw, Exception):
            raise raw
        return raw
    monkeypatch.setattr(llm, "complete", f)
    return n


def test_match_names(idb):
    r = resolve.match_names(["삼성전자", "삼전", "삼성", "한국", "없는회사", "", "삼성전자", "SK하이닉스"])
    assert [x.stock_code for x in r] == ["005930", "000002", "000660"]  # 접두("한국")·미존재 버림, 중복 제거


def test_select_by_codes(idb):
    assert [s["stock_code"] for s in resolve.select_by_codes(["000660", "005930", "000004"])] == ["000660", "005930"]
    for bad in ([], [1]):
        with pytest.raises(IdentityError) as e:
            resolve.select_by_codes(bad)
        assert e.value.code == "INVALID_INPUT"


def test_suggest_industries():
    assert resolve.suggest_industries("반도체 설비") and resolve.suggest_industries("zzzz") == []


def test_select_by_codes_cache_not_ready(tmp_path, monkeypatch):
    from backend import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "x.db"))
    with pytest.raises(IdentityError):
        resolve.select_by_codes(["005930"])


def test_ensure(idb, monkeypatch):
    names = ["삼성전자", "SK하이닉스", 3, "삼성전자"] + [f"x{i}" for i in range(12)]
    seen = {}
    monkeypatch.setattr(resolve, "match_names", lambda n: seen.setdefault("n", n) and [])
    n = fake_llm(monkeypatch, json.dumps({"names": names}))
    assert run(theme.ensure("원전")) == ([], 10, False)
    assert len(seen["n"]) == 10 and seen["n"][:2] == ["삼성전자", "SK하이닉스"] and 3 not in seen["n"]
    run(theme.ensure("원전"))  # 빈 결과는 캐시 안 함 → 재호출
    assert theme.cached_codes("원전") == [] and n["n"] == 2


def test_ensure_cache_and_errors(idb, monkeypatch):
    n = fake_llm(monkeypatch, json.dumps({"names": ["삼성전자", "SK하이닉스"]}))
    assert run(theme.ensure("원 전")) == (["005930", "000660"], 2, False)
    assert run(theme.ensure("원전")) == (["005930", "000660"], 2, True) and n["n"] == 1  # 정규화 키 히트
    assert theme.cached_codes("원전") == ["005930", "000660"]
    for bad in ("not json", json.dumps({"names": "x"}), llm.LlmError("TIMEOUT")):
        fake_llm(monkeypatch, bad)
        with pytest.raises(llm.LlmError):
            run(theme.ensure("다른주제"))
        assert theme.cached_codes("다른주제") == []


# ---------- analyze ----------
def stub_ensure(monkeypatch, result):
    calls = []

    async def f(text):
        calls.append(text)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(an.theme, "ensure", f)
    return calls


def test_analyze_fallback_success(env, monkeypatch):
    calls = stub_ensure(monkeypatch, (["005930", "000660"], 4, False))
    r = go(env, OUT(industry="???"))
    assert r["conditions"]["industry"] == "???" and r["missing"] == ["threshold"]
    assert r["clarify_question"].startswith("‘???’은(는) 산업 분류에 없어 AI 일반 지식으로 국내 상장사 2곳") and calls == ["???"]
    ev = [e for e in env[3] if e.payload.get("kind") == "conditions"]
    assert ev and any(e.payload["reason"] == "theme_fallback" for e in ev)


def test_analyze_zero_and_recall(env, monkeypatch):
    stub_ensure(monkeypatch, ([], 3, False))
    r = go(env, OUT(industry="???"), session_id="s1")
    assert r["status"] == "asked" and "일반 지식" in r["clarify_question"] and theme.recall("s1") == "???"
    calls = stub_ensure(monkeypatch, (["005930"], 1, False))  # asked 직후 "일반지식으로 찾아줘" (chitchat 오분류도 복구)
    r = go(env, OUT("chitchat", general_knowledge=True), prev_intent="screen", last_status="asked")
    assert calls == ["???"] and r["intent"] == "screen" and r["conditions"]["industry"] == "???"
    assert theme.recall("s1") is None


def test_analyze_failure_is_asked(env, monkeypatch):
    stub_ensure(monkeypatch, llm.LlmError("TIMEOUT"))
    r = go(env, OUT(industry="???"))
    assert r["status"] == "asked" and "조회도 실패" in r["clarify_question"]
    assert any(e.payload["reason"] == "theme_fallback_failed" for e in env[3] if e.payload.get("kind") == "conditions")


def test_analyze_general_knowledge_no_topic(env, monkeypatch):
    calls = stub_ensure(monkeypatch, ([], 0, False))
    r = go(env, OUT(general_knowledge=True))
    assert "주제를 알려주세요" in r["clarify_question"] and calls == []


def test_analyze_dict_term_skips_and_kept_industry_rechecked(env, monkeypatch):
    calls = stub_ensure(monkeypatch, (["005930"], 1, True))
    go(env, OUT(industry="반도체", general_knowledge=True))
    assert calls == []
    r = go(env, OUT(constraints=[{"metric": "ROE", "value": 10}]), conditions=cond(industry="???"),
           prev_intent="screen", last_status="asked")
    assert calls == ["???"] and r["missing"] == []


# ---------- D3 도구 ----------
def test_tool_theme_pool(monkeypatch):
    from tests.test_d3 import env as _e  # noqa: F401
    monkeypatch.setattr(base, "settings", base.settings.__class__(DATA_GO_KR_KEY="k"))
    monkeypatch.setattr(sc, "settings", base.settings)
    monkeypatch.setattr(sc, "resolve_industry", lambda t: {"status": "unknown", "prefixes": [], "label": "", "options": []})
    monkeypatch.setattr(sc, "select_by_codes", lambda c: [{"stock_code": x, "name": "n" + x, "induty_code": "1"} for x in c])
    fetch(monkeypatch, page([row("000001", 5), row("000009", 9)]))
    r = run(sc.find_stock_candidates.run({"industry": "테마", "extra_industries": ["a"]}))
    assert r.error and r.error.code == "INVALID_INPUT"  # 캐시 없음 → 기존 동작
    theme._cache[theme.key("테마")] = (("000001",), 1)
    r = run(sc.find_stock_candidates.run({"industry": "테마", "extra_industries": ["a"]}))
    assert [c["code"] for c in r.data] == ["000001"]


# ---------- respond ----------
def test_respond_theme_notice(monkeypatch):
    from backend.agent.nodes import respond as rs
    from tests.test_d9 import S, V
    st = S(status="answered", verdicts={"000001": V("PASS")}, metrics={})
    unk = {"status": "unknown", "prefixes": [], "label": "", "options": []}
    okr = {"status": "ok", "prefixes": ["261"], "label": "x", "options": []}

    async def lines(r):
        monkeypatch.setattr(rs, "resolve_industry", r)
        return await rs._theme_notice(st)
    assert "AI의 일반 지식" in run(lines(lambda t: unk)) and "1곳" in run(lines(lambda t: unk))
    assert run(lines(lambda t: okr)) is None

    def boom(t):
        raise ValueError
    assert run(lines(boom)) is None
