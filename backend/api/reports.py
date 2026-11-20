import logging
from datetime import date
from typing import Callable

from fastapi import APIRouter, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.agent import memory
from backend.db import sqlite
from backend.tools.base import mask

logger = logging.getLogger(__name__)

_MSG = {"REPORT_NOT_FOUND": "리포트를 찾을 수 없습니다.", "DRAFT_NOT_FOUND": "먼저 해당 종목을 조회하세요.",
        "INVALID_REQUEST": "요청 형식이 올바르지 않습니다.", "INTERNAL_ERROR": "요청을 처리하지 못했습니다."}


class ApiError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


def _err(status: int, code: str) -> JSONResponse:
    return JSONResponse({"code": code, "message": _MSG[code]}, status_code=status)


class _Route(APIRoute):
    # 라우터 단위 오류 변환: 앱 핸들러(D13) 없이도 {code, message} 계약을 지킨다. 입력값은 에코하지 않는다.
    def get_route_handler(self) -> Callable:
        inner = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await inner(request)
            except ApiError as e:
                return _err(e.status, e.code)
            except RequestValidationError:
                return _err(422, "INVALID_REQUEST")
            except Exception as e:
                logger.error("reports error: %s", mask(repr(e)))
                return _err(500, "INTERNAL_ERROR")
        return handler


router = APIRouter(prefix="/api/reports", route_class=_Route)
_Id = Path(ge=1)


class _Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    memo: str | None = Field(default=None, max_length=1000)

    review_date: str | None = Field(default=None, pattern=r"^(\d{4}-\d{2}-\d{2})?$")

    @field_validator("review_date")
    @classmethod
    def _review(cls, v: str | None) -> str | None:
        if v:
            date.fromisoformat(v)  # 2026-02-30 등 무효 날짜는 ValueError -> 422
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not 1 <= len(v) <= 100:
            raise ValueError("name")
        return v


class CreateBody(_Edit):
    session_id: str = Field(min_length=1, max_length=64)
    stock_code: str = Field(pattern=r"^\d{6}$")


class PatchBody(_Edit):
    # 명시적 null = 삭제, 키 생략 = 변경 없음(model_fields_set)
    target_per: float | None = Field(default=None, gt=0, le=1000, allow_inf_nan=False)
    target_roe: float | None = Field(default=None, ge=-100, le=1000, allow_inf_nan=False)

    @field_validator("target_per", "target_roe", mode="before")
    @classmethod
    def _nobool(cls, v):
        if isinstance(v, bool):
            raise ValueError("bool")
        return v

    @model_validator(mode="after")
    def _any(self):
        if (self.name is None and self.memo is None and self.review_date is None
                and not {"target_per", "target_roe"} & self.model_fields_set):
            raise ValueError("empty")
        return self


@router.get("")
def list_all():
    return sqlite.list_reports()


@router.get("/{report_id}")
def get_one(report_id: int = _Id):
    r = sqlite.get_report(report_id)
    if r is None:
        raise ApiError(404, "REPORT_NOT_FOUND")
    return r


@router.post("", status_code=201)
def create(body: CreateBody):
    d = memory.get_report_draft(body.session_id, body.stock_code)
    if not isinstance(d, dict) or d.get("stock_code") != body.stock_code or not isinstance(d.get("metrics"), dict):
        raise ApiError(404, "DRAFT_NOT_FOUND")
    rid, dup = sqlite.save_report(d, body.name, body.memo, body.review_date)
    return {"id": rid, "duplicate_of": dup}


@router.patch("/{report_id}")
def patch(body: PatchBody, report_id: int = _Id):
    targets = {k: getattr(body, k) for k in ("target_per", "target_roe") if k in body.model_fields_set}
    r = sqlite.update_report(report_id, body.name, body.memo, body.review_date, targets)
    if r is None:
        raise ApiError(404, "REPORT_NOT_FOUND")
    return r


@router.delete("/{report_id}", status_code=204)
def delete(report_id: int = _Id):
    if not sqlite.delete_report(report_id):
        raise ApiError(404, "REPORT_NOT_FOUND")
    return Response(status_code=204)
