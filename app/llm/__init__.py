"""LLM access. Pipelines get their chat model from here, never by constructing clients."""

from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI

from app.config import get_settings


@lru_cache
def get_chat_model() -> BaseChatModel:
    """Azure OpenAI chat model (gpt-4.1-mini deployment), deterministic by default."""
    settings = get_settings()
    missing = [
        name
        for name, value in (
            ("AZURE_OPENAI_LLM_KEY", settings.azure_openai_llm_key),
            ("AZURE_LLM_ENDPOINT", settings.azure_llm_endpoint),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"missing LLM settings: {', '.join(missing)} (see .env.example)")
    return AzureChatOpenAI(
        azure_endpoint=settings.azure_llm_endpoint,
        api_key=settings.azure_openai_llm_key,
        api_version=settings.azure_llm_api_version,
        azure_deployment=settings.azure_llm_deployment_41_mini,
        temperature=0,
        max_retries=3,
    )
