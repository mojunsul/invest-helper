"""D1a 로그 마스킹 필터: args 구조 보존(uvicorn AccessFormatter 호환) + 키 4형태 × 위치."""
import logging
import sys
from urllib.parse import quote, unquote

import pytest
from uvicorn.logging import AccessFormatter

from backend.config import Settings
from backend.tools import base

KEY = "ab/c+d=="
FORMS = [KEY, quote(KEY, safe=""), unquote(quote(KEY, safe="")), quote(unquote(KEY), safe="")]
ACCESS_FMT = '%(client_addr)s - "%(request_line)s" %(status_code)s'


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(base, "settings", Settings(DATA_GO_KR_KEY=KEY))


class Capture(logging.Handler):
    """포맷 오류(handleError)를 실패로 기록하고 포맷 결과를 모은다."""

    def __init__(self, fmt):
        super().__init__()
        self.setFormatter(fmt)
        self.out, self.errors = [], []

    def emit(self, record):
        try:
            self.out.append(self.format(record))
        except Exception:
            self.handleError(record)

    def handleError(self, record):
        self.errors.append(sys.exc_info()[1])


@pytest.fixture
def mk_logger():
    made = []

    def make(fmt):
        lg = logging.getLogger(f"d1a.{len(made)}")
        lg.propagate, lg.level = False, logging.DEBUG
        h = Capture(fmt)
        lg.addHandler(h)
        base.install_log_mask()  # 핸들러 부착 후 설치
        made.append((lg, h))
        return lg, h

    yield make
    for lg, h in made:
        lg.removeHandler(h)


def test_uvicorn_access_log_survives_and_masked(mk_logger):
    lg, h = mk_logger(AccessFormatter(ACCESS_FMT, use_colors=False))
    lg.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", f"/p?serviceKey={quote(KEY, safe='')}&x=1", "1.1", 200)
    assert h.errors == []
    (line,) = h.out
    assert "GET" in line and "200" in line and "***" in line and KEY not in line and quote(KEY, safe="") not in line


def test_uvicorn_access_log_colors_status_int_kept(mk_logger):
    lg, h = mk_logger(AccessFormatter(ACCESS_FMT, use_colors=True))
    lg.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/", "1.1", 404)
    assert h.errors == [] and "404" in h.out[0]


def test_args_structure_preserved():
    r = logging.LogRecord("n", logging.INFO, "f", 1, '%s "%s %s" %d', ("c", "GET", f"/p?k={KEY}", 200), None)
    base._MaskFilter().filter(r)
    assert isinstance(r.args, tuple) and len(r.args) == 4
    assert r.args[3] == 200 and type(r.args[3]) is int
    assert KEY not in r.args[2] and "***" in r.args[2]
    r = logging.LogRecord("n", logging.INFO, "f", 1, "%(a)s %(b)d", ({"a": KEY, "b": 3},), None)
    base._MaskFilter().filter(r)
    assert isinstance(r.args, dict) and set(r.args) == {"a", "b"} and r.args["b"] == 3 and r.args["a"] == "***"


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("where", ["msg", "tuple", "dict", "exc_obj", "exc_info"])
def test_key_forms_by_position(mk_logger, form, where):
    lg, h = mk_logger(logging.Formatter("%(message)s"))
    if where == "msg":
        lg.info(f"k={form}")
    elif where == "tuple":
        lg.info("k=%s", form)
    elif where == "dict":
        lg.info("k=%(k)s", {"k": form})
    elif where == "exc_obj":
        lg.info("e=%s r=%r", ValueError(form), ValueError(form))
    else:
        try:
            raise ValueError(f"bad {form}")
        except ValueError:
            lg.error("fail", exc_info=True)
    assert h.errors == [] and len(h.out) == 1
    for f in FORMS:
        assert f not in h.out[0]
    assert "***" in h.out[0]
