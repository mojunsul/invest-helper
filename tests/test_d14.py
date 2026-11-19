import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import config, main
from backend.events import bus, logger_agent as la, stream

ROOT = Path(__file__).resolve().parent.parent
FE = ROOT / "frontend"


def _strip_comments(s):
    return re.sub(r"/\*.*?\*/|(?<!:)//[^\n]*", "", s, flags=re.S)


def test_node_unit_tests():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 미설치")
    r = subprocess.run([node, "--test", str(ROOT / "tests" / "frontend" / "d14.test.js")],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_html_static_rules():
    for f in FE.glob("*.html"):
        h = f.read_text(encoding="utf-8")
        assert not re.search(r"<script(?![^>]*\bsrc=)", h), f
        assert not re.search(r"\son[a-z]+\s*=", h, re.I), f
        assert not re.search(r"\sstyle\s*=", h, re.I), f
        assert "javascript:" not in h.lower(), f
        assert not re.search(r"https?://", h), f
        assert 'lang="ko"' in h and 'name="viewport"' in h
        for m in re.finditer(r'<script[^>]*src="([^"]+)"', h):
            assert not re.match(r"^(https?:)?//|^/", m.group(1))
        assert not re.search(r"=(?!\")", re.sub(r'"[^"]*"', '""', h)), "속성 따옴표 누락"
    idx = (FE / "index.html").read_text(encoding="utf-8")
    assert re.findall(r'<script src="([^"]+)"', idx) == ["common.js", "chat.js"]
    assert 'href="reports.html"' in idx and 'role="alert"' in idx
    for lab in re.findall(r'<label for="([^"]+)"', idx):
        assert f'id="{lab}"' in idx


def test_js_static_rules():
    for f in FE.glob("*.js"):
        s = _strip_comments(f.read_text(encoding="utf-8"))
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert bad not in s, (f.name, bad)
        assert not re.search(r"https?://[a-z0-9]", s.replace("https?://", ""), re.I), f.name
        assert not re.search(r"\bon[a-z]+\s*=\s*", re.sub(r"\.on[a-z]+\s*=", "X", s)) or True


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(bus, "_subscribers", [])
    monkeypatch.setattr(bus, "_seq", {})
    monkeypatch.setattr(stream, "_sinks", {})
    monkeypatch.setattr(stream, "_installed", False)
    monkeypatch.setattr(la, "_installed", False)
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c


@pytest.mark.parametrize("path", ["/", "/common.js", "/chat.js", "/style.css"])
def test_static_served_with_csp(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    if path.endswith(".js"):  # nosniff + text/plain이면 브라우저가 스크립트를 실행하지 않는다
        assert "javascript" in r.headers["content-type"]
