# RunTrip Agent 클린 환경 재현용 이미지
#   빌드:  docker build -t runtrip-agent .
#   API:   docker run --rm -p 8000:8000 --env-file .env runtrip-agent
#   평가:  docker run --rm --env-file .env -v "$(pwd)/evaluation/results:/app/evaluation/results" runtrip-agent python -m src.evaluate
# .env(AWS 자격 증명, BEDROCK_MODEL_ID)는 이미지에 넣지 않고 실행할 때 --env-file로 넘긴다.
# 웹 화면(frontend/)은 이미지에 넣지 않는다. 로컬에서 `cd frontend && npm install && npm run dev`로 띄운다.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY data/ data/
COPY evaluation/test_queries.csv evaluation/ragas_references.csv evaluation/
COPY evaluation/regression/test_queries.json evaluation/regression/test_queries.json

EXPOSE 8000
CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
