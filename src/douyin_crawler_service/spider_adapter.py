import json
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
import urllib3.util.connection
from loguru import logger
from playwright.sync_api import sync_playwright


DEFAULT_SEED_USER_URL = (
    "https://www.douyin.com/user/"
    "MS4wLjABAAAAEpmH344CkCw2M58T33Q8TuFpdvJsOyaZcbWxAMc6H03wOVFf1Ow4mPP94TDUS4Us"
)
DOUYIN_SHARE_URL_RE = re.compile(r"https?://[^\s]+")
DOUYIN_SHARE_URL_TRAILING_CHARS = "，。,.!！?？;；:：)）]】}\"'"
DOUYIN_WEB_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
}
REQUESTS_DEFAULT_TIMEOUT = (15.0, 45.0)
REQUESTS_TIMEOUT_PATCH_ATTR = "_douyin_crawler_default_timeout_patched"
URLLIB3_IPV4_PATCH_ATTR = "_douyin_crawler_ipv4_patched"


def normalize_user_url(value: str) -> str:
    value = value.strip()
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return f"https://www.douyin.com/user/{value}"


def sec_uid_from_user_url(user_url: str) -> str:
    return user_url.rstrip("/").split("/")[-1].split("?")[0]


def install_requests_default_timeout(timeout: tuple[float, float] = REQUESTS_DEFAULT_TIMEOUT) -> None:
    original_request = requests.sessions.Session.request
    if getattr(original_request, REQUESTS_TIMEOUT_PATCH_ATTR, False):
        return

    def request_with_default_timeout(self, method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return original_request(self, method, url, **kwargs)

    setattr(request_with_default_timeout, REQUESTS_TIMEOUT_PATCH_ATTR, True)
    requests.sessions.Session.request = request_with_default_timeout


def install_requests_ipv4_only() -> None:
    allowed_gai_family = urllib3.util.connection.allowed_gai_family
    if getattr(allowed_gai_family, URLLIB3_IPV4_PATCH_ATTR, False):
        return

    def allowed_ipv4_family():
        return socket.AF_INET

    setattr(allowed_ipv4_family, URLLIB3_IPV4_PATCH_ATTR, True)
    urllib3.util.connection.allowed_gai_family = allowed_ipv4_family


def is_douyin_share_host(host: str) -> bool:
    host = host.lower()
    return (
        host == "iesdouyin.com"
        or host.endswith(".iesdouyin.com")
        or host == "douyin.com"
        or host.endswith(".douyin.com")
    )


def extract_douyin_share_url(value: str) -> str | None:
    for match in DOUYIN_SHARE_URL_RE.finditer(value):
        url = match.group(0).rstrip(DOUYIN_SHARE_URL_TRAILING_CHARS)
        if is_douyin_share_host(urlparse(url).netloc):
            return url
    return None


def resolve_douyin_short_url(url: str, *, timeout: float = 15.0) -> str:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "v.douyin.com":
        return url
    install_requests_ipv4_only()
    try:
        response = requests.get(
            url,
            allow_redirects=False,
            headers=DOUYIN_WEB_HEADERS,
            stream=True,
            timeout=timeout,
        )
        return response.headers.get("location") or response.url
    except requests.RequestException as exc:
        raise ValueError(f"failed to resolve douyin short url: {url}") from exc
    finally:
        if "response" in locals():
            response.close()


def aweme_id_from_douyin_url(url: str) -> str | None:
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    for marker in ("video", "note"):
        if marker in path_parts:
            index = path_parts.index(marker)
            if index + 1 < len(path_parts):
                aweme_id = path_parts[index + 1]
                if aweme_id.isdigit():
                    return aweme_id
    modal_id = (parse_qs(parsed.query).get("modal_id") or [""])[0]
    if modal_id.isdigit():
        return modal_id
    return None


def normalize_aweme_id(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("aweme_id must not be empty")
    input_value = extract_douyin_share_url(value) or value
    parsed = urlparse(input_value)
    if parsed.scheme and parsed.netloc:
        resolved_url = resolve_douyin_short_url(input_value)
        aweme_id = aweme_id_from_douyin_url(resolved_url)
        if aweme_id:
            return aweme_id
        raise ValueError(f"unsupported aweme url: {input_value}")
    aweme_id = input_value.split("?")[0].strip("/")
    if not aweme_id.isdigit():
        raise ValueError("aweme_id must be numeric")
    return aweme_id


def aweme_url_from_id(aweme_id: str) -> str:
    return f"https://www.douyin.com/video/{normalize_aweme_id(aweme_id)}"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    if not rows:
        path.touch()
        return 0
    with path.open("a", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def extract_url_list(url_obj: dict[str, Any]) -> list[str]:
    if not isinstance(url_obj, dict):
        return []
    return [url for url in (url_obj.get("url_list") or []) if url]


def extract_note_image_urls(aweme: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for image in aweme.get("images") or []:
        result.extend(extract_url_list(image))
    return result


def extract_comment_image_urls(comment: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for image in comment.get("image_list") or []:
        result.extend(extract_url_list(image.get("origin_url") or {}))
    return result


class DouyinSpiderEngine:
    def __init__(self, spider_path: Path, *, browser_channel: str | None = "chrome"):
        self.spider_path = spider_path.resolve()
        self.browser_channel = browser_channel
        if not self.spider_path.exists():
            raise FileNotFoundError(f"DouYin_Spider path does not exist: {self.spider_path}")
        if str(self.spider_path) not in sys.path:
            sys.path.insert(0, str(self.spider_path))
        install_requests_ipv4_only()
        install_requests_default_timeout()

    def generate_anonymous_cookie(
        self,
        *,
        seed_user_url: str = DEFAULT_SEED_USER_URL,
        headed: bool = False,
        timeout: int = 30,
    ) -> str:
        webid: str | None = None

        def handle_request(request) -> None:
            nonlocal webid
            url = request.url
            if url.startswith("https://www.douyin.com/aweme/v1/web/user/profile/other/"):
                query_params = parse_qs(urlparse(url).query)
                webid = (query_params.get("webid") or [None])[0]

        logger.info(
            "generating anonymous douyin cookie from {} browser_channel={}",
            seed_user_url,
            self.browser_channel or "playwright-default",
        )
        with sync_playwright() as p:
            launch_options: dict[str, Any] = {
                "headless": not headed,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if self.browser_channel:
                launch_options["channel"] = self.browser_channel
            browser = p.chromium.launch(**launch_options)
            context = browser.new_context(locale="zh-CN", viewport={"width": 1365, "height": 900})
            page = context.new_page()
            page.on("request", handle_request)
            page.goto(seed_user_url, wait_until="domcontentloaded", timeout=45000)
            for _ in range(timeout):
                if webid:
                    break
                page.wait_for_timeout(1000)
            page_cookies = context.cookies(["https://www.douyin.com", "https://douyin.com"])
            browser.close()

        cookie_dict = {cookie["name"]: cookie["value"] for cookie in page_cookies if cookie.get("name")}
        if webid and "s_v_web_id" not in cookie_dict:
            cookie_dict["s_v_web_id"] = webid
        if "s_v_web_id" not in cookie_dict:
            raise RuntimeError("failed to generate anonymous cookie: missing s_v_web_id")
        logger.info(
            "anonymous cookie generated cookie_keys={} has_webid={}",
            sorted(cookie_dict.keys()),
            bool(webid),
        )
        return "; ".join(f"{key}={value}" for key, value in cookie_dict.items())

    def crawl_user(
        self,
        *,
        cookie: str,
        user_url: str,
        result_dir: Path,
        max_pages: int,
        max_comments_per_video: int,
        include_replies: bool,
        sleep_seconds: float,
    ) -> dict[str, Any]:
        user_url = normalize_user_url(user_url)
        if max_pages < 1:
            raise ValueError("max_pages must be >= 1")
        result_dir.mkdir(parents=True, exist_ok=True)
        for file_name in ("user.json", "videos.jsonl", "comments.jsonl", "summary.json"):
            output_file = result_dir / file_name
            if output_file.exists():
                output_file.unlink()

        auth = self._build_auth(cookie)
        douyin_api = self._api()
        sec_uid = sec_uid_from_user_url(user_url)
        logger.info(
            "crawl user started sec_uid={} max_pages={} max_comments_per_video={} include_replies={} result_dir={}",
            sec_uid,
            max_pages,
            max_comments_per_video,
            include_replies,
            result_dir,
        )

        user_payload = douyin_api.get_user_info(auth, user_url)
        user_summary = self._summarize_user(user_payload, user_url)
        logger.info(
            "user info fetched sec_uid={} status_code={} nickname={} aweme_count={}",
            sec_uid,
            user_summary.get("raw_status_code"),
            user_summary.get("nickname"),
            user_summary.get("aweme_count"),
        )
        write_json(result_dir / "user.json", user_summary)

        awemes = self._fetch_aweme_pages(douyin_api, auth, user_url, max_pages, sleep_seconds)
        videos = [self._summarize_aweme(item) for item in awemes]
        append_jsonl(result_dir / "videos.jsonl", videos)
        logger.info("videos written sec_uid={} count={} path={}", sec_uid, len(videos), result_dir / "videos.jsonl")

        total_comments = 0
        comments_with_pictures = 0
        if max_comments_per_video == 0:
            (result_dir / "comments.jsonl").touch()
            logger.info("comment fetching skipped because max_comments_per_video=0")
        for video in videos:
            if max_comments_per_video == 0:
                break
            comments = self._fetch_comments(
                douyin_api=douyin_api,
                auth=auth,
                aweme_id=video["aweme_id"],
                max_comments=max_comments_per_video,
                include_replies=include_replies,
                sleep_seconds=sleep_seconds,
            )
            rows = [
                self._summarize_comment(comment, parent_comment_id=comment.get("_parent_comment_id", "0"))
                for comment in comments
            ]
            video_picture_comments = sum(1 for row in rows if row["picture_urls"])
            total_comments += len(rows)
            comments_with_pictures += video_picture_comments
            append_jsonl(result_dir / "comments.jsonl", rows)
            logger.info(
                "comments written aweme={} count={} picture_comments={} path={}",
                video["aweme_id"],
                len(rows),
                video_picture_comments,
                result_dir / "comments.jsonl",
            )
            time.sleep(sleep_seconds)

        summary = {
            "user_url": user_url,
            "result_dir": str(result_dir),
            "video_count": len(videos),
            "comment_count": total_comments,
            "comments_with_pictures": comments_with_pictures,
        }
        write_json(result_dir / "summary.json", summary)
        logger.info("crawl user finished sec_uid={} summary={}", sec_uid, summary)
        return summary

    def crawl_aweme(
        self,
        *,
        cookie: str,
        aweme_id: str,
        result_dir: Path,
        max_comments_per_video: int,
        include_replies: bool,
        sleep_seconds: float,
    ) -> dict[str, Any]:
        aweme_id = normalize_aweme_id(aweme_id)
        video_url = aweme_url_from_id(aweme_id)
        result_dir.mkdir(parents=True, exist_ok=True)
        for file_name in ("user.json", "videos.jsonl", "comments.jsonl", "summary.json"):
            output_file = result_dir / file_name
            if output_file.exists():
                output_file.unlink()

        auth = self._build_auth(cookie)
        douyin_api = self._api()
        logger.info(
            "crawl aweme started aweme={} max_comments_per_video={} include_replies={} result_dir={}",
            aweme_id,
            max_comments_per_video,
            include_replies,
            result_dir,
        )

        work_payload = douyin_api.get_work_info(auth, video_url)
        aweme = work_payload.get("aweme_detail") or {}
        if not aweme:
            raise RuntimeError(
                f"failed to fetch aweme detail aweme={aweme_id} status_code={work_payload.get('status_code')}"
            )
        video = self._summarize_aweme(aweme)
        append_jsonl(result_dir / "videos.jsonl", [video])
        write_json(result_dir / "user.json", self._summarize_aweme_author(aweme))
        logger.info(
            "aweme detail written aweme={} status_code={} path={}",
            aweme_id,
            work_payload.get("status_code"),
            result_dir / "videos.jsonl",
        )

        total_comments = 0
        comments_with_pictures = 0
        if max_comments_per_video == 0:
            (result_dir / "comments.jsonl").touch()
            logger.info("comment fetching skipped because max_comments_per_video=0")
        else:
            comments = self._fetch_comments(
                douyin_api=douyin_api,
                auth=auth,
                aweme_id=aweme_id,
                max_comments=max_comments_per_video,
                include_replies=include_replies,
                sleep_seconds=sleep_seconds,
            )
            rows = [
                self._summarize_comment(comment, parent_comment_id=comment.get("_parent_comment_id", "0"))
                for comment in comments
            ]
            total_comments = len(rows)
            comments_with_pictures = sum(1 for row in rows if row["picture_urls"])
            append_jsonl(result_dir / "comments.jsonl", rows)
            logger.info(
                "comments written aweme={} count={} picture_comments={} path={}",
                aweme_id,
                total_comments,
                comments_with_pictures,
                result_dir / "comments.jsonl",
            )

        summary = {
            "mode": "aweme",
            "aweme_id": aweme_id,
            "aweme_url": video_url,
            "result_dir": str(result_dir),
            "video_count": 1,
            "comment_count": total_comments,
            "comments_with_pictures": comments_with_pictures,
        }
        write_json(result_dir / "summary.json", summary)
        logger.info("crawl aweme finished aweme={} summary={}", aweme_id, summary)
        return summary

    def _build_auth(self, cookie: str):
        from builder.auth import DouyinAuth

        auth = DouyinAuth()
        auth.perepare_auth(cookie, "", "")
        if "s_v_web_id" not in auth.cookie:
            raise ValueError("cookie missing s_v_web_id; refresh or replace the account cookie")
        return auth

    def _api(self):
        from dy_apis.douyin_api import DouyinAPI

        return DouyinAPI

    def _fetch_aweme_pages(self, douyin_api, auth, user_url: str, max_pages: int, sleep_seconds: float) -> list[dict[str, Any]]:
        max_cursor = "0"
        page = 0
        awemes: list[dict[str, Any]] = []
        while True:
            if max_pages > 0 and page >= max_pages:
                break
            payload = douyin_api.get_user_work_info(auth, user_url, max_cursor)
            items = payload.get("aweme_list") or []
            logger.info(
                "aweme page fetched sec_uid={} page={} cursor={} count={} has_more={} status_code={}",
                sec_uid_from_user_url(user_url),
                page + 1,
                max_cursor,
                len(items),
                payload.get("has_more"),
                payload.get("status_code"),
            )
            awemes.extend(items)
            page += 1
            if payload.get("has_more") != 1 or not items:
                break
            max_cursor = str(payload.get("max_cursor") or "0")
            time.sleep(sleep_seconds)
        return awemes

    def _fetch_comments(
        self,
        *,
        douyin_api,
        auth,
        aweme_id: str,
        max_comments: int,
        include_replies: bool,
        sleep_seconds: float,
    ) -> list[dict[str, Any]]:
        video_url = f"https://www.douyin.com/video/{aweme_id}"
        cursor = "0"
        comments: list[dict[str, Any]] = []
        if max_comments <= 0:
            logger.info("comment fetch skipped aweme={} max_comments={}", aweme_id, max_comments)
            return comments
        while True:
            if max_comments > 0 and len(comments) >= max_comments:
                break
            payload = douyin_api.get_work_out_comment(auth, video_url, cursor)
            page_comments = payload.get("comments") or []
            if max_comments > 0:
                page_comments = page_comments[: max_comments - len(comments)]
            comments.extend(page_comments)
            logger.info(
                "comment page fetched aweme={} cursor={} count={} total={} has_more={} status_code={}",
                aweme_id,
                cursor,
                len(page_comments),
                len(comments),
                payload.get("has_more"),
                payload.get("status_code"),
            )
            if payload.get("has_more") != 1 or not page_comments:
                break
            cursor = str(payload.get("cursor") or "0")
            time.sleep(sleep_seconds)

        if not include_replies:
            return comments

        all_comments = list(comments)
        for comment in comments:
            if (comment.get("reply_comment_total") or 0) <= 0:
                continue
            try:
                replies = douyin_api.get_work_all_inner_comment(auth, comment)
            except Exception as exc:
                logger.warning("failed to fetch replies cid={} err={}", comment.get("cid"), exc)
                continue
            for reply in replies:
                reply["_parent_comment_id"] = comment.get("cid")
            all_comments.extend(replies)
            time.sleep(sleep_seconds)
        return all_comments

    def _summarize_user(self, user_payload: dict[str, Any], user_url: str) -> dict[str, Any]:
        user = user_payload.get("user") or {}
        avatar = user.get("avatar_thumb") or user.get("avatar_300x300") or {}
        return {
            "sec_uid": sec_uid_from_user_url(user_url),
            "uid": user.get("uid"),
            "short_id": user.get("short_id"),
            "unique_id": user.get("unique_id"),
            "nickname": user.get("nickname"),
            "signature": user.get("signature"),
            "avatar": (avatar.get("url_list") or [""])[0],
            "following_count": user.get("following_count"),
            "follower_count": user.get("follower_count"),
            "max_follower_count": user.get("max_follower_count"),
            "total_favorited": user.get("total_favorited"),
            "aweme_count": user.get("aweme_count"),
            "ip_location": user.get("ip_location"),
            "raw_status_code": user_payload.get("status_code"),
        }

    def _summarize_aweme_author(self, aweme: dict[str, Any]) -> dict[str, Any]:
        user = aweme.get("author") or {}
        avatar = user.get("avatar_thumb") or user.get("avatar_300x300") or {}
        return {
            "sec_uid": user.get("sec_uid"),
            "uid": user.get("uid"),
            "short_id": user.get("short_id"),
            "unique_id": user.get("unique_id"),
            "nickname": user.get("nickname"),
            "signature": user.get("signature"),
            "avatar": (avatar.get("url_list") or [""])[0],
            "following_count": user.get("following_count"),
            "follower_count": user.get("follower_count"),
            "max_follower_count": user.get("max_follower_count"),
            "total_favorited": user.get("total_favorited"),
            "aweme_count": user.get("aweme_count"),
            "ip_location": user.get("ip_location"),
        }

    def _summarize_aweme(self, aweme: dict[str, Any]) -> dict[str, Any]:
        author = aweme.get("author") or {}
        statistics = aweme.get("statistics") or {}
        video = aweme.get("video") or {}
        cover = video.get("cover") or video.get("origin_cover") or {}
        play_addr = video.get("play_addr") or video.get("play_addr_h264") or {}
        aweme_id = aweme.get("aweme_id")
        return {
            "aweme_id": aweme_id,
            "aweme_url": f"https://www.douyin.com/video/{aweme_id}",
            "aweme_type": aweme.get("aweme_type"),
            "desc": aweme.get("desc"),
            "create_time": aweme.get("create_time"),
            "author_sec_uid": author.get("sec_uid"),
            "author_uid": author.get("uid"),
            "author_nickname": author.get("nickname"),
            "digg_count": statistics.get("digg_count"),
            "comment_count": statistics.get("comment_count"),
            "collect_count": statistics.get("collect_count"),
            "share_count": statistics.get("share_count"),
            "cover_url": (cover.get("url_list") or [""])[0],
            "video_urls": extract_url_list(play_addr),
            "note_image_urls": extract_note_image_urls(aweme),
        }

    def _summarize_comment(self, comment: dict[str, Any], parent_comment_id: str = "0") -> dict[str, Any]:
        user = comment.get("user") or {}
        avatar = (
            user.get("avatar_medium")
            or user.get("avatar_300x300")
            or user.get("avatar_168x168")
            or user.get("avatar_thumb")
            or {}
        )
        return {
            "comment_id": comment.get("cid"),
            "parent_comment_id": parent_comment_id,
            "aweme_id": comment.get("aweme_id"),
            "content": comment.get("text"),
            "create_time": comment.get("create_time"),
            "ip_location": comment.get("ip_label"),
            "like_count": comment.get("digg_count") or 0,
            "reply_comment_total": comment.get("reply_comment_total") or 0,
            "user_id": user.get("uid"),
            "sec_uid": user.get("sec_uid"),
            "short_user_id": user.get("short_id"),
            "user_unique_id": user.get("unique_id"),
            "nickname": user.get("nickname"),
            "avatar": (avatar.get("url_list") or [""])[0],
            "picture_urls": extract_comment_image_urls(comment),
        }
