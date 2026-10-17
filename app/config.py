from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.constants import (
    DEFAULT_AZURE_LLM_API_VERSION,
    DEFAULT_AZURE_LLM_MODEL,
    DEFAULT_EXTRACTION_BATCH_SIZE,
    DEFAULT_MERGE_CONCURRENCY,
)


# Project root `.env`, so settings load whatever the working directory is.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # Credentials have no defaults: they must come from the env / .env.
    mongo_uri: str
    mongo_db: str = "whatif"

    falkor_host: str = "localhost"
    falkor_port: int = 6379
    falkor_password: str

    auth_secret: str  # signs sign-in tokens

    server_host: str = "127.0.0.1"
    server_port: int = 8000
    debug: bool = False

    azure_openai_llm_key: str = ""
    azure_llm_endpoint: str = ""
    azure_llm_api_version: str = DEFAULT_AZURE_LLM_API_VERSION
    azure_llm_model: str = DEFAULT_AZURE_LLM_MODEL
    azure_embedding_model: str = ""  # embedding deployment; empty disables embed_sim in merges

    extraction_batch_size: int = DEFAULT_EXTRACTION_BATCH_SIZE
    merge_concurrency: int = DEFAULT_MERGE_CONCURRENCY


@lru_cache
def get_settings() -> Settings:
    return Settings()
