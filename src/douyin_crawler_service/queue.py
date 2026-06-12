import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Any

from loguru import logger

from .db import Database
from .spider_adapter import DouyinSpiderEngine


JOB_LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
    "request_id={extra[request_id]} job_id={extra[job_id]} queue={extra[queue_name]} "
    "| {name}:{function}:{line} | {message}"
)


class QueueManager:
    def __init__(self, *, db: Database, engine: DouyinSpiderEngine):
        self.db = db
        self.engine = engine
        self.queues: dict[str, asyncio.Queue[int]] = {}
        self.workers: dict[str, asyncio.Task] = {}
        self.account_semaphores: dict[int, asyncio.Semaphore] = {}
        self.enqueued_job_ids: set[int] = set()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        for job in self.db.queued_jobs():
            await self.enqueue(job["id"], job["queue_name"])

    async def stop(self) -> None:
        logger.info("stopping queue workers count={}", len(self.workers))
        for task in self.workers.values():
            task.cancel()
        await asyncio.gather(*self.workers.values(), return_exceptions=True)

    async def enqueue(self, job_id: int, queue_name: str) -> None:
        async with self._lock:
            if job_id in self.enqueued_job_ids:
                return
            queue = self.queues.setdefault(queue_name, asyncio.Queue())
            self.enqueued_job_ids.add(job_id)
            await queue.put(job_id)
            self.db.add_event(job_id, "info", "job enqueued", {"queue_name": queue_name})
            logger.info("job enqueued id={} queue={} queue_size={}", job_id, queue_name, queue.qsize())
            if queue_name not in self.workers or self.workers[queue_name].done():
                self.workers[queue_name] = asyncio.create_task(self._worker(queue_name))

    async def generate_cookie(self, *, seed_user_url: str, headed: bool, timeout: int) -> str:
        logger.info("anonymous cookie generation requested seed={} headed={} timeout={}", seed_user_url, headed, timeout)
        return await asyncio.to_thread(
            self.engine.generate_anonymous_cookie,
            seed_user_url=seed_user_url,
            headed=headed,
            timeout=timeout,
        )

    async def _worker(self, queue_name: str) -> None:
        logger.info("queue worker started: {}", queue_name)
        queue = self.queues[queue_name]
        while True:
            job_id = await queue.get()
            try:
                self.enqueued_job_ids.discard(job_id)
                await self._run_job(job_id)
            except asyncio.CancelledError:
                logger.info("queue worker cancelled: {}", queue_name)
                raise
            except Exception:
                logger.exception("queue worker failed job_id={} queue={}", job_id, queue_name)
            finally:
                queue.task_done()

    async def _run_job(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        account = self.db.get_account(job["account_id"])
        semaphore = self._account_semaphore(account)
        async with semaphore:
            job_log_path = Path(job["result_dir"]) / "job.log"
            job_log_path.parent.mkdir(parents=True, exist_ok=True)
            job_log_sink_id = logger.add(
                job_log_path,
                rotation="20 MB",
                retention="30 days",
                encoding="utf-8",
                enqueue=True,
                backtrace=True,
                diagnose=False,
                filter=lambda record: record["extra"].get("job_id") == job_id,
                format=JOB_LOG_FORMAT,
            )
            try:
                with logger.contextualize(
                    request_id=f"job-{job_id}",
                    job_id=job_id,
                    account_id=account["id"],
                    queue_name=job["queue_name"],
                ):
                    self.db.mark_running(job_id)
                    logger.info(
                        "job started queue={} account={} result_dir={} job_log={}",
                        job["queue_name"],
                        account["id"],
                        job["result_dir"],
                        job_log_path,
                    )
                    self.db.add_event(
                        job_id,
                        "info",
                        "crawl started",
                        {
                            "account_id": account["id"],
                            "queue_name": job["queue_name"],
                            "user_url": job["user_url"],
                            "result_dir": job["result_dir"],
                            "job_log": str(job_log_path),
                            "params": job["params"],
                        },
                    )
                    summary = await asyncio.to_thread(self._crawl_sync, job, account)
                    self.db.mark_succeeded(
                        job_id,
                        video_count=int(summary.get("video_count") or 0),
                        comment_count=int(summary.get("comment_count") or 0),
                        comments_with_pictures=int(summary.get("comments_with_pictures") or 0),
                    )
                    logger.info("job succeeded summary={}", summary)
            except Exception as exc:
                with logger.contextualize(
                    request_id=f"job-{job_id}",
                    job_id=job_id,
                    account_id=account["id"],
                    queue_name=job["queue_name"],
                ):
                    logger.exception("job failed")
                    self.db.mark_failed(job_id, f"{type(exc).__name__}: {exc}")
            finally:
                logger.remove(job_log_sink_id)

    def _account_semaphore(self, account: dict[str, Any]) -> asyncio.Semaphore:
        account_id = int(account["id"])
        if account_id not in self.account_semaphores:
            self.account_semaphores[account_id] = asyncio.Semaphore(int(account["max_concurrency"]))
        return self.account_semaphores[account_id]

    def _crawl_sync(self, job: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
        cookie = Path(account["cookie_path"]).read_text(encoding="utf-8").strip()
        logger.info("loaded account cookie account={} cookie_path={}", account["id"], account["cookie_path"])
        params = job["params"]
        if params.get("mode") == "aweme":
            return self.engine.crawl_aweme(
                cookie=cookie,
                aweme_id=str(params["aweme_id"]),
                result_dir=Path(job["result_dir"]),
                max_comments_per_video=int(params["max_comments_per_video"]),
                include_replies=bool(params["include_replies"]),
                sleep_seconds=float(params["sleep_seconds"]),
            )
        return self.engine.crawl_user(
            cookie=cookie,
            user_url=job["user_url"],
            result_dir=Path(job["result_dir"]),
            max_pages=int(params["max_pages"]),
            max_comments_per_video=int(params["max_comments_per_video"]),
            include_replies=bool(params["include_replies"]),
            sleep_seconds=float(params["sleep_seconds"]),
        )
