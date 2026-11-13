"""환경 변수 설정. 값은 프로젝트 루트의 .env에서 읽는다."""

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/config.py → 프로젝트 루트
ROOT_DIR = Path(__file__).resolve().parents[1]

# AWS 자격 증명(boto3)과 LangSmith 추적은 프로세스 환경 변수를 직접 읽으므로
# .env 값을 환경 변수로 올린다. 이미 설정된 환경 변수는 덮어쓰지 않는다.
load_dotenv(ROOT_DIR / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    aws_region: str = Field(
        default="us-east-1", validation_alias=AliasChoices("AWS_REGION", "AWS_DEFAULT_REGION")
    )
    bedrock_model_id: str = ""
    judge_model_id: str = ""

    # 웹 검색(DuckDuckGo)이 막혔을 때만 사용자 확인 후 true로 바꾼다
    mock_search: bool = False

    # 외부 API 호출 제한 시간(초)
    http_timeout: float = 20.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
