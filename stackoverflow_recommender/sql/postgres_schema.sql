CREATE SCHEMA IF NOT EXISTS so_clean;

CREATE TABLE IF NOT EXISTS so_clean.posts (
    post_id BIGINT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    body_clean TEXT NOT NULL,
    tags TEXT[] NOT NULL DEFAULT '{}',
    score INTEGER,
    creation_date TIMESTAMPTZ,
    comment_count_crawled INTEGER NOT NULL DEFAULT 0,
    comments_complete BOOLEAN NOT NULL DEFAULT FALSE,
    is_duplicate BOOLEAN NOT NULL DEFAULT FALSE,
    crawled_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS so_clean.comments (
    comment_id BIGINT PRIMARY KEY,
    post_id BIGINT NOT NULL REFERENCES so_clean.posts(post_id),
    text_clean TEXT NOT NULL,
    score INTEGER,
    creation_date TIMESTAMPTZ,
    position INTEGER,
    crawled_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS comments_post_id_idx ON so_clean.comments(post_id);

CREATE TABLE IF NOT EXISTS so_clean.duplicate_pairs (
    query_post_id BIGINT NOT NULL REFERENCES so_clean.posts(post_id),
    relevant_post_id BIGINT NOT NULL,
    crawled_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (query_post_id, relevant_post_id)
);

CREATE INDEX IF NOT EXISTS duplicate_target_idx
    ON so_clean.duplicate_pairs(relevant_post_id);
