from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sqlite3
from typing import Iterator

import pyarrow.dataset as ds

from .cleaning import is_label_leakage_comment


@dataclass
class ValidationReport:
    dataset: str
    posts: int = 0
    comments: int = 0
    duplicate_pairs: int = 0
    duplicate_post_ids: int = 0
    duplicate_comment_ids: int = 0
    duplicate_pairs_repeated: int = 0
    orphan_comments: int = 0
    missing_query_posts: int = 0
    missing_relevant_posts: int = 0
    leakage_comments: int = 0

    @property
    def valid(self) -> bool:
        return not any(
            (
                self.duplicate_post_ids,
                self.duplicate_comment_ids,
                self.duplicate_pairs_repeated,
                self.orphan_comments,
                self.missing_query_posts,
                self.missing_relevant_posts,
                self.leakage_comments,
            )
        )


def _rows(directory: Path, columns: list[str], batch_size: int = 50_000) -> Iterator[dict]:
    dataset = ds.dataset(directory, format="parquet")
    for batch in dataset.to_batches(columns=columns, batch_size=batch_size):
        yield from batch.to_pylist()


def validate_dataset(root: Path) -> ValidationReport:
    report = ValidationReport(dataset=str(root.resolve()))
    index_path = root / ".validation.sqlite3"
    if index_path.exists():
        index_path.unlink()
    connection = sqlite3.connect(index_path)
    connection.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE posts(id INTEGER PRIMARY KEY);
        CREATE TABLE comments(id INTEGER PRIMARY KEY);
        CREATE TABLE pairs(query_id INTEGER, target_id INTEGER, PRIMARY KEY(query_id, target_id));
        """
    )
    try:
        for row in _rows(root / "posts", ["post_id"]):
            report.posts += 1
            cursor = connection.execute("INSERT OR IGNORE INTO posts VALUES (?)", (row["post_id"],))
            report.duplicate_post_ids += int(cursor.rowcount == 0)
        connection.commit()

        for row in _rows(root / "comments", ["comment_id", "post_id", "text_clean"]):
            report.comments += 1
            cursor = connection.execute(
                "INSERT OR IGNORE INTO comments VALUES (?)", (row["comment_id"],)
            )
            report.duplicate_comment_ids += int(cursor.rowcount == 0)
            exists = connection.execute(
                "SELECT 1 FROM posts WHERE id=?", (row["post_id"],)
            ).fetchone()
            report.orphan_comments += int(exists is None)
            report.leakage_comments += int(is_label_leakage_comment(row["text_clean"]))

        for row in _rows(root / "duplicate_pairs", ["query_post_id", "relevant_post_id"]):
            report.duplicate_pairs += 1
            cursor = connection.execute(
                "INSERT OR IGNORE INTO pairs VALUES (?, ?)",
                (row["query_post_id"], row["relevant_post_id"]),
            )
            report.duplicate_pairs_repeated += int(cursor.rowcount == 0)
            query_exists = connection.execute(
                "SELECT 1 FROM posts WHERE id=?", (row["query_post_id"],)
            ).fetchone()
            target_exists = connection.execute(
                "SELECT 1 FROM posts WHERE id=?", (row["relevant_post_id"],)
            ).fetchone()
            report.missing_query_posts += int(query_exists is None)
            report.missing_relevant_posts += int(target_exists is None)
    finally:
        connection.close()
        if index_path.exists():
            index_path.unlink()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate cleaned Stack Overflow Parquet")
    parser.add_argument("dataset", type=Path, nargs="+", help="One or more clean dataset roots")
    args = parser.parse_args()
    reports = [validate_dataset(path) for path in args.dataset]
    payload = [{**asdict(report), "valid": report.valid} for report in reports]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not all(report.valid for report in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
