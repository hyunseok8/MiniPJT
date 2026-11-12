import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { sendChat, type ChatMessage, type ToolCall } from "./api";

interface DisplayMessage extends ChatMessage {
  toolCalls?: ToolCall[];
}

const EXAMPLES = [
  { label: "마라톤 원정", text: "내년 봄에 갈 만한 해외 하프마라톤 찾아줘" },
  { label: "여행 중 대회", text: "3월에 후쿠오카 여행 가는데 그 기간에 열리는 마라톤 대회 있어?" },
  { label: "러닝 코스", text: "교토 가와라마치역 근처 숙소인데 4월 초에 뛸 러닝 코스 알려줘" },
];

// 질문 목록에서 눌러 이동했을 때 그 질문을 잠깐 강조하는 시간(ms)
const HIGHLIGHT_MS = 1600;

export default function App() {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  // 메시지 번호 → 내 질문 말풍선. 질문 목록에서 누르면 이 위치로 스크롤한다
  const questionRefs = useRef(new Map<number, HTMLDivElement>());
  // 새 대화를 시작하면 값이 바뀐다. 이전 대화에서 보낸 요청의 응답이 늦게 오면 버린다
  const conversationId = useRef(0);
  const highlightTimer = useRef<number | undefined>(undefined);

  const questions = messages
    .map((m, index) => ({ index, text: m.content, role: m.role }))
    .filter((m) => m.role === "user");
  const lastUserIndex = questions.length ? questions[questions.length - 1].index : -1;

  useEffect(() => {
    const last = messages[messages.length - 1];
    if (last?.role === "assistant") {
      // 답이 도착하면 내 질문이 화면 위쪽에 오게 한다. 맨 아래로 내리면 긴 답변에 질문이 밀려 가려진다
      questionRefs.current.get(lastUserIndex)?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, loading, lastUserIndex]);

  useEffect(() => () => window.clearTimeout(highlightTimer.current), []);

  async function submit(text: string) {
    const question = text.trim();
    if (!question || loading) return;
    const next: DisplayMessage[] = [...messages, { role: "user", content: question }];
    setMessages(next);
    setInput("");
    setError(null);
    setLoading(true);
    const id = conversationId.current;
    try {
      const res = await sendChat(next.map(({ role, content }) => ({ role, content })));
      if (id !== conversationId.current) return;
      setMessages([...next, { role: "assistant", content: res.answer, toolCalls: res.tool_calls }]);
    } catch (e) {
      if (id !== conversationId.current) return;
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (id === conversationId.current) setLoading(false);
    }
  }

  function newChat() {
    conversationId.current += 1;
    questionRefs.current.clear();
    setMessages([]);
    setInput("");
    setError(null);
    setLoading(false);
    setHighlighted(null);
  }

  function jumpTo(index: number) {
    questionRefs.current.get(index)?.scrollIntoView({ behavior: "smooth", block: "start" });
    setHighlighted(index);
    window.clearTimeout(highlightTimer.current);
    highlightTimer.current = window.setTimeout(() => setHighlighted(null), HIGHLIGHT_MS);
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
    <div className="layout">
      <aside className="sidebar" aria-label="예시 질문">
        <h2>이렇게 물어보세요</h2>
        <div className="examples">
          {EXAMPLES.map((ex) => (
            <button key={ex.label} type="button" onClick={() => submit(ex.text)} disabled={loading}>
              <strong>{ex.label}</strong>
              <span>{ex.text}</span>
            </button>
          ))}
        </div>
      </aside>

      <div className="app">
        <header className="header">
          <div>
            <h1>RunTrip Agent</h1>
            <p>해외 마라톤 대회와 여행지 러닝 코스를 찾아 정리해 드려요.</p>
          </div>
          <button
            type="button"
            className="new-chat"
            onClick={newChat}
            disabled={messages.length === 0 && !loading && !input}
          >
            + 새 대화
          </button>
        </header>

        <main className="messages">
          {messages.length === 0 && (
            <div className="empty">
              <p>왼쪽 예시를 누르거나, 아래에 질문을 입력해 주세요.</p>
            </div>
          )}

          {messages.map((m, i) => (
            <div
              key={i}
              className={`message ${m.role}${highlighted === i ? " highlighted" : ""}`}
              ref={
                m.role === "user"
                  ? (el) => {
                      if (el) questionRefs.current.set(i, el);
                      else questionRefs.current.delete(i);
                    }
                  : undefined
              }
            >
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
            aria-label="질문 입력"
          />
          <button type="submit" disabled={loading || !input.trim()}>
            보내기
          </button>
        </form>
      </div>

      <aside className="question-list" aria-label="이번 대화의 질문">
        <h2>이번 대화의 질문</h2>
        {questions.length === 0 ? (
          <p className="question-list-empty">질문하면 여기에 목록이 생겨요. 누르면 그 질문으로 이동해요.</p>
        ) : (
          <ol>
            {questions.map((q, n) => (
              <li key={q.index}>
                <button
                  type="button"
                  onClick={() => jumpTo(q.index)}
                  className={highlighted === q.index ? "active" : undefined}
                  title={q.text}
                >
                  <span className="question-number">{n + 1}</span>
                  <span className="question-text">{q.text}</span>
                  {loading && q.index === lastUserIndex && <span className="question-status">답변 중</span>}
                </button>
              </li>
            ))}
          </ol>
        )}
      </aside>
    </div>
  );
}
