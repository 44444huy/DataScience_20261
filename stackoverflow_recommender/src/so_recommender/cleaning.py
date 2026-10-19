from __future__ import annotations

from datetime import UTC, datetime
import html
import re
import unicodedata


WHITESPACE_RE = re.compile(r"\s+")
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
QUESTION_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?stackoverflow\.com/questions/\d+",
    re.IGNORECASE,
)
DUPLICATE_PHRASE_RE = re.compile(
    r"\b(?:possible\s+duplicate|duplicate\s+of|already\s+(?:has|been)\s+answered)\b",
    re.IGNORECASE,
)


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u200b", " ").replace("\ufeff", " ")
    return WHITESPACE_RE.sub(" ", text).strip()


def is_label_leakage_comment(value: object) -> bool:
    text = normalize_text(value)
    return bool(QUESTION_URL_RE.search(text) or DUPLICATE_PHRASE_RE.search(text))


def clean_comment(value: object) -> str:
    text = normalize_text(value)
    text = URL_RE.sub(" <URL> ", text)
    return WHITESPACE_RE.sub(" ", text).strip()


def parse_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

