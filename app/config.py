from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.constants import DEFAULT_EXTRACTION_BATCH_SIZE


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongo_uri: str = "mongodb://admin:admin@localhost:27017"
    mongo_db: str = "whatif"

    falkor_host: str = "localhost"
    falkor_port: int = 6379

    azure_openai_llm_key: str = ""
    azure_llm_endpoint: str = ""
    azure_llm_api_version: str = "2025-01-01-preview"
    azure_llm_deployment_41_mini: str = "gpt-4.1-mini"

    extraction_batch_size: int = DEFAULT_EXTRACTION_BATCH_SIZE


@lru_cache
def get_settings() -> Settings:
    return Settings()
