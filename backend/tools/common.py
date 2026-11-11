"""도구 공통 유틸리티."""

import hashlib
import math
from typing import Any

import httpx

from backend.config import get_settings

# 외부 공개 API(Nominatim 등)는 식별 가능한 User-Agent를 요구한다. 개인정보는 넣지 않는다.
USER_AGENT = "RunTripAgent/0.1 (educational project)"


def http_client() -> httpx.Client:
    return httpx.Client(
        timeout=get_settings().http_timeout,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def error(source: str, message: str) -> dict[str, Any]:
    """도구 실패 결과. 에이전트는 이 결과를 받으면 해당 정보를 '확인 불가'로 표시해야 한다."""
    return {"error": message, "source": source}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 좌표 사이의 거리(km)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def stable_int(*parts: object, low: int, high: int) -> int:
    """같은 입력이면 항상 같은 값을 돌려주는 의사 난수. 목업 데이터에 쓴다."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return low + int(digest[:8], 16) % (high - low + 1)
