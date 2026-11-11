"""러닝 코스 도구 (모두 무료, 키 불필요).

- find_running_routes: OpenStreetMap(Overpass API)에서 숙소 근처 공원, 산책로·강변길, 육상 트랙을 찾는다.
- search_popular_routes: 러닝 코스 가이드, 커뮤니티 글에서 그 지역의 인기 러닝 코스를 웹 검색으로 찾는다.
  (Strava 인기 구간 페이지는 무료 검색으로 거의 검색되지 않아 이 방식으로 대신한다.)
"""

from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any

from langchain_core.tools import tool

from backend.tools.common import error, haversine_km, http_client
from backend.tools.search import ddg_search

# 공개 Overpass 서버 (2026-11 실측). 1순위는 전 세계 데이터를 2~6초에 돌려줬다.
# 2순위는 같은 조회에 30~90초가 걸리고, 동시에 여러 건을 보내면 429로 거절한다.
OVERPASS_PRIMARY = "https://overpass.openstreetmap.fr/api/interpreter"
OVERPASS_FALLBACK = "https://overpass-api.de/api/interpreter"
# 1순위가 이 시간 안에 답하지 않으면 2순위에도 보내고, 먼저 성공한 결과를 쓴다.
# 평소에는 서버 한 곳만 쓰므로 무료 공개 서버에 불필요한 부하를 주지 않는다.
HEDGE_DELAY = 8.0
OVERPASS_TIMEOUT = 60.0
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"

KIND_LABELS = {
    "track": "육상 트랙",
    "park": "공원 둘레",
    "path": "산책로·강변길",
}
# 종류별 최소 길이. 짧은 이름 있는 길은 대부분 골목이나 상점가라서 제외한다
MIN_LENGTH_KM = {"track": 0.2, "park": 0.4, "path": 1.0}
MAX_ROUTES = 8
ELEVATION_SAMPLES = 12


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
