from __future__ import annotations

import pyarrow as pa


UTC_TIMESTAMP = pa.timestamp("us", tz="UTC")

POSTS_SCHEMA = pa.schema(
    [
        pa.field("post_id", pa.int64(), nullable=False),
        pa.field("url", pa.string(), nullable=False),
        pa.field("title", pa.string(), nullable=False),
        pa.field("body_clean", pa.string(), nullable=False),
        pa.field("tags", pa.list_(pa.string()), nullable=False),
        pa.field("score", pa.int32()),
        pa.field("creation_date", UTC_TIMESTAMP),
        pa.field("comment_count_crawled", pa.int32(), nullable=False),
        pa.field("comments_complete", pa.bool_(), nullable=False),
        pa.field("is_duplicate", pa.bool_(), nullable=False),
        pa.field("crawled_at", UTC_TIMESTAMP, nullable=False),
    ]
)

COMMENTS_SCHEMA = pa.schema(
    [
        pa.field("comment_id", pa.int64(), nullable=False),
        pa.field("post_id", pa.int64(), nullable=False),
        pa.field("text_clean", pa.string(), nullable=False),
        pa.field("score", pa.int32()),
        pa.field("creation_date", UTC_TIMESTAMP),
        pa.field("position", pa.int32()),
        pa.field("crawled_at", UTC_TIMESTAMP, nullable=False),
    ]
)

DUPLICATE_PAIRS_SCHEMA = pa.schema(
    [
        pa.field("query_post_id", pa.int64(), nullable=False),
        pa.field("relevant_post_id", pa.int64(), nullable=False),
        pa.field("crawled_at", UTC_TIMESTAMP, nullable=False),
    ]
)

