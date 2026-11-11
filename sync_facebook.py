#!/usr/bin/env python3
"""Read a managed Facebook Page with a Page token, then build static TCS articles.

Requires only Python 3.9+ standard library. No token is written into the site.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from site_builder import build_site, normalize_post, load_cached_posts

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
CACHE = ROOT / "cache" / "posts.json"
GRAPH = "https://graph.facebook.com"
BASE_FIELDS = "id,message,story,created_time,updated_time,permalink_url,full_picture"
IMAGE_FIELDS = BASE_FIELDS + ",attachments{media,subattachments}"
MAX_PAGES = 250

class SyncError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def load_env(path=ROOT / ".env"):
    """Read literal KEY=value settings without executing shell code.

    Exported environment variables take precedence over the private file.
    """
    if not path.exists():
        return
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise SyncError(f"Invalid .env setting on line {number}; expected KEY=value")
        try:
            parts = shlex.split(value, comments=True)
        except ValueError:
            raise SyncError(f"Invalid quoting in .env on line {number}") from None
        if len(parts) > 1:
            raise SyncError(f"Quote values containing spaces in .env on line {number}")
        os.environ.setdefault(key, parts[0] if parts else "")


def fetch_json(path, token, params=None, version="v26.0"):
    # Token is sent in the Authorization header, never in a query string.
    url = f"{GRAPH}/{version}/{path.lstrip('/')}"
    if params:
        url += "?" + urlencode(params)
    req = Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json", "User-Agent": "TCS-Page-Importer/1.0"})
    try:
        with urlopen(req, timeout=30) as res:
            body = json.load(res)
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8", "replace"))
            err = detail.get("error", {})
            raise SyncError(f"Graph API error {err.get('code', exc.code)}: {err.get('message','Request failed')}", code=err.get("code", exc.code)) from None
        except (ValueError, AttributeError):
            raise SyncError(f"Graph API returned HTTP {exc.code}") from None
    except (URLError, TimeoutError) as exc:
        raise SyncError(f"Unable to connect to Graph API: {exc}") from None
    if "error" in body:
        err = body["error"]
        raise SyncError(f"Graph API error {err.get('code','?')}: {err.get('message','Request failed')}", code=err.get("code"))
    return body


def get_page_identity(token, explicit_page_id=None, version="v26.0"):
    info = fetch_json(explicit_page_id or "me", token, {"fields": "id,name"}, version)
    return info["id"], info.get("name", "Facebook Page")


def get_page_connection(token, explicit_page_id=None, version="v26.0"):
    """Accept a Page token or resolve a user token to a managed Page token."""
    explicit_page_id = (explicit_page_id or "").strip()
    try:
        info = fetch_json("me", token, {"fields": "id,name,category"}, version)
    except SyncError as exc:
        if exc.code != 100:
            raise
        # The category field belongs to Pages, not personal profiles.
    else:
        if explicit_page_id and str(info["id"]) != explicit_page_id:
            raise SyncError("FB_PAGE_ID does not match the Page belonging to FB_TOKEN. Use that Page's access token.")
        return info["id"], info.get("name", "Facebook Page"), token

    pages = []
    after = None
    for _ in range(MAX_PAGES):
        params = {"fields": "id,name,access_token", "limit": 100}
        if after:
            params["after"] = after
        response = fetch_json("me/accounts", token, params, version)
        pages.extend(response.get("data", []))
        paging = response.get("paging") or {}
        next_after = (paging.get("cursors") or {}).get("after")
        if not paging.get("next") or not next_after or next_after == after:
            break
        after = next_after
    if explicit_page_id:
        pages = [page for page in pages if str(page["id"]) == explicit_page_id]
    if not pages:
        raise SyncError("No accessible managed Page found. Use a Page access token, or grant the user token pages_show_list and access to the intended Page. Check FB_PAGE_ID if set.")
    if len(pages) > 1:
        choices = ", ".join(f"{page.get('name', 'Page')} ({page['id']})" for page in pages)
        raise SyncError(f"Multiple managed Pages found. Set FB_PAGE_ID to select one: {choices}")
    page = pages[0]
    if not page.get("access_token"):
        raise SyncError("Facebook did not return a Page access token. Set FB_TOKEN to the intended Page's access token.")
    return page["id"], page.get("name", "Facebook Page"), page["access_token"]


def fetch_posts(page_id, token, pages=2, limit=50, version="v26.0"):
    path = f"{page_id}/published_posts"
    all_posts = []
    after = None
    fields = IMAGE_FIELDS
    for page_num in range(pages):
        params = {"fields": fields, "limit": min(100, max(1, limit))}
        if after:
            params["after"] = after
        try:
            response = fetch_json(path, token, params, version)
        except SyncError as exc:
            # Some apps cannot request attachment expansions. Still import captions + images.
            if "(#100)" in str(exc) or ("error 100:" in str(exc) and fields == IMAGE_FIELDS):
                fields = BASE_FIELDS
                params["fields"] = fields
                response = fetch_json(path, token, params, version)
            else:
                raise
        chunk = response.get("data", [])
        all_posts.extend(chunk)
        after = (response.get("paging") or {}).get("cursors", {}).get("after")
        if not chunk or not (response.get("paging") or {}).get("next") or not after:
            break
    return all_posts


def download_image(post, output_dir=SITE / "assets" / "fb"):
    """Cache first image; Facebook CDN links can expire. Keep existing local asset."""
    url = post.get("image_url", "")
    if not url:
        return
    digest = hashlib.sha256(post["id"].encode()).hexdigest()[:20]
    # Reuse a cached image even if its CDN URL has expired.
    for old in output_dir.glob(digest + ".*"):
        if old.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            post["local_image"] = f"assets/fb/{old.name}"
            return
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    allowed = hostname == "fbcdn.net" or hostname.endswith(".fbcdn.net") or hostname == "graph.facebook.com"
    if parsed.scheme != "https" or not allowed:
        return  # Don't download untrusted third-party URLs returned in shared links.
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; TCS-Importer/1.0)"})
    try:
        with urlopen(req, timeout=15) as response:
            ct = response.headers.get_content_type()
            extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(ct)
            if not extension:
                return
            data = response.read(10 * 1024 * 1024 + 1)
            if not data or len(data) > 10 * 1024 * 1024:
                return
        output_dir.mkdir(parents=True, exist_ok=True)
        dest = output_dir / (digest + extension)
        dest.write_bytes(data)
        post["local_image"] = f"assets/fb/{dest.name}"
    except (HTTPError, URLError, TimeoutError, OSError):
        pass  # Article is still usable without media.


def main():
    try:
        load_env()
    except (SyncError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description="Build a TCS static news site from Facebook Page posts")
    parser.add_argument("--demo", action="store_true", help="Generate labeled sample articles to preview the layout; no token needed")
    parser.add_argument("--pages", type=int, default=int(os.getenv("FB_PAGES", "2")), help="API pages to fetch for regular sync (default: 2)")
    parser.add_argument("--all", action="store_true", help="Backfill full Page history on first sync (up to 250 API pages)")
    parser.add_argument("--limit", type=int, default=50, help="Posts per API page (maximum: 100)")
    parser.add_argument("--no-images", action="store_true", help="Don't cache Facebook post images")
    parser.add_argument("--check", action="store_true", help="Check Page access and read one post without changing the site")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild existing website data with current settings; no Facebook requests")
    args = parser.parse_args()
    if args.rebuild:
        try:
            payload = json.loads((SITE / "data" / "posts.json").read_text(encoding="utf-8"))
            build_site(payload["posts"], payload["page_name"], payload["source_mode"], SITE)
        except (OSError, ValueError, KeyError) as exc:
            print(f"ERROR: Cannot rebuild existing website ({type(exc).__name__}). Run a sync or demo first.", file=sys.stderr)
            return 1
        print("Rebuilt existing website with current settings.")
        return 0
    if args.demo:
        examples = json.loads((ROOT / "demo_posts.json").read_text(encoding="utf-8"))
        posts = [normalize_post(p, source="demo") for p in examples]
        build_site(posts, "TCS Media Network", mode="demo", site_dir=SITE)
        print(f"Demo generated: {len(posts)} clearly labeled sample articles. Run with FB_TOKEN to import actual Facebook posts.")
        return 0
    token = os.getenv("FB_TOKEN", "").strip()
    if not token:
        print("ERROR: FB_TOKEN is missing. Run: export FB_TOKEN='your_page_access_token'", file=sys.stderr)
        return 2
    version = os.getenv("FB_API_VERSION", "v26.0").strip()
    if not re.fullmatch(r"v\d+\.\d+", version):
        print("ERROR: FB_API_VERSION must look like v26.0", file=sys.stderr)
        return 2
    try:
        page_id, page_name, token = get_page_connection(token, os.getenv("FB_PAGE_ID"), version)
        print(f"Connected to: {page_name} (Page ID: {page_id})")
        if args.check:
            rows = fetch_posts(page_id, token, pages=1, limit=1, version=version)
            print(f"Page posts endpoint accessible; received {len(rows)} post(s). Site was not changed.")
            return 0
        rows = fetch_posts(page_id, token, pages=(MAX_PAGES if args.all else max(1, min(MAX_PAGES, args.pages))), limit=args.limit, version=version)
        print(f"Received {len(rows)} posts from Graph API")
        if not rows:
            print("ERROR: No published posts returned. Check the selected Page and token's pages_read_engagement permission; existing articles were left unchanged.", file=sys.stderr)
            return 1
        old = load_cached_posts(CACHE)
        merged = {p["id"]: p for p in old if p.get("id") and p.get("source") != "demo" and p.get("title", "").strip().casefold() != "photo or video update from tcs"}
        fresh = [normalize_post(item, source="facebook") for item in rows]
        for item in fresh:
            if item["title"].strip().casefold() == "photo or video update from tcs":
                merged.pop(item["id"], None)
                continue
            previous = merged.get(item["id"], {})
            if previous.get("local_image"):
                item["local_image"] = previous["local_image"]
            if not args.no_images and not item.get("local_image"):
                download_image(item)
            merged[item["id"]] = item
        posts = sorted(merged.values(), key=lambda p: p.get("created_time", ""), reverse=True)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"page_id": page_id, "page_name": page_name, "posts": posts}, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE)
        build_site(posts, page_name, mode="live", site_dir=SITE)
        print(f"Generated {len(posts)} article pages in {SITE}/")
        print("Preview: python3 -m http.server 8000 --directory site")
        print("Open: http://localhost:8000")
        return 0
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if exc.code == 190:
            print("Replace FB_TOKEN with a valid, unexpired Facebook access token.", file=sys.stderr)
        elif exc.code in (10, 100, 200):
            print("Check that the token has access to the intended Page and pages_read_engagement permission.", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
