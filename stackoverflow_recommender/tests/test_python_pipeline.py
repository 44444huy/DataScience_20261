import json

import pyarrow.dataset as ds

from so_recommender.python_pipeline import run_pipeline
from so_recommender.validate import validate_dataset


def _write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")


def test_pipeline_cleans_and_removes_label_leakage(tmp_path):
    raw = tmp_path / "raw"
    clean = tmp_path / "clean"
    raw.mkdir()
    crawled_at = "2026-10-17T00:00:00+00:00"
    _write_jsonl(
        raw / "posts.jsonl",
        [
            {
                "post_id": 101,
                "url": "https://stackoverflow.com/questions/101",
                "title": "  Example   question ",
                "body_text": "Body   text",
                "tags": ["Python", "python"],
                "score": 1,
                "comment_count_page": 2,
                "comments_complete": True,
                "is_duplicate": True,
                "crawled_at": crawled_at,
            },
            {
                "post_id": 205,
                "url": "https://stackoverflow.com/questions/205",
                "title": "Canonical question",
                "body_text": "Canonical body",
                "tags": ["python"],
                "score": 10,
                "comment_count_page": 0,
                "comments_complete": True,
                "is_duplicate": False,
                "crawled_at": crawled_at,
            },
        ],
    )
    _write_jsonl(
        raw / "comments.jsonl",
        [
            {
                "comment_id": 1,
                "post_id": 101,
                "text": "Helpful clarification",
                "position": 1,
                "crawled_at": crawled_at,
            },
            {
                "comment_id": 2,
                "post_id": 101,
                "text": "Possible duplicate of https://stackoverflow.com/questions/205",
                "position": 2,
                "crawled_at": crawled_at,
            },
        ],
    )
    _write_jsonl(
        raw / "duplicate_pairs.jsonl",
        [{"query_post_id": 101, "relevant_post_id": 205, "crawled_at": crawled_at}],
    )

    summary = run_pipeline(raw, clean, batch_size=1, overwrite=False)
    assert summary["posts"]["written"] == 2
    assert summary["comments"]["written"] == 1
    assert summary["comments"]["leakage"] == 1
    posts = ds.dataset(clean / "posts", format="parquet").to_table().to_pylist()
    assert posts[0]["title"] == "Example question"
    assert posts[0]["tags"] == ["python"]
    assert validate_dataset(clean).valid


def test_validator_rejects_pair_with_missing_relevant_post(tmp_path):
    raw = tmp_path / "raw"
    clean = tmp_path / "clean"
    raw.mkdir()
    crawled_at = "2026-10-17T00:00:00+00:00"
    _write_jsonl(
        raw / "posts.jsonl",
        [
            {
                "post_id": 101,
                "url": "https://stackoverflow.com/questions/101",
                "title": "Example question",
                "body_text": "Body text",
                "tags": ["python"],
                "crawled_at": crawled_at,
            }
        ],
    )
    _write_jsonl(raw / "comments.jsonl", [])
    _write_jsonl(
        raw / "duplicate_pairs.jsonl",
        [{"query_post_id": 101, "relevant_post_id": 999, "crawled_at": crawled_at}],
    )

    run_pipeline(raw, clean, batch_size=1, overwrite=False)
    report = validate_dataset(clean)
    assert report.missing_relevant_posts == 1
    assert not report.valid
