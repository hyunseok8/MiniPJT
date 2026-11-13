# data/ — 사용한 데이터

RunTrip Agent는 대회 정보, 러닝 코스, 날씨를 **미리 모아 둔 문서 없이** 질문할 때마다 무료 공개 API와 웹 검색으로 가져온다. 답변의 근거는 그때 가져온 도구 결과뿐이다. 그래서 이 폴더에는 목업 기준값만 있다.

## 이 폴더의 파일

| 파일 | 내용 | 쓰는 곳 |
|------|------|---------|
| `mock_travel.json` | 목업 항공권(항공사 이름, 가격 범위)과 목업 숙소(이름, 거리·가격·평점 범위)의 기준값. **실제 가격이 아니다** | `src/tools.py`의 `search_flights`, `search_hotels` |

목업 도구는 같은 질문에 항상 같은 값을 돌려준다. 도시, 날짜, 장소 이름으로 계산한 해시 값으로 범위 안의 값을 고르기 때문이다. 결과에는 `"mock": true`가 붙고, 답변에는 "목업(실제 가격 아님)"으로 표시된다.

## 질문할 때 가져오는 외부 데이터 (저장하지 않음)

| 데이터 | 출처 | 비용 | 도구 |
|--------|------|------|------|
| 대회 정보, 공식 사이트 본문, 인기 코스 글, 러닝 크루 | DuckDuckGo 웹 검색 + 페이지 본문 | 무료, 키 불필요 | `search_web`, `fetch_page`, `search_races`, `search_popular_routes` (`src/retriever.py`, `src/tools.py`) |
| 장소 좌표 | OpenStreetMap Nominatim | 무료, 키 불필요 | `geocode` |
| 숙소 근처 공원·산책로·트랙 | OpenStreetMap Overpass API | 무료, 키 불필요 | `find_running_routes` |
| 고도 | Open-Meteo Elevation API | 무료, 키 불필요 | `get_elevation`, `find_running_routes` |
| 월별 평년 날씨 | Open-Meteo Archive API | 무료, 키 불필요 | `get_weather` |

대화 기록과 사용자의 숙소 위치는 저장하지 않는다 (SERVICE.md 정책 4).

평가용 데이터는 이 폴더가 아니라 `evaluation/`에 있다. 공식 세트는 `evaluation/test_queries.csv`, 보조 회귀 세트는 `evaluation/regression/test_queries.json`이다.
