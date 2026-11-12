"""마라톤 대회 검색 (기능 A). 검색 엔진은 search 도메인의 ddg_search를 쓴다."""

from typing import Any, Literal

from langchain_core.tools import tool

from src.search.tools import ddg_search

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]  # fmt: skip


def _period_words(start_date: str, end_date: str) -> str:
    """검색어용 기간 표현. "2027-03 2027-05" 같은 숫자 형식은 검색 결과가 거의 없어서
    "March to May 2027"처럼 달 이름으로 쓴다 (2026-11-12 실측)."""
    try:
        y1, m1 = int(start_date[:4]), int(start_date[5:7])
        y2, m2 = int(end_date[:4]), int(end_date[5:7])
    except ValueError:
        return f"{start_date} {end_date}"
    first, last = MONTH_NAMES[m1 - 1], MONTH_NAMES[m2 - 1]
    if (y1, m1) == (y2, m2):
        return f"{first} {y1}"
    if y1 == y2:
        return f"{first} to {last} {y1}"
    return f"{first} {y1} to {last} {y2}"


@tool
def search_races(
    region: str,
    start_date: str,
    end_date: str,
    distance: Literal["full", "half", "10k", "any"] = "any",
) -> dict[str, Any]:
    """기간과 지역으로 해외 마라톤 대회를 찾는다. 결과는 검색 결과 목록이므로,
    대회 날짜와 접수 마감일은 반드시 fetch_page로 공식 사이트를 열어 확인한 뒤 답변에 쓴다.

    Args:
        region: 도시, 국가, 또는 대륙 (예: "Osaka", "Japan", "Europe")
        start_date: 검색 기간 시작일 (YYYY-MM-DD)
        end_date: 검색 기간 종료일 (YYYY-MM-DD)
        distance: 종목. full=풀코스, half=하프, 10k=10km, any=전체
    """
    distance_words = {
        "full": "marathon",
        "half": "half marathon",
        "10k": "10k run",
        "any": "marathon race",
    }[distance]
    query = f"{region} {distance_words} {_period_words(start_date, end_date)}"
    result = ddg_search(query, max_results=8)
    result["filters"] = {
        "region": region,
        "start_date": start_date,
        "end_date": end_date,
        "distance": distance,
    }
    return result
