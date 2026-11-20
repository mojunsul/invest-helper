import json
import sqlite3
import threading
from datetime import datetime, timezone

from backend import config
from backend.agent.nodes.respond import DISCLAIMER

_write_lock = threading.Lock()

_DDL = """
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, stock_code TEXT NOT NULL, stock_name TEXT NOT NULL, base_date TEXT,
  metrics_json TEXT NOT NULL, industry_avg_json TEXT NOT NULL, disclosures_json TEXT,
  disclaimer TEXT NOT NULL, memo TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS reports_stock ON reports(stock_code, base_date);
"""
_COLS = ("id, name, stock_code, stock_name, base_date, metrics_json, industry_avg_json, "
         "disclosures_json, disclaimer, memo, created_at, updated_at, review_date")


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(config.DB_PATH)


def _dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False, allow_nan=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summary(r: tuple) -> dict:
    return {"id": r[0], "name": r[1], "stock_code": r[2], "stock_name": r[3], "base_date": r[4],
            "created_at": r[10], "updated_at": r[11], "review_date": r[12]}


def _report(r: tuple) -> dict:
    return {**_summary(r), "metrics": json.loads(r[5]), "industry_avg": json.loads(r[6]),
            "disclosures": None if r[7] is None else json.loads(r[7]), "disclaimer": r[8], "memo": r[9]}


def init_table() -> None:
    con = _connect()
    try:
        with con:
            con.executescript(_DDL)
            if "review_date" not in {r[1] for r in con.execute("PRAGMA table_info(reports)")}:
                con.execute("ALTER TABLE reports ADD COLUMN review_date TEXT")  # 기존 DB 마이그레이션, 기존 행은 NULL
    finally:
        con.close()


def save_report(draft: dict, name: str | None, memo: str | None,
                review_date: str | None = None) -> tuple[int, list[int]]:
    now = _now()
    disc = draft.get("disclosures")
    row = (name if name is not None else f"{draft['stock_name']} 리포트", draft["stock_code"], draft["stock_name"],
           draft.get("base_date"), _dumps(draft["metrics"]), _dumps(draft.get("industry_avg") or {}),
           None if disc is None else _dumps(disc), DISCLAIMER, memo or "", now, now, review_date or None)
    with _write_lock:
        con = _connect()
        try:
            with con:
                dup = [r[0] for r in con.execute(
                    "SELECT id FROM reports WHERE stock_code=? AND base_date IS ? ORDER BY id",
                    (row[1], row[3]))]
                cur = con.execute(
                    "INSERT INTO reports(name, stock_code, stock_name, base_date, metrics_json, industry_avg_json, "
                    "disclosures_json, disclaimer, memo, created_at, updated_at, review_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
                return cur.lastrowid, dup
        finally:
            con.close()


def _query(sql: str, args: tuple = ()) -> list[tuple]:
    con = _connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def list_reports() -> list[dict]:
    return [_summary(r) for r in _query(f"SELECT {_COLS} FROM reports ORDER BY created_at DESC, id DESC")]


def get_report(report_id: int) -> dict | None:
    rows = _query(f"SELECT {_COLS} FROM reports WHERE id=?", (report_id,))
    return _report(rows[0]) if rows else None


def update_report(report_id: int, name: str | None, memo: str | None,
                  review_date: str | None = None) -> dict | None:
    # 컬럼은 name/memo/review_date 고정 화이트리스트만 사용, 값은 전부 ? 바인딩
    sets, args = ["updated_at=?"], [_now()]
    if name is not None:
        sets.append("name=?")
        args.append(name)
    if memo is not None:
        sets.append("memo=?")
        args.append(memo)
    if review_date is not None:
        sets.append("review_date=?")
        args.append(review_date or None)  # "" -> NULL
    with _write_lock:
        con = _connect()
        try:
            with con:
                n = con.execute(f"UPDATE reports SET {', '.join(sets)} WHERE id=?", (*args, report_id)).rowcount
        finally:
            con.close()
    return get_report(report_id) if n else None


def delete_report(report_id: int) -> bool:
    with _write_lock:
        con = _connect()
        try:
            with con:
                return con.execute("DELETE FROM reports WHERE id=?", (report_id,)).rowcount > 0
        finally:
            con.close()
