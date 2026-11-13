"""구조화 출력: 검증을 마친 최종 답변(마크다운)을 Pydantic 모델(TripAnswer)로 바꾼다.

LCEL 체인 `ChatPromptTemplate | llm.with_structured_output(TripAnswer)`을 쓴다.
- 화면에는 마크다운 답변을 그대로 보여 주고, 같은 내용을 dict로도 돌려준다
  (`/api/chat`의 `structured`, `/api/chat/stream`의 마지막 `structured` 이벤트, 평가 결과 JSON).
- 답변에 있는 내용만 옮긴다. 체인이 답변에 없는 링크를 만들면 코드로 지운다(정책 1).
- 구조화에 실패해도 답변은 그대로 나간다. 이때 structured는 None이다.
"""

from typing import Literal

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.llm import get_llm
from src.verify import extract_urls, normalize


class Source(BaseModel):
    title: str = Field(description="링크 이름 (답변에 쓰인 그대로)")
    url: str = Field(description="답변에 나온 주소 그대로")


class Item(BaseModel):
    kind: Literal["race", "route", "flight", "hotel", "weather", "crew", "other"] = Field(
        description="race=대회, route=러닝 코스, flight=항공권, hotel=숙소, weather=날씨, crew=러닝 크루·모임"
    )
    name: str = Field(description="대회, 코스, 항공사, 숙소, 모임의 이름")
    date: str | None = Field(None, description="날짜나 기간 (답변에 쓰인 그대로, 없으면 null)")
    distance: str | None = Field(None, description="종목 거리 또는 코스 거리 (없으면 null)")
    elevation: str | None = Field(None, description="고저차 (없으면 null)")
    price: str | None = Field(None, description="가격 (없으면 null)")
    detail: str | None = Field(None, description="그 밖의 핵심 특징 한 줄 (마감일, 코스 특성 등)")
    source_url: str | None = Field(None, description="이 항목의 출처 주소 (답변에 있을 때만)")
    mock: bool = Field(False, description="답변에서 목업(실제 아님)으로 표시된 항목이면 true")


class TripAnswer(BaseModel):
    answer_type: Literal[
        "race_search",
        "route_search",
        "comparison",
        "clarification",
        "refusal",
        "not_found",
        "other",
    ] = Field(
        description=(
            "race_search=대회 찾기, route_search=러닝 코스 찾기, comparison=대회 비교, "
            "clarification=조건이 모자라 되물음, refusal=정책상 거절, not_found=찾지 못함"
        )
    )
    conclusion: str = Field(description="답변 첫 문장의 결론")
    period: str | None = Field(None, description="답변이 밝힌 날짜 범위 해석 (없으면 null)")
    items: list[Item] = Field(
        default_factory=list, description="답변에 나온 대회, 코스, 항공권, 숙소 등"
    )
    sources: list[Source] = Field(default_factory=list, description="답변에 나온 출처 링크")
    follow_up_question: str | None = Field(
        None, description="답변이 사용자에게 되물은 질문 (없으면 null)"
    )
    uses_mock_data: bool = Field(False, description="답변에 목업 데이터가 있으면 true")


STRUCTURE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "너는 러닝·여행 도우미의 답변을 정해진 구조로 옮기는 변환기다.\n"
            "- 아래 [답변]에 있는 내용만 옮긴다. 새 정보, 추측, 다른 링크를 덧붙이지 않는다.\n"
            "- 값이 답변에 없으면 null로 둔다. 답변에 '확인 불가'라고 적힌 값은 '확인 불가'로 옮긴다.\n"
            "- 날짜, 가격, 거리는 답변에 쓰인 표기 그대로 옮긴다.\n"
            "- 링크 주소는 글자 하나 바꾸지 않고 그대로 옮긴다.",
        ),
        ("human", "[질문]\n{question}\n\n[답변]\n{answer}"),
    ]
)


def build_chain():
    """LCEL 체인: 프롬프트 → 구조화 출력 LLM → TripAnswer."""
    return STRUCTURE_PROMPT | get_llm().with_structured_output(TripAnswer)


def _drop_unknown_links(result: TripAnswer, answer: str) -> TripAnswer:
    """답변에 없는 주소는 지운다. 구조화 체인이 링크를 지어내도 결과에 남지 않게 한다."""
    allowed = {normalize(u) for u in extract_urls(answer)}
    result.sources = [s for s in result.sources if normalize(s.url) in allowed]
    for item in result.items:
        if item.source_url and normalize(item.source_url) not in allowed:
            item.source_url = None
    return result


def structure_answer(question: str, answer: str) -> dict | None:
    """최종 답변을 dict로 바꾼다. 실패하면 None (답변 전달에는 영향을 주지 않는다)."""
    if not answer.strip():
        return None
    try:
        result = build_chain().invoke({"question": question, "answer": answer})
    except Exception:  # 호출 한도, 형식 오류 등
        return None
    return _drop_unknown_links(result, answer).model_dump()
