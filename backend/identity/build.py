"""python -m backend.identity.build [--refresh] [--force] — 캐시의 유일한 writer."""
import argparse
import asyncio
import logging
import re
import sys
from datetime import datetime, timezone

from backend.config import settings
from backend.identity import store
from backend.identity.dart import DartSource, HttpDartSource
from backend.identity.models import BuildReport, IdentityError, norm_name
from backend.tools.base import install_log_mask

logger = logging.getLogger(__name__)
COMMIT_EVERY = 100  # 임의 상수(구현 시 확인 항목)

_RESET = "detail_status='pending', crno=NULL, induty_code=NULL, corp_cls=NULL, detail_at=NULL"
_CHG = "company.corp_code!=excluded.corp_code"  # corp_code가 바뀌면 detail 초기화
_UPSERT = f"""
INSERT INTO company(stock_code, corp_code, name, name_norm, listed) VALUES(?,?,?,?,1)
ON CONFLICT(stock_code) DO UPDATE SET corp_code=excluded.corp_code, name=excluded.name,
  name_norm=excluded.name_norm, listed=1,
  detail_status=CASE WHEN {_CHG} THEN 'pending' ELSE company.detail_status END,
  crno=CASE WHEN {_CHG} THEN NULL ELSE company.crno END,
  induty_code=CASE WHEN {_CHG} THEN NULL ELSE company.induty_code END,
  corp_cls=CASE WHEN {_CHG} THEN NULL ELSE company.corp_cls END,
  detail_at=CASE WHEN {_CHG} THEN NULL ELSE company.detail_at END"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _crno(jurir_no) -> str | None:
    d = re.sub(r"\D", "", str(jurir_no or ""))
    return d if re.fullmatch(r"\d{13}", d) else None


async def build(source: DartSource, *, refresh: bool = False, force: bool = False,
                commit_every: int = COMMIT_EVERY) -> BuildReport:
    con = store.connect_rw()
    stopped = None
    try:
        if force:
            con.execute(f"UPDATE company SET {_RESET}")
            con.commit()
        if force or refresh or store.counts(con.execute(store.COUNTS_SQL).fetchone())["listed"] == 0:
            corps = await source.fetch_corp_list()
            if not corps:
                raise IdentityError("API_ERROR")
            try:  # 한 트랜잭션: 전부 listed=0 -> upsert로 응답 종목만 listed=1 복구
                con.execute("UPDATE company SET listed=0")
                con.executemany(_UPSERT, [(sc, cc, nm, norm_name(nm)) for sc, cc, nm in corps])
                con.execute("INSERT OR REPLACE INTO cache_meta VALUES('corp_synced_at', ?)", (_now(),))
                con.commit()
            except BaseException:
                con.rollback()
                raise
            logger.info("stage A done listed=%d", len(corps))
        rows = con.execute("SELECT stock_code, corp_code FROM company WHERE listed=1 AND "
                           "detail_status IN ('pending','failed') ORDER BY stock_code").fetchall()
        n = 0
        try:
            for sc, cc in rows:
                try:
                    body = await source.fetch_company(cc)
                    if body is None:
                        con.execute("UPDATE company SET detail_status='none', detail_at=? WHERE stock_code=?",
                                    (_now(), sc))
                    else:
                        con.execute("UPDATE company SET detail_status='ok', crno=?, induty_code=?, corp_cls=?, "
                                    "detail_at=? WHERE stock_code=?",
                                    (_crno(body.get("jurir_no")), (body.get("induty_code") or "").strip() or None,
                                     body.get("corp_cls") or None, _now(), sc))
                except IdentityError as e:
                    if e.code in ("RATE_LIMIT", "MISSING_API_KEY"):
                        stopped = e.code
                        break
                    con.execute("UPDATE company SET detail_status='failed', detail_at=? WHERE stock_code=?",
                                (_now(), sc))
                n += 1
                if n % commit_every == 0:
                    con.commit()
        finally:
            con.commit()
        c = store.counts(con.execute(store.COUNTS_SQL).fetchone())
        rep = BuildReport(c["ok"], c["none"], c["failed"], c["pending"], stopped, c["complete"])
        logger.info("build ok=%d none=%d failed=%d pending=%d stopped_by=%s complete=%s", *vars(rep).values())
        return rep
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.identity.build")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    install_log_mask()
    if not settings.DART_API_KEY:
        logger.error("MISSING_API_KEY: DART_API_KEY가 설정되지 않았습니다. .env를 확인하세요.")
        return 1
    try:
        rep = asyncio.run(build(HttpDartSource(), refresh=a.refresh, force=a.force))
    except IdentityError as e:
        logger.error("build aborted code=%s", e.code)
        return 1
    return 0 if rep.complete else 1


if __name__ == "__main__":
    sys.exit(main())
