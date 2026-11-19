import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import get_args

import pytest
from fastapi.testclient import TestClient

from backend import config, main
from backend.agent.nodes.respond import REASON_KO, PCT, fmt
from backend.agent.state import ReasonCode
from backend.events import bus, logger_agent as la, stream

ROOT = Path(__file__).resolve().parent.parent
FE = ROOT / "frontend"


def _node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 미설치")
    return node


def test_node_unit_tests():
    r = subprocess.run([_node(), "--test", str(ROOT / "tests" / "frontend" / "d15.test.js")],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_node_dom_tests():
    r = subprocess.run([_node(), "--test", str(ROOT / "tests" / "frontend" / "d15_dom.test.js")],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_reports_html_rules():
    h = (FE / "reports.html").read_text(encoding="utf-8")
    assert re.findall(r'<script src="([^"]+)"', h) == ["common.js", "reports.js"]
    assert 'href="index.html"' in h and 'role="alert"' in h
    for lab in re.findall(r'<label for="([^"]+)"', h):
        assert f'id="{lab}"' in h
    # JS가 만드는 label for도 대응 id가 있어야 한다
    js = (FE / "reports.js").read_text(encoding="utf-8")
    for lab in re.findall(r'attrs: \{ for: "([^"]+)"', js):
        assert f'id: "{lab}"' in js


def test_reason_text_matches_server():
    node = _node()
    out = subprocess.run([node, "-e", "console.log(JSON.stringify(require('./frontend/common.js').REASON_TEXT))"],
                         capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=60)
    rt = json.loads(out.stdout)
    assert set(rt) == set(get_args(ReasonCode))
    assert rt == REASON_KO


def test_fmt_metric_matches_d9_fmt():
    node = _node()
    cases = [(m, v) for m in ["ROE", "ROA", "OPM", "DEBT_RATIO", "PER", "PBR", "PSR"] for v in [0, 1.005, 12.345, -3.2, 1234.5]]
    js = ("const R=require('./frontend/reports.js');const c=%s;"
          "console.log(JSON.stringify(c.map(([m,v])=>R.fmtMetric(m,v))))" % json.dumps(cases))
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=60)
    got = json.loads(out.stdout)
    # JS toFixed와 Python 반올림이 경계값(1.005 등)에서 다를 수 있어 경계 외 값으로만 비교
    for (m, v), g in zip(cases, got):
        if v == 1.005:
            continue
        assert g == fmt(m, v), (m, v, g)
    assert {"ROE", "ROA", "OPM", "DEBT_RATIO"} == PCT


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


@pytest.mark.parametrize("path", ["/reports.html", "/reports.js"])
def test_static_served_with_csp(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_extensionless_reports_is_404(client):
    assert client.get("/reports").status_code == 404
