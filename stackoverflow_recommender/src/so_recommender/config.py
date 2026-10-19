from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class CrawlConfig:
    base_url: str
    tags: tuple[str, ...]
    listing_pages_per_tag: int
    questions_per_listing: int
    max_questions: int
    request_delay_seconds: float
    request_timeout_seconds: int
    user_agent: str
    respect_robots_txt: bool
    save_html: bool
    raw_dir: Path


def load_crawl_config(path: Path) -> CrawlConfig:
    with path.open("rb") as handle:
        document = tomllib.load(handle)

    crawl = document["crawl"]
    paths = document["paths"]
    return CrawlConfig(
        base_url=str(crawl["base_url"]).rstrip("/"),
        tags=tuple(str(tag) for tag in crawl["tags"]),
        listing_pages_per_tag=int(crawl["listing_pages_per_tag"]),
        questions_per_listing=int(crawl["questions_per_listing"]),
        max_questions=int(crawl["max_questions"]),
        request_delay_seconds=float(crawl["request_delay_seconds"]),
        request_timeout_seconds=int(crawl["request_timeout_seconds"]),
        user_agent=str(crawl["user_agent"]),
        respect_robots_txt=bool(crawl["respect_robots_txt"]),
        save_html=bool(crawl["save_html"]),
        raw_dir=Path(paths["raw_dir"]),
    )

