from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from pyspark.sql import DataFrame, SparkSession, functions as F, types as T


POST_RAW_SCHEMA = T.StructType(
    [
        T.StructField("post_id", T.LongType()),
        T.StructField("url", T.StringType()),
        T.StructField("title", T.StringType()),
        T.StructField("body_html", T.StringType()),
        T.StructField("body_text", T.StringType()),
        T.StructField("tags", T.ArrayType(T.StringType())),
        T.StructField("score", T.IntegerType()),
        T.StructField("creation_date", T.StringType()),
        T.StructField("comment_count_page", T.IntegerType()),
        T.StructField("comments_complete", T.BooleanType()),
        T.StructField("is_duplicate", T.BooleanType()),
        T.StructField("duplicate_target_ids", T.ArrayType(T.LongType())),
        T.StructField("crawled_at", T.StringType()),
    ]
)

COMMENT_RAW_SCHEMA = T.StructType(
    [
        T.StructField("comment_id", T.LongType()),
        T.StructField("post_id", T.LongType()),
        T.StructField("text", T.StringType()),
        T.StructField("score", T.IntegerType()),
        T.StructField("creation_date", T.StringType()),
        T.StructField("position", T.IntegerType()),
        T.StructField("crawled_at", T.StringType()),
    ]
)

PAIR_RAW_SCHEMA = T.StructType(
    [
        T.StructField("query_post_id", T.LongType()),
        T.StructField("relevant_post_id", T.LongType()),
        T.StructField("crawled_at", T.StringType()),
    ]
)

QUESTION_URL_PATTERN = r"(?i)(?:https?://)?(?:www\.)?stackoverflow\.com/questions/\d+"
DUPLICATE_PATTERN = r"(?i)\b(?:possible\s+duplicate|duplicate\s+of|already\s+(?:has|been)\s+answered)\b"
URL_PATTERN = r"(?i)https?://\S+|www\.\S+"


def normalized(column: F.Column) -> F.Column:
    return F.trim(F.regexp_replace(column, r"\s+", " "))


def clean_frames(spark: SparkSession, raw_dir: str) -> tuple[DataFrame, DataFrame, DataFrame, dict]:
    raw_posts = spark.read.schema(POST_RAW_SCHEMA).json(f"{raw_dir}/posts.jsonl")
    raw_comments = spark.read.schema(COMMENT_RAW_SCHEMA).json(f"{raw_dir}/comments.jsonl")
    raw_pairs = spark.read.schema(PAIR_RAW_SCHEMA).json(f"{raw_dir}/duplicate_pairs.jsonl")

    posts = (
        raw_posts.select(
            "post_id",
            normalized(F.col("url")).alias("url"),
            normalized(F.col("title")).alias("title"),
            normalized(F.col("body_text")).alias("body_clean"),
            F.array_sort(
                F.array_distinct(
                    F.transform(
                        F.coalesce(F.col("tags"), F.array().cast("array<string>")),
                        lambda tag: F.lower(normalized(tag)),
                    )
                )
            ).alias("tags"),
            "score",
            F.to_timestamp("creation_date").alias("creation_date"),
            F.coalesce(F.col("comment_count_page"), F.lit(0))
            .cast("int")
            .alias("comment_count_crawled"),
            F.coalesce(F.col("comments_complete"), F.lit(False)).alias("comments_complete"),
            F.coalesce(F.col("is_duplicate"), F.lit(False)).alias("is_duplicate"),
            F.to_timestamp("crawled_at").alias("crawled_at"),
        )
        .where(
            F.col("post_id").isNotNull()
            & (F.length("title") > 0)
            & (F.length("body_clean") > 0)
            & F.col("crawled_at").isNotNull()
        )
        .dropDuplicates(["post_id"])
        .cache()
    )

    leakage_condition = F.col("text").rlike(QUESTION_URL_PATTERN) | F.col("text").rlike(
        DUPLICATE_PATTERN
    )
    leakage_count = raw_comments.where(leakage_condition).count()
    comments = (
        raw_comments.where(~leakage_condition)
        .select(
            "comment_id",
            "post_id",
            normalized(F.regexp_replace(F.col("text"), URL_PATTERN, " <URL> ")).alias(
                "text_clean"
            ),
            "score",
            F.to_timestamp("creation_date").alias("creation_date"),
            "position",
            F.to_timestamp("crawled_at").alias("crawled_at"),
        )
        .where(
            F.col("comment_id").isNotNull()
            & F.col("post_id").isNotNull()
            & (F.length("text_clean") > 0)
            & F.col("crawled_at").isNotNull()
        )
        .dropDuplicates(["comment_id"])
        .join(F.broadcast(posts.select("post_id")), "post_id", "left_semi")
        .cache()
    )

    pairs = (
        raw_pairs.select(
            "query_post_id",
            "relevant_post_id",
            F.to_timestamp("crawled_at").alias("crawled_at"),
        )
        .where(
            F.col("query_post_id").isNotNull()
            & F.col("relevant_post_id").isNotNull()
            & (F.col("query_post_id") != F.col("relevant_post_id"))
            & F.col("crawled_at").isNotNull()
        )
        .dropDuplicates(["query_post_id", "relevant_post_id"])
        .join(
            F.broadcast(posts.select(F.col("post_id").alias("query_post_id"))),
            "query_post_id",
            "left_semi",
        )
        .cache()
    )

    counts = {
        "posts": posts.count(),
        "comments": comments.count(),
        "duplicate_pairs": pairs.count(),
        "comments_removed_label_leakage": leakage_count,
    }
    return posts, comments, pairs, counts


def write_iceberg(
    spark: SparkSession,
    catalog: str,
    namespace: str,
    posts: DataFrame,
    comments: DataFrame,
    pairs: DataFrame,
) -> None:
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {catalog}.{namespace}")
    for name, frame in (
        ("posts", posts),
        ("comments", comments),
        ("duplicate_pairs", pairs),
    ):
        (
            frame.writeTo(f"{catalog}.{namespace}.{name}")
            .using("iceberg")
            .tableProperty("format-version", "2")
            .tableProperty("write.parquet.compression-codec", "zstd")
            .createOrReplace()
        )


def write_parquet(output: str, posts: DataFrame, comments: DataFrame, pairs: DataFrame) -> None:
    posts.write.mode("overwrite").option("compression", "zstd").parquet(f"{output}/posts")
    comments.write.mode("overwrite").option("compression", "zstd").parquet(
        f"{output}/comments"
    )
    pairs.write.mode("overwrite").option("compression", "zstd").parquet(
        f"{output}/duplicate_pairs"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Spark cleaner for Stack Overflow raw JSONL")
    parser.add_argument("--raw-dir", default="/workspace/data/raw")
    parser.add_argument("--parquet-output", default="/workspace/data/clean/spark")
    parser.add_argument("--catalog", default="polaris")
    parser.add_argument("--namespace", default="stackoverflow")
    parser.add_argument("--skip-iceberg", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()
    spark = SparkSession.builder.appName("stackoverflow-cleaning").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    try:
        posts, comments, pairs, counts = clean_frames(spark, args.raw_dir)
        write_parquet(args.parquet_output, posts, comments, pairs)
        if not args.skip_iceberg:
            write_iceberg(spark, args.catalog, args.namespace, posts, comments, pairs)
        summary = {
            "pipeline": "spark",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "iceberg_written": not args.skip_iceberg,
            **counts,
        }
        Path(f"{args.parquet_output}/run_summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, indent=2))
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
