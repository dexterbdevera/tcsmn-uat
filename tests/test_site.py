import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from site_builder import build_site, normalize_post, card, display_date, publication_time, caption_html, page_shell

class TCSBuilderTests(unittest.TestCase):
    def sample(self, id, msg, date="2026-11-10T04:00:00+0000"):
        return normalize_post({"id": id, "message": msg, "created_time": date, "permalink_url": "https://www.facebook.com/example/post"})

    def test_hashtags_and_article_pages(self):
        post = self.sample("123_456", "Example headline\n\nFull caption here. #Trending #Politics #BreakingNews")
        self.assertTrue(post["is_trending"])
        self.assertTrue(post["is_breaking"])
        self.assertIn("Politics", post["categories"])
        self.assertIn("Breaking News", post["categories"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            build_site([post], "TCS", "demo", path)
            self.assertTrue((path / "index.html").exists())
            self.assertTrue((path / "trending.html").exists())
            self.assertTrue((path / "category/politics.html").exists())
            self.assertTrue((path / "articles/123_456.html").exists())
            article = (path / "articles/123_456.html").read_text()
            self.assertIn("Full caption here.", article)
            self.assertIn("View original Facebook post", article)
            trending = (path / "trending.html").read_text()
            self.assertIn("Example headline", trending)

    def test_placeholder_posts_are_removed_from_existing_site(self):
        excluded = self.sample("no_caption", "#Trending #Sports")
        included = self.sample("caption", "A real headline #Politics")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "articles").mkdir()
            (path / "articles/no_caption.html").write_text("Old placeholder article")
            build_site([excluded, included], "TCS", "live", path)
            self.assertFalse((path / "articles/no_caption.html").exists())
            self.assertFalse((path / "category/sports.html").exists())
            self.assertTrue((path / "articles/caption.html").exists())
            data = json.loads((path / "data/posts.json").read_text())
            self.assertEqual(data["post_count"], 1)
            self.assertEqual(data["posts"][0]["id"], "caption")
            self.assertNotIn("Photo or video update from TCS", (path / "latest.html").read_text())

    def test_loudfest_posts_are_removed_on_rebuild(self):
        posts = [self.sample("festival", "Catch THROW at LOUD Fest Manila! #Throw"),
                 self.sample("tickets", "Early bird tickets\nhttps://www.ticketmelon.com/s32productions/loudfest"),
                 self.sample("news", "A real headline #Politics")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "articles").mkdir()
            (path / "articles/festival.html").write_text("Old festival article")
            (path / "category").mkdir()
            (path / "category/throw.html").write_text("Old festival category")
            build_site(posts, "TCS", "live", path)
            self.assertFalse((path / "articles/festival.html").exists())
            self.assertFalse((path / "articles/tickets.html").exists())
            self.assertFalse((path / "category/throw.html").exists())
            data = json.loads((path / "data/posts.json").read_text())
            self.assertEqual([post["id"] for post in data["posts"]], ["news"])
            self.assertNotIn("LOUD Fest", (path / "latest.html").read_text())

    def test_article_body_omits_headline_and_preserves_story(self):
        for headline in ("Example headline", "A long headline with complete details " * 5):
            post = self.sample("story", headline + " #Politics\nStory continues on the next line.\n\nAnother paragraph.")
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)
                build_site([post], "TCS", "live", path)
                html = (path / "articles/story.html").read_text()
                body = html.split('<div class="article-content">', 1)[1].split('<div class="article-tags">', 1)[0]
                self.assertNotIn(headline.strip(), body)
                self.assertIn("Story continues on the next line.", body)
                self.assertIn("Another paragraph.", body)
                self.assertIn("<h1>" + headline.strip() + "</h1>", html)

    def test_card_preview_starts_after_headline(self):
        for headline in ("Example headline", "A long headline with complete details " * 5):
            post = self.sample("preview", headline + " #Politics\n\nStory details here.\n#Trending")
            html = card(post)
            self.assertIn("<p>Story details here.</p>", html)
            self.assertNotIn("<p>" + headline.strip(), html)
        headline_only = self.sample("headline", "Only a headline #Politics")
        self.assertNotIn("<p>", card(headline_only))

    def test_publication_dates_use_facebook_time_in_philippines(self):
        self.assertEqual(display_date("2026-11-09T18:05:00+0000"), "Nov 10, 2026 · 2:05 AM PHT")
        self.assertEqual(display_date("2026-11-09T10:05:00-0800"), "Nov 10, 2026 · 2:05 AM PHT")
        self.assertEqual(display_date("2026-11-10T04:30:00Z"), "Nov 10, 2026 · 12:30 PM PHT")
        for value in (None, "", "invalid"):
            self.assertEqual(display_date(value), "Publication date unavailable")
            self.assertNotIn("<time", publication_time({"created_time": value}))
        post = self.sample("dates", "Headline\n\nBody", "2026-11-09T18:05:00+0000")
        post["updated_time"] = "2026-11-11T10:00:00Z"
        self.assertIn("Nov 10, 2026 · 2:05 AM PHT", card(post))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            build_site([post], "TCS", "live", path)
            article = (path / "articles/dates.html").read_text()
            self.assertIn("Nov 10, 2026 · 2:05 AM PHT", article)
            self.assertNotIn("Nov 11, 2026", article)

    def test_full_reel_links_use_caption_url_and_escape_markup(self):
        html = caption_html('Watch <this>\nFull Reel: https://www.facebook.com/reel/123?x=1&y=2')
        self.assertIn('href="https://www.facebook.com/reel/123?x=1&amp;y=2"', html)
        self.assertIn('>Full Reel</a>', html)
        self.assertIn('&lt;this&gt;', html)
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertNotIn('<a ', caption_html('Full Reel: javascript:alert(1)'))
        post = self.sample("reel", "Headline\nFull Reel: https://www.facebook.com/reel/123")
        self.assertIn('>Full Reel</a>', card(post))

    def test_uat_notice_can_be_enabled_and_removed(self):
        with patch.dict("os.environ", {"UAT_MODE": "true"}):
            html = page_shell("Home", "Content")
            self.assertIn('class="uat-banner"', html)
            self.assertIn('class="uat-watermark"', html)
        for value in ("false", "0", "off"):
            with patch.dict("os.environ", {"UAT_MODE": value}):
                html = page_shell("Home", "Content")
                self.assertNotIn('class="uat-banner"', html)
                self.assertNotIn('class="uat-watermark"', html)

    def test_markup_is_escaped(self):
        post = self.sample("a_b", "<img src=x onerror=alert(1)> #Trending")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            build_site([post], "TCS", "live", path)
            html = (path / "articles/a_b.html").read_text()
            self.assertNotIn('<img src=x onerror=alert(1)>', html)
            self.assertIn('&lt;img src=x', html)

    def test_latest_order_and_categories(self):
        older = self.sample("id_old", "Older post #Sports", "2026-11-09T01:00:00+0000")
        new = self.sample("id_new", "New post #Entertainment", "2026-11-10T01:00:00+0000")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            build_site([older,new], "TCS", "live", path)
            html = (path / "latest.html").read_text()
            self.assertLess(html.index('New post'), html.index('Older post'))
            self.assertTrue((path / 'category/sports.html').exists())
            self.assertTrue((path / 'category/entertainment.html').exists())
            data = json.loads((path / 'data/posts.json').read_text())
            self.assertEqual(data['post_count'], 2)
            self.assertEqual(data['posts'][0]['id'], 'id_new')

if __name__ == '__main__':
    unittest.main()
