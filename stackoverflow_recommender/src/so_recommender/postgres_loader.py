from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any, Iterator

import pyarrow.dataset as ds


POST_SQL = """
INSERT INTO so_clean.posts (
    post_id, url, title, body_clean, tags, score, creation_date,
    comment_count_crawled, comments_complete, is_duplicate, crawled_at
) VALUES (
    %(post_id)s, %(url)s, %(title)s, %(body_clean)s, %(tags)s, %(score)s,
    %(creation_date)s, %(comment_count_crawled)s, %(comments_complete)s,
    %(is_duplicate)s, %(crawled_at)s
)
ON CONFLICT (post_id) DO UPDATE SET
    url = EXCLUDED.url,
    title = EXCLUDED.title,
    body_clean = EXCLUDED.body_clean,
    tags = EXCLUDED.tags,
    score = EXCLUDED.score,
    creation_date = EXCLUDED.creation_date,
    comment_count_crawled = EXCLUDED.comment_count_crawled,
    comments_complete = EXCLUDED.comments_complete,
    is_duplicate = EXCLUDED.is_duplicate,
    crawled_at = EXCLUDED.crawled_at
"""

COMMENT_SQL = """
INSERT INTO so_clean.comments (
    comment_id, post_id, text_clean, score, creation_date, position, crawled_at
) VALUES (
    %(comment_id)s, %(post_id)s, %(text_clean)s, %(score)s,
    %(creation_date)s, %(position)s, %(crawled_at)s
)
ON CONFLICT (comment_id) DO UPDATE SET
    post_id = EXCLUDED.post_id,
    text_clean = EXCLUDED.text_clean,
    score = EXCLUDED.score,
    creation_date = EXCLUDED.creation_date,
    position = EXCLUDED.position,
    crawled_at = EXCLUDED.crawled_at
"""

PAIR_SQL = """
INSERT INTO so_clean.duplicate_pairs (query_post_id, relevant_post_id, crawled_at)
VALUES (%(query_post_id)s, %(relevant_post_id)s, %(crawled_at)s)
ON CONFLICT (query_post_id, relevant_post_id) DO UPDATE SET
    crawled_at = EXCLUDED.crawled_at
"""


def _batches(directory: Path, batch_size: int) -> Iterator[list[dict[str, Any]]]:
    dataset = ds.dataset(directory, format="parquet")
    for batch in dataset.to_batches(batch_size=batch_size):
        yield batch.to_pylist()


def load_postgres(clean_dir: Path, database_url: str, batch_size: int) -> dict[str, Any]:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Install the postgres extra: pip install -e .[postgres]") from exc

    started = time.perf_counter()
    counts = {"posts": 0, "comments": 0, "duplicate_pairs": 0}
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            for rows in _batches(clean_dir / "posts", batch_size):
                cursor.executemany(POST_SQL, rows)
                counts["posts"] += len(rows)
                connection.commit()
            for rows in _batches(clean_dir / "comments", batch_size):
                cursor.executemany(COMMENT_SQL, rows)
                counts["comments"] += len(rows)
                connection.commit()
            for rows in _batches(clean_dir / "duplicate_pairs", batch_size):
                cursor.executemany(PAIR_SQL, rows)
                counts["duplicate_pairs"] += len(rows)
                connection.commit()
    return {
        "pipeline": "postgres_loader",
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "rows": counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Load cleaned Parquet into PostgreSQL")
    parser.add_argument("--clean-dir", type=Path, default=Path("data/clean/python"))
    parser.add_argument(
        "--database-url",
        default="postgresql://stackoverflow:stackoverflow@localhost:5432/stackoverflow",
    )
    parser.add_argument("--batch-size", type=int, default=5_000)
    args = parser.parse_args()
    print(json.dumps(load_postgres(args.clean_dir, args.database_url, args.batch_size), indent=2))


if __name__ == "__main__":
    main()

