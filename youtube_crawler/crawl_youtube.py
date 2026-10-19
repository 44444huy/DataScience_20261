"""Small, sequential YouTube DOM collector. Python 3.10+."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

from playwright.sync_api import TimeoutError as BrowserTimeout, sync_playwright

LOG = logging.getLogger("youtube")
COMMENT_SELECTOR = "ytd-comment-view-model, ytd-comment-renderer"
BLOCK_RE = re.compile(
    r"sign in to confirm you(?:.?re| are) not a bot|confirm you(?:.?re| are) not a bot|"
    r"verify you are human|unusual traffic|automated queries|"
    r"our systems have detected|checking your browser|"
    r"xác nhận.{0,40}không phải.{0,20}(?:rô.?bốt|robot)",
    re.IGNORECASE,
)

# Only rendered DOM is inspected. No ytInitialData, fetch, XHR or data endpoints.
READ_DOM = r"""() => {
  const visible = e => e && e.getClientRects().length > 0;
  const text = e => {
    if (!e) return null;
    const clone = e.cloneNode(true);
    clone.querySelectorAll('img[alt]').forEach(img =>
      img.replaceWith(document.createTextNode(img.getAttribute('alt'))));
    clone.querySelectorAll('br').forEach(br =>
      br.replaceWith(document.createTextNode('\n')));
    return clone.textContent.replace(/[ \t]+/g, ' ').trim() || null;
  };
  const title = Array.from(document.querySelectorAll('h1')).find(visible);
  const labels = Array.from(document.querySelectorAll('[aria-label]'))
    .map(e => e.getAttribute('aria-label'));
  const info = Array.from(document.querySelectorAll('#info'))
    .find(e => visible(e) && /views|lượt xem/i.test(e.innerText));
  const owner = document.querySelector('#owner #channel-name a');
  const description = document.querySelector('#description-inline-expander');
  const count = document.querySelector('ytd-comments #count');
  const comments = Array.from(document.querySelectorAll(
    'ytd-comment-view-model, ytd-comment-renderer')).map(c => {
      const link = Array.from(c.querySelectorAll('a'))
        .find(a => (a.getAttribute('href') || '').includes('lc='));
      const content = c.querySelector('#content-text');
      let id = null;
      if (link) id = new URL(link.href).searchParams.get('lc');
      return {
        comment_id: id,
        text: text(content),
        published_text: c.querySelector('#published-time-text')?.innerText.trim() || null,
        likes_text: c.querySelector('#vote-count-middle, #vote-count')?.innerText.trim() || null,
        like_label: Array.from(c.querySelectorAll('[aria-label]'))
          .map(e => e.getAttribute('aria-label'))
          .find(s => /^(Like this comment|Thích bình luận)/i.test(s || '')) || null,
        source_url: link?.href || null
      };
    }).filter(c => c.comment_id && c.text);
  return {
    title: title?.innerText.trim() || null,
    channel: owner?.innerText.trim() || null,
    views_and_published_text: info?.innerText.trim() || null,
    like_label: labels.find(s => /^(like this video|thích video)/i.test(s || '')) || null,
    description_text: description?.innerText.trim() || null,
    comments_count_text: count?.innerText.trim() || null,
    comments
  };
}"""

READ_BLOCK_TEXT = r"""() => {
  const player = document.querySelector('#movie_player');
  const headings = Array.from(document.querySelectorAll('h1,h2'))
    .filter(e => !e.closest('ytd-comments') && e.getClientRects().length)
    .map(e => e.innerText).join('\n');
  return player ? player.innerText + '\n' + headings
                : (document.body?.innerText || '').slice(0, 8000);
}"""


class StopCollection(RuntimeError):
    """A visible verification requirement or an access/rate restriction."""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def video_url(value):
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        video_id = value
    else:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or host not in {
            "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"
        }:
            raise ValueError("Chỉ nhận URL HTTPS YouTube hoặc video ID 11 ký tự.")
        if host == "youtu.be":
            video_id = parsed.path.strip("/").split("/")[0]
        elif parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/shorts/", "/live/")):
            video_id = parsed.path.split("/")[2]
        else:
            raise ValueError("URL phải là trang watch, shorts hoặc live.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("Video ID không hợp lệ.")
    return "https://www.youtube.com/watch?v=" + video_id


class Store:
    """SQLite is authoritative; JSONL exports can always be regenerated."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "state.sqlite")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS entities (
                kind TEXT NOT NULL, id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(kind, id)
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL, id TEXT NOT NULL, operation TEXT NOT NULL,
                observed_at TEXT NOT NULL, payload TEXT NOT NULL
            );
        """)
        self.db.commit()

    def save(self, kind, entity_id, payload):
        stable = {k: v for k, v in payload.items()
                  if k not in {"observed_at", "published_text"}}
        fingerprint = hashlib.sha256(json.dumps(
            stable, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")).hexdigest()
        encoded = json.dumps(payload, ensure_ascii=False)
        with self.db:
            previous = self.db.execute(
                "SELECT fingerprint FROM entities WHERE kind=? AND id=?",
                (kind, entity_id),
            ).fetchone()
            if previous and previous[0] == fingerprint:
                return "unchanged"
            operation = "update" if previous else "insert"
            self.db.execute(
                "INSERT OR REPLACE INTO entities VALUES (?,?,?,?)",
                (kind, entity_id, fingerprint, encoded),
            )
            self.db.execute(
                "INSERT INTO events(kind,id,operation,observed_at,payload) VALUES (?,?,?,?,?)",
                (kind, entity_id, operation, payload["observed_at"], encoded),
            )
        return operation

    def export(self):
        for kind, filename in (("comment", "comments.jsonl"), ("video", "videos.jsonl")):
            destination = self.directory / filename
            temporary = destination.with_suffix(".jsonl.tmp")
            rows = self.db.execute(
                "SELECT event_id,id,operation,observed_at,payload FROM events "
                "WHERE kind=? ORDER BY event_id", (kind,)
            )
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                for event_id, entity_id, operation, observed_at, payload in rows:
                    event = dict(event_id=event_id, entity_id=entity_id,
                                 operation=operation, observed_at=observed_at,
                                 data=json.loads(payload))
                    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            temporary.replace(destination)

    def close(self):
        self.db.close()


def check_access(page, response=None):
    if response and response.status in (403, 429):
        raise StopCollection(f"HTTP {response.status}: dừng lượt chạy để tránh gửi thêm request.")
    if "accounts.google.com" in page.url or "/sorry/" in page.url:
        raise StopCollection("Trang chuyển sang đăng nhập/xác minh; dừng lượt chạy.")
    body = page.evaluate(READ_BLOCK_TEXT)
    match = BLOCK_RE.search(body)
    if match:
        raise StopCollection("Trang yêu cầu xác minh hoặc báo lưu lượng bất thường: " + match.group(0))
    for frame in page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha']").all():
        if frame.is_visible():
            raise StopCollection("Có CAPTCHA hiển thị; dừng lượt chạy.")


def accept_navigation(page, url, timeout):
    response = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    check_access(page, response)
    for label in ("Reject all", "Từ chối tất cả"):
        button = page.get_by_role("button", name=label, exact=True)
        if button.count() and button.first.is_visible():
            button.first.click(timeout=3000)
            break
    return response


def discover(page, query, maximum, timeout):
    response = accept_navigation(
        page, "https://www.youtube.com/results?search_query=" + quote_plus(query), timeout
    )
    try:
        page.locator("a#video-title").first.wait_for(state="visible", timeout=timeout)
    except BrowserTimeout:
        check_access(page, response)
        raise
    links = page.locator("a#video-title").evaluate_all(
        "(links) => links.map(a => a.href)"
    )
    urls = []
    for link in links:
        try:
            normalized = video_url(link)
        except ValueError:
            continue
        if normalized not in urls:
            urls.append(normalized)
        if len(urls) >= maximum:
            break
    if not urls:
        raise RuntimeError("Trang tìm kiếm không cung cấp URL video có thể đọc.")
    return urls


def collect_video(page, url, args, store):
    started = time.monotonic()
    timeout = int(args.timeout * 1000)
    response = accept_navigation(page, url, timeout)
    try:
        page.locator("h1:visible").first.wait_for(state="visible", timeout=timeout)
    except BrowserTimeout:
        check_access(page, response)
        raise
    page.locator("video").evaluate_all("(videos) => videos.forEach(v => v.pause())")
    # Expanding the description often exposes exact views/date instead of 108K/9mo.
    for label in ("...more", "…more", "...thêm", "…thêm", "Thêm"):
        button = page.get_by_role("button", name=label, exact=True)
        if button.count() and button.first.is_visible():
            button.first.click(timeout=3000)
            break
    check_access(page)
    note = None
    try:
        # The comments header may not render until this section is scrolled into view.
        section = page.locator("ytd-comments").first
        section.wait_for(state="attached", timeout=timeout)
        section.scroll_into_view_if_needed(timeout=timeout)
        header = page.get_by_role(
            "heading", name=re.compile(r"comments|bình luận", re.IGNORECASE)
        ).first
        header.wait_for(state="visible", timeout=timeout)
        header.scroll_into_view_if_needed(timeout=timeout)
        page.locator(COMMENT_SELECTOR).first.wait_for(state="visible", timeout=timeout)
    except BrowserTimeout:
        check_access(page)
        note = "comments_not_loaded_within_timeout"

    found = {}
    stalled = 0
    scrolls = 0
    latest = page.evaluate(READ_DOM)
    while True:
        check_access(page)
        before = len(found)
        latest = page.evaluate(READ_DOM)
        for comment in latest.pop("comments"):
            if comment["comment_id"] not in found and len(found) < args.max_comments:
                found[comment["comment_id"]] = comment
        if len(found) >= args.max_comments:
            note = "sample_limit"
            break
        if note == "comments_not_loaded_within_timeout":
            break
        if scrolls >= args.max_scrolls:
            note = "scroll_limit"
            break
        stalled = stalled + 1 if len(found) == before else 0
        if stalled >= 3:
            note = "no_more_new_comments_in_loaded_dom"
            break
        rows = page.locator(COMMENT_SELECTOR)
        if not rows.count():
            break
        old_count = rows.count()
        rows.last.scroll_into_view_if_needed(timeout=timeout)
        page.mouse.wheel(0, 650)
        page.wait_for_timeout(int(args.scroll_delay * 1000))
        try:
            page.wait_for_function(
                "n => document.querySelectorAll('ytd-comment-view-model, ytd-comment-renderer').length > n",
                arg=old_count, timeout=min(timeout, 4000),
            )
        except BrowserTimeout:
            pass  # Bounded stalled-round detection decides when to stop.
        scrolls += 1

    observed = utc_now()
    video_id = parse_qs(urlparse(url).query)["v"][0]
    latest.update(video_id=video_id, source_url=url, observed_at=observed)
    changes = {"insert": 0, "update": 0, "unchanged": 0}
    store.save("video", video_id, latest)
    for comment in found.values():
        comment.update(video_id=video_id, observed_at=observed)
        changes[store.save("comment", comment["comment_id"], comment)] += 1
    result = dict(video_id=video_id, title=latest.get("title"),
                  comments_read=len(found), changes=changes, stop_reason=note,
                  scrolls=scrolls, elapsed_seconds=round(time.monotonic() - started, 2))
    LOG.info("%s: %d comments; %s", video_id, len(found), changes)
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", action="append", default=[], help="Có thể truyền nhiều lần.")
    p.add_argument("--urls-file", type=Path, help="Một URL/video ID trên mỗi dòng.")
    p.add_argument("--query", help="Tìm video qua trang tìm kiếm YouTube.")
    p.add_argument("--max-videos", type=int, default=3)
    p.add_argument("--max-comments", type=int, default=50, help="Mẫu bình luận mỗi video.")
    p.add_argument("--max-scrolls", type=int, default=20)
    p.add_argument("--video-delay", type=float, default=10, help="Giây nghỉ giữa các video, tối thiểu 5.")
    p.add_argument("--scroll-delay", type=float, default=2, help="Giây nghỉ giữa các lần cuộn, tối thiểu 1.")
    p.add_argument("--timeout", type=float, default=25)
    p.add_argument("--output", type=Path, default=Path("data"))
    p.add_argument("--headed", action="store_true", help="Hiện cửa sổ để quan sát lần chạy đầu.")
    p.add_argument("--channel", choices=["chrome", "msedge"], help="Dùng trình duyệt đã cài.")
    return p


def main():
    p = parser()
    args = p.parse_args()
    if args.max_videos < 1 or args.max_comments < 1 or args.max_scrolls < 0 or args.timeout <= 0:
        p.error("Giới hạn video/bình luận/timeout phải dương; max-scrolls không được âm.")
    if args.video_delay < 5 or args.scroll_delay < 1:
        p.error("video-delay tối thiểu 5 giây; scroll-delay tối thiểu 1 giây.")
    sources = args.url[:]
    if args.urls_file:
        try:
            sources.extend(line.strip() for line in args.urls_file.read_text(
                encoding="utf-8-sig").splitlines() if line.strip() and not line.lstrip().startswith("#"))
        except OSError as error:
            p.error(str(error))
    try:
        urls = list(dict.fromkeys(video_url(value) for value in sources))[:args.max_videos]
    except ValueError as error:
        p.error(str(error))
    if not urls and not args.query:
        p.error("Truyền --url, --urls-file hoặc --query.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = Store(args.output)
    summary = {"started_at": utc_now(), "method": "rendered_DOM", "videos": [], "status": "running"}
    exit_code = 0
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not args.headed, channel=args.channel)
            try:
                context = browser.new_context(locale="vi-VN", viewport={"width": 1366, "height": 900})
                page = context.new_page()
                if not urls:
                    urls = discover(page, args.query, args.max_videos, int(args.timeout * 1000))
                    page.wait_for_timeout(int(args.video_delay * 1000))
                for index, url in enumerate(urls):
                    if index:
                        page.wait_for_timeout(int(args.video_delay * 1000))
                    try:
                        summary["videos"].append(collect_video(page, url, args, store))
                    except StopCollection:
                        raise
                    except Exception as error:
                        check_access(page)
                        LOG.error("%s: %s", url, error)
                        summary["videos"].append({"source_url": url, "error": str(error)})
                        exit_code = 1
                summary["status"] = "completed" if exit_code == 0 else "completed_with_errors"
            finally:
                browser.close()
    except StopCollection as error:
        summary.update(status="stopped_access_restriction", reason=str(error))
        LOG.error("%s", error)
        exit_code = 2
    except KeyboardInterrupt:
        summary["status"] = "interrupted"
        exit_code = 130
    except Exception as error:
        summary.update(status="failed", reason=str(error))
        LOG.error("%s", error)
        exit_code = 1
    finally:
        summary["finished_at"] = utc_now()
        store.export()
        destination = args.output / "run_summary.json"
        destination.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        store.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
