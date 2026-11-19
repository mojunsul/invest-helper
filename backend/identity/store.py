import sqlite3
from pathlib import Path

from backend import config
from backend.identity.models import IdentityError

BUSY_TIMEOUT_S = 5.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS company(
  stock_code TEXT PRIMARY KEY, corp_code TEXT NOT NULL, name TEXT NOT NULL, name_norm TEXT NOT NULL,
  listed INTEGER NOT NULL DEFAULT 1, crno TEXT, induty_code TEXT, corp_cls TEXT,
  detail_status TEXT NOT NULL DEFAULT 'pending', detail_at TEXT);
CREATE INDEX IF NOT EXISTS ix_company_corp ON company(corp_code);
CREATE INDEX IF NOT EXISTS ix_company_name ON company(name_norm);
CREATE INDEX IF NOT EXISTS ix_company_ind ON company(induty_code);
CREATE TABLE IF NOT EXISTS cache_meta(key TEXT PRIMARY KEY, value TEXT);
"""

COUNTS_SQL = ("SELECT COUNT(*), SUM(detail_status='ok'), SUM(detail_status='pending'), "
              "SUM(detail_status='failed'), SUM(detail_status='none') FROM company WHERE listed=1")


def connect_rw() -> sqlite3.Connection:
    """CLI(단일 writer) 전용."""
    con = sqlite3.connect(config.DB_PATH, timeout=BUSY_TIMEOUT_S)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def read(sql: str, params: tuple = ()) -> list[tuple]:
    """읽기 전용·호출 단위 연결. 파일/테이블이 없으면 빈 결과(=빈 캐시)."""
    p = Path(config.DB_PATH)
    if not p.exists():
        return []
    try:
        con = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_S)
        try:
            con.execute("PRAGMA case_sensitive_like=ON")  # LIKE 인덱스 사용(입력은 모두 소문자 정규화)
            return con.execute(sql, params).fetchall()
        finally:
            con.close()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return []
        raise IdentityError("API_ERROR") from None
    except sqlite3.Error:
        raise IdentityError("API_ERROR") from None


def counts(row: tuple | None) -> dict:
    """COUNTS_SQL 결과 1행 -> {listed, ok, pending, failed, none, total, complete}."""
    listed, ok, pending, failed, none = (int(x or 0) for x in (row or (0,) * 5))
    total = listed - none
    return dict(listed=listed, ok=ok, pending=pending, failed=failed, none=none, total=total,
                complete=total > 0 and (pending + failed) * 20 <= total)  # 5%(임의 가정)


def read_counts() -> dict:
    rows = read(COUNTS_SQL)
    return counts(rows[0] if rows else None)


def like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
