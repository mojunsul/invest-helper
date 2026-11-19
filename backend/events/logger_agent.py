import json
import logging
import sqlite3
import threading

from backend import config
from backend.events import bus
from backend.events.bus import Event
from backend.tools.base import mask

logger = logging.getLogger(__name__)
_write_lock = threading.Lock()
_installed = False

_DDL = """
CREATE TABLE IF NOT EXISTS run_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, seq INTEGER NOT NULL,
  ts TEXT NOT NULL, type TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS run_logs_session ON run_logs(session_id);
"""


def _exec(sql: str, args: tuple = (), fetch: bool = False):
    con = sqlite3.connect(config.DB_PATH)
    try:
        with con:
            cur = con.execute(sql, args)
            return cur.fetchall() if fetch else cur.rowcount
    finally:
        con.close()


def init_table() -> None:
    con = sqlite3.connect(config.DB_PATH)
    try:
        with con:
            con.executescript(_DDL)
    finally:
        con.close()


def install() -> None:
    global _installed
    init_table()
    with _write_lock:
        if _installed:
            return
        _installed = True
    bus.subscribe(record)


def record(ev: Event) -> None:
    try:
        pj = mask(json.dumps(ev.payload, ensure_ascii=False))
        with _write_lock:
            _exec("INSERT INTO run_logs(session_id, seq, ts, type, payload_json) VALUES (?,?,?,?,?)",
                  (ev.session_id, ev.seq, ev.ts, ev.type, pj))
    except Exception as e:
        logger.warning("run_logs insert failed: %s", mask(str(e)))


def list_sessions() -> list[dict]:
    rows = _exec("SELECT session_id, MIN(ts), COUNT(*) FROM run_logs GROUP BY session_id "
                 "ORDER BY MIN(ts) DESC", fetch=True)
    return [{"session_id": s, "started_at": t, "event_count": n} for s, t, n in rows]


def get_session(session_id: str) -> list[Event]:
    rows = _exec("SELECT session_id, seq, ts, type, payload_json FROM run_logs "
                 "WHERE session_id=? ORDER BY id", (session_id,), fetch=True)
    return [Event(session_id=s, seq=q, ts=t, type=ty, payload=json.loads(p)) for s, q, t, ty, p in rows]


def delete_session(session_id: str) -> int:
    with _write_lock:
        return _exec("DELETE FROM run_logs WHERE session_id=?", (session_id,))
