# 미니 PJT: RunTrip Agent

## 무엇을 푸나
해외 마라톤 원정이나 여행지 러닝을 준비하는 러너가 여러 사이트에 흩어진 대회 일정·접수 마감·코스·항공·숙소·날씨 정보를 일일이 찾아 비교해야 하는 문제를, 한 번의 질문으로 출처가 달린 비교표로 정리해 준다.

## 활용한 패턴 (Day 1~7)
- Day _: **도구 호출(Tool Calling) 에이전트** — LangGraph `StateGraph`에서 LLM이 도구 10개(대회 검색, 항공·숙소, 좌표·고도, 러닝 코스, 날씨, 웹 검색·페이지 읽기)를 스스로 골라 부르고, 결과를 보고 다시 판단한다 (`agent → tools → agent`).
- Day _: **병렬 도구 호출** — 서로의 결과가 필요 없는 도구(예: 후보 대회 3곳의 공식 사이트)를 한 번의 응답에서 함께 부르게 해 LLM 왕복을 줄였다. 기능 A 응답이 105초에서 24초로 줄었다.
- Day _: **검색 기반 RAG** — 미리 모은 문서 대신, 웹 검색으로 후보 문서를 찾고 공식 페이지 본문을 읽어 그 내용만 근거로 답한다 (`src/retriever.py`).
- Day _: **프롬프트 엔지니어링과 가드레일** — 시스템 프롬프트에 기간 해석 규칙, "모르면 '확인 불가'", 예약·결제 대행 금지, 개인정보 미제공, 건강 판단 금지 정책을 넣었다.
- Day _: **출력 검증과 자기 수정(Reflection)** — `verify` 노드가 최종 답변의 링크를 도구 결과와 코드로 대조한다. 근거 없는 링크가 있으면 에이전트에게 한 번 다시 쓰게 하고, 그래도 남으면 지운다. 진행 멘트, 숫자 없는 고저차, 요청 기간 밖 날짜도 코드로 고친다.
- Day _: **스트리밍 응답** — LangGraph `stream()`과 FastAPI SSE로 도구 진행 상황("대회 검색 중")과 답변 글자를 생성되는 대로 화면에 보낸다.
- Day _: **LLM-as-a-Judge 평가 루프** — 인-아웃 세트를 돌려 기대 도구는 코드로, 금지 항목은 구조화 출력(`with_structured_output`)을 쓰는 LLM 판정기로 채점한다. 실패 원인을 고치고 다시 도는 반복 루프로 개선했다.

## 아키텍처

```
[브라우저: React 채팅 화면 :5173]
        │  POST /api/chat/stream (SSE: 진행 상황 · 글자 조각 · 최종 답변)
        ▼
[FastAPI  src/api.py :8000]
        │  stream_agent() / run_agent()
        ▼
┌──────────── LangGraph 그래프  src/agent.py ─────────────────────────┐
│                                                                      │
│   ┌─────────┐  도구 호출   ┌──────────────────────────────────────┐  │
│   │  agent  │ ───────────▶ │ tools  (src/tools.py, src/retriever.py)│  │
│   │ (LLM)   │ ◀─────────── │  · search_races, search_web, fetch_page│ ──▶ DuckDuckGo (무료)
│   └─────────┘   도구 결과  │  · geocode, find_running_routes,       │ ──▶ OpenStreetMap (무료)
│      │   ▲                 │    search_popular_routes               │
│      │   │ 근거 없는 링크  │  · get_elevation, get_weather          │ ──▶ Open-Meteo (무료)
│      │   │ → 1회 다시 쓰기 │  · search_flights, search_hotels (목업)│ ──▶ data/mock_travel.json
│ 최종 │   │                 └──────────────────────────────────────┘  │
│ 답변 ▼   │                                                           │
│   ┌──────────┐  링크·진행 멘트·고저차·기간 밖 날짜 검사 (src/verify.py) │
│   │  verify  │ ─────────────────────────────────────────▶ 최종 답변    │
│   └──────────┘                                                       │
└──────────────────────────────────────────────────────────────────────┘
        │ LLM 호출
        ▼
[Amazon Bedrock: Claude Sonnet 4.5 (global 교차 추론, us-west-2)]  ← 유일한 유료 API

[평가] src/evaluate.py ── run_agent() × 12문항 ──▶ src/judge.py(LLM 판정기) ──▶ evaluation/results/
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
#   BEDROCK_MODEL_ID=global.anthropic.claude-sonnet-4-5-20250929-v1:0
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
- 개선폭: **+3 ~ +4건**
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
- **Claude Sonnet 4.5 (Bedrock global 교차 추론 프로필):** 품질과 계정의 하루 토큰 한도 사이에서 고른 모델이다. us 프로필이 한도에 걸린 뒤 global 프로필로 바꿨다.
- **판정기 보정:** 도구 결과를 9,000자까지 보여 주고, 출력 필드 순서를 `근거 → 판정`으로 바꿔 근거와 판정이 엇갈리지 않게 했다. 판정기 결과는 도구 결과 원문으로 다시 확인한 뒤 원인을 정했다.
- **속도 개선:**
  - Overpass는 빠른 미러를 먼저 부르고, 8초 안에 답이 없으면 다른 서버에 동시 요청한다.
  - 검색은 빠른 엔진부터 쓰고, 대회 검색어를 달 이름으로 만든다.
  - 서로 관계없는 도구는 한 번에 함께 부른다.
- **완성 판정은 연속 2회 통과:** LLM 답변은 매번 달라서 한 번 통과로는 완성으로 보지 않았다.

### 남은 한계 · 향후 개선 방향
- **P-02 (기간 안 대회가 없을 때):** 공식 사이트를 열어 확인하지 않고 바로 "없음"으로 답한다. 후보 대회의 날짜를 공식 사이트로 확인하게 프롬프트를 고칠 예정이다.
- **판정 기준의 경계 사례:** "(요청 기간 밖)"이라고 표시하고 넣은 대회(P-01), "가능하지만 더위 조심"(P-04)을 판정기가 실행마다 다르게 본다. 기준을 문장으로 명확히 해야 한다.
- **아직 하지 못한 평가:** 기대 특성(`expected_traits`)의 사람 검토가 남아 있다. RAGAS는 2차 실행 한 번만 측정했다.
- **faithfulness 0.77:** 답변 주장의 약 19%가 도구 결과로 뒷받침되지 않았다. 문장별 판정을 저장하도록 평가 스크립트를 고쳐, 어떤 종류의 문장이 근거 없이 나오는지 분석하고 프롬프트나 출력 검증으로 줄여야 한다.
- **무료 API의 불안정:** 검색 실패, 404 페이지, 장소를 찾지 못하는 경우가 실행마다 몇 건씩 섞인다. 에이전트는 이를 "확인 불가"로 표시한다.
- **목업:** 항공권과 숙소는 목업이다. 무료로 쓸 수 있는 실제 가격 API를 찾으면 도구만 바꿔 끼울 수 있다.

## 핵심 코드 위치
- `src/agent.py:78` — 메인 에이전트 그래프 (`build_graph`: agent → tools → verify)
  - `src/agent.py:143` — `run_agent()`: 평가와 `/api/chat`
  - `src/agent.py:161` — `stream_agent()`: 웹 화면 스트리밍
- `src/tools.py` — 도메인 도구
  - `search_races` :60, `search_flights` :109, `search_hotels` :145, `geocode` :188, `get_elevation` :215, `find_running_routes` :381, `search_popular_routes` :445, `get_weather` :469
  - 도구 등록부 `ALL_TOOLS` :525
- `src/retriever.py` — RAG 파이프라인: `ddg_search` :46 → `search_web` :82 → `fetch_page` :95
- `src/prompts.py:5` — 시스템 프롬프트 (정책 1~5, 기간 해석)
- `src/verify.py` — 출력 검증
  - `find_unverified` :65, `strip_preamble` :101, `fix_elevation_cells` :143, `mark_out_of_range` :208
- `src/api.py:80` — SSE 스트리밍 엔드포인트
- `src/evaluate.py:64` — 문항 채점
- `src/judge.py:90` — LLM 판정기
- `src/ragas_eval.py` — RAGAS 4개 지표 계산 (기준 정보 항목: `evaluation/ragas_references.csv`)
