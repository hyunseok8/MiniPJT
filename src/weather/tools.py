"""날씨 도구: 과거 관측값으로 특정 월의 평년 날씨를 계산한다 (Open-Meteo Archive)."""

import calendar
from datetime import date
from statistics import mean
from typing import Any

from langchain_core.tools import tool

from src.shared.http import error, http_client

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
