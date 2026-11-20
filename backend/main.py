import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend import config
from backend.api import chat, logs, reports
from backend.db import sqlite
from backend.events import logger_agent, stream
from backend.identity import resolve
from backend.tools import registry
from backend.tools.base import install_log_mask

logger = logging.getLogger(__name__)
# Windows 레지스트리가 .js를 text/plain으로 매핑하면 nosniff 때문에 브라우저가 스크립트를 차단한다
mimetypes.add_type("text/javascript", ".js")
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
_HEADERS = {"X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                       "frame-ancestors 'none'"}
_FIELDS = {"session_id", "message"}  # 필드명 화이트리스트: 사용자 입력 키가 메시지에 반사되지 않게
_HTTP = {404: ("NOT_FOUND", "요청한 경로를 찾을 수 없습니다."),
         405: ("METHOD_NOT_ALLOWED", "허용되지 않는 메서드입니다.")}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    install_log_mask()
    registry.load_all()
    sqlite.init_table()
    logger_agent.install()
    stream.install()
    s = config.settings
    missing = [n for n in ("DATA_GO_KR_KEY", "DART_API_KEY", "GMS_API_KEY") if not getattr(s, n)]
    if missing:
        logger.info("missing keys: %s", ", ".join(missing))
    try:
        if not resolve.cache_status()["complete"]:
            logger.info("identity cache incomplete: run `python -m backend.identity.build`")
    except Exception:
        logger.info("identity cache unavailable: run `python -m backend.identity.build`")
    yield


# /docs: CDN·인라인 스크립트 없이 로컬 swagger-ui-dist(frontend/vendor/swagger)만 사용 -> CSP 완화 불필요. /redoc 미제공
_DOCS_HTML = ("<!doctype html><html><head><meta charset=\"utf-8\"><title>API docs</title>"
              "<link rel=\"stylesheet\" href=\"/vendor/swagger/swagger-ui.css\"></head>"
              "<body><div id=\"swagger-ui\"></div>"
              "<script src=\"/vendor/swagger/swagger-ui-bundle.js\"></script>"
              "<script src=\"/vendor/swagger/docs-init.js\"></script></body></html>")

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


def _err(status: int, code: str, msg: str) -> JSONResponse:
    return JSONResponse({"code": code, "message": msg}, status_code=status, headers=_HEADERS)


@app.exception_handler(RequestValidationError)
async def _validation(request: Request, exc: RequestValidationError):
    names = sorted({str(e["loc"][-1]) for e in exc.errors() if e.get("loc") and str(e["loc"][-1]) in _FIELDS})
    return _err(422, "INVALID_REQUEST", "요청 값이 올바르지 않습니다: " + ", ".join(names or ["요청"]))


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    code, msg = _HTTP.get(exc.status_code, ("HTTP_ERROR", "요청을 처리하지 못했습니다."))
    return _err(exc.status_code, code, msg)


@app.exception_handler(Exception)
async def _internal(request: Request, exc: Exception):
    logger.exception("unhandled error")
    return _err(500, "INTERNAL_ERROR", "요청을 처리하지 못했습니다.")


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.update(_HEADERS)
    return resp


@app.get("/docs", include_in_schema=False)
async def _docs() -> HTMLResponse:
    return HTMLResponse(_DOCS_HTML)


app.include_router(reports.router)
app.include_router(chat.router)
app.include_router(logs.router)
if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True))
