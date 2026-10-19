from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import shutil
import sqlite3
import time
import tracemalloc
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .cleaning import (
    clean_comment,
    is_label_leakage_comment,
    normalize_text,
    optional_int,
    parse_datetime,
)
from .io_utils import read_jsonl, utc_now_iso
from .schemas import COMMENTS_SCHEMA, DUPLICATE_PAIRS_SCHEMA, POSTS_SCHEMA


LOGGER = logging.getLogger("so_recommender.python_pipeline")


@dataclass
class EntityStats:
    read: int = 0
    written: int = 0
    duplicate: int = 0
    invalid: int = 0
    leakage: int = 0
    orphan: int = 0


class KeyIndex:
    """Disk-backed id index so millions of ids do not have to stay in RAM."""

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.executescript(
            """
            CREATE TABLE post_ids (id INTEGER PRIMARY KEY);
            CREATE TABLE comment_ids (id INTEGER PRIMARY KEY);
            CREATE TABLE duplicate_pairs (
                query_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                PRIMARY KEY(query_id, target_id)
            );
            """
        )

    def add_post(self, value: int) -> bool:
        return self._insert("INSERT OR IGNORE INTO post_ids(id) VALUES (?)", (value,))

    def has_post(self, value: int) -> bool:
        row = self.connection.execute("SELECT 1 FROM post_ids WHERE id=?", (value,)).fetchone()
        return row is not None

    def add_comment(self, value: int) -> bool:
        return self._insert("INSERT OR IGNORE INTO comment_ids(id) VALUES (?)", (value,))

    def add_pair(self, query_id: int, target_id: int) -> bool:
        return self._insert(
            "INSERT OR IGNORE INTO duplicate_pairs(query_id, target_id) VALUES (?, ?)",
            (query_id, target_id),
        )

    def _insert(self, statement: str, values: tuple[int, ...]) -> bool:
        cursor = self.connection.execute(statement, values)
        return cursor.rowcount == 1

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()


class PartitionedParquetWriter:
    def __init__(self, directory: Path, schema: pa.Schema, batch_size: int) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.schema = schema
        self.batch_size = batch_size
        self.buffer: list[dict[str, Any]] = []
        self.part_number = 0

    def add(self, record: dict[str, Any]) -> None:
        self.buffer.append(record)
        if len(self.buffer) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self.buffer:
            return
        table = pa.Table.from_pylist(self.buffer, schema=self.schema)
        pq.write_table(
            table,
            self.directory / f"part-{self.part_number:05d}.parquet",
            compression="zstd",
            use_dictionary=True,
        )
        self.buffer.clear()
        self.part_number += 1

    def close(self) -> None:
        self.flush()
        if self.part_number == 0:
            empty = pa.Table.from_pylist([], schema=self.schema)
            pq.write_table(
                empty,
                self.directory / "part-00000.parquet",
                compression="zstd",
            )
            self.part_number = 1


def _clean_post(record: dict[str, Any]) -> dict[str, Any] | None:
    post_id = optional_int(record.get("post_id"))
    title = normalize_text(record.get("title"))
    body = normalize_text(record.get("body_text"))
    crawled_at = parse_datetime(record.get("crawled_at"))
    if not post_id or not title or not body or crawled_at is None:
        return None
    raw_tags = record.get("tags") or []
    tags = sorted({normalize_text(tag).lower() for tag in raw_tags if normalize_text(tag)})
    return {
        "post_id": post_id,
        "url": normalize_text(record.get("url")),
        "title": title,
        "body_clean": body,
        "tags": tags,
        "score": optional_int(record.get("score")),
        "creation_date": parse_datetime(record.get("creation_date")),
        "comment_count_crawled": optional_int(record.get("comment_count_page")) or 0,
        "comments_complete": bool(record.get("comments_complete", False)),
        "is_duplicate": bool(record.get("is_duplicate", False)),
        "crawled_at": crawled_at,
    }


def _clean_comment(record: dict[str, Any]) -> dict[str, Any] | None:
    comment_id = optional_int(record.get("comment_id"))
    post_id = optional_int(record.get("post_id"))
    text = clean_comment(record.get("text"))
    crawled_at = parse_datetime(record.get("crawled_at"))
    if not comment_id or not post_id or not text or crawled_at is None:
        return None
    return {
        "comment_id": comment_id,
        "post_id": post_id,
        "text_clean": text,
        "score": optional_int(record.get("score")),
        "creation_date": parse_datetime(record.get("creation_date")),
        "position": optional_int(record.get("position")),
        "crawled_at": crawled_at,
    }


def _clean_pair(record: dict[str, Any]) -> dict[str, Any] | None:
    query_id = optional_int(record.get("query_post_id"))
    target_id = optional_int(record.get("relevant_post_id"))
    crawled_at = parse_datetime(record.get("crawled_at"))
    if not query_id or not target_id or query_id == target_id or crawled_at is None:
        return None
    return {
        "query_post_id": query_id,
        "relevant_post_id": target_id,
        "crawled_at": crawled_at,
    }


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _prepare_output(output_dir: Path, overwrite: bool) -> None:
    resolved = output_dir.resolve()
    if str(resolved) == resolved.anchor or len(resolved.parts) < 3:
        raise ValueError(f"Unsafe output directory: {resolved}")
    if output_dir.exists() and any(output_dir.iterdir()):
        if not overwrite:
            raise FileExistsError(f"{output_dir} is not empty; pass --overwrite")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def run_pipeline(raw_dir: Path, output_dir: Path, batch_size: int, overwrite: bool) -> dict[str, Any]:
    _prepare_output(output_dir, overwrite)
    started = time.perf_counter()
    tracemalloc.start()
    key_index = KeyIndex(output_dir / "_key_index.sqlite3")
    post_writer = PartitionedParquetWriter(output_dir / "posts", POSTS_SCHEMA, batch_size)
    comment_writer = PartitionedParquetWriter(output_dir / "comments", COMMENTS_SCHEMA, batch_size)
    pair_writer = PartitionedParquetWriter(
        output_dir / "duplicate_pairs", DUPLICATE_PAIRS_SCHEMA, batch_size
    )
    post_stats = EntityStats()
    comment_stats = EntityStats()
    pair_stats = EntityStats()

    try:
        for raw in read_jsonl(raw_dir / "posts.jsonl"):
            post_stats.read += 1
            cleaned = _clean_post(raw)
            if cleaned is None:
                post_stats.invalid += 1
                continue
            if not key_index.add_post(cleaned["post_id"]):
                post_stats.duplicate += 1
                continue
            post_writer.add(cleaned)
            post_stats.written += 1
        post_writer.close()
        key_index.connection.commit()

        for raw in read_jsonl(raw_dir / "comments.jsonl"):
            comment_stats.read += 1
            if is_label_leakage_comment(raw.get("text")):
                comment_stats.leakage += 1
                continue
            cleaned = _clean_comment(raw)
            if cleaned is None:
                comment_stats.invalid += 1
                continue
            if not key_index.has_post(cleaned["post_id"]):
                comment_stats.orphan += 1
                continue
            if not key_index.add_comment(cleaned["comment_id"]):
                comment_stats.duplicate += 1
                continue
            comment_writer.add(cleaned)
            comment_stats.written += 1
        comment_writer.close()

        for raw in read_jsonl(raw_dir / "duplicate_pairs.jsonl"):
            pair_stats.read += 1
            cleaned = _clean_pair(raw)
            if cleaned is None:
                pair_stats.invalid += 1
                continue
            if not key_index.has_post(cleaned["query_post_id"]):
                pair_stats.orphan += 1
                continue
            if not key_index.add_pair(cleaned["query_post_id"], cleaned["relevant_post_id"]):
                pair_stats.duplicate += 1
                continue
            pair_writer.add(cleaned)
            pair_stats.written += 1
        pair_writer.close()
    finally:
        key_index.close()
        post_writer.close()
        comment_writer.close()
        pair_writer.close()

    _, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    index_path = output_dir / "_key_index.sqlite3"
    if index_path.exists():
        index_path.unlink()

    summary: dict[str, Any] = {
        "pipeline": "python",
        "finished_at": utc_now_iso(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "python_peak_allocated_bytes": peak_memory,
        "input_bytes": _directory_size(raw_dir),
        "output_bytes": _directory_size(output_dir),
        "posts": post_stats.__dict__,
        "comments": comment_stats.__dict__,
        "duplicate_pairs": pair_stats.__dict__,
    }
    (output_dir / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clean raw Stack Overflow JSONL with Python")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/clean/python"))
    parser.add_argument("--batch-size", type=int, default=25_000)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")
    summary = run_pipeline(args.raw_dir, args.output_dir, args.batch_size, args.overwrite)
    LOGGER.info("Completed: %s", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
