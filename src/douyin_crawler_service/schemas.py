from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field


JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
AccountStatus = Literal["active", "disabled", "invalid"]


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    cookie: str = ""
    refresh_cookie: bool = False
    seed_user_url: str = (
        "https://www.douyin.com/user/"
        "MS4wLjABAAAAEpmH344CkCw2M58T33Q8TuFpdvJsOyaZcbWxAMc6H03wOVFf1Ow4mPP94TDUS4Us"
    )
    headed: bool = False
    cookie_timeout: int = Field(default=30, ge=5, le=120)
    max_concurrency: int = Field(default=1, ge=1, le=8)


class AccountOut(BaseModel):
    id: int
    name: str
    status: AccountStatus
    cookie_path: str
    max_concurrency: int
    created_at: str
    updated_at: str


class JobCreate(BaseModel):
    account_id: int
    user_url: str = Field(min_length=1)
    queue_name: str = Field(default="default", min_length=1, max_length=80)
    max_pages: int = Field(default=1, ge=1, le=100)
    max_comments_per_video: int = Field(default=20, ge=0, le=1000)
    include_replies: bool = False
    sleep_seconds: float = Field(default=1.0, ge=0, le=30)


class AwemeJobCreate(BaseModel):
    account_id: int = Field(validation_alias=AliasChoices("account_id", "accountId"))
    aweme_id: str = Field(min_length=1, validation_alias=AliasChoices("aweme_id", "awemeId"))
    queue_name: str = Field(default="default", min_length=1, max_length=80, validation_alias=AliasChoices("queue_name", "queueName"))
    max_comments_per_video: int = Field(
        default=20,
        ge=0,
        le=1000,
        validation_alias=AliasChoices("max_comments_per_video", "maxCommentsPerVideo", "max_comments", "maxComments"),
    )
    include_replies: bool = Field(default=False, validation_alias=AliasChoices("include_replies", "includeReplies"))
    sleep_seconds: float = Field(default=1.0, ge=0, le=30, validation_alias=AliasChoices("sleep_seconds", "sleepSeconds"))


class JobOut(BaseModel):
    id: int
    account_id: int
    queue_name: str
    status: JobStatus
    user_url: str
    params: dict[str, Any]
    result_dir: str
    error: str | None
    video_count: int
    comment_count: int
    comments_with_pictures: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str


class JobResult(BaseModel):
    job: JobOut
    summary: dict[str, Any] | None = None
    files: dict[str, str]
