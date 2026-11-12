import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { streamChat, type ChatMessage, type StreamEvent, type ToolCall } from "./api";

interface DisplayMessage extends ChatMessage {
  toolCalls?: ToolCall[];
  // 답변이 아직 만들어지는 중이면 true. 글자가 타자 치듯 이어 붙는다
  streaming?: boolean;
  // 글자가 나오기 전에 보여 주는 진행 상황 (예: "대회 검색 중")
  status?: string;
}

// 도구 이름 → 진행 상황 문구
const TOOL_LABELS: Record<string, string> = {
  search_races: "대회 검색",
  search_web: "웹 검색",
  fetch_page: "공식 사이트 확인",
  search_flights: "항공권 조회",
  search_hotels: "숙소 조회",
  geocode: "위치 찾기",
  find_running_routes: "근처 코스 찾기",
  search_popular_routes: "인기 코스 검색",
  get_elevation: "고저차 계산",
  get_weather: "날씨 조회",
};

function toolStatus(names: string[]): string {
  const labels = [...new Set(names.map((n) => TOOL_LABELS[n] ?? n))];
  return `${labels.join(", ")} 중`;
}

const EXAMPLES = [
  { label: "마라톤 원정", text: "내년 봄에 갈 만한 해외 하프마라톤 찾아줘" },
  { label: "여행 중 대회", text: "3월에 후쿠오카 여행 가는데 그 기간에 열리는 마라톤 대회 있어?" },
  { label: "러닝 코스", text: "교토 가와라마치역 근처 숙소인데 4월 초에 뛸 러닝 코스 알려줘" },
];

// 이전 대화. 이 창의 메모리에만 두고 브라우저 저장소에는 저장하지 않는다 (CLAUDE.md 금지 사항).
// 새로고침하거나 창을 닫으면 사라진다
interface SavedConversation {
  id: number;
  title: string;
  messages: DisplayMessage[];
}

// 다 받은 메시지만 남긴다 (만들던 답변, 실패해서 빈 답변은 뺀다)
function completedMessages(list: DisplayMessage[]): DisplayMessage[] {
  return list.filter((m) => !m.streaming && m.content.trim());
}

// 질문 목록에서 눌러 이동했을 때 그 질문을 잠깐 강조하는 시간(ms)
const HIGHLIGHT_MS = 1600;

// 화면 모드. 기본은 다크이고, 사용자가 고른 모드만 브라우저에 기억한다 (대화 내용은 저장하지 않는다)
type Theme = "dark" | "light";
const THEME_KEY = "runtrip-theme";

function loadTheme(): Theme {
  try {
    return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export default function App() {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const [theme, setTheme] = useState<Theme>(loadTheme);
  const [saved, setSaved] = useState<SavedConversation[]>([]);
  // 지금 화면의 대화가 이전 대화에서 불러온 것이면 그 번호. 새 대화면 null
  const [currentId, setCurrentId] = useState<number | null>(null);
  const nextSavedId = useRef(1);
  const bottomRef = useRef<HTMLDivElement>(null);
  // 메시지 번호 → 내 질문 말풍선. 질문 목록에서 누르면 이 위치로 스크롤한다
  const questionRefs = useRef(new Map<number, HTMLDivElement>());
  // 새 대화를 시작하면 값이 바뀐다. 이전 대화에서 보낸 요청의 응답이 늦게 오면 버린다
  const conversationId = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const highlightTimer = useRef<number | undefined>(undefined);

  const questions = messages
    .map((m, index) => ({ index, text: m.content, role: m.role }))
    .filter((m) => m.role === "user");
  const lastUserIndex = questions.length ? questions[questions.length - 1].index : -1;

  useEffect(() => {
    // 질문을 보내면 그 질문이 화면 위쪽에 오게 한다. 답변은 그 아래에 타자 치듯 채워진다.
    // 글자가 붙을 때마다 스크롤하면 화면이 흔들리므로 메시지 수가 바뀔 때만 움직인다
    if (lastUserIndex >= 0) {
      questionRefs.current.get(lastUserIndex)?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [messages.length, lastUserIndex]);

  useEffect(() => {
    if (error) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [error]);

  useEffect(() => () => window.clearTimeout(highlightTimer.current), []);

  useEffect(() => {
    if (theme === "light") document.documentElement.dataset.theme = "light";
    else delete document.documentElement.dataset.theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      // 저장소를 쓸 수 없는 환경(사생활 보호 모드 등)에서는 이번 창에서만 유지된다
    }
  }, [theme]);

  // 만들어지는 중인 답변(마지막 메시지)을 고친다
  function updateDraft(patch: (draft: DisplayMessage) => DisplayMessage) {
    setMessages((list) => {
      const last = list[list.length - 1];
      if (!last || last.role !== "assistant" || !last.streaming) return list;
      return [...list.slice(0, -1), patch(last)];
    });
  }

  function onStreamEvent(event: StreamEvent) {
    switch (event.type) {
      case "token":
        updateDraft((d) => ({ ...d, content: d.content + event.text, status: undefined }));
        break;
      case "tools":
        // 도구를 부르기 전에 쓴 글자는 중간 생각이라 지우고, 지금 하는 일을 보여 준다
        updateDraft((d) => ({ ...d, content: "", status: toolStatus(event.names) }));
        break;
      case "rewrite":
        updateDraft((d) => ({ ...d, content: "", status: "출처를 다시 확인하는 중" }));
        break;
      case "done":
        // 출력 검증까지 마친 최종 답변으로 바꿔 끼운다
        updateDraft((d) => ({
          ...d,
          content: event.answer,
          toolCalls: event.tool_calls,
          streaming: false,
          status: undefined,
        }));
        break;
      case "error":
        throw new Error(event.message);
    }
  }

  async function submit(text: string) {
    const question = text.trim();
    if (!question || loading) return;
    // 서버에는 완성된 메시지만 보낸다 (중간에 실패한 빈 답변은 빼고)
    const history: ChatMessage[] = [
      ...completedMessages(messages).map(({ role, content }) => ({ role, content })),
      { role: "user", content: question },
    ];
    setMessages((list) => [
      ...list.filter((m) => !(m.role === "assistant" && !m.content.trim())),
      { role: "user", content: question },
      { role: "assistant", content: "", streaming: true, status: "질문을 살펴보는 중" },
    ]);
    setInput("");
    setError(null);
    setLoading(true);
    const id = conversationId.current;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamChat(
        history,
        (event) => {
          if (id === conversationId.current) onStreamEvent(event);
        },
        controller.signal,
      );
    } catch (e) {
      if (id !== conversationId.current) return;
      setError(e instanceof Error ? e.message : String(e));
      // 답을 다 받지 못했으면 만들던 답변을 지운다
      setMessages((list) => {
        const last = list[list.length - 1];
        return last?.role === "assistant" && last.streaming ? list.slice(0, -1) : list;
      });
    } finally {
      if (id === conversationId.current) {
        setLoading(false);
        abortRef.current = null;
      }
    }
  }

  // 지금 대화를 이전 대화에 보관하고 화면을 비운다
  function leaveCurrent() {
    // 답을 기다리는 중이면 연결을 끊는다 (서버도 그 답을 더 보내지 않는다). 받던 답변은 보관하지 않는다
    abortRef.current?.abort();
    abortRef.current = null;
    conversationId.current += 1;

    const done = completedMessages(messages);
    if (done.some((m) => m.role === "user")) {
      const title = done.find((m) => m.role === "user")!.content;
      if (currentId !== null) {
        // 불러왔던 대화면 그 자리를 새 내용으로 바꾼다 (이어서 질문한 내용까지 남는다)
        setSaved((list) => list.map((c) => (c.id === currentId ? { ...c, title, messages: done } : c)));
      } else {
        const id = nextSavedId.current++;
        setSaved((list) => [{ id, title, messages: done }, ...list]);
      }
    }

    questionRefs.current.clear();
    setInput("");
    setError(null);
    setLoading(false);
    setHighlighted(null);
  }

  function newChat() {
    leaveCurrent();
    setMessages([]);
    setCurrentId(null);
  }

  function openSaved(conversation: SavedConversation) {
    if (conversation.id === currentId) return;
    leaveCurrent();
    setMessages(conversation.messages);
    setCurrentId(conversation.id);
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

        <h2 className="history-title">이전 대화</h2>
        {saved.length === 0 ? (
          <p className="history-empty">새 대화를 시작하면 지금 대화가 여기에 남아요. 새로고침하면 사라져요.</p>
        ) : (
          <ul className="history">
            {saved.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => openSaved(c)}
                  className={c.id === currentId ? "active" : undefined}
                  aria-current={c.id === currentId ? "true" : undefined}
                  title={c.title}
                >
                  <span className="history-text">{c.title}</span>
                  <span className="history-meta">질문 {c.messages.filter((m) => m.role === "user").length}개</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>

      <div className="app">
        <header className="header">
          <div>
            <h1>RunTrip</h1>
            <p>해외 마라톤 대회와 여행지 러닝 코스를 찾아 정리해 드려요.</p>
          </div>
          <div className="header-actions">
            <button
              type="button"
              className="theme-toggle"
              onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
              aria-label={theme === "dark" ? "라이트 모드로 바꾸기" : "다크 모드로 바꾸기"}
            >
              {theme === "dark" ? "라이트 모드" : "다크 모드"}
            </button>
            <button
              type="button"
              className="new-chat"
              onClick={newChat}
              disabled={messages.length === 0 && !loading && !input}
            >
              + 새 대화
            </button>
          </div>
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
                  {m.streaming && !m.content && (
                    <p className="status" role="status">
                      <span className="status-dot" aria-hidden="true" />
                      {m.status ?? "답변을 준비하는 중"}
                    </p>
                  )}
                  <div className={m.streaming && m.content ? "markdown typing" : "markdown"}>
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
