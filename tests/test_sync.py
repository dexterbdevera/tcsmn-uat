import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sync_facebook as sync


class SyncTests(unittest.TestCase):
    def test_env_file_and_export_precedence(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"FB_TOKEN": "exported"}, clear=True):
            path = Path(tmp) / ".env"
            path.write_text("FB_TOKEN='file-token'\nexport FB_PAGE_ID=123 # comment\nEMPTY=\n")
            sync.load_env(path)
            self.assertEqual(os.environ["FB_TOKEN"], "exported")
            self.assertEqual(os.environ["FB_PAGE_ID"], "123")
            self.assertEqual(os.environ["EMPTY"], "")

    def test_page_token_is_used_directly(self):
        with patch.object(sync, "fetch_json", return_value={"id": "123", "name": "TCS", "category": "News"}):
            self.assertEqual(sync.get_page_connection("page-secret"), ("123", "TCS", "page-secret"))
            with self.assertRaisesRegex(sync.SyncError, "does not match"):
                sync.get_page_connection("page-secret", "456")

    def test_user_token_selects_page_and_uses_its_token(self):
        responses = [
            sync.SyncError("category is not a User field", code=100),
            {"data": [{"id": "123", "name": "Other", "access_token": "other-secret"}],
             "paging": {"next": "unused", "cursors": {"after": "cursor"}}},
            {"data": [{"id": "456", "name": "TCS", "access_token": "page-secret"}]},
        ]
        with patch.object(sync, "fetch_json", side_effect=responses) as fetch:
            self.assertEqual(sync.get_page_connection("user-secret", "456"), ("456", "TCS", "page-secret"))
            self.assertEqual(fetch.call_args.args[2]["after"], "cursor")

    def test_ambiguous_pages_require_selection_without_exposing_tokens(self):
        responses = [sync.SyncError("User field", code=100), {"data": [
            {"id": "1", "name": "One", "access_token": "secret1"},
            {"id": "2", "name": "Two", "access_token": "secret2"},
        ]}]
        with patch.object(sync, "fetch_json", side_effect=responses):
            with self.assertRaises(sync.SyncError) as raised:
                sync.get_page_connection("user-secret")
        self.assertIn("FB_PAGE_ID", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    def test_expired_token_does_not_trigger_page_discovery(self):
        with patch.object(sync, "fetch_json", side_effect=sync.SyncError("Expired", code=190)) as fetch:
            with self.assertRaises(sync.SyncError):
                sync.get_page_connection("expired")
            self.assertEqual(fetch.call_count, 1)

    def test_sync_builds_live_site_and_check_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site"
            cache = Path(tmp) / "cache/posts.json"
            posts = [{"id": "123_456", "message": "Real imported story #Trending",
                      "created_time": "2026-11-10T04:00:00+0000"}]
            with patch.object(sync, "SITE", site), patch.object(sync, "CACHE", cache), \
                 patch.object(sync, "load_env"), patch.dict(os.environ, {"FB_TOKEN": "secret"}), \
                 patch.object(sync, "get_page_connection", return_value=("123", "TCS", "page-secret")), \
                 patch.object(sync, "fetch_posts", return_value=posts) as fetch, \
                 contextlib.redirect_stdout(io.StringIO()):
                with patch("sys.argv", ["sync_facebook.py", "--check"]):
                    self.assertEqual(sync.main(), 0)
                self.assertFalse(site.exists())
                self.assertFalse(cache.exists())
                with patch("sys.argv", ["sync_facebook.py", "--no-images"]):
                    self.assertEqual(sync.main(), 0)
                self.assertEqual(fetch.call_args.args[:2], ("123", "page-secret"))
            public = (site / "data/posts.json").read_text()
            self.assertEqual(json.loads(public)["source_mode"], "live")
            self.assertTrue((site / "articles/123_456.html").exists())
            self.assertNotIn("secret", public)


if __name__ == "__main__":
    unittest.main()
