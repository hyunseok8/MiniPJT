"""LangGraph 에이전트 그래프.

흐름:
  agent → verify → (근거 없는 링크가 있으면 1번까지) agent → verify → 종료
  agent는 LangChain create_agent로 만든 ReAct 에이전트(서브그래프)다:
    모델(LLM) → 도구 호출이 있으면 tools → 다시 모델 → 도구 호출이 없으면 끝
    미들웨어 (실행 순서대로):
      1. InputGuardMiddleware      입력 개인정보 마스킹, 프롬프트 인젝션 차단 (src/guardrails.py)
      2. PIIMiddleware             카드번호·주민등록번호를 입력과 출력에서 가림 (LangChain 기본 제공)
      3. system_prompt             오늘 날짜를 넣은 시스템 프롬프트 (src/middleware.py)
      4. HistorySummaryMiddleware  긴 이전 대화를 요약본으로 바꿔 모델에 넘김
      5. ModelRetryMiddleware      Bedrock 일시 오류 재시도
  verify → 근거 없는 링크가 없으면 종료
         → 있으면 1번까지 agent에게 고쳐 쓰게 돌려보내고, 그래도 남으면 그 링크를 지운 뒤 종료
그래프가 끝나면 최종 답변을 구조화 출력(TripAnswer, src/structured.py)으로도 바꿔 돌려준다.
"""

import json
from collections.abc import Iterator
from functools import lru_cache
from typing import Any, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langgraph.graph import END, START, MessagesState, StateGraph

from src.guardrails import MASKS, build_guard_middleware
from src.llm import get_llm
from src.middleware import build_agent_middleware
from src.structured import structure_answer
from src.tools import ALL_TOOLS
from src.verify import (
    feedback_message,
    find_unverified,
    fix_elevation_cells,
    mark_out_of_range,
    strip_preamble,
    strip_unverified,
)

# ======================================================================
# 상태와 결과 형식
# ======================================================================


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


# ======================================================================
# 그래프
# ======================================================================

# 도구 호출이 끝없이 반복되지 않도록 그래프 단계 수를 제한한다
# create_agent 서브그래프는 미들웨어 훅도 단계로 세므로 넉넉히 둔다
RECURSION_LIMIT = 100
# 평가용으로 남기는 도구 결과의 최대 길이(도구 호출 1회당).
# fetch_page 본문(최대 8000자)과 JSON 포장을 모두 담도록 넉넉히 둔다.
# 4000자로 자르던 때 판정기가 잘린 뒤쪽의 근거를 보지 못해 '지어냄'으로 오판한 일이 있었다.
TOOL_RESULT_CHARS = 9000


# 근거 없는 링크가 나왔을 때 agent에게 고쳐 쓰게 하는 최대 횟수
MAX_REWRITES = 1


# create_agent 안에서 LLM을 부르는 노드 이름. 스트리밍에서 이 노드의 글자만 화면에 보낸다
MODEL_NODE = "model"


@lru_cache
def build_react_agent():
    """도구 호출(ReAct) 에이전트: bind_tools와 도구 실행 반복은 create_agent가 맡는다."""
    return create_agent(
        model=get_llm(),
        tools=ALL_TOOLS,
        middleware=[*build_guard_middleware(), *build_agent_middleware()],
        name="runtrip_agent",
    )


@lru_cache
def build_graph():

    def verify_node(state: AgentState) -> dict[str, Any]:
        messages = state["messages"]
        answer_msg = messages[-1]
        unverified = find_unverified(_text(answer_msg), messages[:-1])
        rewrites = state.get("rewrites", 0)
        log = dict(state.get("verification") or {})
        log.setdefault("history", []).append(unverified)
        if unverified and rewrites < MAX_REWRITES:
            return {
                "rewrites": rewrites + 1,
                "verification": log,
                "messages": [
                    HumanMessage(
                        feedback_message(unverified), additional_kwargs={"from_verifier": True}
                    )
                ],
            }
        text = _text(answer_msg)
        if unverified:
            # 고쳐 쓴 뒤에도 남은 링크는 코드로 지운다
            log["stripped"] = unverified
            text = strip_unverified(text, unverified)
        text, removed = strip_preamble(text)
        if removed:
            log["preamble_removed"] = removed
        text, elevations = fix_elevation_cells(text)
        if elevations:
            log["elevation_fixed"] = elevations
        text, out_of_range = mark_out_of_range(text)
        if out_of_range:
            log["out_of_range_marked"] = out_of_range
        if text == _text(answer_msg):
            return {"verification": log}
        # 같은 id로 돌려주면 기존 답변 메시지가 교체된다
        return {"verification": log, "messages": [AIMessage(content=text, id=answer_msg.id)]}

    def after_verify(state: AgentState) -> str:
        return "agent" if isinstance(state["messages"][-1], HumanMessage) else END

    graph = StateGraph(AgentState)
    graph.add_node("agent", build_react_agent())
    graph.add_node("verify", verify_node)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", "verify")
    graph.add_conditional_edges("verify", after_verify, {"agent": "agent", END: END})
    return graph.compile()


def _text(message: BaseMessage) -> str:
    """Bedrock 응답은 문자열이거나 콘텐츠 블록 목록이다. 텍스트만 모은다."""
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def run_agent(history: list[dict[str, str]]) -> dict[str, Any]:
    """대화 기록을 받아 에이전트를 실행한다.

    Args:
        history: [{"role": "user" | "assistant", "content": "..."}] 형식의 대화 기록.
            마지막 항목이 사용자 질문이다. 서버는 대화를 저장하지 않는다(정책 4).

    Returns:
        answer: 최종 답변
        tool_calls: 호출한 도구 이름과 인자 목록
        tool_results: 도구가 돌려준 결과 (앞부분만). 평가 루프에서 답변이 도구 결과에 근거했는지 판정하는 데 쓴다
        tool_errors: 도구 오류 목록 (외부 장애 구분용)
        guardrail: 가린 개인정보 종류, 프롬프트 인젝션 차단 여부
        structured: 최종 답변의 구조화 출력 (TripAnswer dict, 실패하면 None)
    """
    messages = _to_messages(history)
    result = build_graph().invoke(_initial_state(messages), {"recursion_limit": RECURSION_LIMIT})
    summary = _summarize(result, len(messages))
    summary["structured"] = structure_answer(_last_question(history), summary["answer"])
    return summary


def stream_agent(history: list[dict[str, str]]) -> Iterator[dict[str, Any]]:
    """run_agent()와 같은 그래프를 돌리면서 진행 상황을 이벤트로 흘려보낸다 (웹 화면의 타자 치듯 보이는 답변용).

    이벤트:
        {"type": "token", "text": ...}   agent 노드가 쓰는 글자 조각
        {"type": "tools", "names": [...]} agent가 도구를 부르기로 했다. 그 전에 흘려보낸 글자는 중간 생각이므로 화면에서 지운다
        {"type": "rewrite"}               출력 검증이 근거 없는 링크를 찾아 다시 쓰게 했다. 화면의 글자를 지운다
        {"type": "done", ...}             출력 검증까지 마친 최종 결과 (answer, tool_calls, tool_errors, guardrail)
        {"type": "structured", "data": ...} 최종 답변의 구조화 출력 (done 뒤에 온다. 실패하면 data는 None)
    """
    messages = _to_messages(history)
    final_state: dict[str, Any] | None = None
    # subgraphs=True: create_agent 서브그래프 안의 모델 글자와 도구 호출도 받는다
    for namespace, mode, payload in build_graph().stream(
        _initial_state(messages),
        {"recursion_limit": RECURSION_LIMIT},
        stream_mode=["messages", "updates", "values"],
        subgraphs=True,
    ):
        if mode == "messages":
            chunk, meta = payload
            # 요약 체인 같은 다른 LLM 호출의 글자는 보내지 않는다
            if meta.get("langgraph_node") == MODEL_NODE and isinstance(chunk, AIMessageChunk):
                text = _text(chunk)
                if text:
                    yield {"type": "token", "text": text}
        elif mode == "updates":
            for node, update in (payload or {}).items():
                new = (update.get("messages") or []) if isinstance(update, dict) else []
                if (
                    namespace
                    and node == MODEL_NODE
                    and new
                    and isinstance(new[-1], AIMessage)
                    and new[-1].tool_calls
                ):
                    yield {"type": "tools", "names": [c["name"] for c in new[-1].tool_calls]}
                elif (
                    not namespace and node == "verify" and new and isinstance(new[-1], HumanMessage)
                ):
                    yield {"type": "rewrite"}
        elif mode == "values" and not namespace:
            final_state = payload
    if final_state is None:
        raise RuntimeError("에이전트가 결과 없이 끝났다.")
    result = _summarize(final_state, len(messages))
    yield {
        "type": "done",
        "answer": result["answer"],
        "tool_calls": result["tool_calls"],
        "tool_errors": result["tool_errors"],
        "guardrail": result["guardrail"],
    }
    yield {
        "type": "structured",
        "data": structure_answer(_last_question(history), result["answer"]),
    }


def _to_messages(history: list[dict[str, str]]) -> list[BaseMessage]:
    # 이전 대화의 답변은 그때 이미 검증을 거쳤으므로 출력 검증에서 근거로 인정한다
    return [
        HumanMessage(m["content"])
        if m["role"] == "user"
        else AIMessage(m["content"], additional_kwargs={"from_history": True})
        for m in history
    ]


def _last_question(history: list[dict[str, str]]) -> str:
    return next((m["content"] for m in reversed(history) if m["role"] == "user"), "")


def _initial_state(messages: list[BaseMessage]) -> dict[str, Any]:
    return {"messages": messages, "rewrites": 0, "verification": {}}


def _summarize(result: dict[str, Any], n_input: int) -> dict[str, Any]:
    new_messages = result["messages"][n_input:]
    tool_calls = [
        {"name": call["name"], "args": call["args"]}
        for msg in new_messages
        if isinstance(msg, AIMessage)
        for call in msg.tool_calls
    ]
    return {
        "answer": _text(new_messages[-1]),
        "tool_calls": tool_calls,
        "tool_results": [
            {"tool": msg.name or "", "content": _text(msg)[:TOOL_RESULT_CHARS]}
            for msg in new_messages
            if isinstance(msg, ToolMessage)
        ],
        "tool_errors": _tool_errors(new_messages),
        # history: 검증할 때마다 찾은 근거 없는 링크, stripped: 끝까지 남아 코드로 지운 링크
        "verification": result.get("verification", {}),
        "guardrail": _guardrail_log(result["messages"], new_messages),
    }


def _guardrail_log(messages: list[BaseMessage], new_messages: list[BaseMessage]) -> dict[str, Any]:
    """가드레일이 한 일: 사용자 메시지에서 가린 개인정보 종류, 인젝션 차단 여부."""
    masked = sorted(
        {
            kind
            for m in messages
            if isinstance(m, HumanMessage)
            for kind, token in MASKS.items()
            if token in _text(m)
        }
    )
    blocked = any(isinstance(m, AIMessage) and m.name == "input_guard" for m in new_messages)
    return {"masked_input": masked, "injection_blocked": blocked}


def _tool_errors(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """도구가 돌려준 오류(API 키 없음, 외부 장애 등). 평가 루프에서 외부 장애를 구분하는 데 쓴다."""
    errors = []
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        try:
            payload = json.loads(msg.content) if isinstance(msg.content, str) else None
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and "error" in payload:
            errors.append({"tool": msg.name or "", "error": str(payload["error"])})
        elif msg.status == "error":
            errors.append({"tool": msg.name or "", "error": _text(msg)})
    return errors
