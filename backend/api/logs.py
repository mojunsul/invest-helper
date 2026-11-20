from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from backend.events import logger_agent
from backend.schemas import LogEvent, LogSession, SessionId, err_responses

router = APIRouter(prefix="/api/logs")


def split_runs(events) -> list[dict]:
    run, has, prev_final, out = 0, False, False, []
    for e in events:
        if not has or prev_final or (e.type == "stage_start" and e.payload.get("stage") == "analyze"):
            run += 1
        has, prev_final = True, e.type == "final"
        out.append({**e.model_dump(), "run": run})
    return out


@router.get("", responses={200: {"model": list[LogSession]}})
def list_logs():
    return logger_agent.list_sessions()


@router.get("/{session_id}", responses={200: {"model": list[LogEvent]}, **err_responses(404, 422)})
def get_log(session_id: SessionId):
    evs = logger_agent.get_session(session_id.lower())
    if not evs:
        return JSONResponse({"code": "SESSION_NOT_FOUND", "message": "로그가 없습니다."}, status_code=404)
    return split_runs(evs)


@router.delete("/{session_id}", status_code=204,
               responses={204: {"description": "삭제됨(멱등)"}, **err_responses(422)})
def delete_log(session_id: SessionId):
    logger_agent.delete_session(session_id.lower())
    return Response(status_code=204)
