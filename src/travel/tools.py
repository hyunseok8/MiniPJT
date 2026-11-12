"""항공권, 숙소 도구. 지금은 항상 목업 데이터를 돌려준다 (SERVICE.md 3장)."""

import hashlib
from typing import Any

from langchain_core.tools import tool

ORIGIN_AIRPORT = "ICN"
MOCK_NOTICE = (
    "목업 데이터다. 실제 가격이 아니며, 답변에 반드시 '목업(실제 가격 아님)'이라고 표시한다."
)


def stable_int(*parts: object, low: int, high: int) -> int:
    """같은 입력이면 항상 같은 값을 돌려주는 의사 난수. 목업 데이터에 쓴다."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return low + int(digest[:8], 16) % (high - low + 1)


@tool
def search_flights(destination_city: str, depart_date: str, return_date: str) -> dict[str, Any]:
    """인천(ICN) 출발 왕복 항공권의 대략적인 가격을 조회한다. 출발 공항은 항상 인천이다.

    Args:
        destination_city: 도착 도시 (예: "Osaka", "Berlin")
        depart_date: 출발일 (YYYY-MM-DD)
        return_date: 귀국일 (YYYY-MM-DD)
    """
    base = stable_int(destination_city.lower(), low=250_000, high=1_600_000)
    offers = []
    for i, airline in enumerate(["Mock Air", "Sample Airlines", "Demo Jet"]):
        price = base + stable_int(destination_city, depart_date, airline, low=0, high=250_000)
        offers.append(
            {
                "airline": airline,
                "price_krw": round(price, -3),
                "stops": i % 2,
            }
        )
    return {
        "mock": True,
        "notice": MOCK_NOTICE,
        "origin": ORIGIN_AIRPORT,
        "destination_city": destination_city,
        "depart_date": depart_date,
        "return_date": return_date,
        "offers": sorted(offers, key=lambda o: o["price_krw"]),
    }


@tool
def search_hotels(near: str, check_in: str, check_out: str) -> dict[str, Any]:
    """지정한 장소(대회장, 역, 주소) 근처 숙소를 조회한다.

    Args:
        near: 기준 장소 (예: "Osaka Castle Park", "Kawaramachi Station, Kyoto")
        check_in: 체크인 날짜 (YYYY-MM-DD)
        check_out: 체크아웃 날짜 (YYYY-MM-DD)
    """
    hotels = []
    for name in ["Mock Hotel Central", "Sample Inn Station", "Demo Stay Riverside"]:
        hotels.append(
            {
                "name": name,
                "distance_km": stable_int(near, name, low=2, high=20) / 10,
                "price_per_night_krw": round(
                    stable_int(near, name, check_in, low=60_000, high=320_000), -3
                ),
                "rating": stable_int(near, name, "rating", low=35, high=49) / 10,
            }
        )
    return {
        "mock": True,
        "notice": MOCK_NOTICE,
        "near": near,
        "check_in": check_in,
        "check_out": check_out,
        "hotels": sorted(hotels, key=lambda h: h["distance_km"]),
    }
