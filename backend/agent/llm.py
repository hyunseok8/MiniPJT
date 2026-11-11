"""Amazon Bedrock의 Claude 모델 연결."""

from langchain_aws import ChatBedrockConverse

from backend.config import get_settings


class LLMConfigError(RuntimeError):
    """LLM 설정이 빠져 에이전트를 실행할 수 없을 때."""


def get_llm(model_id: str | None = None, temperature: float = 0.0) -> ChatBedrockConverse:
    settings = get_settings()
    model = model_id or settings.bedrock_model_id
    if not model:
        raise LLMConfigError("BEDROCK_MODEL_ID가 설정되지 않았다. .env를 확인한다.")
    return ChatBedrockConverse(
        model=model,
        region_name=settings.aws_region,
        temperature=temperature,
    )
