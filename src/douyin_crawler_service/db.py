import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                create table if not exists accounts (
                    id integer primary key autoincrement,
                    name text not null unique,
                    status text not null default 'active',
                    cookie_path text not null,
                    max_concurrency integer not null default 1,
                    created_at text not null,
                    updated_at text not null
                );

                create table if not exists jobs (
                    id integer primary key autoincrement,
                    account_id integer not null references accounts(id),
                    queue_name text not null,
                    status text not null,
                    user_url text not null,
                    params_json text not null,
                    result_dir text not null,
                    error text,
                    video_count integer not null default 0,
                    comment_count integer not null default 0,
                    comments_with_pictures integer not null default 0,
                    created_at text not null,
                    started_at text,
                    finished_at text,
                    updated_at text not null
                );

                create index if not exists idx_jobs_status_queue on jobs(status, queue_name, id);
                create index if not exists idx_jobs_account on jobs(account_id, id);

                create table if not exists job_events (
                    id integer primary key autoincrement,
                    job_id integer not null references jobs(id),
                    level text not null,
                    message text not null,
                    detail_json text,
                    created_at text not null
                );

                create index if not exists idx_job_events_job on job_events(job_id, id);
                """
            )

    def create_account(self, *, name: str, cookie_path: str, max_concurrency: int) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                insert into accounts (name, status, cookie_path, max_concurrency, created_at, updated_at)
                values (?, 'active', ?, ?, ?, ?)
                """,
                (name, cookie_path, max_concurrency, now, now),
            )
            row = conn.execute("select * from accounts where id = ?", (cur.lastrowid,)).fetchone()
            return dict(row)

    def get_account(self, account_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("select * from accounts where id = ?", (account_id,)).fetchone()
        if not row:
            raise KeyError(f"account not found: {account_id}")
        return dict(row)

    def list_accounts(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("select * from accounts order by id desc").fetchall()
        return [dict(row) for row in rows]

    def update_account_cookie(self, account_id: int, cookie_path: str) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "update accounts set cookie_path = ?, status = 'active', updated_at = ? where id = ?",
                (cookie_path, now, account_id),
            )
        return self.get_account(account_id)

    def create_job(
        self,
        *,
        account_id: int,
        queue_name: str,
        user_url: str,
        params: dict[str, Any],
        result_dir: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                insert into jobs (
                    account_id, queue_name, status, user_url, params_json, result_dir,
                    created_at, updated_at
                )
                values (?, ?, 'queued', ?, ?, ?, ?, ?)
                """,
                (account_id, queue_name, user_url, json.dumps(params), result_dir, now, now),
            )
            row = conn.execute("select * from jobs where id = ?", (cur.lastrowid,)).fetchone()
            return self._decode_job(dict(row))

    def get_job(self, job_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("select * from jobs where id = ?", (job_id,)).fetchone()
        if not row:
            raise KeyError(f"job not found: {job_id}")
        return self._decode_job(dict(row))

    def list_jobs(self, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if status:
                rows = conn.execute(
                    "select * from jobs where status = ? order by id desc limit ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute("select * from jobs order by id desc limit ?", (limit,)).fetchall()
        return [self._decode_job(dict(row)) for row in rows]

    def queued_jobs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("select * from jobs where status in ('queued', 'running') order by id asc").fetchall()
        jobs = [self._decode_job(dict(row)) for row in rows]
        for job in jobs:
            if job["status"] == "running":
                self.mark_queued(job["id"])
                job["status"] = "queued"
        return jobs

    def mark_queued(self, job_id: int) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "update jobs set status = 'queued', started_at = null, error = null, updated_at = ? where id = ?",
                (now, job_id),
            )
        self.add_event(job_id, "info", "job queued")

    def mark_running(self, job_id: int) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "update jobs set status = 'running', started_at = ?, updated_at = ? where id = ?",
                (now, now, job_id),
            )
        self.add_event(job_id, "info", "job running")

    def mark_succeeded(self, job_id: int, *, video_count: int, comment_count: int, comments_with_pictures: int) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                update jobs
                set status = 'succeeded', finished_at = ?, updated_at = ?,
                    video_count = ?, comment_count = ?, comments_with_pictures = ?, error = null
                where id = ?
                """,
                (now, now, video_count, comment_count, comments_with_pictures, job_id),
            )
        self.add_event(
            job_id,
            "info",
            "job succeeded",
            {
                "video_count": video_count,
                "comment_count": comment_count,
                "comments_with_pictures": comments_with_pictures,
            },
        )

    def mark_failed(self, job_id: int, error: str) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "update jobs set status = 'failed', error = ?, finished_at = ?, updated_at = ? where id = ?",
                (error[:4000], now, now, job_id),
            )
        self.add_event(job_id, "error", "job failed", {"error": error[:4000]})

    def add_event(self, job_id: int, level: str, message: str, detail: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into job_events (job_id, level, message, detail_json, created_at)
                values (?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    level,
                    message,
                    json.dumps(detail, ensure_ascii=False) if detail is not None else None,
                    utc_now(),
                ),
            )

    def list_job_events(self, job_id: int, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "select * from job_events where job_id = ? order by id asc limit ?",
                (job_id, limit),
            ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            detail_json = item.pop("detail_json", None)
            item["detail"] = json.loads(detail_json) if detail_json else None
            events.append(item)
        return events

    def _decode_job(self, row: dict[str, Any]) -> dict[str, Any]:
        row["params"] = json.loads(row.pop("params_json"))
        return row
