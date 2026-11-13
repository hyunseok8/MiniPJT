"""에이전트 미들웨어: 시스템 프롬프트, 대화 이력 요약, 모델 호출 재시도.

에이전트(create_agent)에 붙는 미들웨어다 (src/agent.py). 가드레일 미들웨어는 src/guardrails.py에 있다.
- system_prompt (dynamic_prompt): 모델을 부를 때마다 오늘 날짜를 넣은 시스템 프롬프트를 만든다.
  서버가 며칠 켜져 있어도 "내년", "다음 달"을 그날 기준으로 해석한다.
- HistorySummaryMiddleware: 이전 대화가 길면 LCEL 체인으로 요약해서, 모델에는 요약본을 넘긴다.
  상태(state)의 원래 메시지는 바꾸지 않는다. 출력 검증(verify)은 이전 답변의 링크를 근거로 인정하므로
  원래 기록이 남아 있어야 한다. 바로 앞 답변과 지금 질문은 요약하지 않고 그대로 넘긴다.
- ModelRetryMiddleware (LangChain 기본 제공): Bedrock 호출 한도·일시 장애로 실패하면 간격을 늘려 다시 부른다.
"""

from collections.abc import Callable
from typing import Any, NotRequired

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ModelRetryMiddleware,
    dynamic_prompt,
)
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from src.llm import get_llm
from src.prompts import build_system_prompt

# 이전 대화(바로 앞 답변 제외)가 이 글자 수를 넘으면 요약한다. 비교표가 든 답변 두세 개 분량
HISTORY_SUMMARY_CHARS = 6000

SUMMARY_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "너는 러닝·여행 도우미의 대화 기록을 요약한다. 다음 질문에 답하는 데 필요한 사실만 한국어로 짧게 정리한다.\n"
            "- 사용자가 밝힌 여행지, 숙소 위치, 기간, 원하는 종목·거리 같은 조건\n"
            "- 앞서 찾은 대회·코스의 이름, 날짜, 거리, 가격과 그 출처 링크 (링크 주소는 글자 그대로 옮긴다)\n"
            "- 확인하지 못한 정보는 '확인 불가'로 남긴다\n"
            "새 정보를 덧붙이거나 추측하지 않는다. 카드번호, 연락처 같은 개인정보는 옮기지 않는다.",
        ),
        ("human", "요약할 대화 기록:\n\n{history}"),
    ]
)


def _text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


@dynamic_prompt
def system_prompt(request: ModelRequest) -> str:
    return build_system_prompt()


class HistorySummaryState(AgentState):
    history_summary: NotRequired[str]
    # 요약본으로 대신한 메시지들의 id
    summarized_ids: NotRequired[list[str]]


class HistorySummaryMiddleware(AgentMiddleware[HistorySummaryState]):
    """긴 이전 대화를 요약본 하나로 바꿔 모델에 넘긴다 (대화 이력 요약 미들웨어)."""

    state_schema = HistorySummaryState

    def __init__(self, threshold_chars: int = HISTORY_SUMMARY_CHARS) -> None:
        super().__init__()
        self.threshold_chars = threshold_chars
        self._chain = None

    @property
    def chain(self):
        # LCEL: 프롬프트 → LLM → 문자열
        if self._chain is None:
            self._chain = SUMMARY_PROMPT | get_llm() | StrOutputParser()
        return self._chain

    def before_agent(self, state: HistorySummaryState, runtime: Any) -> dict[str, Any] | None:
        if state.get("history_summary"):
            return None
        messages = state["messages"]
        # 지금 질문(마지막 사용자 메시지)과 바로 앞 답변은 그대로 둔다
        last_user = max(
            (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=None
        )
        if last_user is None:
            return None
        keep_from = last_user
        if keep_from > 0 and isinstance(messages[keep_from - 1], AIMessage):
            keep_from -= 1
        older = messages[:keep_from]
        if sum(len(_text(m)) for m in older) <= self.threshold_chars:
            return None
        history = "\n\n".join(
            f"[{'사용자' if isinstance(m, HumanMessage) else '도우미'}] {_text(m)}" for m in older
        )
        summary = self.chain.invoke({"history": history})
        return {"history_summary": summary, "summarized_ids": [m.id for m in older if m.id]}

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        summary = request.state.get("history_summary")
        ids = set(request.state.get("summarized_ids") or [])
        if not summary or not ids:
            return handler(request)
        kept = [m for m in request.messages if m.id not in ids]
        note = HumanMessage(f"[이전 대화 요약]\n{summary}")
        return handler(request.override(messages=[note, *kept]))


def build_agent_middleware() -> list[AgentMiddleware]:
    """가드레일 뒤에 붙는 미들웨어 목록 (실행 순서대로)."""
    return [
        system_prompt,
        HistorySummaryMiddleware(),
        # 호출 한도(ThrottlingException) 같은 일시 오류는 1초, 2초, 4초 간격으로 세 번까지 다시 부른다.
        # 끝내 실패하면 오류를 그대로 올려 화면과 평가에 "에이전트 실행 실패"로 보이게 한다
        ModelRetryMiddleware(max_retries=3, on_failure="error", initial_delay=1.0, max_delay=30.0),
    ]
