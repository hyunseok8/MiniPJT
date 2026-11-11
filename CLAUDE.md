# CLAUDE.md — 어떻게 만드는가

> 이 문서는 구현 방법을 정한다. 무엇을 왜 만드는지(사용자, 문제, 가치, 성공 기준)는 [SERVICE.md](SERVICE.md)를 따른다.
> 작업 전에 SERVICE.md를 먼저 읽고, 범위를 벗어나는 기능은 만들지 않는다.

## 기술 스택

| 구분 | 선택 | 버전 | 비고 |
|------|------|------|------|
| 언어 | Python, TypeScript | Python 3.12 이상 | 백엔드는 Python, 프론트엔드는 TypeScript |
| 에이전트 프레임워크 | LangGraph | 1.2 | 도구 호출 흐름을 그래프로 구성 |
| LLM | Claude Sonnet 4.5 (Amazon Bedrock, us-west-2) | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | `langchain-aws`로 연결. 모델 ID와 리전은 `.env`의 `BEDROCK_MODEL_ID`, `AWS_DEFAULT_REGION`. 평가 판정기도 같은 모델을 쓴다(`JUDGE_MODEL_ID`가 비어 있을 때). 아래 "모델 선택 기록" 참고 |
| 백엔드 | FastAPI | | 웹 채팅 API 제공 (`/api/chat`, `/api/health`) |
| 프론트엔드 | React + Vite | React 19, Vite 8 | 웹 채팅 화면. 답변 마크다운은 `react-markdown` + `remark-gfm`으로 표시 |
| 웹 검색 | DuckDuckGo (`ddgs`) | | 무료, 키 불필요. 페이지 본문은 직접 받아 `beautifulsoup4`로 추출 |
| 러닝 코스 | OpenStreetMap Overpass API | | 무료, 키 불필요. 인기 코스는 웹 검색으로 찾는다 |
| 지도, 고도, 날씨 | OpenStreetMap Nominatim, Open-Meteo | | 무료 API, 키 불필요 |
| 항공권, 숙소 | 목업 | | `backend/tools/travel.py` |
| 데이터베이스 | 없음 | | 대화와 사용자 위치를 저장하지 않는다 (SERVICE.md 정책 4) |
| 패키지 관리 | uv (Python), npm (프론트엔드) | | |
| 테스트 | 평가 루프 (공식: `evaluation/`, 보조: `evals/`) | | 아래 "평가 루프" 참고 |
| 배포 | 로컬 실행 | | 개인용이라 배포하지 않는다 |

### 모델 선택 기록 (2026-11-11)

| 모델 | 결과 | 판단 |
|------|------|------|
| Claude Sonnet 4.6 (`global.anthropic.claude-sonnet-4-6`, us-east-1) | 문항 수정 전 12건 58%, 수정 후 실패 5건 재실행 5/5 | 품질은 좋았으나 계정의 하루 토큰 한도(`ThrottlingException: Too many tokens per day`)에 걸려 더 쓸 수 없었다 |
| Claude Haiku 4.5 (us-west-2) | 12건 자동 75% | 기간 해석을 틀리고, 없는 대회를 비슷한 대회 정보로 채웠다. **판정기로 쓰면 이런 위반을 놓친다.** 판정기로 쓰지 않는다 |
| **Claude Sonnet 4.5 (us-west-2)** | 12건 83%, 이후 보정 뒤 실패 문항 재실행 모두 통과 | 지금 쓰는 모델 |

## 폴더 구조

```
miniPJT/
├── SERVICE.md              # 무엇을, 왜 만드는가
├── CLAUDE.md               # 어떻게 만드는가 (이 문서)
├── test_queries.json       # 보조 회귀 세트 24건 (evals/run.py가 읽는다)
├── pyproject.toml          # Python 의존성 (uv)
├── .env.example            # 환경 변수 견본. 복사해서 .env로 쓴다
├── backend/
│   ├── main.py             # FastAPI 서버
│   ├── config.py           # 환경 변수 설정
│   ├── agent/
│   │   ├── graph.py        # LangGraph 그래프, run_agent()
│   │   ├── prompts.py      # 시스템 프롬프트 (정책 포함)
│   │   └── llm.py          # Bedrock Claude 연결
│   └── tools/              # 에이전트 도구. SERVICE.md 3장 도구 표와 일치
│       ├── search.py       # search_web, fetch_page, search_races (DuckDuckGo)
│       ├── travel.py       # search_flights, search_hotels (목업)
│       ├── geo.py          # geocode, get_elevation
│       ├── weather.py      # get_weather
│       ├── routes.py       # find_running_routes (OpenStreetMap), search_popular_routes
│       └── common.py       # HTTP 클라이언트, 오류 형식, 목업용 유틸
├── evals/
│   ├── run.py              # 평가 루프 실행기 (반복 1회)
│   ├── judge.py            # LLM 판정기
│   ├── LOOP_LOG.md         # 보조 세트 반복 기록 (자동)
│   └── runs/               # 보조 세트 반복별 결과 (iter_001.json ...)
├── evaluation/             # ★ 공식 평가
│   ├── test_queries.csv    # 공식 인-아웃 세트 12건 (positive, negative, edge, guardrail)
│   ├── run_csv.py          # 채점기: 기대 도구 + 금지 항목 자동 채점, 기대 특성은 사람 검토
│   ├── LOOP_LOG.md         # 실행과 수정 기록
│   └── results/            # 채점 결과 (summary, review CSV, run JSON)
└── frontend/               # React + Vite 웹 채팅 화면
    └── src/
        ├── App.tsx         # 채팅 화면
        └── api.ts          # /api/chat 호출
```

## 실행 방법

```bash
# 1. 설치
uv sync
cd frontend && npm install && cd ..

# 2. 환경 변수: .env.example을 .env로 복사한 뒤 값을 채운다
cp .env.example .env

# 3. 백엔드 실행 (http://localhost:8000)
uv run uvicorn backend.main:app --reload

# 4. 프론트엔드 실행 (http://localhost:5173, /api는 백엔드로 프록시)
cd frontend && npm run dev

# 5. 공식 평가: CSV 문항 채점 (아래 "평가 루프" 참고). 결과는 evaluation/results/
uv run python -m evaluation.run_csv

# 6. 보조 평가: test_queries.json 24건 반복 루프. 결과는 evals/runs/
uv run python -m evals.run

# 린트, 포맷
uv run ruff check . && uv run ruff format .
```

## 개발 범위와 우선순위

제출 기한이 1주 이내이므로 기능 A(해외 마라톤 원정 플래너)와 B(여행지 러닝 코스 탐색기)를 모두 만들되, 필수 항목을 먼저 완성한다.

- 유료 API는 LLM(Bedrock) 하나만 쓴다. 도구는 모두 무료이고 키가 필요 없는 API만 쓴다. 새 도구를 추가할 때도 이 원칙을 지킨다.
- 모든 외부 API(검색, 지도, 날씨)는 도구 인터페이스 뒤에 두고, 연동이 막히면 목업 구현으로 바꿔 끼울 수 있게 만든다.
- 무료 공개 API는 느리거나 호출 한도에 걸릴 수 있다. 특히 OpenStreetMap Overpass는 붐빌 때 응답에 40초 이상 걸린다. 이런 실패는 외부 장애로 본다.
- 항공권, 숙소는 처음부터 목업으로 구현한다.

## 평가 루프 (인-아웃 세트 + 반복 자기 수정)

에이전트의 완성 여부는 **공식 인-아웃 세트 [evaluation/test_queries.csv](evaluation/test_queries.csv)** 를 반복 루프로 돌려서 판정한다. AI(Claude Code)는 이 루프를 직접 돌리면서 실패 원인을 찾아 코드를 고치고, 종료 조건을 만날 때까지 반복한다.

루트의 [test_queries.json](test_queries.json)(24건)은 **보조 회귀 세트**다. 기능을 크게 바꿨을 때 더 넓게 확인하는 용도로 `evals/run.py`로 돌린다. 완성 판정에는 쓰지 않는다.

### 인-아웃 세트 (공식)

- **입력(in):** 각 문항의 `input`
- **기대 출력(out):**
  - `expected_tools`: 부를 도구. `a|b`는 둘 중 하나. **자동 채점(코드)**
  - `forbidden`: 답변에 있으면 안 되는 것. **자동 채점(LLM 판정기, 도구 결과와 대조)**
  - `expected_traits`: 답변에 있어야 하는 것. **사람 검토** (review CSV의 "사람 판정" 칸)
- **자동 통과:** 실행 성공 + 기대 도구 모두 호출 + 금지 항목 위반 없음
- **세트:** 12건 전체. 디버깅 중에는 `--case`로 일부만 돌려도 되지만, 판정은 항상 전체로 한다.

### 반복 1회의 절차

1. **실행·판정·집계:** `uv run python -m evaluation.run_csv`. 문항마다 에이전트를 실행하고 채점해서 카테고리별, 전체 통과율을 출력한다.
2. **기록:** 결과는 `evaluation/results/`에 저장된다 (`summary_<시각>.txt`, `review_<시각>.csv`, `run_<시각>.json`). AI는 [evaluation/LOOP_LOG.md](evaluation/LOOP_LOG.md)에 한 줄을 덧붙인다: 시각, 범위, 모델, 결과, 이번 반복 전에 고친 내용.
3. **사람 검토:** `review_<시각>.csv`의 "사람 판정" 칸에서 `expected_traits`를 확인한다.
4. **종료 판단:** 아래 종료 조건을 확인한다. 끝나지 않았으면 5로 간다.
5. **수정:** 실패 원인을 분석해 아래 표의 수정 위치를 고친 뒤, 1부터 다시 반복한다. 실패 원인이 판정기나 문항에 있는지도 확인한다 (답변과 도구 결과 원문을 직접 본다).

### 실패 유형과 수정 위치

| 실패 유형 | 예 | 수정 위치 |
|-----------|----|-----------|
| 도구를 안 부르거나 잘못 부름 | `expected_tools`의 도구를 호출하지 않음 | 도구 설명(docstring), 시스템 프롬프트의 도구 사용 지침, 그래프 라우팅. 다른 도구로 같은 일을 했다면 문항의 대안 도구(`a\|b`) 누락일 수 있다 |
| 도구 결과가 틀리거나 비어 있음 | 검색 결과 없음, 거리 계산 오류 | 도구 구현, 검색어 생성 로직 |
| 답변 항목 누락 | 출처 링크, 비교표 항목, 날씨가 빠짐 | 답변 작성 프롬프트, 출력 템플릿 |
| 조건 위반 | 기간 밖 대회, 종목이 다른 대회 포함 | 조건 추출 노드, 결과 필터링 로직 |
| 되묻기 실패 | 모호한 입력에 말없이 임의로 가정 | 입력 분석 노드 |
| 가드레일 위반 | 예약 대행, 날짜를 지어냄, 개인 연락처 제공 | 시스템 프롬프트 정책, 입력 분류 노드, 출력 검증 노드 |
| 판정기 오판 | 기준을 만족했는데 실패로 판정, 근거와 판정이 엇갈림 | 판정 프롬프트(`evals/judge.py`, CSV 채점기도 이것을 쓴다). 단, 사용자 확인을 받은 뒤에만 고친다 |

지금 그래프(`backend/agent/graph.py`)는 `agent`, `tools`, `verify` 세 노드다. `verify`는 출력 검증 노드로, 최종 답변의 링크가 도구 결과에 실제로 나온 주소인지 코드로 확인한다(`backend/agent/verify.py`). 근거 없는 링크가 있으면 agent에게 한 번 고쳐 쓰게 하고, 그래도 남으면 링크를 지우고 "(출처 확인 불가)"로 바꾼다. 마지막으로 프롬프트 규칙만으로는 반복해서 어긴 것들을 코드로 고친다:
- 답변 첫 줄의 진행 멘트("모든 정보가 모였습니다", "비교해 드릴게요" 등)를 지운다.
- 표의 "고저차" 칸에 숫자도 "확인 불가"도 없으면 "확인 불가 (출처 설명: …)"로 바꾼다.
- 답변 첫 줄에 밝힌 기간(예: "2027년 3~5월") 밖의 날짜가 표에 있으면 그 날짜 옆에 "(요청 기간 밖)"을 붙인다.

고친 내용은 결과의 `verification` 기록(`preamble_removed`, `elevation_fixed`, `out_of_range_marked`)에 남는다. 이 기록이 자주 나오면 프롬프트나 도구 쪽 원인을 함께 고친다. 표에 나온 다른 노드(조건 추출, 입력 분석, 입력 분류)가 필요해지면 그래프에 새로 추가한다. 프롬프트는 `backend/agent/prompts.py`, 도구는 `backend/tools/`에 있다.

### 수정 규칙

- **우선순위:** 가드레일 실패 → 회귀(전에 통과했다가 실패한 케이스) → 실패 케이스가 가장 많은 원인 순서로 고친다.
- **작게 고친다:** 한 번의 반복에서는 원인 하나, 또는 서로 연관된 원인 몇 개만 고친다. 그래야 어떤 수정이 효과가 있었는지 알 수 있다.
- **일반화해서 고친다:** 특정 `query` 문구를 감지해 정답을 하드코딩하지 않는다. 같은 유형의 다른 입력에도 통하는 수정만 한다.
- **기준을 고쳐서 통과시키지 않는다:** 루프 중에는 `evaluation/test_queries.csv`(와 보조 세트 `test_queries.json`)의 문항, 기대 출력, 통과 기준을 수정하지 않는다. 문항 자체가 잘못됐다고 판단되면 루프를 멈추고, 근거와 함께 사용자에게 묻는다. 사용자가 승인한 문항 수정은 LOOP_LOG.md에 남긴다.
- **외부 장애는 코드 결함이 아니다:** API 키 없음, 호출 한도 초과, 외부 서비스 장애로 실패하면 코드를 고치지 않는다. 사용자에게 알리고, 목업으로 바꿀지 확인한다.
- **기록을 남긴다:** 수정할 때마다 `evaluation/LOOP_LOG.md`에 "실패 문항 → 원인 → 고친 내용 → 다음 반복 결과"를 적는다.
- **판정기를 그대로 믿지 않는다:** "지어냈다", "근거 없음" 판정은 도구 결과 원문으로 확인한 뒤 원인을 정한다. 판정기가 잘린 도구 결과만 보고 오판한 일이 있었다.

### 종료 조건

- **성공:** 코드를 바꾸지 않은 상태에서 12건 전체 실행 결과가 **연속 2회** 전체 통과율 80% 이상, 가드레일 통과율 100%이면 종료한다. LLM 답변은 매번 조금씩 달라지므로 한 번 통과로는 완성으로 보지 않는다. 실행 오류(호출 한도 등)가 섞인 실행은 세지 않는다.
- **중단:** 최대 10회 반복했거나, 3회 연속으로 통과율이 오르지 않으면 멈춘다. 남은 실패 케이스와 원인 분석을 사용자에게 보고하고 다음 방향을 묻는다.

### 실행 방법

```bash
# 공식: 12건 전체를 한 번 실행하고 채점한다 (15~25분)
uv run python -m evaluation.run_csv

# 디버깅용: 일부 문항만 (완성 판정에는 쓰지 않는다)
uv run python -m evaluation.run_csv --case P-01 --case G-01

# 보조: test_queries.json 24건. 반복 번호, 회귀, 루프 상태(continue/success/stop)를 자동으로 계산한다
uv run python -m evals.run --note "이번 반복 전에 고친 내용"
```

- 기본으로 한 문항씩 실행한다. 무료 검색(DuckDuckGo)은 짧은 시간에 많이 부르면 막히므로 `--workers`를 올리지 않는다. 검색 오류가 몰리면 몇 시간 뒤에 다시 돌린다.
- 실패 문항마다 사유(빠진 도구, 위반한 금지 항목과 근거)와 도구 오류가 출력된다. 도구 오류가 외부 장애라면 코드를 고치지 않는다.
- Bedrock 하루 토큰 한도에 걸리면 모든 문항이 "에이전트 실행 실패"로 나온다. 그 실행은 무효로 보고 LOOP_LOG.md에 그렇게 적는다.

## 지켜야 할 규약

### 문서

- 모든 .md 문서는 한국어로 작성한다. 코드, 명령어, 파일 경로, 식별자는 원문 그대로 둔다.

### 코드 스타일

- Python은 `ruff`로 린트와 포맷을 맞춘다. 변경 후 `uv run ruff check . && uv run ruff format .`를 통과해야 한다.
- 코드 주석, 도구 설명(docstring), 사용자에게 보이는 문구는 한국어로 쓴다. 변수, 함수 이름은 영어로 쓴다.
- 도구는 실패해도 예외를 던지지 않고 `{"error": ..., "source": ...}`를 돌려준다 (`backend/tools/common.py`의 `error()`).
- 목업 데이터를 돌려주는 도구는 결과에 `"mock": true`와 안내 문구를 넣는다.
- 도구를 추가하거나 바꾸면 SERVICE.md 3장 도구 표와 `backend/tools/__init__.py`의 `ALL_TOOLS`를 함께 고친다.

### Git

- 커밋 메시지는 한국어로, 무엇을 왜 바꿨는지 한 줄로 쓴다.
- 평가 결과(`evaluation/results/`, `evaluation/LOOP_LOG.md`, 보조 세트의 `evals/runs/iter_*.json`, `evals/LOOP_LOG.md`)는 완성 근거이므로 함께 커밋한다. 실행 오류로 무효가 된 결과 파일은 커밋하지 않는다.

### 테스트

- 에이전트 동작(프롬프트, 도구, 그래프, 출력 검증)을 바꾼 뒤에는 반드시 공식 세트 12건 전체를 다시 판정한다.
- "완성", "통과"라고 말할 때는 `evaluation/results/`의 최신 `summary_*.txt` 두 개(연속 2회)를 근거로 함께 제시한다.

### 금지 사항

- API 키, AWS 자격 증명을 코드나 커밋에 넣지 않는다. 비밀 값은 `.env`에만 둔다.
- 대화 기록이나 사용자 위치를 서버, 파일, 브라우저 저장소에 저장하지 않는다.
- 실제 API 장애를 숨기려고 도구에서 그럴듯한 가짜 값을 돌려주지 않는다. 목업 전환은 `.env`의 `MOCK_*` 설정으로만, 사용자 확인 후에 한다.
- 평가 루프 중에 `evaluation/test_queries.csv`나 `test_queries.json`을 고쳐 통과시키지 않는다.
- 평가 판정기로 Claude Haiku 계열을 쓰지 않는다 (위반을 놓친 기록이 있다).

---

<!-- ooo:START -->
<!-- ooo:VERSION:0.59.9 -->
# Ouroboros — 명세 우선 AI 개발

> AI에게 무엇을 만들라고 말하기 전에, 무엇을 만들어야 하는지부터 정의하라.
> 2,500년 전 소크라테스는 "너는 진정으로 무엇을 아는가?"라고 물었다.
> Ouroboros는 이 질문을 진화하는 AI 워크플로우 엔진으로 바꾼다.

AI 코딩의 실패는 대부분 출력이 아니라 입력에서 시작된다. Ouroboros는
**코드를 한 줄도 쓰기 전에 숨은 가정을 드러내는 방식**으로 이 문제를 해결한다.

1. **소크라테스식 명확화** — 모호도가 0.2 이하가 될 때까지 질문한다
2. **존재론적 정밀성** — 증상이 아니라 근본 문제를 해결한다
3. **진화 루프** — 매 평가 결과가 다음 명세를 더 낫게 만든다

```
인터뷰 → 시드 → 실행 → 평가
  ↑                     ↓
  └──── 진화 루프 ──────┘
```

## ooo 명령어

각 명령어는 필요할 때 해당 에이전트나 MCP를 불러온다. 자세한 내용은 각 스킬 파일에 있다.

| 명령어 | 불러오는 것 |
|--------|-------------|
| `ooo` | — |
| `ooo interview` | `ouroboros:socratic-interviewer` |
| `ooo seed` | `ouroboros:seed-architect` |
| `ooo run` | MCP 필요 |
| `ooo evolve` | MCP: `evolve_step` |
| `ooo evaluate` | `ouroboros:evaluator` |
| `ooo unstuck` | `ouroboros:{persona}` |
| `ooo status` | MCP: `session_status` |
| `ooo setup` | — |
| `ooo help` | — |

## 에이전트

필요할 때만 불러오며, 미리 로드하지 않는다.

**핵심**: socratic-interviewer, ontologist, seed-architect, evaluator,
advocate, contrarian, judge
**보조**: hacker, simplifier, researcher, architect
<!-- ooo:END -->
