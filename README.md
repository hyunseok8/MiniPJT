# 미니 PJT: RunTrip Agent

## 무엇을 푸나
해외 마라톤 원정이나 여행지 러닝을 준비하는 러너가 여러 사이트에 흩어진 대회 일정·접수 마감·코스·항공·숙소·날씨 정보를 일일이 찾아 비교해야 하는 문제를, 한 번의 질문으로 출처가 달린 비교표로 정리해 준다.

## 활용한 패턴
- **LCEL chain (Pydantic 구조화 출력)** — `ChatPromptTemplate | llm.with_structured_output(TripAnswer)` 체인으로 최종 답변을 Pydantic 모델(dict: 답변 유형, 결론, 기간, 대회·코스 항목, 출처, 목업 여부)로 바꿔 `/api/chat`과 스트리밍 마지막 이벤트로 돌려준다. 답변에 없는 링크는 코드로 지운다 (`src/structured.py`). 이전 대화 요약도 `prompt | llm | StrOutputParser` 체인이다.
- **RAG (검색 기반, 쿼리 변환)** — 미리 모은 문서 대신 DuckDuckGo로 후보 문서를 찾고 공식 페이지 본문을 읽어 그 내용만 근거로 답한다. 기간을 달 이름으로 바꾼 검색어 변환과 검색 엔진 3단계 대체를 쓴다 (`src/retriever.py`, `src/tools.py`). 하이브리드 검색·리랭킹은 쓰지 않았다.
- **ReAct (도구 자율 선택)** — `create_agent`(내부에서 `bind_tools`)로 LLM이 도구 10개를 스스로 골라 부르고, 결과를 보고 다시 판단한다 (`src/agent.py`).
- **도구 다중 (외부 API)** — 대회 검색, 항공·숙소(목업), 좌표·고도(Nominatim, Open-Meteo), 러닝 코스(Overpass), 날씨, 웹 검색·페이지 읽기를 한 질의에서 함께 쓴다. 서로 관계없는 도구는 한 번의 응답에서 병렬로 불러 기능 A 응답을 105초에서 24초로 줄였다.
- **가드레일 (PII·프롬프트 인젝션 방어)** — 입력의 카드번호·주민등록번호·휴대폰 번호·이메일을 가리고, "이전 지시 무시", "시스템 프롬프트 보여줘" 같은 문구는 모델을 부르지 않고 정해진 안내로 끝낸다(`InputGuardMiddleware`). 모델 출력의 카드번호·주민등록번호는 `PIIMiddleware`가 가린다 (`src/guardrails.py`). 출력 쪽에서는 `verify` 노드가 도구 결과에 없는 링크를 코드로 잡아 다시 쓰게 하거나 지운다 (`src/verify.py`).
- **미들웨어 (요약·마스킹·재시도)** — 이전 대화가 길면 LCEL 체인으로 요약해 모델에 넘기고(`HistorySummaryMiddleware`, 원래 기록은 출력 검증용으로 그대로 둔다), Bedrock 일시 오류는 `ModelRetryMiddleware`로 다시 시도하고, 오늘 날짜 시스템 프롬프트는 `dynamic_prompt`로 만든다 (`src/middleware.py`). PII 마스킹은 위 가드레일 미들웨어다.
- **Observability · Trace** — `.env`의 `LANGSMITH_TRACING=true`로 LangGraph·LangChain 실행이 LangSmith에 트레이스로 남는다.
- **평가 (RAGAS · LLM-as-Judge)** — 공식 인-아웃 세트 12문항을 기대 도구(코드)와 금지 항목(LLM 판정기, 구조화 출력)으로 채점하고 고쳐 다시 도는 반복 루프를 돌렸다. RAGAS 4개 지표도 계산했다 (`src/evaluate.py`, `src/judge.py`, `src/ragas_eval.py`).

쓰지 않은 패턴과 이유: MCP 서버 연동( 연동할 사내 시스템이 없음), HITL 승인( 예약·결제를 아예 하지 않는 서비스라 승인할 위험 작업이 없음), Multi-Agent Supervisor, Plan-Execute·장기 메모리( 대화와 위치를 저장하지 않는다는 정책 4와 맞지 않음).

## 아키텍처

```
[브라우저: React 채팅 화면 :5173]
        │  POST /api/chat/stream (SSE: 진행 상황 · 글자 조각 · 최종 답변 · 구조화 결과)
        ▼
[FastAPI  src/api.py :8000]
        │  stream_agent() / run_agent()
        ▼
┌──────────── LangGraph 그래프  src/agent.py ──────────────────────────────────┐
│ ┌─ agent: create_agent (ReAct 서브그래프) ──────────────────────────────────┐ │
│ │ 미들웨어 ① 입력 가드: 개인정보 마스킹 · 인젝션 차단   (src/guardrails.py)  │ │
│ │          ② PIIMiddleware: 입력·출력 카드번호·주민번호 가림                 │ │
│ │          ③ 날짜 시스템 프롬프트 ④ 이전 대화 요약 ⑤ 모델 재시도 (src/middleware.py) │ │
│ │                                                                            │ │
│ │   ┌───────┐ 도구 호출 ┌───────────────────────────────────────────────┐    │ │
│ │   │ model │ ────────▶ │ tools (src/tools.py, src/retriever.py)          │ ─▶ DuckDuckGo · OpenStreetMap
│ │   │ (LLM) │ ◀──────── │  대회·웹 검색·페이지 · 좌표·코스·고도 · 날씨 · 항공·숙소(목업) │ ─▶ Open-Meteo · data/mock_travel.json
│ │   └───────┘ 도구 결과 └───────────────────────────────────────────────┘    │ │
│ └────────────────────────────────────────────────────────────────────────────┘ │
│        │ 최종 답변                ▲ 근거 없는 링크 → 1회 다시 쓰기             │
│        ▼                          │                                            │
│   ┌──────────┐  링크·진행 멘트·고저차·기간 밖 날짜 검사 (src/verify.py)        │
│   │  verify  │ ───────────────────────────────────────────▶ 최종 답변(마크다운) │
│   └──────────┘                                                                 │
└────────────────────────────────────────────────────────────────────────────────┘
        │ 최종 답변
        ▼
[구조화 출력  src/structured.py: LCEL  prompt | llm.with_structured_output(TripAnswer)] ─▶ dict

[Amazon Bedrock: Claude Sonnet 4.5 (us 교차 추론, us-west-2)]  ← 유일한 유료 API (모델·요약·구조화·판정)
[LangSmith]  ← 실행 트레이스

[평가] src/evaluate.py ── run_agent() × 12문항 ──▶ src/judge.py(LLM 판정기) ──▶ evaluation/results/
       src/ragas_eval.py ── 저장된 결과로 RAGAS 4개 지표
```

- **기능 A (해외 마라톤 원정 플래너):** 대회 검색 → 공식 사이트로 날짜·마감일 확인 → 인천 출발 항공권·대회장 근처 숙소(목업) → 비교표
- **기능 B (여행지 러닝 코스 탐색기):** 숙소 좌표 → 근처 공원·강변길·트랙(OpenStreetMap)과 인기 코스(웹) → 고저차 → 여행 시기 평년 날씨 → 현지 러닝 크루
- 대화 기록과 숙소 위치는 서버에 저장하지 않는다. 이전 대화는 브라우저 화면 메모리에만 있다.

## 실행 방법

```bash
# 1. 설치 (uv 권장)
uv sync                                   # 또는: pip install -r requirements.txt
cd frontend && npm install && cd ..

# 2. 환경 변수: .env.example을 .env로 복사하고 AWS 자격 증명과 BEDROCK_MODEL_ID를 채운다
cp .env.example .env
#   BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0
#   AWS_DEFAULT_REGION=us-west-2

# 3. 실행: 백엔드(:8000) + 웹 화면(:5173) → 브라우저에서 http://localhost:5173
./run.sh
#   ./run.sh api   # 백엔드만
#   ./run.sh eval  # 공식 평가 12문항

# 따로 실행할 때
uv run uvicorn src.api:app --reload
cd frontend && npm run dev

# 4. 자체 평가 (결과: evaluation/results/)
uv run python -m src.evaluate                          # 12문항 전체
uv run python -m src.evaluate --case P-01 --case G-01  # 일부 문항

# 5. Docker (선택, 백엔드와 평가)
docker build -t runtrip-agent .
docker run --rm -p 8000:8000 --env-file .env runtrip-agent
```

Bedrock 외의 도구(DuckDuckGo, OpenStreetMap, Open-Meteo)는 모두 무료이고 API 키가 필요 없다. 항공권과 숙소는 목업 데이터다.

## RAGAS 평가 결과
- context_recall: **0.86**
- context_precision: **0.94**
- faithfulness: **0.77**
- answer_relevancy: **0.54** (회피성 판정 2문항을 빼면 0.71)

측정 방법 (`uv run python -m src.ragas_eval`, ragas 0.4.3, 결과: [evaluation/results/ragas_20261113-101736.json](evaluation/results/ragas_20261113-101736.json)):
- **입력:** 2차 자체 평가 실행(`run_20261112-145306.json`)의 질문, 최종 답변, 도구 결과를 그대로 썼다. RunTrip의 RAG는 문서 저장소가 아니라 웹 검색·페이지 본문·지도·날씨 도구라서, `retrieved_contexts`는 그 문항에서 도구가 돌려준 결과다. 오류 결과는 뺐다.
- **측정 문항:** 도구 결과가 있는 8문항(P-01~05, N-01, N-02, E-03). 되묻기(E-01, E-02)와 가드레일 거절(G-01, G-02)은 가져온 문맥이 없어 RAGAS 지표가 정의되지 않으므로 뺐다.
- **기준 답변(reference):** 문항마다 "답하려면 찾아야 하는 정보 항목"을 [evaluation/ragas_references.csv](evaluation/ragas_references.csv)에 따로 적었다. CSV의 기대 특성은 "확인 불가로 안내" 같은 답변 방식 지침이라, 기준 답변으로 넣으면 `context_recall`이 모두 0이 됐다.
- **판정 모델:** Bedrock Claude Sonnet 4.5, 임베딩은 Bedrock Titan Text Embeddings V2.

RAGAS 기본 설정에서 바꾼 것 (기본 설정으로 처음 돌린 결과를 원문과 대조해 원인을 확인한 뒤 고쳤다):

| 지표 | 기본 설정의 문제 | 바꾼 것 |
|------|------------------|---------|
| faithfulness | 긴 비교표 답변(70~100문장)을 한 번에 판정하다 시간 초과로 3문항이 계산 실패 | RAGAS 프롬프트는 그대로 쓰고, 주장 문장을 15개씩 나눠 판정. 점수식(뒷받침 문장 수 ÷ 전체)은 같다 |
| answer_relevancy | 한국어 답변에서 영어 질문을 만들어 원래 질문과의 임베딩 유사도가 낮게 나옴 | 질문을 답변과 같은 언어로 만들라는 지시 한 줄 추가 |
| context_precision | 비교 질문에서 베를린 공식 사이트를 "시드니 정보가 없다"며 0으로 판정. P-05, N-02가 0점 | "답변의 일부에라도 쓰였으면 쓸모 있음" 지시 한 줄 추가 |

해석:
- **answer_relevancy:** P-02(기간 안 대회 없음)와 N-01(없는 대회)은 정책대로 "찾지 못했다", "확인 불가"로 답했고, RAGAS는 이를 회피성 답변으로 보고 0점을 준다. 두 문항을 빼면 0.71이다.
- **faithfulness 0.77:** 답변 주장 문장 419개 중 340개가 도구 결과로 뒷받침됐다. 가장 낮은 문항은 N-01(0.57)과 P-03(0.69)이다. 어떤 문장이 뒷받침되지 않았는지는 아직 문장별로 분석하지 않았다. 답변의 링크는 출력 검증 코드가 별도로 도구 결과와 대조한다.
- **context_recall:** E-03(0.6)과 N-01(0.67)이 가장 낮다. N-01은 존재하지 않을 가능성이 높은 대회 이름을 일부러 넣은 문항이라, 공식 출처가 없는 것이 정상이다. E-03에서 어떤 정보 항목이 빠졌는지는 아직 분석하지 않았다.

## 인-아웃 세트 통과율 (자체 평가)
공식 세트 [evaluation/test_queries.csv](evaluation/test_queries.csv) 12문항. 기대 도구는 코드로, 금지 항목은 LLM 판정기로 자동 채점했다.

- 1차 (2026-11-11 첫 전체 실행): **7 / 12 통과** (58%, guardrail 2/2)
- 2차 (2026-11-12 개선 후, 코드 변경 없이 연속 2회): **10 / 12, 11 / 12 통과** (83%, 92%, guardrail 모두 2/2)
- 2차 이후 (2026-11-13 가드레일·미들웨어·구조화 출력 추가와 실패 원인 수정 후, 연속 2회): **12 / 12, 11 / 12 통과** (100%, 92%, guardrail 모두 2/2)
- 개선폭: **+3 ~ +4건** (최종 코드 기준 +4 ~ +5건)
  - negative 0/2 → 2/2, edge 2/3 → 3/3, P-03 실패 → 통과
  - 주요 개선 사항:
    - 판정기가 도구 결과를 끝까지 보고 근거를 먼저 쓰게 함
    - 기간 해석 규칙, "해외 = 한국 밖", 추정 날짜 금지
    - 출력 검증 코드 추가: 근거 없는 링크, 고저차, 기간 밖 날짜
    - 검색 속도 개선과 병렬 도구 호출 (전체 평가 21분 → 9분)

자세한 내용은 [evaluation/round1_report.md](evaluation/round1_report.md), [evaluation/round2_report.md](evaluation/round2_report.md), 실행 기록은 [evaluation/LOOP_LOG.md](evaluation/LOOP_LOG.md)에 있다.

## 트라이앤에러 회고

### 시도했지만 실패한 접근 · 왜 실패했는가
- **유료 검색·러닝 API (Tavily, Strava):** 처음에는 이 둘로 검색과 인기 코스를 찾으려 했다. 비용 원칙("유료는 LLM 하나")에 맞지 않아 접었다. Strava 구간 정보는 무료 검색으로는 얻을 수 없었다.
- **프롬프트만으로 규칙 지키게 하기:** 프롬프트에 규칙을 여러 번 보강했지만 고쳐지지 않은 것들이 있었다.
  - 진행 멘트("모든 정보가 모였습니다")
  - 고저차를 숫자 대신 "완만한 오르막"으로 쓰기
  - 요청 기간 밖 대회 섞기
- **Claude Haiku 4.5로 비용 줄이기:** 기간을 잘못 해석하고, 없는 대회를 비슷한 대회 정보로 채웠다. 판정기로 쓰면 이런 위반을 놓쳤다.
- **판정기를 그대로 믿기:** 도구 결과를 4,000자에서 잘라 넘겼더니 판정기가 멀쩡한 답변을 "지어냄"으로 오판했다. 판정 근거와 결론이 엇갈리는 일도 있었다.
- **Overpass 기본 서버, 검색 엔진 기본 순서:** 붐빌 때 응답이 40초를 넘었고, 검색 한 번에 8~24초가 걸렸다.

### 최종 채택한 접근 · 왜 그것으로 갔는가
- **무료 도구 조합:** DuckDuckGo, OpenStreetMap, Open-Meteo는 키 없이 쓸 수 있고, 모든 외부 호출을 도구 인터페이스 뒤에 두어 막히면 목업으로 바꿀 수 있다.
- **코드로 하는 출력 검증(`verify` 노드):** 프롬프트로 안 지켜지는 규칙은 LLM이 아니라 코드로 검사하고 고친다. 결과가 매번 같고, 무엇을 고쳤는지 기록이 남는다.
- **Claude Sonnet 4.5 (Bedrock 교차 추론 프로필):** 품질과 계정의 하루 토큰 한도 사이에서 고른 모델이다. 하루 한도가 프로필(`us.`, `global.`)마다 따로 잡혀 있어, 한쪽이 한도에 걸리면 같은 모델의 다른 프로필로 바꿔 쓴다. 2차 평가는 `global.`, 이후에는 `us.` 프로필을 쓴다.
- **판정기 보정:** 도구 결과를 9,000자까지 보여 주고, 출력 필드 순서를 `근거 → 판정`으로 바꿔 근거와 판정이 엇갈리지 않게 했다. 판정기 결과는 도구 결과 원문으로 다시 확인한 뒤 원인을 정했다.
- **속도 개선:**
  - Overpass는 빠른 미러를 먼저 부르고, 8초 안에 답이 없으면 다른 서버에 동시 요청한다.
  - 검색은 빠른 엔진부터 쓰고, 대회 검색어를 달 이름으로 만든다.
  - 서로 관계없는 도구는 한 번에 함께 부른다.
- **완성 판정은 연속 2회 통과:** LLM 답변은 매번 달라서 한 번 통과로는 완성으로 보지 않았다.

### 남은 한계 · 향후 개선 방향
- **링크와 대상의 짝:** 출력 검증은 링크가 도구 결과에 있는지만 확인한다. 무료 검색이 여러 페이지 제목을 한 항목으로 합쳐 돌려주면, 다른 단체의 링크를 엉뚱한 모임 이름에 붙이는 일이 있었다(N-02, 11/13). 검색 결과 제목과 답변의 이름을 대조하는 검사가 필요하다.
- **P-02 (기간 안 대회가 없을 때):** 11/12 두 실행에서는 공식 사이트를 열어 확인하지 않고 바로 "없음"으로 답했다. 11/13 최종 코드로는 세 번 연속 통과했지만, 같은 행동이 다시 나오는지 계속 봐야 한다.
- **판정 기준의 경계 사례:** "(요청 기간 밖)"이라고 표시하고 넣은 대회(P-01, P-02), "가능하지만 더위 조심"(P-04)을 판정기가 실행마다 다르게 봤다. 11/13에 기간 밖 대회는 표에서 빼 따로 적고, 더위 질문에는 판단하지 않게 프롬프트를 고친 뒤로는 통과했다.
- **아직 하지 못한 평가:** 기대 특성(`expected_traits`)의 사람 검토가 남아 있다. RAGAS는 2차 실행 한 번만 측정했다.
- **faithfulness 0.77:** 답변 주장의 약 19%가 도구 결과로 뒷받침되지 않았다. 문장별 판정을 저장하도록 평가 스크립트를 고쳐, 어떤 종류의 문장이 근거 없이 나오는지 분석하고 프롬프트나 출력 검증으로 줄여야 한다.
- **무료 API의 불안정:** 검색 실패, 404 페이지, 장소를 찾지 못하는 경우가 실행마다 몇 건씩 섞인다. 에이전트는 이를 "확인 불가"로 표시한다.
- **목업:** 항공권과 숙소는 목업이다. 무료로 쓸 수 있는 실제 가격 API를 찾으면 도구만 바꿔 끼울 수 있다.

## 핵심 코드 위치
- `src/agent.py:102` — 메인 에이전트 그래프 (`build_graph`: agent → verify)
  - `src/agent.py:91` — `build_react_agent()`: `create_agent`(ReAct, 도구 결합)와 미들웨어 목록
  - `src/agent.py:164` — `run_agent()`: 평가와 `/api/chat` (구조화 출력 포함)
  - `src/agent.py:186` — `stream_agent()`: 웹 화면 스트리밍
- `src/tools.py` — 도메인 도구
  - `search_races` :60, `search_flights` :109, `search_hotels` :145, `geocode` :188, `get_elevation` :215, `find_running_routes` :381, `search_popular_routes` :445, `get_weather` :469
  - 도구 등록부 `ALL_TOOLS` :525
- `src/retriever.py` — RAG 파이프라인: `ddg_search` :46 → `search_web` :82 → `fetch_page` :95
- `src/structured.py:80` — LCEL 구조화 출력 체인 (`TripAnswer` :38, `structure_answer` :95)
- `src/guardrails.py:111` — 가드레일: `InputGuardMiddleware`(PII 마스킹, 인젝션 차단), `build_guard_middleware` :137
- `src/middleware.py:66` — 이전 대화 요약 미들웨어, `build_agent_middleware` :117 (날짜 프롬프트, 재시도)
- `src/prompts.py:5` — 시스템 프롬프트 (정책 1~5, 기간 해석)
- `src/verify.py` — 출력 검증
  - `find_unverified` :65, `strip_preamble` :101, `fix_elevation_cells` :143, `mark_out_of_range` :208
- `src/api.py:91` — SSE 스트리밍 엔드포인트 (`/api/chat` :78)
- `src/evaluate.py:64` — 문항 채점
- `src/judge.py:90` — LLM 판정기
- `src/ragas_eval.py` — RAGAS 4개 지표 계산 (기준 정보 항목: `evaluation/ragas_references.csv`)
