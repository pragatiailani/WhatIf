"""LLM access. Pipelines get their chat model from here, never by constructing clients."""

from functools import lru_cache

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings

from app.config import Settings, get_settings
from app.llm.callbacks import LlmCallLogger


def _require_credentials(settings: Settings) -> None:
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


@lru_cache
def get_chat_model() -> BaseChatModel:
    """Azure OpenAI chat model (`AZURE_LLM_MODEL` deployment), deterministic by default."""
    settings = get_settings()
    _require_credentials(settings)
    return AzureChatOpenAI(
        azure_endpoint=settings.azure_llm_endpoint,
        api_key=settings.azure_openai_llm_key,
        api_version=settings.azure_llm_api_version,
        azure_deployment=settings.azure_llm_model,
        temperature=0,
        max_retries=3,
        callbacks=[LlmCallLogger(settings.azure_llm_model)],
    )


@lru_cache
def get_embeddings() -> Embeddings | None:
    """Azure OpenAI embeddings (`AZURE_EMBEDDING_MODEL` deployment), or None if not configured."""
    settings = get_settings()
    if not settings.azure_embedding_model:
        return None
    _require_credentials(settings)
    return AzureOpenAIEmbeddings(
        azure_endpoint=settings.azure_llm_endpoint,
        api_key=settings.azure_openai_llm_key,
        api_version=settings.azure_llm_api_version,
        azure_deployment=settings.azure_embedding_model,
        dimensions=settings.azure_embedding_dimensions or None,
        max_retries=3,
    )
