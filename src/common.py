"""여러 도구가 함께 쓰는 HTTP 클라이언트와 도구 오류 형식."""

from typing import Any

import httpx

from src.config import get_settings

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
