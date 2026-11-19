"""D7 소관 라우터만 정의. 그래프 조립은 D9."""
from typing import Literal


def route_after_analyze(state) -> Literal["plan", "respond"]:
    if state.get("status") in ("asked", "error"):
        return "respond"
    if state.get("intent") == "chitchat":
        return "respond"
    return "plan"
