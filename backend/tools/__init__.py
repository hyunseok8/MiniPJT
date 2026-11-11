"""에이전트가 쓰는 도구 목록. SERVICE.md 3장의 도구 표와 일치해야 한다.
LLM(Bedrock)을 뺀 모든 도구는 무료이며 API 키가 필요 없다.
"""

from backend.tools.geo import geocode, get_elevation
from backend.tools.routes import find_running_routes, search_popular_routes
from backend.tools.search import fetch_page, search_races, search_web
from backend.tools.travel import search_flights, search_hotels
from backend.tools.weather import get_weather

ALL_TOOLS = [
    search_web,
    fetch_page,
    search_races,
    search_flights,
    search_hotels,
    geocode,
    find_running_routes,
    search_popular_routes,
    get_elevation,
    get_weather,
]
