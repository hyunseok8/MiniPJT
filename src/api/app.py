"""FastAPI 서버: 웹 채팅 API.

실행: uv run uvicorn src.api.app:app --reload
"""

import json
from collections.abc import Iterator

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from src.agent import run_agent, stream_agent
from src.api.schemas import ChatRequest, ChatResponse
from src.shared.llm import LLMConfigError

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
