from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from backend.events import logger_agent
from backend.schemas import SessionId

router = APIRouter(prefix="/api/logs")


def split_runs(events) -> list[dict]:
    run, has, prev_final, out = 0, False, False, []
    for e in events:
        if not has or prev_final or (e.type == "stage_start" and e.payload.get("stage") == "analyze"):
            run += 1
        has, prev_final = True, e.type == "final"
        out.append({**e.model_dump(), "run": run})
    return out


@router.get("")
def list_logs():
    return logger_agent.list_sessions()


@router.get("/{session_id}")
def get_log(session_id: SessionId):
    evs = logger_agent.get_session(session_id.lower())
    if not evs:
        return JSONResponse({"code": "SESSION_NOT_FOUND", "message": "로그가 없습니다."}, status_code=404)
    return split_runs(evs)


@router.delete("/{session_id}", status_code=204)
def delete_log(session_id: SessionId):
    logger_agent.delete_session(session_id.lower())
    return Response(status_code=204)
