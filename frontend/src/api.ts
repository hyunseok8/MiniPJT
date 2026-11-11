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
