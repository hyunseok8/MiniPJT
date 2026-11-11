import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { sendChat, type ChatMessage, type ToolCall } from "./api";

interface DisplayMessage extends ChatMessage {
  toolCalls?: ToolCall[];
}

const EXAMPLES = [
  { label: "마라톤 원정", text: "내년 봄에 갈 만한 해외 하프마라톤 찾아줘" },
  { label: "여행 중 대회", text: "4월 둘째 주에 오사카 여행 가는데 그 기간에 열리는 마라톤 대회 있어?" },
  { label: "러닝 코스", text: "교토 가와라마치역 근처 숙소인데 4월 초에 뛸 러닝 코스 알려줘" },
];

export default function App() {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function submit(text: string) {
    const question = text.trim();
    if (!question || loading) return;
    const next: DisplayMessage[] = [...messages, { role: "user", content: question }];
    setMessages(next);
    setInput("");
    setError(null);
    setLoading(true);
    try {
      const res = await sendChat(next.map(({ role, content }) => ({ role, content })));
      setMessages([...next, { role: "assistant", content: res.answer, toolCalls: res.tool_calls }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit(input);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit(input);
    }
  }

  return (
    <div className="app">
      <header className="header">
        <h1>RunTrip Agent</h1>
        <p>해외 마라톤 대회와 여행지 러닝 코스를 찾아 정리해 드려요.</p>
      </header>

      <main className="messages">
        {messages.length === 0 && (
          <div className="empty">
            <p>이렇게 물어보세요</p>
            <div className="examples">
              {EXAMPLES.map((ex) => (
                <button key={ex.label} type="button" onClick={() => submit(ex.text)}>
                  <strong>{ex.label}</strong>
                  <span>{ex.text}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`message ${m.role}`}>
            {m.role === "assistant" ? (
              <>
                <div className="markdown">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      a: (props) => <a {...props} target="_blank" rel="noreferrer" />,
                    }}
                  >
                    {m.content}
                  </ReactMarkdown>
                </div>
                {m.toolCalls && m.toolCalls.length > 0 && (
                  <details className="tools">
                    <summary>사용한 도구 {m.toolCalls.length}개</summary>
                    <ul>
                      {m.toolCalls.map((t, j) => (
                        <li key={j}>
                          <code>{t.name}</code> {JSON.stringify(t.args)}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </>
            ) : (
              <p>{m.content}</p>
            )}
          </div>
        ))}

        {loading && <div className="message assistant loading">정보를 찾는 중이에요. 1~2분 걸릴 수 있어요…</div>}
        {error && <div className="error">{error}</div>}
        <div ref={bottomRef} />
      </main>

      <form className="composer" onSubmit={onSubmit}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="예: 10월에 기록 잘 나오는 평탄한 해외 풀코스 마라톤 추천해줘"
          rows={2}
          disabled={loading}
        />
        <button type="submit" disabled={loading || !input.trim()}>
          보내기
        </button>
      </form>
    </div>
  );
}
