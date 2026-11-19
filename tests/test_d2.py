import asyncio
import io
import json
import logging
import sqlite3
import zipfile

import httpx
import pytest

from backend import config
from backend.config import Settings
from backend.identity import build as B
from backend.identity import dart, resolve, store
from backend.identity.models import IdentityError, norm_name
from backend.tools import base

CORPS = [("005930", "00126380", "삼성전자"), ("005935", "00126381", "삼성전자우"),
         ("000660", "00164779", "SK하이닉스"), ("035420", "00266961", "NAVER"),
         ("000001", "00000001", "(주)가_나%다"), ("000002", "00000002", "삼성"),
         ("000003", "00000003", "한국반도체"), ("000004", "00000004", "한국소재")]
IND = {"005930": "26110", "005935": "26110", "000660": "26120", "035420": "63120",
       "000001": "26210", "000002": "26210", "000003": "26299"}  # 000004: 013(none)


class Fake:
    def __init__(self, corps=CORPS, stop_at=None):
        self.corps, self.stop_at, self.calls = corps, stop_at, []

    async def fetch_corp_list(self):
        return self.corps

    async def fetch_company(self, corp_code):
        self.calls.append(corp_code)
        if self.stop_at and len(self.calls) == self.stop_at:
            raise IdentityError("RATE_LIMIT")
        sc = next(s for s, c, _ in CORPS if c == corp_code)
        if sc == "000004":
            return None
        return {"jurir_no": "110111-0000000" if sc == "005930" else "", "induty_code": IND[sc], "corp_cls": "Y"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    return tmp_path / "t.db"


def run(src=None, **kw):
    kw.setdefault("min_interval_s", 0)
    return asyncio.run(B.build(src or Fake(), **kw))


def test_empty_cache_rules(db):
    for f in (lambda: resolve.resolve_stock("삼성"), lambda: resolve.resolve_stock("005930"),
              lambda: resolve.get_corp_code("005930"), lambda: resolve.get_crno("005930"),
              lambda: resolve.select_by_industry(["26"])):
        with pytest.raises(IdentityError) as e:
            f()
        assert e.value.code == "API_ERROR" and "build" in e.value.message
    assert resolve.similar_industries("26") == []
    assert resolve.resolve_industry("26")["status"] == "ok"
    assert resolve.cache_status()["complete"] is False
    sqlite3.connect(db).execute("CREATE TABLE x(a)")  # 파일은 있으나 테이블 없음 -> 빈 캐시
    with pytest.raises(IdentityError):
        resolve.resolve_stock("삼성")


def test_build_and_lookup(db):
    rep = run()
    assert (rep.ok, rep.none, rep.failed, rep.pending_left, rep.complete) == (7, 1, 0, 0, True)
    assert resolve.get_corp_code("005930") == "00126380"
    assert resolve.get_crno("005930") == "1101110000000"
    with pytest.raises(IdentityError) as e:  # crno 없음
        resolve.get_crno("000660")
    assert e.value.code == "NO_RESULT"
    with pytest.raises(IdentityError) as e:
        resolve.get_corp_code("999999")
    assert e.value.code == "NO_RESULT"
    assert resolve.cache_status()["corp_synced_at"]
    codes = [s["stock_code"] for s in resolve.select_by_industry(["261"])]
    assert codes == sorted(["005930", "005935", "000660"])
    for bad in ([], ["2"], ["abc"], ["26%"], "26"):
        with pytest.raises(IdentityError) as e:
            resolve.select_by_industry(bad)
        assert e.value.code == "INVALID_INPUT"


def test_resolve_stock_stages(db):
    run()
    m = resolve.resolve_stock(" 005930 ")
    assert (m.status, m.matched_by, m.matches[0].name) == ("ok", "code", "삼성전자")
    assert resolve.resolve_stock("999999").status == "none"
    m = resolve.resolve_stock("삼성전자")  # 정확 일치에서 멈춤(삼성전자우는 제외)
    assert (m.status, m.matched_by, m.total) == ("ok", "exact", 1)
    m = resolve.resolve_stock("삼성")  # 정확 1건("삼성")
    assert (m.status, m.matched_by) == ("ok", "exact")
    m = resolve.resolve_stock("삼성전")  # 접두 2건, 이름 길이순
    assert (m.status, m.matched_by, m.total) == ("multiple", "prefix", 2)
    assert [r.stock_code for r in m.matches] == ["005930", "005935"]
    m = resolve.resolve_stock("하이닉스")  # 포함
    assert (m.status, m.matched_by) == ("ok", "contains")
    assert resolve.resolve_stock("naver").status == "ok"
    assert resolve.resolve_stock("(주) 가나다").matches[0].stock_code == "000001"
    assert resolve.resolve_stock("없는회사").status == "none"
    assert resolve.resolve_stock("???").status == "none"
    assert resolve.resolve_stock("%").status == "none"  # LIKE 와일드카드 이스케이프
    assert norm_name("㈜가_나") == "가나"


def test_industry(db, tmp_path, monkeypatch):
    p = tmp_path / "terms.json"
    p.write_text(json.dumps([{"term": "반도체", "prefixes": ["261"]}, {"term": "반도체장비", "prefixes": ["262"]},
                             {"term": "소프트웨어", "prefixes": ["631"]}]), "utf-8")
    monkeypatch.setattr(resolve, "TERMS_PATH", p)
    resolve._terms.cache_clear()
    run()
    r = resolve.resolve_industry("반도체")
    assert (r["status"], r["prefixes"]) == ("ok", ["261"])  # 정확 일치 우선
    assert resolve.resolve_industry("반도체 산업업")["status"] == "unknown"  # 접미 단일 패스: '반도체산업' 남음
    assert resolve.resolve_industry("반도체관련주")["status"] == "ok"
    r = resolve.resolve_industry("반도")  # 두 term에 포함 -> ambiguous
    assert r["status"] == "ambiguous" and len(r["options"]) == 2 and r["options"][0]["n_listed"] == 3
    assert resolve.resolve_industry("전혀모름")["status"] == "unknown"
    assert resolve.resolve_industry("업")["status"] == "unknown"
    assert resolve.resolve_industry("26")["prefixes"] == ["26"]
    # 유사 업종: 261 제외, 부모 26 아래 262(2건 합산 000001,000002), 262가 5개 안에
    assert resolve.similar_industries("반도체") == ["262"]
    assert resolve.similar_industries("26") == []  # 길이 2 확장 불가
    assert resolve.similar_industries("모름") == []
    resolve._terms.cache_clear()


def test_terms_validation(tmp_path, monkeypatch):
    for terms in ([{"term": "가", "prefixes": ["1"]}],
                  [{"term": "반도체업", "prefixes": ["1"]}, {"term": "반도체산업", "prefixes": ["2"]}]):
        p = tmp_path / "t.json"
        p.write_text(json.dumps(terms), "utf-8")
        monkeypatch.setattr(resolve, "TERMS_PATH", p)
        resolve._terms.cache_clear()
        with pytest.raises(ValueError):
            resolve._terms()
    resolve._terms.cache_clear()


def test_stage_a_and_b_resume(db):
    f = Fake(stop_at=3)
    rep = run(f)
    assert rep.stopped_by == "RATE_LIMIT" and rep.complete is False and rep.pending_left == 6
    f2 = Fake()
    rep = run(f2)  # 재개: 중단 행부터 pending만
    assert rep.complete and len(f2.calls) == 6
    # 단계 A 재실행(--refresh): corp_code 변경 행 초기화, 사라진 종목 listed=0, ok 행은 보존
    corps = [c for c in CORPS if c[0] != "000003"]
    corps[0] = ("005930", "99999999", "삼성전자")
    f3 = Fake(corps)
    f3.fetch_company = lambda cc: asyncio.sleep(0, {"jurir_no": "", "induty_code": "26111", "corp_cls": "Y"})
    rep = run(f3, refresh=True)
    con = sqlite3.connect(db)
    assert con.execute("SELECT listed FROM company WHERE stock_code='000003'").fetchone() == (0,)
    assert con.execute("SELECT induty_code, corp_code FROM company WHERE stock_code='005930'").fetchone() == \
        ("26111", "99999999")
    assert con.execute("SELECT induty_code FROM company WHERE stock_code='000660'").fetchone() == ("26120",)
    with pytest.raises(IdentityError):
        resolve.get_corp_code("000003")


def test_empty_corp_list_rejected_keeps_data(db):
    run()
    with pytest.raises(IdentityError) as e:
        run(Fake([]), refresh=True)
    assert e.value.code == "API_ERROR"
    assert resolve.cache_status()["total"] == 7


def test_force_resets_then_rebuilds(db):
    run()
    f = Fake(stop_at=2)
    rep = run(f, force=True)
    assert rep.stopped_by == "RATE_LIMIT" and rep.complete is False
    with pytest.raises(IdentityError) as e:
        resolve.select_by_industry(["26"])
    assert "다시 실행" in e.value.message
    assert resolve.resolve_stock("삼성전자").status == "ok"  # 미완료에서도 이름 조회는 정상


def test_failed_row_continues_and_retried(db):
    class Flaky(Fake):
        async def fetch_company(self, cc):
            if cc == "00126380":
                raise IdentityError("TIMEOUT")
            return await super().fetch_company(cc)
    rep = run(Flaky())
    assert rep.failed == 1 and not rep.complete  # 1*20 > 7
    rep = run()
    assert rep.failed == 0 and rep.complete


def _zip(members: dict[str, bytes]) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for k, v in members.items():
            z.writestr(k, v)
    return b.getvalue()


XML = ("<result><list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name>"
       "<stock_code>005930</stock_code></list><list><corp_code>1</corp_code><corp_name>비상장</corp_name>"
       "<stock_code> </stock_code></list></result>").encode()


def test_corp_xml_parsing_safety(monkeypatch):
    assert dart.parse_corp_xml(_zip({"CORPCODE.xml": XML})) == [("005930", "00126380", "삼성전자")]
    for bad in (_zip({"a.xml": XML}), _zip({"CORPCODE.xml": XML, "b": b"x"}),
                _zip({"CORPCODE.xml": b'<!DOCTYPE r [<!ENTITY x "y">]><r>&x;</r>'})):
        with pytest.raises(Exception):
            dart.parse_corp_xml(bad)
    monkeypatch.setattr(dart, "CORP_XML_MAX_BYTES", 10)
    with pytest.raises(IdentityError):
        dart.parse_corp_xml(_zip({"CORPCODE.xml": XML}))
    monkeypatch.setattr(dart, "CORP_ZIP_MAX_BYTES", 10)
    with pytest.raises(IdentityError):
        dart.parse_corp_xml(_zip({"CORPCODE.xml": XML}))


KEY = "SECRETKEY123"
REAL = httpx.AsyncClient


def _mock(monkeypatch, handler):
    monkeypatch.setattr(base, "settings", Settings(DART_API_KEY=KEY))
    monkeypatch.setattr(dart, "settings", Settings(DART_API_KEY=KEY))
    monkeypatch.setattr(dart.httpx, "AsyncClient",
                        lambda **kw: REAL(transport=httpx.MockTransport(handler), **kw))


@pytest.mark.parametrize("status,code", [("010", "MISSING_API_KEY"), ("011", "MISSING_API_KEY"),
                                         ("020", "RATE_LIMIT"), ("800", "API_ERROR")])
def test_company_status_mapping(monkeypatch, status, code):
    _mock(monkeypatch, lambda r: httpx.Response(200, json={"status": status}))
    with pytest.raises(IdentityError) as e:
        asyncio.run(dart.HttpDartSource().fetch_company("1"))
    assert e.value.code == code


def test_company_ok_013_429_and_garbage(monkeypatch):
    _mock(monkeypatch, lambda r: httpx.Response(200, json={"status": "013"}))
    assert asyncio.run(dart.HttpDartSource().fetch_company("1")) is None
    _mock(monkeypatch, lambda r: httpx.Response(200, json={"status": "000", "induty_code": "26"}))
    assert asyncio.run(dart.HttpDartSource().fetch_company("1"))["induty_code"] == "26"
    for resp, code in ((httpx.Response(429), "RATE_LIMIT"), (httpx.Response(500), "API_ERROR"),
                       (httpx.Response(200, content=b"not json"), "API_ERROR")):
        _mock(monkeypatch, lambda r, resp=resp: resp)
        with pytest.raises(IdentityError) as e:
            asyncio.run(dart.HttpDartSource().fetch_company("1"))
        assert e.value.code == code


def test_key_not_leaked_on_exception(monkeypatch, caplog):
    def boom(request):
        raise httpx.ConnectError(f"fail {request.url}")  # URL에 키 포함
    _mock(monkeypatch, boom)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(IdentityError) as e:
        asyncio.run(dart.HttpDartSource().fetch_company("1"))
    assert e.value.code == "API_ERROR" and e.value.__cause__ is None and e.value.__suppress_context__
    assert KEY not in e.value.message and KEY not in caplog.text

    def to(request):
        raise httpx.ReadTimeout("t")
    _mock(monkeypatch, to)
    with pytest.raises(IdentityError) as e:
        asyncio.run(dart.HttpDartSource().fetch_company("1"))
    assert e.value.code == "TIMEOUT"


def test_main_missing_key(monkeypatch):
    monkeypatch.setattr(B, "settings", Settings(DART_API_KEY=""))
    assert B.main([]) == 1


class Seq(Fake):
    """calls[i]번째(1부터) 호출의 동작을 plan(i)로 결정: None=정상, 예외 인스턴스=raise."""
    def __init__(self, plan):
        super().__init__()
        self.plan = plan

    async def fetch_company(self, corp_code):
        err = self.plan(len(self.calls) + 1)
        if err:
            self.calls.append(corp_code)
            raise err
        return await super().fetch_company(corp_code)


def test_build_interval(db, monkeypatch):
    got = []

    async def rec(s):
        got.append(s)
    monkeypatch.setattr(B, "_sleep", rec)
    f = Fake()
    run(f, min_interval_s=0.2)
    assert got == [0.2] * len(f.calls) and got
    got.clear()
    run(Fake(), force=True, min_interval_s=0)
    assert got == []


def test_network_streak_stops_and_resumes(db, monkeypatch):
    monkeypatch.setattr(B, "NETWORK_ABORT_AFTER", 3)
    f = Seq(lambda i: dart.DartNetworkError() if i >= 3 else None)
    rep = run(f)
    assert rep.stopped_by == "NETWORK" and len(f.calls) == 2 + 3
    assert rep.failed == 3 and rep.pending_left == len(CORPS) - 5 and rep.complete is False
    rep = run(Fake())
    assert rep.failed == 0 and rep.complete


def test_network_streak_stops_and_logs(db, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    run(Seq(lambda i: dart.DartNetworkError()))
    assert "옵션 없이 다시 실행" in caplog.text


def test_timeout_counts_in_streak(db):
    f = Seq(lambda i: IdentityError("TIMEOUT"))
    rep = run(f)
    assert rep.stopped_by == "NETWORK" and len(f.calls) == B.NETWORK_ABORT_AFTER


def test_success_resets_streak(db, monkeypatch):
    monkeypatch.setattr(B, "NETWORK_ABORT_AFTER", 2)
    f = Seq(lambda i: dart.DartNetworkError() if i % 2 else None)
    rep = run(f)
    assert rep.stopped_by is None and len(f.calls) == len(CORPS)


def test_plain_api_error_continues(db):
    f = Seq(lambda i: IdentityError("API_ERROR"))
    rep = run(f)
    assert rep.stopped_by is None and len(f.calls) == len(CORPS) and rep.failed == len(CORPS)


@pytest.mark.parametrize("exc,net,code", [(httpx.ReadError("x"), True, "API_ERROR"),
                                          (httpx.RemoteProtocolError("x"), True, "API_ERROR"),
                                          (httpx.ReadTimeout("x"), False, "TIMEOUT")])
def test_dart_transport_mapping(monkeypatch, exc, net, code):
    def h(request):
        raise exc
    _mock(monkeypatch, h)
    with pytest.raises(IdentityError) as e:
        asyncio.run(dart.HttpDartSource().fetch_company("1"))
    assert isinstance(e.value, dart.DartNetworkError) is net and e.value.code == code


@pytest.mark.parametrize("resp", [httpx.Response(200, content=b"not json"), httpx.Response(500)])
def test_dart_non_network_errors(monkeypatch, resp):
    _mock(monkeypatch, lambda r: resp)
    with pytest.raises(IdentityError) as e:
        asyncio.run(dart.HttpDartSource().fetch_company("1"))
    assert not isinstance(e.value, dart.DartNetworkError) and e.value.code == "API_ERROR"


class _Flaky6(Fake):  # 정렬상 6번째(005930)만 TIMEOUT
    async def fetch_company(self, cc):
        if cc == "00126380":
            raise IdentityError("TIMEOUT")
        return await super().fetch_company(cc)


def _stage_b(caplog):
    msgs = [r.getMessage() for r in caplog.records]
    return [m for m in msgs if m.startswith("stage B start")], [m for m in msgs if m.startswith("stage B progress")]


def test_progress_log_start_and_periodic(db, caplog):
    caplog.set_level(logging.INFO)
    run(commit_every=3)
    start, prog = _stage_b(caplog)
    assert "total=8" in start[0] and len(prog) == 2
    assert "3/8 ok=3 none=0 failed=0" in prog[0] and "6/8 ok=5 none=1 failed=0" in prog[1]


def test_progress_log_counts_failed(db, caplog):
    caplog.set_level(logging.INFO)
    run(_Flaky6(), commit_every=3)
    _, prog = _stage_b(caplog)
    assert len(prog) == 2 and "6/8 ok=4 none=1 failed=1" in prog[1]


def test_progress_log_none_when_below_period_or_done(db, caplog):
    caplog.set_level(logging.INFO)
    run()
    start, prog = _stage_b(caplog)
    assert "total=8" in start[0] and prog == []
    caplog.clear()
    run()
    start, prog = _stage_b(caplog)
    assert "total=0" in start[0] and prog == []
