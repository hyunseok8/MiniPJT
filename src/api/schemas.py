"""웹 채팅 API의 요청, 응답 형식."""

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    # 비교표가 들어간 이전 답변은 4000자를 넘기도 한다. 이어지는 질문에 함께 보내므로 넉넉히 받는다
    content: str = Field(min_length=1, max_length=20000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)


class ToolCall(BaseModel):
    name: str
    args: dict


class ToolError(BaseModel):
    tool: str
    error: str


class ChatResponse(BaseModel):
    answer: str
    tool_calls: list[ToolCall]
    tool_errors: list[ToolError]
