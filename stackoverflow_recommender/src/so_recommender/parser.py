from __future__ import annotations

from datetime import UTC, datetime
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag


QUESTION_PATH_RE = re.compile(r"^/questions/(\d+)(?:/|$)")
INTEGER_RE = re.compile(r"-?\d+")


def canonical_question_url(base_url: str, href: str) -> tuple[int, str] | None:
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    match = QUESTION_PATH_RE.match(parsed.path)
    if not match:
        return None
    post_id = int(match.group(1))
    return post_id, f"{parsed.scheme}://{parsed.netloc}/questions/{post_id}"


def parse_listing(html: str, base_url: str) -> list[tuple[int, str]]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[int, str] = {}
    selectors = (
        ".s-post-summary--content-title a.s-link",
        ".question-summary h3 a.question-hyperlink",
        "a.question-hyperlink",
    )
    for selector in selectors:
        for anchor in soup.select(selector):
            href = anchor.get("href")
            if not href:
                continue
            parsed = canonical_question_url(base_url, str(href))
            if parsed:
                found[parsed[0]] = parsed[1]
        if found:
            break
    return list(found.items())


def _int_from_node(node: Tag | None, *attribute_names: str) -> int | None:
    if node is None:
        return None
    for name in attribute_names:
        raw = node.get(name)
        if raw is not None:
            match = INTEGER_RE.search(str(raw).replace(",", ""))
            if match:
                return int(match.group())
    match = INTEGER_RE.search(node.get_text(" ", strip=True).replace(",", ""))
    return int(match.group()) if match else None


def _datetime_from_node(node: Tag | None) -> str | None:
    if node is None:
        return None
    raw = node.get("datetime") or node.get("title")
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat(timespec="seconds")


def _extract_duplicate_targets(soup: BeautifulSoup, base_url: str, post_id: int) -> list[int]:
    targets: set[int] = set()
    for notice in soup.select(".js-post-notice, aside.s-notice, .question-status"):
        notice_text = notice.get_text(" ", strip=True).lower()
        if "duplicate" not in notice_text and "already has answers" not in notice_text:
            continue
        for anchor in notice.select("a[href]"):
            parsed = canonical_question_url(base_url, str(anchor.get("href")))
            if parsed and parsed[0] != post_id:
                targets.add(parsed[0])
    return sorted(targets)


def parse_question_page(
    html: str,
    page_url: str,
    crawled_at: str,
) -> tuple[dict, list[dict], list[dict]]:
    soup = BeautifulSoup(html, "html.parser")
    base_url = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
    canonical = canonical_question_url(base_url, page_url)

    question = soup.select_one("#question")
    if question is None:
        raise ValueError("Question container '#question' was not found")

    post_id_raw = question.get("data-questionid") or question.get("data-post-id")
    post_id = int(post_id_raw) if post_id_raw else (canonical[0] if canonical else 0)
    if post_id <= 0:
        raise ValueError("Question id was not found")

    title_node = soup.select_one("h1 a.question-hyperlink, h1")
    body_node = question.select_one(".js-post-body, .s-prose")
    if title_node is None or body_node is None:
        raise ValueError(f"Question {post_id} is missing title or body")

    title = title_node.get_text(" ", strip=True)
    body_html = body_node.decode_contents()
    body_text = body_node.get_text(" ", strip=True)
    tags = [tag.get_text(" ", strip=True) for tag in question.select("a.post-tag")]
    score_node = question.select_one(".js-vote-count, [itemprop='upvoteCount']")
    creation_node = question.select_one(
        "time[itemprop='dateCreated'], .post-signature.owner .relativetime, .user-action-time .relativetime"
    )
    targets = _extract_duplicate_targets(soup, base_url, post_id)

    comments: list[dict] = []
    for position, comment in enumerate(question.select(".comments-list .comment"), start=1):
        text_node = comment.select_one(".comment-copy")
        if text_node is None:
            continue
        comment_id_raw = comment.get("data-comment-id") or comment.get("id", "")
        comment_id_match = INTEGER_RE.search(str(comment_id_raw))
        if not comment_id_match:
            continue
        score = _int_from_node(comment.select_one(".comment-score span, .comment-score"))
        date_node = comment.select_one("time, .relativetime-clean, .comment-date .relativetime")
        comments.append(
            {
                "comment_id": int(comment_id_match.group()),
                "post_id": post_id,
                "text": text_node.get_text(" ", strip=True),
                "score": score,
                "creation_date": _datetime_from_node(date_node),
                "position": position,
                "crawled_at": crawled_at,
            }
        )

    post = {
        "post_id": post_id,
        "url": canonical[1] if canonical else page_url,
        "title": title,
        "body_html": body_html,
        "body_text": body_text,
        "tags": tags,
        "score": _int_from_node(score_node, "data-value", "content"),
        "creation_date": _datetime_from_node(creation_node),
        "comment_count_page": len(comments),
        "comments_complete": "show more comments" not in question.get_text(" ", strip=True).lower(),
        "is_duplicate": bool(targets),
        "duplicate_target_ids": targets,
        "crawled_at": crawled_at,
    }
    pairs = [
        {
            "query_post_id": post_id,
            "relevant_post_id": target_id,
            "crawled_at": crawled_at,
        }
        for target_id in targets
    ]
    return post, comments, pairs

