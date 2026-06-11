import json
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from loguru import logger

from .db import Database, utc_now
from .queue import QueueManager
from .schemas import AccountCreate, AccountOut, JobCreate, JobOut, JobResult
from .settings import Settings, get_settings
from .spider_adapter import DouyinSpiderEngine


def configure_logging(settings: Settings) -> None:
    log_dir = settings.service_data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_format = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
        "request_id={extra[request_id]} job_id={extra[job_id]} queue={extra[queue_name]} "
        "| {name}:{function}:{line} | {message}"
    )
    logger.remove()
    logger.configure(
        patcher=lambda record: record["extra"].update(
            {
                "request_id": record["extra"].get("request_id", "-"),
                "job_id": record["extra"].get("job_id", "-"),
                "queue_name": record["extra"].get("queue_name", "-"),
            }
        )
    )
    logger.add(sys.stderr, format=log_format, backtrace=True, diagnose=False)
    logger.add(
        log_dir / "service.log",
        rotation="20 MB",
        retention="14 days",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,
        format=log_format,
    )


def account_out(row: dict[str, Any]) -> AccountOut:
    return AccountOut(**row)


def job_out(row: dict[str, Any]) -> JobOut:
    return JobOut(**row)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path, *, limit: int, with_pictures: bool = False) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line in file:
            item = json.loads(line)
            if with_pictures and not item.get("picture_urls"):
                continue
            rows.append(item)
            if len(rows) >= limit:
                break
    return rows


def tail_text(path: Path, *, lines: int) -> str:
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:]) + ("\n" if content else "")


def create_app() -> FastAPI:
    settings = get_settings()
    settings.service_data_dir.mkdir(parents=True, exist_ok=True)
    settings.account_dir.mkdir(parents=True, exist_ok=True)
    settings.job_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(settings)

    db = Database(settings.db_path)
    engine = DouyinSpiderEngine(settings.douyin_spider_path)
    queue_manager = QueueManager(db=db, engine=engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.db = db
        app.state.engine = engine
        app.state.queue_manager = queue_manager
        logger.info(
            "service starting data_dir={} spider_path={}",
            settings.service_data_dir,
            settings.douyin_spider_path,
        )
        await queue_manager.start()
        try:
            yield
        finally:
            logger.info("service stopping")
            await queue_manager.stop()

    app = FastAPI(title="Douyin Crawler Service", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_logging(request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or uuid4().hex
        started = time.perf_counter()
        with logger.contextualize(request_id=request_id):
            logger.info(
                "http request started method={} path={} client={}",
                request.method,
                request.url.path,
                request.client.host if request.client else "-",
            )
            try:
                response = await call_next(request)
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000
                logger.exception(
                    "http request failed method={} path={} elapsed_ms={:.2f}",
                    request.method,
                    request.url.path,
                    elapsed_ms,
                )
                raise
            elapsed_ms = (time.perf_counter() - started) * 1000
            response.headers["x-request-id"] = request_id
            logger.info(
                "http request finished method={} path={} status={} elapsed_ms={:.2f}",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
            return response

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "spider_path": str(settings.douyin_spider_path),
            "data_dir": str(settings.service_data_dir),
            "db_path": str(settings.db_path),
            "log_path": str(settings.service_data_dir / "logs" / "service.log"),
        }

    @app.post("/accounts", response_model=AccountOut)
    async def create_account(payload: AccountCreate) -> AccountOut:
        logger.info("create account request name={} refresh_cookie={}", payload.name, payload.refresh_cookie)
        cookie = payload.cookie.strip()
        if not cookie:
            logger.info("account {} has no cookie, generating anonymous cookie", payload.name)
            cookie = await queue_manager.generate_cookie(
                seed_user_url=payload.seed_user_url,
                headed=payload.headed,
                timeout=payload.cookie_timeout,
            )
        account_path = settings.account_dir / f"{payload.name}.cookie"
        account_path.write_text(cookie, encoding="utf-8")
        try:
            row = db.create_account(
                name=payload.name,
                cookie_path=str(account_path),
                max_concurrency=payload.max_concurrency,
            )
        except Exception as exc:
            logger.exception("create account failed name={}", payload.name)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.info("account created id={} name={}", row["id"], row["name"])
        return account_out(row)

    @app.get("/accounts", response_model=list[AccountOut])
    def list_accounts() -> list[AccountOut]:
        return [account_out(row) for row in db.list_accounts()]

    @app.post("/accounts/{account_id}/refresh-cookie", response_model=AccountOut)
    async def refresh_account_cookie(
        account_id: int,
        seed_user_url: str = Query(
            default="https://www.douyin.com/user/MS4wLjABAAAAEpmH344CkCw2M58T33Q8TuFpdvJsOyaZcbWxAMc6H03wOVFf1Ow4mPP94TDUS4Us"
        ),
        headed: bool = False,
        timeout: int = 30,
    ) -> AccountOut:
        try:
            account = db.get_account(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        logger.info("refresh account cookie id={} seed={}", account_id, seed_user_url)
        cookie = await queue_manager.generate_cookie(seed_user_url=seed_user_url, headed=headed, timeout=timeout)
        cookie_path = Path(account["cookie_path"])
        cookie_path.parent.mkdir(parents=True, exist_ok=True)
        cookie_path.write_text(cookie, encoding="utf-8")
        row = db.update_account_cookie(account_id, str(cookie_path))
        return account_out(row)

    @app.post("/jobs", response_model=JobOut)
    async def create_job(payload: JobCreate) -> JobOut:
        try:
            account = db.get_account(payload.account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if account["status"] != "active":
            raise HTTPException(status_code=400, detail=f"account is not active: {account['status']}")

        params = {
            "max_pages": payload.max_pages,
            "max_comments_per_video": payload.max_comments_per_video,
            "include_replies": payload.include_replies,
            "sleep_seconds": payload.sleep_seconds,
        }
        result_dir = settings.job_dir / "pending"
        row = db.create_job(
            account_id=payload.account_id,
            queue_name=payload.queue_name,
            user_url=payload.user_url,
            params=params,
            result_dir=str(result_dir),
        )
        job_result_dir = settings.job_dir / str(row["id"])
        job_result_dir.mkdir(parents=True, exist_ok=True)
        # Keep result_dir stable and visible in the job row after id allocation.
        with db.connect() as conn:
            conn.execute("update jobs set result_dir = ?, updated_at = ? where id = ?", (str(job_result_dir), utc_now(), row["id"]))
        row = db.get_job(row["id"])
        db.add_event(row["id"], "info", "job created", {"queue_name": row["queue_name"], "account_id": row["account_id"]})
        logger.info(
            "job created id={} queue={} account={} user={}",
            row["id"],
            row["queue_name"],
            row["account_id"],
            row["user_url"],
        )
        await queue_manager.enqueue(row["id"], row["queue_name"])
        return job_out(row)

    @app.get("/jobs", response_model=list[JobOut])
    def list_jobs(limit: int = Query(default=100, ge=1, le=500), status: str | None = None) -> list[JobOut]:
        return [job_out(row) for row in db.list_jobs(limit=limit, status=status)]

    @app.get("/jobs/{job_id}", response_model=JobOut)
    def get_job(job_id: int) -> JobOut:
        try:
            return job_out(db.get_job(job_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/jobs/{job_id}/events")
    def get_job_events(job_id: int, limit: int = Query(default=200, ge=1, le=1000)) -> list[dict[str, Any]]:
        try:
            db.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return db.list_job_events(job_id, limit=limit)

    @app.get("/jobs/{job_id}/result", response_model=JobResult)
    def get_job_result(job_id: int) -> JobResult:
        try:
            job = db.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        result_dir = Path(job["result_dir"])
        files = {
            "user": str(result_dir / "user.json"),
            "videos": str(result_dir / "videos.jsonl"),
            "comments": str(result_dir / "comments.jsonl"),
            "summary": str(result_dir / "summary.json"),
            "job_log": str(result_dir / "job.log"),
        }
        return JobResult(job=job_out(job), summary=read_json(result_dir / "summary.json"), files=files)

    @app.get("/jobs/{job_id}/comments")
    def get_job_comments(
        job_id: int,
        limit: int = Query(default=100, ge=1, le=1000),
        with_pictures: bool = False,
    ) -> list[dict[str, Any]]:
        try:
            job = db.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return read_jsonl(Path(job["result_dir"]) / "comments.jsonl", limit=limit, with_pictures=with_pictures)

    @app.get("/jobs/{job_id}/logs")
    def get_job_logs(job_id: int, lines: int = Query(default=200, ge=1, le=5000)) -> Response:
        try:
            job = db.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=tail_text(Path(job["result_dir"]) / "job.log", lines=lines),
            media_type="text/plain; charset=utf-8",
        )

    @app.get("/queues")
    def queues() -> dict[str, Any]:
        return {
            name: {
                "size": queue.qsize(),
                "worker_done": queue_manager.workers.get(name).done() if name in queue_manager.workers else None,
            }
            for name, queue in queue_manager.queues.items()
        }

    return app
