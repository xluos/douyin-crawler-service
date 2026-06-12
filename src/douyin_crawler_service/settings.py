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
    service_host: str = Field(default="127.0.0.1", alias="SERVICE_HOST")
    service_port: int = Field(default=18099, ge=1024, le=65535, alias="SERVICE_PORT")
    worker_poll_seconds: float = Field(default=1.0, alias="WORKER_POLL_SECONDS")
    browser_channel: str = Field(default="chrome", alias="BROWSER_CHANNEL")

    @property
    def playwright_browser_channel(self) -> str | None:
        browser_channel = self.browser_channel.strip()
        return browser_channel or None

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
