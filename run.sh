#!/usr/bin/env bash
# RunTrip Agent 실행 스크립트 (Git Bash, macOS, Linux)
#   ./run.sh          백엔드(:8000)와 웹 화면(:5173)을 함께 실행한다. Ctrl+C로 둘 다 끈다
#   ./run.sh api      백엔드만 실행한다
#   ./run.sh eval     공식 평가 12문항을 실행하고 채점한다 (결과: evaluation/results/)
# 처음 한 번: .env.example을 .env로 복사해 AWS 자격 증명과 BEDROCK_MODEL_ID를 채운다.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo ".env가 없습니다. .env.example을 .env로 복사한 뒤 값을 채워 주세요." >&2
  exit 1
fi

# uv가 있으면 uv로, 없으면 requirements.txt로 설치한 python으로 실행한다
if command -v uv >/dev/null 2>&1; then
  uv sync --quiet
  PY=(uv run python)
else
  PY=(python)
fi

case "${1:-all}" in
  api)
    exec "${PY[@]}" -m uvicorn src.api:app --host 127.0.0.1 --port 8000
    ;;
  eval)
    exec "${PY[@]}" -m src.evaluate
    ;;
  all)
    "${PY[@]}" -m uvicorn src.api:app --host 127.0.0.1 --port 8000 &
    API_PID=$!
    trap 'kill "$API_PID" 2>/dev/null || true' EXIT INT TERM
    (cd frontend && { [ -d node_modules ] || npm install; } && npm run dev -- --host 127.0.0.1)
    ;;
  *)
    echo "사용법: ./run.sh [all|api|eval]" >&2
    exit 1
    ;;
esac
