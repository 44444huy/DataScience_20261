import json
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError, sync_playwright
from crawl_youtube import BLOCK_RE, READ_DOM, Store, StopCollection, check_access, video_url


class StateTests(unittest.TestCase):
    def test_deduplication_changes_and_export_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            payload = {"comment_id": "c1", "text": "pin tốt", "observed_at": "2026-10-14T00:00:00Z",
                       "published_text": "1 day ago", "likes_text": "1"}
            self.assertEqual(store.save("comment", "c1", payload), "insert")
            payload.update(observed_at="2026-10-15T00:00:00Z", published_text="2 days ago")
            self.assertEqual(store.save("comment", "c1", payload), "unchanged")
            payload["text"] = "pin yếu"
            self.assertEqual(store.save("comment", "c1", payload), "update")
            payload["likes_text"] = "2"
            self.assertEqual(store.save("comment", "c1", payload), "update")
            store.close()  # Simulate ending before an export was produced.
            store = Store(folder)
            store.export()
            exported = Path(folder, "comments.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual([json.loads(row)["operation"] for row in exported],
                             ["insert", "update", "update"])
            store.export()
            self.assertEqual(Path(folder, "comments.jsonl").read_text(
                encoding="utf-8").splitlines(), exported)
            store.close()

    def test_urls_and_rejected_hosts(self):
        self.assertEqual(video_url("https://youtu.be/B9Gn-K8zQ2Q?t=10"),
                         "https://www.youtube.com/watch?v=B9Gn-K8zQ2Q")
        self.assertEqual(video_url("B9Gn-K8zQ2Q"),
                         "https://www.youtube.com/watch?v=B9Gn-K8zQ2Q")
        for value in ["https://evil.example/watch?v=B9Gn-K8zQ2Q",
                      "https://youtube.com.evil.example/watch?v=B9Gn-K8zQ2Q",
                      "file:///tmp/video", "too-short"]:
            with self.assertRaises(ValueError):
                video_url(value)

    def test_verification_message_variants(self):
        for text in ["Sign in to confirm you're not a bot",
                     "Sign in to confirm you are not a bot",
                     "Sign in to confirm you’re not a bot",
                     "Verify you are human"]:
            self.assertIsNotNone(BLOCK_RE.search(text))
        self.assertIsNone(BLOCK_RE.search("No robots were used to build this phone."))


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except PlaywrightError as error:
            cls.playwright.stop()
            raise unittest.SkipTest("Không có Chromium chạy được: " + str(error).splitlines()[0])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_dom_keeps_emoji_and_ignores_rows_without_ids(self):
        page = self.browser.new_page()
        page.set_content("""
          <h1 hidden>Old video</h1><h1>Review điện thoại</h1>
          <div id="owner"><div id="channel-name"><a>Kênh thử</a></div></div>
          <div id="info">100 lượt xem 14 thg 10, 2026</div>
          <ytd-comments><h2 id="count">2 bình luận</h2>
          <ytd-comment-view-model>
            <span id="published-time-text"><a href="https://www.youtube.com/watch?v=B9Gn-K8zQ2Q&amp;lc=c123">2 ngày trước</a></span>
            <div id="content-text">Pin tốt <img alt="❤"><br>nhưng giá cao</div>
            <span id="vote-count-middle">12</span>
          </ytd-comment-view-model>
          <ytd-comment-view-model><div id="content-text">Không có permalink</div></ytd-comment-view-model>
          </ytd-comments>
        """)
        result = page.evaluate(READ_DOM)
        self.assertEqual(result["title"], "Review điện thoại")
        self.assertEqual(result["channel"], "Kênh thử")
        self.assertEqual(len(result["comments"]), 1)
        self.assertEqual(result["comments"][0]["comment_id"], "c123")
        self.assertIn("❤", result["comments"][0]["text"])
        self.assertIn("nhưng giá cao", result["comments"][0]["text"])
        page.close()

    def test_verification_stops_and_comment_text_does_not_trigger_it(self):
        page = self.browser.new_page()
        page.set_content('<div id="movie_player">Sign in to confirm you are not a bot</div>')
        with self.assertRaises(StopCollection):
            check_access(page)
        page.set_content('<div id="movie_player">Play</div><h1>Review</h1>'
                         '<ytd-comments>verify you are human</ytd-comments>')
        check_access(page)
        page.close()


if __name__ == "__main__":
    unittest.main()
