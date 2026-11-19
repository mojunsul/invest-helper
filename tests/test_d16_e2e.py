"""D16 E2E: TestClient -> /api/chat -> run_turn -> graph -> 노드 -> Tool 본문. mock은 HTTP 시임·LLM·날짜·키뿐."""
import json
import logging
import sqlite3
import time
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import config, main
from backend.agent import llm, memory
from backend.agent.nodes import analyze as an
from backend.agent.nodes import replan as rp
from backend.agent.nodes import respond
from backend.api import chat as chat_api
from backend.config import Settings
from backend.events import bus, logger_agent as la, stream
from backend.events.bus import Event
from backend.identity import resolve, store
from backend.tools import base
from backend.tools import disclosures as disc
from backend.tools import financials as fin
from backend.tools import stock_candidates as sc

SECRET = "E2E_SECRET_KEY"
KEYS = Settings(DATA_GO_KR_KEY=SECRET, DART_API_KEY=SECRET + "2", GMS_API_KEY=SECRET + "3", REPLAN_MAX=3)
RESP_KEYS = {"answer", "status", "verdicts", "industry_avg", "steps", "tool_calls", "replans",
             "disclaimer", "missing_api_key", "saveable"}
NET: list = []  # 네트워크 가드 발동 기록(AssertionError는 Tool 래퍼가 삼키므로 별도 기록)

# code: (이름, 시장, 시총, 순이익, 자본, 부채비율, induty_code). 순이익/자본이 None이면 재무 응답 없음
UNIVERSE = {
    "000001": ("알파반도체", "KOSPI", 1000, 100, 500, 100, "26110"),
    "000002": ("베타반도체", "KOSPI", 3000, 200, 1000, 100, "26110"),
    "000003": ("감마반도체", "KOSDAQ", 500, 25, 500, 100, "26110"),
    "000004": ("델타반도체", "KOSPI", 800, 100, 500, 100, "26110"),
    "000005": ("엡실론반도체", "KOSPI", 700, None, None, None, "26110"),
    "100001": ("은행하나", "KOSPI", 1000, 100, 1000, 800, "64121"),   # ROE 10
    "100002": ("은행둘", "KOSPI", 1000, 120, 1000, 900, "64121"),     # ROE 12
    "100003": ("은행셋", "KOSPI", 1000, 80, 1000, 1100, "64121"),     # ROE 8
    "200001": ("유사금융", "KOSDAQ", 500, 50, 1000, 300, "64190"),    # ROE 5
}
SEMI = ["000001", "000002", "000003", "000004"]
DISC_ROW = {"corp_code": "00000001", "stock_code": "000001", "report_nm": "분기보고서",
            "rcept_no": "20261101000001", "rcept_dt": "20261101"}


def crno(code):
    return "1" + code + "000000"


def corp(code):
    return f"{int(code):08d}"


def A(**kw):
    return json.dumps({"intent": "screen", "reason": "r", **kw}, ensure_ascii=False)


C_ROE_PER = [{"metric": "ROE", "op": ">=", "value": 10}, {"metric": "PER", "op": "<=", "ref": "industry_avg"}]
C_BANK = [{"metric": "ROE", "op": ">=", "value": 25}, {"metric": "DEBT_RATIO", "op": "<=", "value": 30}]


class World:
    def __init__(self, mp, caplog):
        self.mp, self.caplog = mp, caplog
        self.queue, self.summary, self.llm_calls = [], "조건을 충족한 종목을 정리했습니다.", []
        self.with5, self.find, self.fin, self.disc, self.price = False, 0, [], [], 0
        self.sids = {}

    def set_settings(self, s):
        for m in (config, base, sc, fin, disc, llm, rp):
            self.mp.setattr(m, "settings", s)


@pytest.fixture(autouse=True)
def net_guard(monkeypatch):
    NET.clear()
    from fastapi.testclient import TestClient as TC
    orig_sync, orig_async = httpx.Client.send, httpx.AsyncClient.send

    def sync_send(self, *a, **k):
        if isinstance(self, TC):  # TestClient는 인-프로세스 ASGI 전송이라 허용
            return orig_sync(self, *a, **k)
        NET.append("sync")
        raise AssertionError("real HTTP")

    async def async_send(self, *a, **k):
        NET.append("async")
        raise AssertionError("real HTTP")
    monkeypatch.setattr(httpx.Client, "send", sync_send)
    monkeypatch.setattr(httpx.AsyncClient, "send", async_send)
    yield
    assert NET == []


@pytest.fixture
def w(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "e2e.db"))
    monkeypatch.setattr(bus, "_subscribers", [])
    monkeypatch.setattr(bus, "_seq", {})
    monkeypatch.setattr(stream, "_sinks", {})
    monkeypatch.setattr(stream, "_installed", False)
    monkeypatch.setattr(la, "_installed", False)
    memory.clear_all()
    world = World(monkeypatch, caplog)
    world.set_settings(KEYS)

    terms = tmp_path / "terms.json"
    terms.write_text(json.dumps([{"term": "반도체", "prefixes": ["261"]}, {"term": "은행", "prefixes": ["6412"]},
                                 {"term": "금융지원", "prefixes": ["6419"]}], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(resolve, "TERMS_PATH", terms)
    resolve._terms.cache_clear()
    con = store.connect_rw()
    with con:
        for code, (name, _m, _c, _n, _e, _d, ind) in UNIVERSE.items():
            con.execute("INSERT INTO company(stock_code, corp_code, name, name_norm, listed, crno, induty_code, "
                        "corp_cls, detail_status) VALUES (?,?,?,?,1,?,?,?, 'ok')",
                        (code, corp(code), name, name, crno(code), ind, "Y"))
    con.close()

    for m in (sc, fin, disc):
        monkeypatch.setattr(m, "_today", lambda: date(2026, 11, 19))

    async def fetch_page(basDt, page, rows):
        world.find += 1
        items = [{"srtnCd": c, "mrktCtg": v[1], "clpr": "100", "mrktTotAmt": str(v[2])}
                 for c, v in UNIVERSE.items() if c != "000005" or world.with5]
        return {"response": {"header": {"resultCode": "00"},
                             "body": {"totalCount": len(items), "items": {"item": items}}}}

    async def fetch_fin(cr, year):
        world.fin.append(year)
        code = next(c for c in UNIVERSE if crno(c) == cr)
        _n, _m, _c, ni, eq, debt, _i = UNIVERSE[code]
        items = ""
        if ni is not None and year == 2025:
            items = {"item": [{"crno": cr, "bizYear": "2025", "fnclDcd": "110", "enpSaleAmt": "1000",
                               "enpBzopPft": "150", "enpCrtmNpf": str(ni), "enpTastAmt": "1000",
                               "enpTcptAmt": str(eq), "fnclDebtRto": str(debt)}]}
        return {"response": {"header": {"resultCode": "00"}, "body": {"items": items}}}

    async def fetch_price(basDt, code):
        world.price += 1
        return {"response": {"header": {"resultCode": "00"}, "body": {"totalCount": 1, "items": {
            "item": [{"srtnCd": code, "mrktTotAmt": str(UNIVERSE[code][2])}]}}}}

    async def fetch_list(corp_code, bgn, n):
        world.disc.append(corp_code)
        return {"status": "000", "list": [{**DISC_ROW, "corp_code": corp_code, "stock_code": "000001"}]
                if corp_code == corp("000001") else []}

    async def complete(messages, *, json_mode, timeout_s=0):
        world.llm_calls.append((json_mode, messages))
        if json_mode:
            return world.queue.pop(0)
        if world.summary is None:
            raise llm.LlmError("API_ERROR")
        return world.summary

    monkeypatch.setattr(sc, "_fetch_page", fetch_page)
    monkeypatch.setattr(fin, "_fetch_fin", fetch_fin)
    monkeypatch.setattr(fin, "_fetch_price", fetch_price)
    monkeypatch.setattr(disc, "_fetch_list", fetch_list)
    monkeypatch.setattr(llm, "complete", complete)
    yield world
    memory.clear_all()
    resolve._terms.cache_clear()


@pytest.fixture
def c(w):
    with TestClient(main.app) as client:
        yield client


SID = "123e4567-e89b-42d3-a456-426614174000"


def ask(c, w, analyze_json, sid=SID, msg="질문"):
    w.queue.append(analyze_json)
    return c.post("/api/chat", json={"session_id": sid, "message": msg})


def log_events(c, sid=SID):
    return c.get(f"/api/logs/{sid}").json()


def run_slice(evs, run):
    return [Event(**{k: e[k] for k in ("session_id", "seq", "ts", "type", "payload")})
            for e in evs if e["run"] == run]


def assert_common(c, w, r, run=1, sid=SID, seams=True):
    """§2-4 공통 불변식 1~7."""
    assert r.status_code == 200
    b = r.json()
    assert set(b) == RESP_KEYS and b["disclaimer"] == respond.DISCLAIMER
    assert "INTERNAL_ERROR" not in r.text and "SESSION_BUSY" not in r.text
    lr = c.get(f"/api/logs/{sid}")
    evs = lr.json()
    assert sorted({e["run"] for e in evs}) == list(range(1, run + 1))
    for n in range(1, run + 1):
        ev = [e for e in evs if e["run"] == n]
        assert ev[0]["type"] == "stage_start" and ev[0]["payload"]["stage"] == "analyze"
        assert ev[-1]["type"] == "final" and [e["type"] for e in ev].count("final") == 1
        seqs = [e["seq"] for e in ev]
        assert seqs == sorted(set(seqs))
    last = run_slice(evs, run)
    assert b["steps"] == chat_api.to_steps(last) and b["tool_calls"] == chat_api.to_tool_calls(last)
    assert b["replans"] == [e.payload for e in last if e.type == "replan"]
    assert last[-1].payload["status"] == b["status"]
    payloads = " ".join(x[0] for x in sqlite3.connect(config.DB_PATH).execute("SELECT payload_json FROM run_logs"))
    for text in (r.text, lr.text, payloads, w.caplog.text):
        assert SECRET not in text
    if seams:  # cache_hit=false 인 tool_call 수 == 시임 호출 수 (세션 전체 누적), cache_hit=true 는 시임 0
        def miss(tool):
            return sum(e["payload"]["tool"] == tool and not e["payload"]["cache_hit"]
                       for e in evs if e["type"] == "tool_call")
        assert miss("find_stock_candidates") == w.find
        assert miss("get_financials") == w.fin.count(2025)
        assert miss("search_disclosures") == len(w.disc)
    assert NET == []
    return b


def verdicts(b):
    return {v["stock_code"]: v["status"] for v in b["verdicts"]}


def calls(b, tool=None, typ="call"):
    return [t for t in b["tool_calls"] if t["type"] == typ and (tool is None or t["tool"] == tool)]


def stages(b):
    return [s["stage"] for s in b["steps"]]


def llm_decisions(c, kind):
    return [e["payload"]["decision"] for e in log_events(c) if e["type"] == "llm_decision" and e["payload"]["kind"] == kind]


# ---------------- 요청 1 / 1b ----------------
def test_req1_screen_answered(c, w):
    b = assert_common(c, w, ask(c, w, A(industry="반도체", constraints=C_ROE_PER, want_disclosures=False)))
    assert b["status"] == "answered"
    assert verdicts(b) == {"000001": "PASS", "000002": "FAIL", "000003": "FAIL", "000004": "PASS"}
    assert b["industry_avg"]["PER"] == {"value": 12.5, "n": 4} and b["replans"] == []
    assert len(calls(b, "find_stock_candidates")) == 1 and len(calls(b, "get_financials")) == 4
    assert calls(b, "search_disclosures") == []
    assert stages(b) == ["analyze", "plan", "execute", "judge", "respond"] and {s["round"] for s in b["steps"]} == {0}
    assert "| 알파반도체(000001) |" in b["answer"] and "| 델타반도체(000004) |" in b["answer"]
    assert "| 베타반도체(000002) |" not in b["answer"] and "산업 평균" in b["answer"]
    assert w.summary in b["answer"] and llm_decisions(c, "summary") == ["llm"]
    assert b["answer"].endswith(respond.DISCLAIMER)
    assert [s["stock_code"] for s in b["saveable"]] == ["000001", "000004"] and b["missing_api_key"] is False
    assert [e["payload"]["status"] for e in log_events(c) if e["type"] == "final"] == ["answered"]


def test_req1b_disclosures_pass_only(c, w):
    b = assert_common(c, w, ask(c, w, A(industry="반도체", constraints=C_ROE_PER, want_disclosures=True)))
    assert b["status"] == "answered"
    assert verdicts(b)["000002"] == "FAIL" and sorted(w.disc) == [corp("000001"), corp("000004")]
    assert len(calls(b, "search_disclosures")) == 2
    assert "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261101000001" in b["answer"]
    assert "공시 없음" in b["answer"]
    prompts = [json.dumps(m, ensure_ascii=False) for jm, m in w.llm_calls if not jm]
    assert prompts and all("분기보고서" not in p for p in prompts)


# ---------------- 요청 2 / 2b ----------------
def test_req2_replan_cache_limit(c, w):
    # DESIGN §10-2는 "3회"이나 D0 승인 5(효과 있는 단계만 센다)에 따라 시장 미지정이면 재계획 2회
    w.summary = None  # 템플릿 경로
    b = assert_common(c, w, ask(c, w, A(industry="은행", constraints=C_BANK)))
    assert b["status"] == "limit" and [r["change"] for r in b["replans"]] == ["similar_industry", "limit_up"]
    assert sorted({s["round"] for s in b["steps"]}) == [0, 1, 2]
    assert [s["stage"] for s in b["steps"] if s["round"] == 1] == ["plan", "execute", "judge"]
    assert [s["stage"] for s in b["steps"] if s["round"] == 2] == ["plan", "execute", "judge", "respond"]
    assert w.fin.count(2025) == 4  # 종목당 HTTP 1회, 나머지는 캐시
    fc = calls(b, "get_financials")
    assert sum(t["cache_hit"] for t in fc) == 7 and sum(not t["cache_hit"] for t in fc) == 4
    assert [t["cache_hit"] for t in fc if t["round"] == 1].count(False) == 1  # 6419 종목 신규 1회
    assert b["industry_avg"]["PER"]["n"] == 4
    a = b["answer"]
    assert "재계획 이력" in a and "12.00%" in a and "조건을 완화할까요?" in a and a.endswith(respond.DISCLAIMER)
    assert b["saveable"] == [] and llm_decisions(c, "summary") == ["template"]
    assert [e["payload"]["status"] for e in log_events(c) if e["type"] == "final"] == ["limit"]


def test_req2b_market_specified(c, w):
    w.summary = None
    b = assert_common(c, w, ask(c, w, A(industry="은행", market="KOSPI", constraints=C_BANK)))
    assert b["status"] == "limit"
    assert [r["change"] for r in b["replans"]] == ["market_expand", "similar_industry", "limit_up"]
    assert "시장 확대" in b["answer"] and w.fin.count(2025) == 4


# ---------------- 요청 3 ----------------
def test_req3_no_financials_undecidable(c, w):
    w.with5, w.summary = True, None
    b = assert_common(c, w, ask(c, w, A(industry="반도체", constraints=C_ROE_PER)))
    assert b["status"] == "answered" and b["replans"] == []
    v = {x["stock_code"]: x for x in b["verdicts"]}
    assert v["000005"]["status"] == "UNDECIDABLE" and "NO_FINANCIALS" in v["000005"]["reasons"]
    assert {k: x["status"] for k, x in v.items() if k != "000005"} == \
        {"000001": "PASS", "000002": "FAIL", "000003": "FAIL", "000004": "PASS"}
    assert b["industry_avg"]["PER"]["n"] == 4 and len(calls(b, "get_financials")) == 5
    bad = [t for t in calls(b, "get_financials", "result") if not t["ok"]]
    assert len(bad) == 1 and bad[0]["error_code"] == "NO_FINANCIALS"
    assert "재무정보 없음" in b["answer"]


def test_d4a_all_financial_candidates_error_with_note(c, w, monkeypatch):
    async def empty(cr, year):  # 은행 후보 전부 재무 API 0건
        w.fin.append(year)
        return {"response": {"header": {"resultCode": "00"}, "body": {"items": ""}}}
    monkeypatch.setattr(fin, "_fetch_fin", empty)
    w.summary = None
    b = assert_common(c, w, ask(c, w, A(industry="은행", constraints=C_BANK)), seams=False)
    assert b["status"] == "error" and b["replans"] == []
    assert set(verdicts(b).values()) == {"UNDECIDABLE"} and len(verdicts(b)) == 3
    assert "재무정보 없음 3" in b["answer"] and "※ " + fin.NO_FIN_FINANCIAL in b["answer"]


# ---------------- 요청 4 ----------------
def test_req4_chitchat(c, w):
    b = assert_common(c, w, ask(c, w, A(intent="chitchat"), msg="안녕"))
    assert b["status"] == "answered" and b["tool_calls"] == [] and set(stages(b)) <= {"analyze", "respond"}
    assert b["answer"] == respond.CHITCHAT + "\n\n" + respond.DISCLAIMER
    assert memory.build_state(SID, "x")["conditions"] == an.default_conditions()
    assert [e["payload"]["status"] for e in log_events(c) if e["type"] == "final"] == ["answered"]


# ---------------- 요청 5 / 5b ----------------
def test_req5_asked_then_followup_promotes(c, w):
    b = assert_common(c, w, ask(c, w, A(industry=None, constraints=[{"metric": "ROE"}]), msg="ROE 높은 종목"))
    assert b["status"] == "asked" and b["tool_calls"] == [] and stages(b) == ["analyze", "respond"]
    assert b["answer"].startswith("어떤 산업") and b["answer"].endswith(respond.DISCLAIMER)
    assert (w.find, w.fin, w.price, w.disc) == (0, [], 0, [])
    assert [e["payload"]["status"] for e in log_events(c) if e["type"] == "final"] == ["asked"]
    # 5b: 같은 세션 후속
    b2 = assert_common(c, w, ask(c, w, A(industry="반도체", bare_number=10), msg="반도체에서 10% 이상"), run=2)
    assert b2["status"] == "answered" and w.find == 1
    assert memory.build_state(SID, "x")["conditions"]["constraints"] == \
        [{"metric": "ROE", "op": ">=", "value": 10.0, "ref": None}]
    assert verdicts(b2)["000003"] == "FAIL" and verdicts(b2)["000001"] == "PASS"


# ---------------- 키 누락 2종 ----------------
def test_missing_data_keys(c, w):
    w.set_settings(Settings())
    b = assert_common(c, w, ask(c, w, A(industry="반도체", constraints=C_ROE_PER)), seams=False)
    assert b["status"] == "error" and b["missing_api_key"] is True and b["replans"] == []
    assert "judge" not in stages(b) and ".env" in b["answer"]
    assert (w.find, w.fin, w.disc) == (0, [], [])


def test_missing_llm_key(c, w, monkeypatch):
    async def no_key(messages, *, json_mode, timeout_s=0):
        raise llm.LlmError("MISSING_API_KEY")
    monkeypatch.setattr(llm, "complete", no_key)
    b = assert_common(c, w, c.post("/api/chat", json={"session_id": SID, "message": "x"}), seams=False)
    assert b["status"] == "error" and b["tool_calls"] == [] and stages(b) == ["analyze", "respond"]
    assert b["missing_api_key"] is True and memory._get(SID) is None


# ---------------- reports / logs 연결 ----------------
def test_reports_lookup_logs_flow(c, w):
    b = assert_common(c, w, ask(c, w, A(industry="반도체", constraints=C_ROE_PER)))
    code = b["saveable"][0]["stock_code"]
    assert code == "000001"
    r = c.post("/api/reports", json={"session_id": SID, "stock_code": code})
    assert r.status_code == 201 and r.json()["duplicate_of"] == []
    rid = r.json()["id"]
    rep = c.get(f"/api/reports/{rid}").json()
    v = rep["metrics"]["values"]
    assert v["ROE"] == 20.0 and v["PER"] == 10.0 and rep["disclaimer"] == respond.DISCLAIMER
    assert rep["metrics"] == memory.get_report_draft(SID, code)["metrics"]
    r2 = c.post("/api/reports", json={"session_id": SID, "stock_code": code})
    assert r2.status_code == 201 and r2.json()["duplicate_of"] == [rid]
    time.sleep(0.05)
    p = c.patch(f"/api/reports/{rid}", json={"name": "새이름", "memo": "메모"}).json()
    assert p["name"] == "새이름" and p["memo"] == "메모" and p["metrics"] == rep["metrics"]
    assert p["updated_at"] > rep["updated_at"] and p["created_at"] == rep["created_at"]
    assert c.delete(f"/api/reports/{rid}").status_code == 204
    g = c.get(f"/api/reports/{rid}")
    assert g.status_code == 404 and g.json()["code"] == "REPORT_NOT_FOUND"

    cond = memory.build_state(SID, "x")["conditions"]
    lb = assert_common(c, w, ask(c, w, json.dumps({"intent": "lookup", "reason": "r", "target_stock": "알파반도체"})),
                       run=2)
    assert lb["status"] == "answered"
    assert all(lbl in lb["answer"] for lbl in ("ROE", "ROA", "영업이익률", "PER", "PBR", "PSR", "부채비율"))
    assert memory.build_state(SID, "x")["conditions"] == cond

    lst = c.get("/api/logs").json()
    detail = log_events(c)
    assert len(lst) == 1 and lst[0]["session_id"] == SID and lst[0]["event_count"] == len(detail)
    assert c.delete(f"/api/logs/{SID}").status_code == 204
    g = c.get(f"/api/logs/{SID}")
    assert g.status_code == 404 and g.json()["code"] == "SESSION_NOT_FOUND"
    assert memory.build_state(SID, "x")["conditions"]["industry"] == "반도체"  # 메모리 유지


# ---------------- 정적 서빙 ----------------
def test_static_serving(c, w):
    for p in ("/", "/reports.html", "/chat.js", "/common.js", "/reports.js", "/style.css"):
        r = c.get(p)
        assert r.status_code == 200, p
        assert "script-src 'self'" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
    r = c.get("/reports")
    assert r.status_code == 404 and set(r.json()) == {"code", "message"}
    assert c.get("/api/logs").json() == []  # /api 라우트가 정적보다 우선
