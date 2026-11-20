"""D16 준비 점검: requirements·import 완결성·기동 스모크·문서/설정 정합. 네트워크·실제 키 불필요."""
import ast
import importlib
import importlib.metadata as md
import json
import os
import pkgutil
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import warnings
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import backend
from backend import config, main
from backend.agent import llm, memory
from backend.config import Settings
from backend.events import bus, logger_agent as la, stream
from backend.identity import resolve
from backend.tools import base
from backend.tools import disclosures as disc
from backend.tools import financials as fin
from backend.tools import stock_candidates as sc

ROOT = Path(__file__).resolve().parent.parent
LOCAL = {"backend", "tests", "scripts"}
DIST = {"dotenv": "python-dotenv"}  # import명 != 배포명
EMPTY_KEYS = {"DATA_GO_KR_KEY": "", "DART_API_KEY": "", "GMS_API_KEY": ""}  # .env 실제 키가 자식 프로세스로 새지 않게


def norm(n):
    return re.sub(r"[-_.]+", "-", n).lower()


def requirements():
    out = []
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def test_requirements_pinned_and_installed():
    bad = []
    for line in requirements():
        m = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", line)
        if not m:
            bad.append(f"not pinned: {line}")
            continue
        try:
            have = md.version(m[1])
        except md.PackageNotFoundError:
            have = None
        if have != m[2]:
            bad.append(f"{m[1]}: pin {m[2]} installed {have}")
    assert not bad, bad


def test_requirements_cover_imports():
    mods = set()
    for d in ("backend", "scripts", "tests"):
        for f in (ROOT / d).rglob("*.py"):
            for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(n, ast.Import):
                    mods |= {a.name.split(".")[0] for a in n.names}
                elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                    mods.add(n.module.split(".")[0])
    third = mods - set(sys.stdlib_module_names) - LOCAL
    have = {norm(line.split("==")[0]) for line in requirements()}
    missing = sorted(m for m in third if norm(DIST.get(m, m)) not in have)
    assert not missing, f"not in requirements.txt: {missing}"
    unused = sorted(h for h in have if h not in {norm(DIST.get(m, m)) for m in third})
    if unused:  # starlette·uvicorn 등 간접 의존이 있어 경고만
        warnings.warn(f"requirements entries never imported directly: {unused}")


def test_import_smoke_in_process():
    for m in pkgutil.walk_packages(backend.__path__, "backend."):
        importlib.import_module(m.name)


def repo_db_state():
    p = ROOT / "invest_helper.db"
    return (p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None


def test_import_main_subprocess_no_side_effects():
    before = repo_db_state()
    r = subprocess.run([sys.executable, "-c", "import backend.main"], cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, **EMPTY_KEYS}, timeout=60)
    assert r.returncode == 0, r.stderr[-500:]
    assert repo_db_state() == before


@pytest.fixture
def guard(monkeypatch):
    hits = []
    orig = httpx.Client.send

    def sync_send(self, *a, **k):
        if isinstance(self, TestClient):
            return orig(self, *a, **k)
        hits.append(1)
        raise AssertionError("real HTTP")

    async def async_send(self, *a, **k):
        hits.append(1)
        raise AssertionError("real HTTP")
    monkeypatch.setattr(httpx.Client, "send", sync_send)
    monkeypatch.setattr(httpx.AsyncClient, "send", async_send)
    yield hits
    assert hits == []


def test_app_boot_smoke_no_keys_no_cache(monkeypatch, tmp_path, guard):
    before = repo_db_state()
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "boot.db"))
    for m in (config, base, sc, fin, disc, llm):
        monkeypatch.setattr(m, "settings", Settings())
    for mod, attr, val in ((bus, "_subscribers", []), (bus, "_seq", {}), (stream, "_sinks", {}),
                           (stream, "_installed", False), (la, "_installed", False)):
        monkeypatch.setattr(mod, attr, val)
    memory.clear_all()
    with TestClient(main.app) as c:
        assert c.get("/api/reports").json() == [] and c.get("/api/logs").json() == []
        r = c.post("/api/chat", json={"session_id": "123e4567-e89b-42d3-a456-426614174000", "message": "x"})
        assert r.status_code == 200 and r.json()["status"] == "error" and r.json()["missing_api_key"] is True
    memory.clear_all()
    assert repo_db_state() == before


@pytest.mark.skipif(bool(os.getenv("SKIP_BOOT_SMOKE")), reason="SKIP_BOOT_SMOKE")
def test_uvicorn_command_smoke(tmp_path):
    before = repo_db_state()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = open(tmp_path / "uvicorn.log", "w+", encoding="utf-8")
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1",
                          "--port", str(port)], cwd=tmp_path, stdout=log, stderr=subprocess.STDOUT,
                         env={**os.environ, **EMPTY_KEYS, "PYTHONPATH": str(ROOT)})
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline, up = time.time() + 15, False
        while time.time() < deadline and not up:
            if p.poll() is not None:
                break
            try:
                up = urllib.request.urlopen(base_url + "/api/reports", timeout=2).status == 200
            except OSError:
                time.sleep(0.3)
        log.seek(0)
        tail = log.read()[-400:]
        assert up, f"uvicorn did not start: {tail}"
        r = urllib.request.urlopen(base_url + "/", timeout=5)
        assert r.status == 200 and "script-src 'self'" in r.headers["Content-Security-Policy"]
    finally:
        p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(5)
        log.close()
    assert (tmp_path / "invest_helper.db").exists()  # DB_PATH는 cwd 기준 상대경로
    assert repo_db_state() == before


@pytest.mark.real_db
def test_db_path_is_relative():
    # README의 "저장소 루트에서 실행" 문구와 짝. 바뀌면 README도 함께 갱신
    assert not os.path.isabs(config.DB_PATH)


@pytest.fixture
def real_terms(monkeypatch):
    resolve._terms.cache_clear()
    monkeypatch.setattr(resolve, "TERMS_PATH", ROOT / "backend" / "identity" / "data" / "industry_terms.json")
    yield
    resolve._terms.cache_clear()


def test_terms_file_format(real_terms):
    data = json.loads(resolve.TERMS_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    resolve._terms()  # 길이·중복 검증 통과


@pytest.mark.parametrize("term", ["반도체", "은행"])
def test_representative_terms_present(real_terms, term):
    assert resolve.resolve_industry(term)["status"] == "ok"


REQUIRED_TERMS = {"반도체", "은행", "증권", "보험", "금융", "자동차", "바이오", "제약", "화학", "철강", "건설",
                  "조선", "2차전지", "소프트웨어", "게임", "통신", "유통", "음식료", "전기전자"}


def _term_items():
    return json.loads((ROOT / "backend" / "identity" / "data" / "industry_terms.json").read_text(encoding="utf-8"))


def test_terms_schema(real_terms):
    items = _term_items()
    for it in items:
        assert set(it) == {"term", "prefixes"}, it
        pf = it["prefixes"]
        assert isinstance(pf, list) and pf and len(set(pf)) == len(pf), it
        assert all(isinstance(p, str) and re.fullmatch(r"\d{2,5}", p) for p in pf), it
    assert REQUIRED_TERMS <= {it["term"] for it in items}


def test_terms_exact_resolves_to_self(real_terms):
    # 정확 일치 경로만 탄다(DB 불필요). ambiguous면 DB를 건드려 실패 -> 용어 충돌 신호
    for it in _term_items():
        r = resolve.resolve_industry(it["term"])
        assert r["status"] == "ok" and r["prefixes"] == it["prefixes"], (it, r)


@pytest.mark.real_db
@pytest.mark.skipif(os.environ.get("INVEST_REAL_DB") != "1", reason="실DB 점검: INVEST_REAL_DB=1일 때만")
def test_terms_prefixes_have_listed_companies_real_db(real_terms):
    bad = [(it["term"], it["prefixes"]) for it in _term_items() if resolve._n_listed(it["prefixes"]) < 1]
    assert not bad, bad


def test_readme_required_items():
    t = (ROOT / "README.md").read_text(encoding="utf-8")
    for kw in ("pip install", ".env", "backend.identity.build", "uvicorn backend.main:app", "127.0.0.1",
               "industry_terms.json", "미확인", "투자 권유"):
        assert kw in t, kw
    assert "--host 0.0.0.0" not in t.replace("0.0.0.0` 바인드 금지", "")


def test_env_example_matches_settings():
    keys = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            keys[k.strip()] = v.strip()
    assert set(keys) == set(Settings.__dataclass_fields__)
    assert all(v == "" for k, v in keys.items() if k.endswith("_KEY"))


def test_gitignore_and_untracked_env():
    ig = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert ".env" in ig and "*.db" in ig
    if shutil.which("git"):
        r = subprocess.run(["git", "ls-files", ".env"], cwd=ROOT, capture_output=True, text=True)
        if r.returncode == 0:
            assert r.stdout.strip() == ""


def test_design_docs_exist():
    for n in range(17):
        assert list((ROOT / "docs" / "design").glob(f"D{n}-*.md")), n
