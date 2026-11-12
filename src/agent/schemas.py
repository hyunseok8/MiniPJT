"""에이전트 그래프의 상태와 결과 형식."""

from typing import Any, TypedDict

from langgraph.graph import MessagesState


class AgentState(MessagesState):
    # 출력 검증이 agent에게 고쳐 쓰게 한 횟수
    rewrites: int
    # 출력 검증 기록 (history, stripped, preamble_removed, elevation_fixed, out_of_range_marked)
    verification: dict[str, Any]


class ToolCall(TypedDict):
    name: str
    args: dict[str, Any]


class ToolError(TypedDict):
    tool: str
    error: str
