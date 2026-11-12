export type Role = "user" | "assistant";

export interface ChatMessage {
  role: Role;
  content: string;
}

export interface ToolCall {
  name: string;
  args: Record<string, unknown>;
}

export interface ChatResponse {
  answer: string;
  tool_calls: ToolCall[];
  tool_errors: { tool: string; error: string }[];
}

// /api/chat/stream 이벤트 (backend/agent/graph.py의 stream_agent()와 같은 형식)
export type StreamEvent =
  | { type: "token"; text: string }
  | { type: "tools"; names: string[] }
  | { type: "rewrite" }
  | ({ type: "done" } & ChatResponse)
  | { type: "error"; message: string };

// 대화 기록은 브라우저 메모리에만 두고 서버에 저장하지 않는다 (SERVICE.md 정책 4)
export async function sendChat(messages: ChatMessage[]): Promise<ChatResponse> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ?? `서버 오류 (${res.status})`);
  }
  return res.json();
}

// 답변이 만들어지는 대로 이벤트를 받는다. done이나 error 이벤트 없이 연결이 끊기면 오류로 본다
export async function streamChat(
  messages: ChatMessage[],
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
    signal,
  });
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => null);
    const detail = body?.detail;
    throw new Error(typeof detail === "string" ? detail : `서버 오류 (${res.status})`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = false;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      for (const line of block.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        const event = JSON.parse(line.slice(6)) as StreamEvent;
        if (event.type === "done" || event.type === "error") finished = true;
        onEvent(event);
      }
    }
  }
  if (!finished) throw new Error("답변을 받는 중에 연결이 끊겼어요. 다시 질문해 주세요.");
}
