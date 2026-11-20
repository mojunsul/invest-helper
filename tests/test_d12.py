import copy
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import config
from backend.agent import memory
from backend.agent.nodes import respond
from backend.api import reports
from backend.db import sqlite as db
from backend.events import logger_agent as la

SID = "s1"
ROW = {"values": {"PER": 10.5, "PBR": None}, "fiscal_period": "2024Q4", "actual_basis": "2024Q4",
       "estimated": True, "reasons": {"PBR": "NO_DATA"}}


def draft(code="005930", name="삼성전자", bd="2024-12-31", disc=None):
    return respond._drafts_base({"candidates": [{"code": code, "name": name}]}, code, copy.deepcopy(ROW),
                                {"PER": {"value": 9.0, "n": 3}}, disc, bd)


def seed(d, sid=SID):
    memory.update_memory(sid, {"intent": "screen", "status": "answered", "report_draft": {d["stock_code"]: d}})


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    memory.clear_all()
    db.init_table()
    yield
    memory.clear_all()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(reports.router)
    return TestClient(app, raise_server_exceptions=False)


def post(c, **kw):
    return c.post("/api/reports", json={"session_id": SID, "stock_code": "005930", **kw})


# ---- sqlite ----
def test_init_idempotent_and_order_independent():
    db.init_table(); db.init_table(); la.init_table(); db.init_table()
    names = {r[0] for r in sqlite3.connect(config.DB_PATH).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"reports", "run_logs"} <= names


def test_save_mapping_and_disclosures_none_vs_empty():
    a, _ = db.save_report(draft(disc=None), None, None)
    b, _ = db.save_report(draft(disc=[]), "n", "m")
    c, _ = db.save_report(draft(disc=[{"title": "공시", "date": "2024-01-01", "url": "http://x"}]), None, None)
    ra, rb, rc = db.get_report(a), db.get_report(b), db.get_report(c)
    assert ra["disclosures"] is None and rb["disclosures"] == []
    assert rc["disclosures"][0]["title"] == "공시"
    assert ra["name"] == "삼성전자 리포트" and ra["memo"] == "" and rb["name"] == "n" and rb["memo"] == "m"
    assert ra["disclaimer"] == respond.DISCLAIMER and ra["metrics"] == ROW
    assert ra["industry_avg"] == {"PER": {"value": 9.0, "n": 3}} and ra["base_date"] == "2024-12-31"
    raw = sqlite3.connect(config.DB_PATH).execute("SELECT metrics_json FROM reports WHERE id=?", (a,)).fetchone()[0]
    assert "삼성전자" not in raw and "NO_DATA" in raw  # 한글은 ensure_ascii=False로 보존되는지는 name 컬럼으로 확인
    assert db.get_report(a)["stock_name"] == "삼성전자"


def test_nan_rejected():
    d = draft()
    d["metrics"]["values"]["PER"] = float("nan")
    with pytest.raises(ValueError):
        db.save_report(d, None, None)


def test_duplicates():
    i1, d1 = db.save_report(draft(), None, None)
    i2, d2 = db.save_report(draft(), None, None)
    i3, d3 = db.save_report(draft(bd="2023-12-31"), None, None)
    i4, d4 = db.save_report(draft(code="000660"), None, None)
    assert d1 == [] and d2 == [i1] and d3 == [] and d4 == []
    n1, e1 = db.save_report(draft(bd=None), None, None)
    n2, e2 = db.save_report(draft(bd=None), None, None)
    assert e1 == [] and e2 == [n1] and len(db.list_reports()) == 6


def test_update_only_name_memo(monkeypatch):
    ticks = iter(f"2024-01-01T00:00:{n:02d}+00:00" for n in range(60))  # Windows 시계 틱 의존 제거
    monkeypatch.setattr(db, "_now", lambda: next(ticks))
    i, _ = db.save_report(draft(), None, None)
    before = db.get_report(i)
    after = db.update_report(i, "새이름", None)
    assert after["name"] == "새이름" and after["memo"] == before["memo"]
    assert after["metrics"] == before["metrics"] and after["base_date"] == before["base_date"]
    assert after["created_at"] == before["created_at"] and after["updated_at"] >= before["updated_at"]
    assert after["updated_at"] != before["updated_at"]
    assert db.update_report(i, None, "메모")["memo"] == "메모"
    assert db.update_report(999, "x", None) is None


def test_delete_and_missing():
    i, _ = db.save_report(draft(), None, None)
    assert db.delete_report(i) is True and db.get_report(i) is None
    assert db.delete_report(i) is False and db.get_report(12345) is None


def test_list_order_desc():
    ids = [db.save_report(draft(), None, None)[0] for _ in range(3)]
    assert [r["id"] for r in db.list_reports()] == ids[::-1]
    assert "metrics" not in db.list_reports()[0]


def test_sql_injection_and_xss_preserved():
    evil, xss = "'; DROP TABLE reports;--", "<script>alert(1)</script>"
    i, _ = db.save_report(draft(), evil, xss)
    r = db.get_report(i)
    assert r["name"] == evil and r["memo"] == xss
    db.update_report(i, xss, evil)
    r = db.get_report(i)
    assert r["name"] == xss and r["memo"] == evil
    assert db.list_reports()


def test_disclaimer_import_no_cycle():
    import subprocess, sys
    assert subprocess.run([sys.executable, "-c", "import backend.db.sqlite"]).returncode == 0
    assert subprocess.run([sys.executable, "-c", "import backend.api.reports"]).returncode == 0


# ---- API ----
def test_post_201_and_get_match(client):
    d = draft(disc=[])
    seed(d)
    r = post(client, name="  내 리포트 ", memo="메모")
    assert r.status_code == 201 and r.json()["duplicate_of"] == []
    g = client.get(f"/api/reports/{r.json()['id']}").json()
    assert g["name"] == "내 리포트" and g["memo"] == "메모" and g["metrics"] == d["metrics"]
    assert g["disclosures"] == [] and g["disclaimer"] == respond.DISCLAIMER
    r2 = post(client)
    assert r2.status_code == 201 and r2.json()["duplicate_of"] == [r.json()["id"]]
    assert len(client.get("/api/reports").json()) == 2


def test_draft_not_found(client):
    r = post(client)
    assert r.status_code == 404 and r.json() == {"code": "DRAFT_NOT_FOUND", "message": "먼저 해당 종목을 조회하세요."}
    seed(draft())
    assert client.post("/api/reports", json={"session_id": SID, "stock_code": "000660"}).status_code == 404
    assert client.post("/api/reports", json={"session_id": "other", "stock_code": "005930"}).status_code == 404


def test_draft_key_content_mismatch(client):
    d = draft(code="000660")
    memory.update_memory(SID, {"intent": "screen", "status": "answered", "report_draft": {"005930": d}})
    assert post(client).json()["code"] == "DRAFT_NOT_FOUND"


@pytest.mark.parametrize("body", [
    {"session_id": SID, "stock_code": "12345"}, {"session_id": SID, "stock_code": "12345a"},
    {"session_id": "", "stock_code": "005930"}, {"session_id": "x" * 65, "stock_code": "005930"},
    {"session_id": SID, "stock_code": "005930", "name": "   "},
    {"session_id": SID, "stock_code": "005930", "name": "n" * 101},
    {"session_id": SID, "stock_code": "005930", "memo": "m" * 1001},
    {"session_id": SID, "stock_code": "005930", "metrics": {"PER": 1}},
    {"session_id": SID, "stock_code": "005930", "base_date": "x"},
    {"stock_code": "005930"},
])
def test_post_invalid(client, body):
    seed(draft())
    r = client.post("/api/reports", json=body)
    assert r.status_code == 422 and set(r.json()) == {"code", "message"} and r.json()["code"] == "INVALID_REQUEST"


def test_boundary_lengths_ok(client):
    seed(draft())
    assert post(client, name="n" * 100, memo="m" * 1000).status_code == 201
    assert post(client, memo="").status_code == 201


def test_no_input_reflection(client):
    seed(draft())
    secret = "SECRET-INJECT-VALUE"
    r = client.post("/api/reports", json={"session_id": SID, "stock_code": "005930", secret: secret})
    assert secret not in r.text
    r = client.post("/api/reports", json={"session_id": SID, "stock_code": secret})
    assert secret not in r.text
    assert client.post("/api/reports", content=b"{bad", headers={"content-type": "application/json"}).status_code == 422


def test_patch(client):
    seed(draft())
    i = post(client).json()["id"]
    for body in ({"metrics": {}}, {}, {"name": "x", "base_date": "y"}, {"name": ""}, {"memo": "m" * 1001}):
        r = client.patch(f"/api/reports/{i}", json=body)
        assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"
    r = client.patch(f"/api/reports/{i}", json={"memo": "새 메모"})
    assert r.status_code == 200 and r.json()["memo"] == "새 메모" and r.json()["name"] == "삼성전자 리포트"
    assert r.json()["metrics"] == ROW
    r = client.patch("/api/reports/999", json={"memo": "x"})
    assert r.status_code == 404 and r.json()["code"] == "REPORT_NOT_FOUND"


def test_delete_flow_and_path_ids(client):
    seed(draft())
    i = post(client).json()["id"]
    r = client.delete(f"/api/reports/{i}")
    assert r.status_code == 204 and r.content == b""
    assert client.get(f"/api/reports/{i}").status_code == 404
    assert client.delete(f"/api/reports/{i}").json()["code"] == "REPORT_NOT_FOUND"
    for bad in ("abc", "0", "-1"):
        r = client.get(f"/api/reports/{bad}")
        assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"


def test_internal_error_fixed_message(client, monkeypatch):
    def boom(*a):
        raise RuntimeError("secret path C:\\x SELECT * FROM reports")
    monkeypatch.setattr(db, "list_reports", boom)
    r = client.get("/api/reports")
    assert r.status_code == 500
    assert r.json() == {"code": "INTERNAL_ERROR", "message": "요청을 처리하지 못했습니다."}


# ---- 통합 ----
def test_snapshot_survives_draft_replacement(client):
    seed(draft())
    i = post(client).json()["id"]
    before = client.get(f"/api/reports/{i}").json()
    seed(draft(bd="2025-03-31"))
    memory.clear_all()
    assert client.get(f"/api/reports/{i}").json() == before


def test_lookup_style_draft_saveable(client):
    d = respond._drafts_base({"candidates": [{"code": "005930", "name": "삼성전자"}]}, "005930", copy.deepcopy(ROW),
                             {}, None, ROW["actual_basis"])
    seed(d)
    i = post(client).json()["id"]
    g = client.get(f"/api/reports/{i}").json()
    assert g["base_date"] == "2024Q4" and g["industry_avg"] == {} and g["disclosures"] is None


# ---- D12b review_date ----
def _post(client, **kw):
    d = draft()
    seed(d)
    return client.post("/api/reports", json={"session_id": SID, "stock_code": d["stock_code"], **kw})


def test_review_migration_idempotent():
    con = sqlite3.connect(config.DB_PATH)
    con.execute("DROP TABLE reports")
    con.execute("CREATE TABLE reports(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, stock_code TEXT NOT NULL, "
                "stock_name TEXT NOT NULL, base_date TEXT, metrics_json TEXT NOT NULL, industry_avg_json TEXT NOT NULL, "
                "disclosures_json TEXT, disclaimer TEXT NOT NULL, memo TEXT NOT NULL DEFAULT '', "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    con.execute("INSERT INTO reports(name,stock_code,stock_name,base_date,metrics_json,industry_avg_json,disclaimer,memo,"
                "created_at,updated_at) VALUES('old','005930','삼성전자','2024-12-31','{}','{}','d','m','t','t')")
    con.commit()
    con.close()
    db.init_table()
    db.init_table()
    r = db.get_report(1)
    assert r["name"] == "old" and r["memo"] == "m" and r["review_date"] is None


def test_review_post_get(client):
    assert _post(client, review_date="2026-12-01").status_code == 201
    assert client.get("/api/reports/1").json()["review_date"] == "2026-12-01"
    assert client.get("/api/reports").json()[0]["review_date"] == "2026-12-01"
    _post(client)
    assert client.get("/api/reports/2").json()["review_date"] is None


def test_review_patch(client):
    _post(client, name="n", memo="m")
    before = client.get("/api/reports/1").json()
    r = client.patch("/api/reports/1", json={"review_date": "2027-01-02"})
    assert r.status_code == 200 and r.json()["review_date"] == "2027-01-02"
    assert r.json()["name"] == "n" and r.json()["memo"] == "m" and r.json()["updated_at"] > before["updated_at"]
    r = client.patch("/api/reports/1", json={"review_date": ""})
    assert r.status_code == 200 and r.json()["review_date"] is None


@pytest.mark.parametrize("v", ["2026-02-30", "2026/12/01", "20261201", 123])
def test_review_invalid(client, v):
    assert _post(client, review_date=v).status_code == 422
    _post(client)
    assert client.patch("/api/reports/1", json={"review_date": v}).status_code == 422


def test_review_past_ok(client):
    assert _post(client, review_date="2000-01-01").status_code == 201
    assert client.patch("/api/reports/1", json={"review_date": "2000-01-02"}).status_code == 200


def test_review_not_in_duplicate(client):
    _post(client, review_date="2026-12-01")
    assert _post(client, review_date="2027-01-01").json()["duplicate_of"] == [1]
