"""LLM 판정기의 출력 형식."""

from pydantic import BaseModel, Field


class ItemVerdict(BaseModel):
    # 필드 순서가 생성 순서다: 근거를 먼저 쓰고 판정을 내리게 해서 근거와 판정이 엇갈리는 일을 줄인다
    index: int = Field(description="기준 항목의 index")
    evidence: str = Field(description="판정 근거: 답변 인용과 대조한 도구 결과, 그리고 결론")
    passed: bool = Field(description="evidence의 결론과 반드시 일치해야 한다")


class JudgeResult(BaseModel):
    verdicts: list[ItemVerdict]
