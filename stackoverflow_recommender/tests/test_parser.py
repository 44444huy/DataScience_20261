from so_recommender.parser import parse_listing, parse_question_page


LISTING_HTML = """
<div class="s-post-summary--content-title">
  <a class="s-link" href="/questions/101/example-title">Example</a>
</div>
"""

QUESTION_HTML = """
<html><body>
  <h1><a class="question-hyperlink">How do I combine dictionaries?</a></h1>
  <aside class="s-notice js-post-notice">
    This question already has answers here:
    <a href="/questions/205/canonical-question">Canonical question</a>
  </aside>
  <div id="question" data-questionid="101">
    <div class="js-vote-count" data-value="7">7</div>
    <div class="s-prose js-post-body"><p>I have two dictionaries.</p></div>
    <a class="post-tag">python</a><a class="post-tag">dictionary</a>
    <time itemprop="dateCreated" datetime="2020-01-02T03:04:05Z"></time>
    <ul class="comments-list">
      <li class="comment" data-comment-id="9001">
        <span class="comment-score"><span>2</span></span>
        <span class="comment-copy">Which Python version?</span>
        <time datetime="2020-01-02T04:00:00Z"></time>
      </li>
    </ul>
  </div>
</body></html>
"""


def test_listing_parser_extracts_canonical_question_url():
    assert parse_listing(LISTING_HTML, "https://stackoverflow.com") == [
        (101, "https://stackoverflow.com/questions/101")
    ]


def test_question_parser_extracts_post_comments_and_duplicate_pair():
    post, comments, pairs = parse_question_page(
        QUESTION_HTML,
        "https://stackoverflow.com/questions/101/example-title",
        "2026-10-17T00:00:00+00:00",
    )
    assert post["post_id"] == 101
    assert post["tags"] == ["python", "dictionary"]
    assert post["duplicate_target_ids"] == [205]
    assert comments[0]["comment_id"] == 9001
    assert pairs == [
        {
            "query_post_id": 101,
            "relevant_post_id": 205,
            "crawled_at": "2026-10-17T00:00:00+00:00",
        }
    ]

