"""검색 기반 RAG 파이프라인: 검색(Retrieve) → 본문 읽기(Read) → 답변 근거로 쓰기.

RunTrip은 미리 모은 문서 대신 웹에서 그때그때 자료를 찾는다.
1. ddg_search / search_web: DuckDuckGo로 후보 문서(제목, 주소, 요약)를 찾는다 (무료, 키 불필요).
2. fetch_page: 후보 중 공식 사이트 본문을 받아 텍스트만 뽑는다 (최대 MAX_PAGE_CHARS자).
3. 에이전트는 이 결과만 근거로 답하고, 답변의 링크는 src/verify.py가 이 결과와 대조해 검사한다.
search_races(대회), search_popular_routes(코스)도 내부에서 ddg_search를 쓴다 (src/tools.py)."""

import re
import threading
import time
from typing import Any

from bs4 import BeautifulSoup
from ddgs import DDGS
from langchain_core.tools import tool

from src.common import error, http_client
from src.config import get_settings

MAX_PAGE_CHARS = 8000

# 검색 엔진 시도 순서: (검색원, 제한 시간 초). 실패하면 기다리지 않고 바로 다음 검색원으로 넘어간다.
# 2026-11-12 실측: yahoo는 대부분 1초 안팎이지만 일본어 검색어 등에서 결과가 없을 때가 있고,
# bing은 1~11초로 느리지만 그런 검색어를 찾았다. auto는 막힌 검색원들을 차례로 시도하느라 7~23초 걸렸다.
# 예전 순서(auto → 2초 대기 → bing → 5초 대기 → auto)는 검색 1회가 20초를 넘기는 일이 잦았다.
SEARCH_ATTEMPTS = [("yahoo", 5), ("bing", 8), ("auto", 8)]
# 여러 케이스를 동시에 돌려도 검색 호출(검색어 하나) 사이에 최소 이 간격(초)을 둔다
MIN_SEARCH_INTERVAL = 1.0
_search_lock = threading.Lock()
_last_search = [0.0]


def _throttle() -> None:
    with _search_lock:
        gap = _last_search[0] + MIN_SEARCH_INTERVAL - time.monotonic()
        if gap > 0:
            time.sleep(gap)
        _last_search[0] = time.monotonic()


# 일부 사이트는 브라우저가 아닌 요청을 막으므로 일반적인 브라우저 형식의 User-Agent를 쓴다
PAGE_USER_AGENT = "Mozilla/5.0 (compatible; RunTripAgent/0.1)"


def ddg_search(query: str, max_results: int = 5) -> dict[str, Any]:
    if get_settings().mock_search:
        return {
            "mock": True,
            "notice": "목업 검색 결과다. 실제 정보가 아니므로 답변에 사실처럼 쓰지 말고 '확인 불가'로 표시한다.",
            "query": query,
            "results": [],
        }
    raw = None
    failures = []
    _throttle()
    for backend, timeout_s in SEARCH_ATTEMPTS:
        try:
            raw = DDGS(timeout=timeout_s).text(query, max_results=max_results, backend=backend)
            break
        except Exception as exc:  # 호출 한도, 시간 초과, "No results found" 등: 다음 검색원으로
            failures.append(f"{backend}: {exc}")
    if raw is None:  # 끝내 실패한 외부 장애는 그대로 알린다
        return error("duckduckgo", "검색 실패: " + " / ".join(failures))
    return {
        "query": query,
        "results": [
            {"title": r.get("title"), "url": r.get("href"), "content": r.get("body")} for r in raw
        ],
    }


def _page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "svg", "form"]):
        tag.decompose()
    text = soup.get_text("\n")
    return re.sub(r"\n\s*\n+", "\n", text).strip()


@tool
def search_web(query: str, max_results: int = 5) -> dict[str, Any]:
    """웹을 검색한다. 마라톤 대회 정보, 러닝 커뮤니티 글, 현지 러닝 크루 모임,
    대회 접수 방법을 찾을 때 쓴다. 검색어는 현지 언어나 영어로 쓰면 결과가 더 좋다.
    결과의 url은 답변의 출처 링크로 쓴다. 요약(content)만으로 부족하면 fetch_page로 본문을 읽는다.

    Args:
        query: 검색어
        max_results: 최대 결과 수 (1~10)
    """
    return ddg_search(query, max(1, min(max_results, 10)))


@tool
def fetch_page(url: str) -> dict[str, Any]:
    """웹 페이지 본문을 읽는다. 대회 공식 사이트에서 날짜, 접수 마감일, 코스 정보를 확인하거나
    커뮤니티 글의 내용을 확인할 때 쓴다. 본문은 앞부분 8000자까지만 돌려준다.

    Args:
        url: 읽을 페이지 주소
    """
    if get_settings().mock_search:
        return {
            "mock": True,
            "url": url,
            "content": "",
            "notice": "목업 모드라 본문을 읽지 않았다.",
        }
    try:
        with http_client() as client:
            resp = client.get(url, headers={"User-Agent": PAGE_USER_AGENT})
            resp.raise_for_status()
    except Exception as exc:
        return error("web", f"페이지 읽기 실패: {exc}")
    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type:
        return error("web", f"읽을 수 없는 형식이다 ({content_type}): {url}")
    text = _page_text(resp.text)
    return {
        "url": str(resp.url),
        "content": text[:MAX_PAGE_CHARS],
        "truncated": len(text) > MAX_PAGE_CHARS,
    }
