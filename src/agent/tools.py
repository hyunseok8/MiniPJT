"""에이전트가 쓰는 도구 목록(등록부). SERVICE.md 3장의 도구 표와 일치해야 한다.
도구 구현은 각 도메인 폴더의 tools.py에 있다. LLM(Bedrock)을 뺀 모든 도구는 무료이며 API 키가 필요 없다.
"""

from src.places.tools import geocode, get_elevation
from src.races.tools import search_races
from src.routes.tools import find_running_routes, search_popular_routes
from src.search.tools import fetch_page, search_web
from src.travel.tools import search_flights, search_hotels
from src.weather.tools import get_weather

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
