"""FastAPI 서버: 웹 채팅 API."""

from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from backend.agent import run_agent
from backend.agent.llm import LLMConfigError

app = FastAPI(title="RunTrip Agent")


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


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


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    if req.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="마지막 메시지는 사용자 질문이어야 합니다.")
    try:
        result = await run_in_threadpool(run_agent, [m.model_dump() for m in req.messages])
    except LLMConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # Bedrock 인증 실패, 호출 한도 초과 등 외부 장애
        raise HTTPException(status_code=502, detail=f"에이전트 실행 실패: {exc}") from exc
    return ChatResponse(**result)
