from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    douyin_spider_path: Path = Field(
        default=Path("/Users/bytedance/Documents/AIWorkspace/douyin-tool-eval/vendor/DouYin_Spider"),
        alias="DOUYIN_SPIDER_PATH",
    )
    service_data_dir: Path = Field(default=Path("data"), alias="SERVICE_DATA_DIR")
    worker_poll_seconds: float = Field(default=1.0, alias="WORKER_POLL_SECONDS")

    @property
    def db_path(self) -> Path:
        return self.service_data_dir / "service.db"

    @property
    def account_dir(self) -> Path:
        return self.service_data_dir / "accounts"

    @property
    def job_dir(self) -> Path:
        return self.service_data_dir / "jobs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
