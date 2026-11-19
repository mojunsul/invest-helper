"""D7 소관 라우터만 정의. 그래프 조립은 D9."""
from typing import Literal


def route_after_analyze(state) -> Literal["plan", "respond"]:
    if state.get("status") in ("asked", "error"):
        return "respond"
    if state.get("intent") == "chitchat":
        return "respond"
    return "plan"


# ---- D9 조립: 정점 5개(analyze, plan, execute, judge, respond), decide 정점 없음 ----
RECURSION_LIMIT = 40  # 임의 상수. 최대 14 step 예상, 안전 여유


def build_graph():
    from langgraph.graph import END, START, StateGraph

    from backend.agent.nodes.analyze import analyze
    from backend.agent.nodes.plan import plan
    from backend.agent.nodes.replan import execute_step, judge_step, route_after_execute, route_after_judge
    from backend.agent.nodes.respond import respond
    from backend.agent.state import AgentState

    g = StateGraph(AgentState)
    for name, fn in (("analyze", analyze), ("plan", plan), ("execute", execute_step),
                     ("judge", judge_step), ("respond", respond)):
        g.add_node(name, fn)
    g.add_edge(START, "analyze")
    g.add_conditional_edges("analyze", route_after_analyze, {"plan": "plan", "respond": "respond"})
    g.add_edge("plan", "execute")
    g.add_conditional_edges("execute", route_after_execute,
                            {"plan": "plan", "judge": "judge", "respond": "respond"})
    g.add_conditional_edges("judge", route_after_judge, {"plan": "plan", "respond": "respond"})
    g.add_edge("respond", END)
    return g.compile()


async def run_agent(state: dict) -> dict:
    return await build_graph().ainvoke(state, config={"recursion_limit": RECURSION_LIMIT})
