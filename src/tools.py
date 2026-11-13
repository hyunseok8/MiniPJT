"""도메인 도구. 에이전트(LLM)가 부르는 도구 10개와 그 구현.

SERVICE.md 3장의 도구 표와 일치해야 한다. LLM(Bedrock)을 뺀 모든 도구는 무료이고 키가 필요 없다.
- 대회 찾기(기능 A): search_races
- 항공권, 숙소(목업, 기준값은 data/mock_travel.json): search_flights, search_hotels
- 장소, 고도: geocode, get_elevation (Nominatim, Open-Meteo)
- 러닝 코스(기능 B): find_running_routes (OpenStreetMap Overpass), search_popular_routes
- 날씨: get_weather (Open-Meteo)
- 웹 검색, 페이지 읽기: search_web, fetch_page (src/retriever.py)
도구는 실패해도 예외를 던지지 않고 src/common.py의 error() 형식을 돌려준다.
"""

import calendar
import hashlib
import json
import math
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date
from statistics import mean
from typing import Any, Literal

from langchain_core.tools import tool

from src.common import error, http_client
from src.config import ROOT_DIR
from src.retriever import ddg_search, fetch_page, search_web

# 목업 항공권·숙소의 기준값 (항공사, 숙소 이름, 가격 범위)
MOCK_TRAVEL = json.loads((ROOT_DIR / "data" / "mock_travel.json").read_text(encoding="utf-8"))


# ======================================================================
# 기능 A: 마라톤 대회 찾기
# ======================================================================

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


# ======================================================================
# 항공권, 숙소 (목업)
# ======================================================================

ORIGIN_AIRPORT = MOCK_TRAVEL["origin_airport"]
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
    flights = MOCK_TRAVEL["flights"]
    low, high = flights["base_price_krw"]
    base = stable_int(destination_city.lower(), low=low, high=high)
    spread_low, spread_high = flights["date_spread_krw"]
    offers = []
    for i, airline in enumerate(flights["airlines"]):
        price = base + stable_int(
            destination_city, depart_date, airline, low=spread_low, high=spread_high
        )
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
    spec = MOCK_TRAVEL["hotels"]
    dist_low, dist_high = spec["distance_km_x10"]
    price_low, price_high = spec["price_per_night_krw"]
    rating_low, rating_high = spec["rating_x10"]
    hotels = []
    for name in spec["names"]:
        hotels.append(
            {
                "name": name,
                "distance_km": stable_int(near, name, low=dist_low, high=dist_high) / 10,
                "price_per_night_krw": round(
                    stable_int(near, name, check_in, low=price_low, high=price_high), -3
                ),
                "rating": stable_int(near, name, "rating", low=rating_low, high=rating_high) / 10,
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


# ======================================================================
# 장소 좌표, 고도 (OpenStreetMap Nominatim, Open-Meteo)
# ======================================================================

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"


@tool
def geocode(place: str) -> dict[str, Any]:
    """장소명이나 주소를 좌표(위도, 경도)로 바꾼다. 숙소, 역, 대회장 위치를 찾을 때 쓴다.
    도시 이름까지 함께 넣어야 정확하다 (예: "Kawaramachi Station, Kyoto").

    Args:
        place: 장소명 또는 주소
    """
    try:
        with http_client() as client:
            resp = client.get(NOMINATIM_URL, params={"q": place, "format": "json", "limit": 1})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return error("nominatim", f"지오코딩 실패: {exc}")
    if not data:
        return error("nominatim", f"장소를 찾지 못했다: {place}")
    top = data[0]
    return {
        "place": place,
        "lat": float(top["lat"]),
        "lon": float(top["lon"]),
        "display_name": top.get("display_name"),
        "source": "OpenStreetMap Nominatim",
    }


@tool
def get_elevation(points: list[list[float]]) -> dict[str, Any]:
    """좌표 목록의 고도를 조회하고 고저차를 계산한다. 코스의 경로 좌표를 순서대로 넣으면
    누적 상승/하강 고도를 돌려준다.

    Args:
        points: [[위도, 경도], ...] 형식의 좌표 목록 (최대 100개)
    """
    points = points[:100]
    if not points:
        return error("open-meteo", "좌표가 비어 있다.")
    try:
        with http_client() as client:
            resp = client.get(
                ELEVATION_URL,
                params={
                    "latitude": ",".join(str(p[0]) for p in points),
                    "longitude": ",".join(str(p[1]) for p in points),
                },
            )
            resp.raise_for_status()
            elevations = resp.json().get("elevation", [])
    except Exception as exc:
        return error("open-meteo", f"고도 조회 실패: {exc}")
    gain = sum(max(b - a, 0) for a, b in zip(elevations, elevations[1:], strict=False))
    loss = sum(max(a - b, 0) for a, b in zip(elevations, elevations[1:], strict=False))
    return {
        "elevations_m": elevations,
        "total_gain_m": round(gain, 1),
        "total_loss_m": round(loss, 1),
        "range_m": round(max(elevations) - min(elevations), 1) if elevations else None,
        "source": "Open-Meteo Elevation API",
    }


# ======================================================================
# 기능 B: 러닝 코스 (OpenStreetMap Overpass + 웹 검색)
# ======================================================================

# 공개 Overpass 서버 (2026-11 실측). 1순위는 전 세계 데이터를 2~6초에 돌려줬다.
# 2순위는 같은 조회에 30~90초가 걸리고, 동시에 여러 건을 보내면 429로 거절한다.
OVERPASS_PRIMARY = "https://overpass.openstreetmap.fr/api/interpreter"
OVERPASS_FALLBACK = "https://overpass-api.de/api/interpreter"
# 1순위가 이 시간 안에 답하지 않으면 2순위에도 보내고, 먼저 성공한 결과를 쓴다.
# 평소에는 서버 한 곳만 쓰므로 무료 공개 서버에 불필요한 부하를 주지 않는다.
HEDGE_DELAY = 8.0
OVERPASS_TIMEOUT = 60.0

KIND_LABELS = {
    "track": "육상 트랙",
    "park": "공원 둘레",
    "path": "산책로·강변길",
}
# 종류별 최소 길이. 짧은 이름 있는 길은 대부분 골목이나 상점가라서 제외한다
MIN_LENGTH_KM = {"track": 0.2, "park": 0.4, "path": 1.0}
MAX_ROUTES = 8
ELEVATION_SAMPLES = 12


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 좌표 사이의 거리(km)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _overpass_query(lat: float, lon: float, radius_m: int) -> str:
    around = f"around:{radius_m},{lat},{lon}"
    return f"""
[out:json][timeout:30];
(
  way({around})["leisure"="track"];
  way({around})["leisure"="park"]["name"];
  way({around})["highway"~"^(footway|path|cycleway)$"]["name"];
);
out geom;
"""


def _overpass_request(url: str, query: str) -> list[dict[str, Any]]:
    with http_client() as client:
        resp = client.post(url, data={"data": query}, timeout=OVERPASS_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("elements", [])


def _overpass(query: str) -> list[dict[str, Any]]:
    """1순위 서버에 먼저 보내고, 늦거나 실패하면 2순위에도 보내 먼저 성공한 결과를 돌려준다."""
    pool = ThreadPoolExecutor(max_workers=2)
    futures = {pool.submit(_overpass_request, OVERPASS_PRIMARY, query): OVERPASS_PRIMARY}
    failures: list[str] = []
    try:
        done, _ = wait(futures, timeout=HEDGE_DELAY)
        for fut in done:
            try:
                return fut.result()
            except Exception as exc:
                failures.append(f"{futures.pop(fut)}: {exc}")
        # 1순위가 늦거나 실패했다: 2순위에도 보낸다
        futures[pool.submit(_overpass_request, OVERPASS_FALLBACK, query)] = OVERPASS_FALLBACK
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for fut in done:
                url = futures.pop(fut)
                try:
                    return fut.result()
                except Exception as exc:
                    failures.append(f"{url}: {exc}")
        raise RuntimeError("모든 Overpass 서버 실패 - " + " / ".join(failures))
    finally:
        # 남은 요청은 기다리지 않는다 (결과는 버린다)
        pool.shutdown(wait=False, cancel_futures=True)


def _kind(tags: dict[str, str]) -> str:
    if tags.get("leisure") == "track":
        return "track"
    if tags.get("leisure") == "park":
        return "park"
    return "path"


def _length_km(geometry: list[dict[str, float]]) -> float:
    return sum(
        haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
        for a, b in zip(geometry, geometry[1:], strict=False)
    )


def _sample(points: list[dict[str, float]], n: int) -> list[dict[str, float]]:
    if len(points) <= n:
        return points
    step = (len(points) - 1) / (n - 1)
    return [points[round(i * step)] for i in range(n)]


def _elevation_ranges(routes: list[dict[str, Any]], client) -> None:
    """경로마다 표본 좌표의 고도를 한 번의 요청으로 조회해 고저차(최고-최저)를 채운다."""
    samples = [_sample(r.pop("_points"), ELEVATION_SAMPLES) for r in routes]
    flat = [p for s in samples for p in s]
    if not flat:
        return
    try:
        resp = client.get(
            ELEVATION_URL,
            params={
                "latitude": ",".join(str(p["lat"]) for p in flat),
                "longitude": ",".join(str(p["lon"]) for p in flat),
            },
        )
        resp.raise_for_status()
        elevations = resp.json().get("elevation", [])
    except Exception:
        for r in routes:
            r["elev_range_m"] = None
        return
    i = 0
    for route, sample in zip(routes, samples, strict=True):
        values = elevations[i : i + len(sample)]
        i += len(sample)
        route["elev_range_m"] = round(max(values) - min(values), 1) if values else None


@tool
def find_running_routes(lat: float, lon: float, radius_km: float = 2.0) -> dict[str, Any]:
    """좌표 주변에서 달리기 좋은 장소(공원 둘레, 이름 있는 산책로·강변길, 육상 트랙)를
    OpenStreetMap에서 찾는다. 장소마다 길이, 고저차(최고-최저 고도), 기준 좌표에서의 거리,
    OpenStreetMap 링크를 돌려준다. 좌표는 geocode로 먼저 구한다.
    러너들이 추천하는 인기 코스는 이 도구가 아니라 search_popular_routes로 찾는다.

    Args:
        lat: 기준 위도 (숙소 위치 등)
        lon: 기준 경도
        radius_km: 탐색 반경 km (0.5~5)
    """
    radius_km = max(0.5, min(radius_km, 5.0))
    try:
        with http_client() as client:
            elements = _overpass(_overpass_query(lat, lon, int(radius_km * 1000)))

            # 같은 이름, 같은 종류의 조각난 길은 하나의 코스로 묶는다
            groups: dict[tuple[str, str], dict[str, Any]] = defaultdict(
                lambda: {"length_km": 0.0, "points": [], "ids": []}
            )
            for el in elements:
                geometry = el.get("geometry") or []
                if len(geometry) < 2:
                    continue
                tags = el.get("tags", {})
                kind = _kind(tags)
                name = tags.get("name:en") or tags.get("name") or KIND_LABELS[kind]
                group = groups[(name, kind)]
                group["length_km"] += _length_km(geometry)
                group["points"] += geometry
                group["ids"].append(el["id"])

            routes = []
            for (name, kind), g in groups.items():
                if g["length_km"] < MIN_LENGTH_KM[kind]:
                    continue
                nearest = min(haversine_km(lat, lon, p["lat"], p["lon"]) for p in g["points"])
                routes.append(
                    {
                        "name": name,
                        "type": KIND_LABELS[kind],
                        "length_km": round(g["length_km"], 2),
                        "distance_from_origin_km": round(nearest, 2),
                        "url": f"https://www.openstreetmap.org/way/{g['ids'][0]}",
                        "_points": g["points"],
                    }
                )
            # 가까울수록, 길수록(5km까지) 앞에 둔다
            routes.sort(key=lambda r: r["distance_from_origin_km"] - 0.3 * min(r["length_km"], 5))
            routes = routes[:MAX_ROUTES]
            _elevation_ranges(routes, client)
    except Exception as exc:
        return error("overpass", f"OpenStreetMap 조회 실패: {exc}")
    for r in routes:
        r.pop("_points", None)
    return {
        "source": "OpenStreetMap (Overpass API), 고도: Open-Meteo",
        "radius_km": radius_km,
        "note": "length_km는 길(또는 공원 둘레)의 총 길이다. 실제 러닝 코스는 왕복이나 여러 바퀴로 거리를 맞춘다.",
        "routes": routes,
    }


@tool
def search_popular_routes(area: str, max_results: int = 6) -> dict[str, Any]:
    """지역의 인기 러닝 코스를 러닝 코스 가이드, 관광청 안내, 경로 공유 사이트, 커뮤니티 글에서 찾는다.
    결과는 검색 결과 목록(제목, 링크, 요약)이다. 코스 이름, 거리, 고저차는 요약에 있으면 쓰고,
    부족하면 fetch_page로 페이지를 읽는다. 끝내 확인하지 못한 값은 '확인 불가'로 적는다.

    Args:
        area: 지역이나 장소 (영어 권장, 예: "Marina Bay Singapore", "Kamogawa Kyoto")
        max_results: 최대 결과 수 (1~10)
    """
    result = ddg_search(f"best running routes {area}", max(1, min(max_results, 10)))
    if "error" not in result:
        result["source"] = "러닝 코스 가이드, 커뮤니티 글 (DuckDuckGo 검색)"
    return result


# ======================================================================
# 날씨 (Open-Meteo 평년값)
# ======================================================================

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY_VARS = "temperature_2m_max,temperature_2m_min,precipitation_sum,relative_humidity_2m_mean"


@tool
def get_weather(lat: float, lon: float, month: int, years: int = 3) -> dict[str, Any]:
    """특정 위치, 특정 월의 평년 날씨(최근 몇 년 관측 평균)를 조회한다. 예보가 아니라 평년값이다.

    Args:
        lat: 위도
        lon: 경도
        month: 월 (1~12)
        years: 평균을 낼 최근 연도 수 (1~5)
    """
    if not 1 <= month <= 12:
        return error("open-meteo", f"월 값이 잘못됐다: {month}")
    years = max(1, min(years, 5))
    today = date.today()
    # 올해 해당 월이 아직 끝나지 않았으면 작년부터 센다
    last_year = today.year if (today.year, month) < (today.year, today.month) else today.year - 1
    highs, lows, rain_totals, rainy_days, humidity = [], [], [], [], []
    try:
        with http_client() as client:
            for year in range(last_year - years + 1, last_year + 1):
                last_day = calendar.monthrange(year, month)[1]
                resp = client.get(
                    ARCHIVE_URL,
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "start_date": f"{year}-{month:02d}-01",
                        "end_date": f"{year}-{month:02d}-{last_day}",
                        "daily": DAILY_VARS,
                        "timezone": "auto",
                    },
                )
                resp.raise_for_status()
                daily = resp.json()["daily"]
                highs += [v for v in daily["temperature_2m_max"] if v is not None]
                lows += [v for v in daily["temperature_2m_min"] if v is not None]
                precip = [v for v in daily["precipitation_sum"] if v is not None]
                rain_totals.append(sum(precip))
                rainy_days.append(sum(1 for v in precip if v >= 1.0))
                humidity += [v for v in daily.get("relative_humidity_2m_mean", []) if v is not None]
    except Exception as exc:
        return error("open-meteo", f"날씨 조회 실패: {exc}")
    return {
        "basis": f"{last_year - years + 1}~{last_year}년 {month}월 관측 평균 (평년값, 예보 아님)",
        "avg_high_c": round(mean(highs), 1) if highs else None,
        "avg_low_c": round(mean(lows), 1) if lows else None,
        "avg_monthly_precip_mm": round(mean(rain_totals), 1) if rain_totals else None,
        "avg_rainy_days": round(mean(rainy_days), 1) if rainy_days else None,
        "avg_humidity_pct": round(mean(humidity)) if humidity else None,
        "source": "Open-Meteo Historical Weather API",
    }


# ======================================================================
# 도구 등록부: 에이전트가 쓰는 도구 목록
# ======================================================================

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
