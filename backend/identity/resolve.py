"""요청 경로: 동기·읽기 전용(DB만)."""
import json
import re
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path

from backend.identity import store
from backend.identity.models import (CACHE_NOT_READY, INDUSTRY_NOT_READY, CacheStatus, IdentityError,
                                     IndustryResolution, IndustryStock, StockMatch, StockRef, norm_name)

TERMS_PATH = Path(__file__).parent / "data" / "industry_terms.json"
_SUFFIX = re.compile(r"(업종|산업|관련주|섹터|업)$")
_DIGITS = re.compile(r"^\d{2,5}$")


def _require_cache() -> dict:
    c = store.read_counts()
    if c["listed"] == 0:
        raise IdentityError("API_ERROR", CACHE_NOT_READY)
    return c


def _str_arg(x) -> str:
    if not isinstance(x, str):
        raise IdentityError("INVALID_INPUT")
    return x


def resolve_stock(query: str) -> StockMatch:
    _str_arg(query)
    _require_cache()
    q = unicodedata.normalize("NFKC", query).strip()
    cols = "stock_code, corp_code, name"
    if re.fullmatch(r"[0-9A-Z]{6}", q):
        rows = store.read(f"SELECT {cols} FROM company WHERE listed=1 AND stock_code=?", (q,))
        if rows:
            return StockMatch("ok", (StockRef(*rows[0]),), 1, "code")
        return StockMatch("none", (), 0, None)
    n = norm_name(q)
    if not n:
        return StockMatch("none", (), 0, None)
    esc = store.like_escape(n)
    stages = [("exact", "name_norm=?", n), ("prefix", "name_norm LIKE ? ESCAPE '\\'", esc + "%")]
    if len(n) >= 2:
        stages.append(("contains", "name_norm LIKE ? ESCAPE '\\'", "%" + esc + "%"))
    for by, cond, p in stages:
        rows = store.read(f"SELECT {cols} FROM company WHERE listed=1 AND {cond} "
                          "ORDER BY length(name), stock_code", (p,))
        if rows:
            refs = tuple(StockRef(*r) for r in rows[:10])
            return StockMatch("ok" if len(rows) == 1 else "multiple", refs, len(rows), by)
    return StockMatch("none", (), 0, None)


def get_corp_code(stock_code: str) -> str:
    _str_arg(stock_code)
    _require_cache()
    rows = store.read("SELECT corp_code FROM company WHERE listed=1 AND stock_code=?", (stock_code,))
    if not rows:
        raise IdentityError("NO_RESULT")
    return rows[0][0]


def get_crno(stock_code: str) -> str:
    _str_arg(stock_code)
    _require_cache()
    rows = store.read("SELECT crno FROM company WHERE listed=1 AND stock_code=?", (stock_code,))
    if not rows or not rows[0][0]:
        raise IdentityError("NO_RESULT")
    return rows[0][0]


# ---- 산업 ----
def _norm_term(s: str) -> str:
    return _SUFFIX.sub("", re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)))


@lru_cache(maxsize=1)
def _terms() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """(정규화 term, 원문 term, prefixes). 길이<2 또는 중복이면 ValueError."""
    out, seen = [], set()
    for e in json.loads(TERMS_PATH.read_text(encoding="utf-8")):
        n = _norm_term(e["term"])
        if len(n) < 2 or n in seen:
            raise ValueError(f"invalid industry term: {e['term']!r}")
        seen.add(n)
        out.append((n, e["term"], tuple(e["prefixes"])))
    return tuple(out)


def _like_any(prefixes) -> tuple[str, tuple]:
    return ("(" + " OR ".join("induty_code LIKE ? ESCAPE '\\'" for _ in prefixes) + ")",
            tuple(store.like_escape(p) + "%" for p in prefixes))


def _n_listed(prefixes) -> int:
    cond, params = _like_any(prefixes)
    return store.read(f"SELECT COUNT(*) FROM company WHERE listed=1 AND {cond}", params)[0][0]


def resolve_industry(text: str) -> IndustryResolution:
    unknown: IndustryResolution = {"status": "unknown", "prefixes": [], "label": "", "options": []}
    if not isinstance(text, str):
        return unknown
    q = _norm_term(text)
    if len(q) < 2:
        return unknown
    if _DIGITS.match(q):
        return {"status": "ok", "prefixes": [q], "label": q, "options": []}
    terms = _terms()
    cands = [t for t in terms if t[0] == q] or [t for t in terms if q in t[0]]
    uniq: dict[tuple, tuple] = {}
    for t in cands:
        uniq.setdefault(tuple(sorted(set(t[2]))), t)
    if len(uniq) == 1:
        (pf, t), = uniq.items()
        return {"status": "ok", "prefixes": list(pf), "label": t[1], "options": []}
    if len(uniq) > 1:
        opts = [{"term": t[1], "prefixes": list(pf), "n_listed": _n_listed(pf)}
                for pf, t in list(uniq.items())[:5]]
        return {"status": "ambiguous", "prefixes": [], "label": "", "options": opts}
    return unknown


def select_by_industry(prefixes: list[str]) -> list[IndustryStock]:
    c = _require_cache()
    if not c["complete"]:
        raise IdentityError("API_ERROR", INDUSTRY_NOT_READY)
    if (not isinstance(prefixes, (list, tuple)) or not prefixes
            or not all(isinstance(p, str) and _DIGITS.match(p) for p in prefixes)):
        raise IdentityError("INVALID_INPUT")
    cond, params = _like_any(prefixes)
    rows = store.read("SELECT stock_code, name, induty_code FROM company WHERE listed=1 AND "
                      f"detail_status='ok' AND {cond} ORDER BY stock_code", params)
    return [{"stock_code": a, "name": b, "induty_code": d} for a, b, d in rows]


def similar_industries(text: str) -> list[str]:
    r = resolve_industry(text)
    if r["status"] != "ok":
        return []
    primary = tuple(r["prefixes"])
    codes = [x[0] for x in store.read("SELECT induty_code FROM company WHERE listed=1 AND "
                                      "detail_status='ok' AND induty_code IS NOT NULL")]
    others = [c for c in codes if not c.startswith(primary)]
    cnt: dict[str, int] = {}
    for p in primary:
        L = len(p)
        if L < 3:  # 2자리 이하는 확장 불가
            continue
        for k, v in Counter(c[:L] for c in others if c.startswith(p[:-1]) and len(c) >= L).items():
            cnt[k] = v
    return [k for k, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]


def cache_status() -> CacheStatus:
    c = store.read_counts()
    rows = store.read("SELECT value FROM cache_meta WHERE key='corp_synced_at'")
    return {"total": c["total"], "ok": c["ok"], "pending": c["pending"], "failed": c["failed"],
            "complete": c["complete"], "corp_synced_at": rows[0][0] if rows else None}
