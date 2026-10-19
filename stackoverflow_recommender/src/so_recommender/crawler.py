from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import logging
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote, urljoin
from urllib.robotparser import RobotFileParser

import requests

from .config import CrawlConfig, load_crawl_config
from .io_utils import append_jsonl, utc_now_iso
from .parser import parse_listing, parse_question_page


LOGGER = logging.getLogger("so_recommender.crawler")
BLOCK_MARKERS = ("cf-chl-", "captcha", "human verification", "just a moment...")


class CrawlBlocked(RuntimeError):
    """Raised when the site asks the crawler to stop or verify itself."""


class CrawlState:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS listings (
                url TEXT PRIMARY KEY,
                tag TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                http_status INTEGER,
                error TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS questions (
                post_id INTEGER PRIMARY KEY,
                url TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                http_status INTEGER,
                error TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def seed_listing(self, url: str, tag: str, page_number: int) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO listings(url, tag, page_number, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (url, tag, page_number, utc_now_iso()),
        )
        self.connection.commit()

    def pending_listings(self) -> list[tuple[str, str, int]]:
        return list(
            self.connection.execute(
                """
                SELECT url, tag, page_number FROM listings
                WHERE status = 'pending' ORDER BY tag, page_number
                """
            )
        )

    def add_question(self, post_id: int, url: str, source: str) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO questions(post_id, url, source, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (post_id, url, source, utc_now_iso()),
        )
        self.connection.commit()

    def pending_questions(self, limit: int) -> list[tuple[int, str]]:
        return list(
            self.connection.execute(
                """
                SELECT post_id, url FROM questions
                WHERE status = 'pending' ORDER BY post_id DESC LIMIT ?
                """,
                (limit,),
            )
        )

    def mark_listing(self, url: str, status: str, http_status: int | None, error: str | None) -> None:
        self.connection.execute(
            """
            UPDATE listings SET status=?, http_status=?, error=?, updated_at=? WHERE url=?
            """,
            (status, http_status, error, utc_now_iso(), url),
        )
        self.connection.commit()

    def mark_question(
        self,
        post_id: int,
        status: str,
        http_status: int | None,
        error: str | None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE questions SET status=?, http_status=?, error=?, updated_at=? WHERE post_id=?
            """,
            (status, http_status, error, utc_now_iso(), post_id),
        )
        self.connection.commit()

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for table in ("listings", "questions"):
            rows = self.connection.execute(
                f"SELECT status, COUNT(*) FROM {table} GROUP BY status"  # noqa: S608
            )
            for status, count in rows:
                result[f"{table}_{status}"] = int(count)
        return result


class PoliteHttpClient:
    def __init__(self, config: CrawlConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.8",
            }
        )
        self.last_request_at = 0.0
        self.robots = RobotFileParser()
        self.robots.set_url(urljoin(config.base_url, "/robots.txt"))
        if config.respect_robots_txt:
            response = self.session.get(
                self.robots.url,
                timeout=config.request_timeout_seconds,
            )
            response.raise_for_status()
            self.robots.parse(response.text.splitlines())

    def get(self, url: str) -> requests.Response:
        if self.config.respect_robots_txt and not self.robots.can_fetch(self.config.user_agent, url):
            raise PermissionError(f"robots.txt does not allow crawling {url}")
        elapsed = time.monotonic() - self.last_request_at
        if elapsed < self.config.request_delay_seconds:
            time.sleep(self.config.request_delay_seconds - elapsed)
        response = self.session.get(url, timeout=self.config.request_timeout_seconds)
        self.last_request_at = time.monotonic()
        if response.status_code in (403, 429):
            raise CrawlBlocked(f"HTTP {response.status_code} from {url}; crawler stopped")
        response.raise_for_status()
        lower_sample = response.text[:100_000].lower()
        if any(marker in lower_sample for marker in BLOCK_MARKERS):
            raise CrawlBlocked(f"Verification page detected at {url}; crawler stopped")
        return response


def _save_html(raw_dir: Path, kind: str, key: str, html: str) -> Path:
    safe_key = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16] if kind == "listings" else key
    path = raw_dir / "html" / kind / f"{safe_key}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def _listing_url(config: CrawlConfig, tag: str, page: int) -> str:
    return (
        f"{config.base_url}/questions/tagged/{quote(tag)}"
        f"?tab=Newest&page={page}&pagesize={config.questions_per_listing}"
    )


def crawl(config: CrawlConfig) -> dict[str, int]:
    if "replace-with-your-email" in config.user_agent:
        raise ValueError(
            "Set a real contact address in config/crawler.toml before crawling"
        )
    config.raw_dir.mkdir(parents=True, exist_ok=True)
    state = CrawlState(config.raw_dir / "state.sqlite3")
    try:
        for tag in config.tags:
            for page in range(1, config.listing_pages_per_tag + 1):
                state.seed_listing(_listing_url(config, tag, page), tag, page)

        client = PoliteHttpClient(config)
        for url, tag, page_number in state.pending_listings():
            LOGGER.info("Listing %s page %s", tag, page_number)
            try:
                response = client.get(url)
                if config.save_html:
                    _save_html(config.raw_dir, "listings", url, response.text)
                for post_id, question_url in parse_listing(response.text, config.base_url):
                    state.add_question(post_id, question_url, f"tag:{tag}")
                state.mark_listing(url, "done", response.status_code, None)
            except CrawlBlocked:
                state.mark_listing(url, "blocked", None, "site requested crawler stop")
                raise
            except Exception as exc:  # keep state for a resumable run
                LOGGER.exception("Listing failed: %s", url)
                state.mark_listing(url, "failed", None, str(exc)[:500])

        completed_this_run = 0
        while completed_this_run < config.max_questions:
            pending = state.pending_questions(config.max_questions - completed_this_run)
            if not pending:
                break
            for expected_post_id, url in pending:
                LOGGER.info("Question %s", expected_post_id)
                try:
                    response = client.get(url)
                    crawled_at = utc_now_iso()
                    post, comments, pairs = parse_question_page(response.text, url, crawled_at)
                    if post["post_id"] != expected_post_id:
                        raise ValueError(
                            f"Expected question {expected_post_id}, parsed {post['post_id']}"
                        )
                    if config.save_html:
                        _save_html(config.raw_dir, "questions", str(expected_post_id), response.text)
                    append_jsonl(config.raw_dir / "posts.jsonl", [post])
                    append_jsonl(config.raw_dir / "comments.jsonl", comments)
                    append_jsonl(config.raw_dir / "duplicate_pairs.jsonl", pairs)
                    for target_id in post["duplicate_target_ids"]:
                        state.add_question(
                            int(target_id),
                            f"{config.base_url}/questions/{target_id}",
                            f"duplicate-target:{expected_post_id}",
                        )
                    state.mark_question(expected_post_id, "done", response.status_code, None)
                    completed_this_run += 1
                except CrawlBlocked:
                    state.mark_question(
                        expected_post_id,
                        "blocked",
                        None,
                        "site requested crawler stop",
                    )
                    raise
                except Exception as exc:
                    LOGGER.exception("Question failed: %s", url)
                    state.mark_question(expected_post_id, "failed", None, str(exc)[:500])
        counts = state.counts()
        counts["questions_completed_this_run"] = completed_this_run
        return counts
    finally:
        state.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crawl public Stack Overflow question pages")
    parser.add_argument("--config", type=Path, default=Path("config/crawler.toml"))
    parser.add_argument("--max-questions", type=int, help="Override max questions for this run")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    config = load_crawl_config(args.config)
    if args.max_questions is not None:
        if args.max_questions <= 0:
            raise SystemExit("--max-questions must be positive")
        config = replace(config, max_questions=args.max_questions)
    try:
        summary = crawl(config)
    except CrawlBlocked as exc:
        LOGGER.error("%s", exc)
        raise SystemExit(2) from exc
    LOGGER.info("Completed: %s", summary)


if __name__ == "__main__":
    main()
