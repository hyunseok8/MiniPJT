"""RAGAS 평가: 공식 평가 실행 결과(run_*.json)의 질문·답변·도구 결과로 RAGAS 지표 4개를 계산한다.

    uv run python -m src.ragas_eval                                      # 최신 run_*.json
    uv run python -m src.ragas_eval --run evaluation/results/run_20261112-145306.json

에이전트를 다시 실행하지 않는다. src/evaluate.py가 저장한 결과를 그대로 쓴다.

RunTrip의 RAG는 미리 만든 문서 저장소가 아니라 질문할 때마다 부르는 도구(웹 검색, 페이지 본문,
지도·날씨 API)다. 그래서 RAGAS 입력을 다음처럼 대응시킨다.
- user_input: 문항의 질문 (CSV input)
- response: 에이전트의 최종 답변
- retrieved_contexts: 그 문항에서 도구가 돌려준 결과. 오류 결과는 뺀다
- reference: evaluation/ragas_references.csv의 "답하려면 찾아야 하는 정보 항목" 목록.
  문항의 기대 특성(expected_traits)은 "확인 못 하면 '확인 불가'로 안내" 같은 답변 방식 지침이라
  문맥과 대조할 수 없어서(모두 0점이 된다), 정보 항목으로 따로 적었다

지표:
- faithfulness: 답변의 주장이 도구 결과로 뒷받침되는 비율 (RAGAS 프롬프트로 주장을 뽑고 15문장씩 나눠 판정)
- answer_relevancy: 답변이 질문에 맞는 정도 (답변에서 거꾸로 만든 질문과 원래 질문의 임베딩 유사도).
  RAGAS는 "찾지 못했다", "확인 불가" 같은 답을 회피성(noncommittal)으로 보고 0점을 준다
- context_precision: 가져온 도구 결과 중 답변에 실제로 쓸모 있는 것이 앞쪽에 있는 정도 (기준 답변 없이 답변 기준으로 판정)
- context_recall: 위 정보 항목 중 도구 결과가 실제로 담고 있는 비율 (필요한 정보를 얼마나 가져왔나)

도구를 부르지 않은 문항(되묻기, 가드레일 거절)은 가져온 문맥이 없어 RAGAS 지표가 정의되지 않으므로 뺀다.
판정 LLM은 에이전트와 같은 Bedrock Claude, 임베딩은 Bedrock Titan Text Embeddings V2다.
"""

import argparse
import asyncio
import csv
import json
import math
import sys
import types
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_aws import BedrockEmbeddings, ChatBedrockConverse

from src.config import ROOT_DIR, get_settings

RESULTS_DIR = ROOT_DIR / "evaluation" / "results"
REFERENCES_FILE = ROOT_DIR / "evaluation" / "ragas_references.csv"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
JUDGE_MAX_TOKENS = 16000
# faithfulness에서 한 번에 판정하는 주장 문장 수
NLI_BATCH = 15

# RAGAS 기본 지시에 덧붙이는 문장 (CLAUDE.md "RAGAS 평가" 참고)
# - context_precision: 비교 질문에서 한쪽 대회 자료를 "그것만으로 답이 안 된다"며 0으로 판정하는 일을 막는다
PRECISION_NOTE = (
    " A context counts as useful (verdict 1) if any part of it was used for any part of the"
    " answer. It does not need to support the whole answer by itself."
)
# - answer_relevancy: 한국어 답변에서 영어 질문을 만들면 원래 질문과의 임베딩 유사도가 언어 차이로 낮아진다
RELEVANCY_NOTE = " Write the question in the same language as the answer."
METRIC_NAMES = ["context_recall", "context_precision", "faithfulness", "answer_relevancy"]


def _patch_vertexai_imports() -> None:
    """ragas 0.4.3은 최신 langchain-community에서 빠진 Vertex AI 클래스를 import한다.
    RunTrip은 Vertex AI를 쓰지 않으므로, import만 통과하도록 빈 클래스를 넣는다."""
    name = "langchain_community.chat_models.vertexai"
    if name not in sys.modules:
        module = types.ModuleType(name)
        module.ChatVertexAI = type("ChatVertexAI", (), {})
        sys.modules[name] = module
    import langchain_community.llms as community_llms

    try:
        community_llms.VertexAI  # noqa: B018
    except Exception:
        community_llms.VertexAI = type("VertexAI", (), {})


def _context_text(content: str) -> str:
    """도구 결과(JSON 문자열)를 읽기 쉬운 텍스트로 바꾼다. \\uXXXX 이스케이프를 풀어 토큰을 줄인다."""
    try:
        return json.dumps(json.loads(content), ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return content


def _is_error(content: str) -> bool:
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(payload, dict) and "error" in payload


def load_references(path: Path = REFERENCES_FILE) -> dict[str, list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {
            row["id"]: [t.strip() for t in row["reference"].split(";") if t.strip()]
            for row in csv.DictReader(f)
        }


def build_samples(run: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    references = load_references()
    samples, skipped = [], []
    for r in run["results"]:
        contexts = [
            f"[{t['tool']}] {_context_text(t['content'])}"
            for t in r.get("tool_results", [])
            if not _is_error(t["content"])
        ]
        if (
            r.get("status") != "ok"
            or not r.get("answer")
            or not contexts
            or r["id"] not in references
        ):
            skipped.append(r["id"])
            continue
        samples.append(
            {
                "id": r["id"],
                "category": r["category"],
                "user_input": r["input"],
                "response": r["answer"],
                "retrieved_contexts": contexts,
                "reference": "\n".join(references[r["id"]]),
            }
        )
    return samples, skipped


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None and not math.isnan(v)]
    return round(sum(clean) / len(clean), 3) if clean else None


def _clean(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return round(float(value), 3)


async def _faithfulness_one(metric: Any, llm: Any, sample: dict[str, Any]) -> dict[str, Any]:
    """RAGAS Faithfulness와 같은 방식: 답변을 주장 문장으로 나누고, 각 문장이 문맥으로 뒷받침되는지 판정한다.
    점수 = 뒷받침되는 문장 수 / 전체 문장 수.
    RAGAS는 모든 문장을 한 번에 판정해, 긴 비교표 답변(70~80문장)은 출력이 길어져 시간 안에 끝나지 않았다.
    같은 RAGAS 프롬프트로 NLI_BATCH개씩 나눠 판정한다."""
    from ragas.metrics._faithfulness import NLIStatementInput, StatementGeneratorInput

    generated = await metric.statement_generator_prompt.generate(
        data=StatementGeneratorInput(question=sample["user_input"], answer=sample["response"]),
        llm=llm,
    )
    statements = [s for s in generated.statements if s.strip()]
    if not statements:
        return {"score": None, "statements": 0, "supported": 0}
    context = "\n".join(sample["retrieved_contexts"])
    supported = 0
    for i in range(0, len(statements), NLI_BATCH):
        batch = statements[i : i + NLI_BATCH]
        verdicts = await metric.nli_statements_prompt.generate(
            data=NLIStatementInput(context=context, statements=batch), llm=llm
        )
        supported += sum(1 for v in verdicts.statements if v.verdict)
    return {
        "score": round(supported / len(statements), 3),
        "statements": len(statements),
        "supported": supported,
    }


async def _faithfulness_all(
    metric: Any, llm: Any, samples: list[dict[str, Any]], workers: int
) -> dict[str, dict[str, Any]]:
    gate = asyncio.Semaphore(workers)

    async def run(sample: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        async with gate:
            for attempt in range(3):
                try:
                    result = await _faithfulness_one(metric, llm, sample)
                    print(
                        f"  {sample['id']}: {result['supported']}/{result['statements']}",
                        flush=True,
                    )
                    return sample["id"], result
                except Exception as exc:  # 호출 한도, 형식 오류 등
                    print(
                        f"  {sample['id']}: 실패 {attempt + 1}회 ({type(exc).__name__})", flush=True
                    )
            return sample["id"], {"score": None, "statements": 0, "supported": 0}

    return dict(await asyncio.gather(*(run(s) for s in samples)))


def main() -> int:
    parser = argparse.ArgumentParser(description="RAGAS 지표 계산")
    parser.add_argument("--run", type=Path, help="src/evaluate.py가 만든 run_*.json (기본: 최신)")
    parser.add_argument("--case", action="append", help="일부 문항만 (디버깅용)")
    parser.add_argument(
        "--workers", type=int, default=4, help="동시 판정 수 (Bedrock 한도 때문에 작게)"
    )
    args = parser.parse_args()

    run_path = (args.run or max(RESULTS_DIR.glob("run_*.json"))).resolve()
    run = json.loads(run_path.read_text(encoding="utf-8"))
    samples, skipped = build_samples(run)
    if args.case:
        samples = [s for s in samples if s["id"] in args.case]
    print(f"입력: {run_path.relative_to(ROOT_DIR)}")
    print(f"측정 문항 {len(samples)}개: {', '.join(s['id'] for s in samples)}")
    print(f"제외(도구 결과 또는 정보 항목 없음): {', '.join(skipped) or '없음'}\n")

    warnings.filterwarnings("ignore", category=DeprecationWarning)
    _patch_vertexai_imports()
    from ragas import EvaluationDataset, RunConfig, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (
        Faithfulness,
        LLMContextPrecisionWithoutReference,
        LLMContextRecall,
        ResponseRelevancy,
    )

    settings = get_settings()
    # 긴 비교표 답변은 판정 출력도 길다. 기본 출력 길이에서 잘려 형식 오류가 나지 않게 늘린다
    judge_llm = ChatBedrockConverse(
        model=settings.judge_model_id or settings.bedrock_model_id,
        region_name=settings.aws_region,
        temperature=0.0,
        max_tokens=JUDGE_MAX_TOKENS,
    )
    llm = LangchainLLMWrapper(judge_llm)
    embeddings = LangchainEmbeddingsWrapper(
        BedrockEmbeddings(model_id=EMBEDDING_MODEL_ID, region_name=settings.aws_region)
    )

    precision = LLMContextPrecisionWithoutReference(name="context_precision")
    precision.context_precision_prompt.instruction += PRECISION_NOTE
    relevancy = ResponseRelevancy(name="answer_relevancy")
    relevancy.question_generation.instruction += RELEVANCY_NOTE
    metrics = [LLMContextRecall(name="context_recall"), precision, relevancy]

    dataset = EvaluationDataset.from_list(
        [{k: v for k, v in s.items() if k not in ("id", "category")} for s in samples]
    )
    result = evaluate(
        dataset,
        metrics=metrics,
        llm=llm,
        embeddings=embeddings,
        run_config=RunConfig(max_workers=args.workers, timeout=600, max_retries=6, max_wait=90),
        show_progress=True,
        raise_exceptions=False,
    )
    rows = result.to_pandas().to_dict(orient="records")

    print("\nfaithfulness 계산 중 (주장 문장을 나눠 판정)...")
    faithfulness = Faithfulness(llm=llm)
    faith = asyncio.run(_faithfulness_all(faithfulness, llm, samples, args.workers))

    per_case = []
    for s, row in zip(samples, rows, strict=True):
        scores = {m: _clean(row.get(m)) for m in METRIC_NAMES if m != "faithfulness"}
        scores["faithfulness"] = faith[s["id"]]["score"]
        per_case.append(
            {
                "id": s["id"],
                "category": s["category"],
                "contexts": len(s["retrieved_contexts"]),
                **{m: scores[m] for m in METRIC_NAMES},
                "statements": faith[s["id"]]["statements"],
                "supported": faith[s["id"]]["supported"],
            }
        )
    summary = {m: _mean([c[m] for c in per_case]) for m in METRIC_NAMES}

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = RESULTS_DIR / f"ragas_{stamp}.json"
    out_path.write_text(
        json.dumps(
            {
                "timestamp": stamp,
                "source_run": str(run_path.relative_to(ROOT_DIR)),
                "judge_model": settings.judge_model_id or settings.bedrock_model_id,
                "embedding_model": EMBEDDING_MODEL_ID,
                "measured": [s["id"] for s in samples],
                "skipped_no_context": skipped,
                "summary": summary,
                "per_case": per_case,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n[RAGAS 결과] 평균 (측정 문항 기준, 계산 실패한 칸은 평균에서 뺀다)")
    for m in METRIC_NAMES:
        print(f"  {m:18s} {summary[m]}")
    print("\n[문항별]")
    print("  id     ctx  " + "  ".join(f"{m[:17]:>17s}" for m in METRIC_NAMES))
    for c in per_case:
        cells = "  ".join(f"{'-' if c[m] is None else c[m]:>17}" for m in METRIC_NAMES)
        print(f"  {c['id']:6s} {c['contexts']:3d}  {cells}")
    print(f"\n결과 파일: {out_path.relative_to(ROOT_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
