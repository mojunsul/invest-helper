"""D23(A): /docs 로컬 서빙(CSP 불변). 실제 frontend/ 마운트 사용."""
import re

from fastapi.testclient import TestClient

from backend import main


def test_docs_local_only_and_headers():
    with TestClient(main.app, raise_server_exceptions=False) as c:
        r = c.get("/docs")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
        scripts = re.findall(r"<script([^>]*)>", r.text)
        assert scripts and all(re.fullmatch(r'\s*src="/vendor/swagger/[\w.-]+\.js"', a) for a in scripts)
        assert "</script>" not in re.sub(r"<script[^>]*></script>", "", r.text)  # 인라인 본문 없음
        assert "http" not in r.text  # /docs HTML 본문에 외부 URL 없음(번들 JS 제외)
        for p, ct in [("/vendor/swagger/swagger-ui-bundle.js", "javascript"), ("/vendor/swagger/swagger-ui.css", "css"),
                      ("/vendor/swagger/docs-init.js", "javascript")]:
            x = c.get(p)
            assert x.status_code == 200 and ct in x.headers["content-type"]
        for p in ["/", "/docs", "/vendor/swagger/swagger-ui-bundle.js", "/api/reports", "/api/nope", "/redoc",
                  "/docs/oauth2-redirect"]:
            h = c.get(p).headers
            for k, v in main._HEADERS.items():
                assert h[k] == v, (p, k)
        assert "frame-ancestors 'none'" in main._HEADERS["Content-Security-Policy"]
        assert "script-src 'self';" in main._HEADERS["Content-Security-Policy"]
        assert c.get("/redoc").status_code == 404 and c.get("/docs/oauth2-redirect").status_code == 404
        assert c.get("/openapi.json").status_code == 200
        assert "/docs" not in main.app.openapi()["paths"]
