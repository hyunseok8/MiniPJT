"""LLM 판정기: 케이스의 must_include, must_not 항목을 하나씩 판정한다."""

import json
from datetime import date
from typing import Any

from src.evaluation.prompt import JUDGE_PROMPT
from src.evaluation.schemas import JudgeResult
from src.shared.config import get_settings
from src.shared.llm import get_llm

# 판정기에 넘기는 도구 결과 전체의 최대 길이 (비용 제한). 페이지 본문 10여 개가 들어가는 크기
MAX_TOOL_RESULTS_CHARS = 100000


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
