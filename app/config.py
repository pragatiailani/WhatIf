from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongo_uri: str = "mongodb://admin:admin@localhost:27017"
    mongo_db: str = "whatif"

    falkor_host: str = "localhost"
    falkor_port: int = 6379


@lru_cache
def get_settings() -> Settings:
    return Settings()
