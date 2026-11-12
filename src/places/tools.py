"""지도 도구: 지오코딩(OpenStreetMap Nominatim), 고도(Open-Meteo)."""

from typing import Any

from langchain_core.tools import tool

from src.shared.http import error, http_client

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
