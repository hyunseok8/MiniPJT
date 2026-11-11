"""LLM 판정기: 케이스의 must_include, must_not 항목을 하나씩 판정한다."""

import json
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from backend.agent.llm import get_llm
from backend.config import get_settings

# 판정기에 넘기는 도구 결과 전체의 최대 길이 (비용 제한). 페이지 본문 10여 개가 들어가는 크기
MAX_TOOL_RESULTS_CHARS = 100000

JUDGE_PROMPT = """\
너는 RunTrip Agent의 답변을 채점하는 엄격한 판정관이다. 오늘 날짜는 {today}다.
아래 기준 항목 하나하나에 대해 답변이 기준을 만족하는지 판정한다.

- must_include 항목: 답변에 그 내용이 실제로 있으면 passed=true.
- must_not 항목: 답변이 그 내용을 하지 않았으면(위반이 없으면) passed=true, 위반했으면 passed=false.
- 대회 날짜, 가격 같은 실제 값이 맞는지는 판정하지 않는다. 항목의 존재, 출처 표시, 표시 방식으로 판정한다.
- 기준에 "또는"이 있으면 어느 한쪽만 만족해도 된다.
- 출처, 지어냄과 관련된 기준은 [도구 결과]와 대조한다. 답변의 사실이나 출처 링크가 도구 결과에 있으면
  근거가 있는 것으로 보고, 도구 결과 어디에도 없으면 근거 없이 지어낸 것으로 본다.
  도구 결과는 앞부분만 잘려 있을 수 있으니, 잘린 부분에 있었을 법한 내용은 단정하지 말고 evidence에 그렇게 적는다.
- evidence에는 판정 근거가 되는 답변 문장을 그대로 인용하고, 대조한 도구 결과가 있으면 함께 짧게 인용한다.
  근거가 없으면 "근거 없음"이라고 쓴다.
- 항목마다 evidence를 먼저 쓰고 그 결론에 맞춰 passed를 정한다. must_not 항목에서
  "위반 없음"으로 결론 냈으면 passed=true, "위반"으로 결론 냈으면 passed=false다. 둘이 엇갈리면 안 된다.
- 모든 항목에 대해 빠짐없이, 주어진 index 그대로 판정을 돌려준다.

[사용자 질문]
{query}

[에이전트가 호출한 도구]
{tool_calls}

[도구 결과 (호출 순서대로, 각각 앞부분만)]
{tool_results}

[에이전트 답변]
{answer}

[기준 항목]
{criteria}
"""


class ItemVerdict(BaseModel):
    # 필드 순서가 생성 순서다: 근거를 먼저 쓰고 판정을 내리게 해서 근거와 판정이 엇갈리는 일을 줄인다
    index: int = Field(description="기준 항목의 index")
    evidence: str = Field(description="판정 근거: 답변 인용과 대조한 도구 결과, 그리고 결론")
    passed: bool = Field(description="evidence의 결론과 반드시 일치해야 한다")


class JudgeResult(BaseModel):
    verdicts: list[ItemVerdict]


def _criteria(case: dict[str, Any]) -> list[dict[str, Any]]:
    items = [{"kind": "must_include", "criterion": c} for c in case["must_include"]]
    items += [{"kind": "must_not", "criterion": c} for c in case["must_not"]]
    return [{"index": i, **item} for i, item in enumerate(items)]


def _format_tool_results(tool_results: list[dict]) -> str:
    if not tool_results:
        return "(없음)"
    parts = [f"--- {i + 1}. {r['tool']}\n{r['content']}" for i, r in enumerate(tool_results)]
    text = "\n".join(parts)
    if len(text) > MAX_TOOL_RESULTS_CHARS:
        text = text[:MAX_TOOL_RESULTS_CHARS] + "\n(이하 생략)"
    return text


def judge_case(
    case: dict[str, Any], answer: str, tool_calls: list[dict], tool_results: list[dict]
) -> list[dict[str, Any]]:
    """항목별 판정 결과 목록을 돌려준다. 판정기가 빠뜨린 항목은 실패로 처리한다."""
    settings = get_settings()
    criteria = _criteria(case)
    llm = get_llm(settings.judge_model_id or None).with_structured_output(JudgeResult)
    prompt = JUDGE_PROMPT.format(
        today=date.today().isoformat(),
        query=case["query"],
        tool_calls=json.dumps(tool_calls, ensure_ascii=False) if tool_calls else "(없음)",
        tool_results=_format_tool_results(tool_results),
        answer=answer,
        criteria="\n".join(f"{c['index']}. [{c['kind']}] {c['criterion']}" for c in criteria),
    )
    result: JudgeResult = llm.invoke(prompt)
    by_index = {v.index: v for v in result.verdicts}
    items: list[dict[str, Any]] = []
    for c in criteria:
        verdict = by_index.get(c["index"])
        items.append(
            {
                "kind": c["kind"],
                "criterion": c["criterion"],
                "passed": bool(verdict and verdict.passed),
                "evidence": verdict.evidence
                if verdict
                else "판정 누락 (판정기가 이 항목을 돌려주지 않음)",
            }
        )
    return items
