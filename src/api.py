"""FastAPI 서버: 웹 채팅 API.

실행: uv run uvicorn src.api:app --reload
"""

import json
from collections.abc import Iterator
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.agent import run_agent, stream_agent
from src.llm import LLMConfigError

# ======================================================================
# 요청, 응답 형식
# ======================================================================


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


# ======================================================================
# 라우트
# ======================================================================

app = FastAPI(title="RunTrip Agent")


def _check_last_is_user(req: ChatRequest) -> None:
    if req.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="마지막 메시지는 사용자 질문이어야 합니다.")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """답변이 완성되면 한 번에 돌려준다 (평가 도구, 다른 클라이언트용)."""
    _check_last_is_user(req)
    try:
        result = await run_in_threadpool(run_agent, [m.model_dump() for m in req.messages])
    except LLMConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # Bedrock 인증 실패, 호출 한도 초과 등 외부 장애
        raise HTTPException(status_code=502, detail=f"에이전트 실행 실패: {exc}") from exc
    return ChatResponse(**result)


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    """답변이 만들어지는 대로 Server-Sent Events로 흘려보낸다 (웹 화면용).

    이벤트 형식은 src.agent.graph.stream_agent()를 따르고, 실패하면 {"type": "error"}를 보낸다.
    """
    _check_last_is_user(req)
    history = [m.model_dump() for m in req.messages]

    def events() -> Iterator[str]:
        try:
            for event in stream_agent(history):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except LLMConfigError as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"
        except Exception as exc:  # Bedrock 인증 실패, 호출 한도 초과 등 외부 장애
            message = f"에이전트 실행 실패: {exc}"
            yield f"data: {json.dumps({'type': 'error', 'message': message}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
