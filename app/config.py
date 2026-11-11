from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

# anchored to the project folder, so commands work from any directory
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # no default: a server that forgets to set it must not fall back to local behavior
    environment: Literal["local", "test", "production"]
    database_url: PostgresDsn


@lru_cache
def get_settings() -> Settings:
    return Settings()
