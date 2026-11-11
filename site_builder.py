"""Generate accessible, SEO-friendly static HTML article pages from Graph API JSON."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone, timedelta
from html import escape
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
HASH_RE = re.compile(r"(?<![\w])#([a-zA-Z][\w]*)", re.UNICODE)
META_TAGS = {"trending", "featured", "latest", "latestnews", "tcs", "tcsmedia", "thecommentsectionmedia", "tcsnews", "news"}
KNOWN_NAMES = {"breakingnews": "Breaking News", "currentaffairs": "Current Affairs", "entertainment": "Entertainment", "technology": "Technology", "politics": "Politics", "community": "Community", "sports": "Sports", "lifestyle": "Lifestyle", "culture": "Culture", "business": "Business", "philippines": "Philippines", "world": "World", "international": "International", "localnews": "Local News"}


def pretty_tag(tag):
    low = tag.casefold()
    if low in KNOWN_NAMES:
        return KNOWN_NAMES[low]
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", tag).replace("_", " ")
    return spaced.title()


def htmltxt(text):
    return escape(str(text or ""), quote=True)


def parse_date(date):
    try:
        normalized = date.replace("Z", "+00:00")
        normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", normalized)
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, TypeError, AttributeError):
        return None


def display_date(date):
    dt = parse_date(date)
    if dt is None:
        return "Publication date unavailable"
    dt = dt.astimezone(timezone(timedelta(hours=8)))
    hour = dt.hour % 12 or 12
    period = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%b')} {dt.day}, {dt.year} · {hour}:{dt.minute:02} {period} PHT"


def publication_time(post):
    date = post.get("created_time", "")
    dt = parse_date(date)
    if dt is None:
        return '<span class="publication-date">Publication date unavailable</span>'
    return f'<time datetime="{htmltxt(dt.isoformat())}">{htmltxt(display_date(date))}</time>'


def extract_image(raw):
    if raw.get("full_picture"):
        return raw["full_picture"]
    for attachment in (raw.get("attachments") or {}).get("data", []):
        image = ((attachment.get("media") or {}).get("image") or {}).get("src")
        if image:
            return image
        for child in (attachment.get("subattachments") or {}).get("data", []):
            image = ((child.get("media") or {}).get("image") or {}).get("src")
            if image:
                return image
    return ""


def normalize_post(raw, source="facebook"):
    message = str(raw.get("message") or raw.get("story") or "").strip()
    first = next((line.strip() for line in message.splitlines() if line.strip() and not line.lstrip().startswith("#")), "")
    if first:
        first = re.sub(r"\s*#[\w]+", "", first).strip()
        title = (first[:118].rsplit(" ", 1)[0] + "…") if len(first) > 118 else first
    else:
        title = "Photo or video update from TCS"
    hashtags = list(dict.fromkeys("#" + tag for tag in HASH_RE.findall(message)))
    # Case-insensitive dedupe, preserving original spelling.
    hashtags = list({tag.casefold(): tag for tag in hashtags}.values())
    categories = [pretty_tag(h[1:]) for h in hashtags if h[1:].casefold() not in META_TAGS]
    # Some users tag topical posts only with #BreakingNews; keep that as a category.
    raw_id = str(raw.get("id") or "")
    if not raw_id:
        raise ValueError("Post is missing an id")
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "-", raw_id)[:100]
    permalink = raw.get("permalink_url", "")
    if urlparse(permalink).scheme not in ("https", "http"):
        permalink = ""
    return {
        "id": raw_id,
        "slug": safe_id,
        "title": title,
        "message": message,
        "created_time": raw.get("created_time", ""),
        "updated_time": raw.get("updated_time", ""),
        "permalink_url": permalink,
        "image_url": extract_image(raw),
        "local_image": "",
        "hashtags": hashtags,
        "categories": list(dict.fromkeys(categories)),
        "is_trending": any(x.casefold() == "#trending" for x in hashtags),
        "is_featured": any(x.casefold() == "#featured" for x in hashtags),
        "is_breaking": any(x.casefold() == "#breakingnews" for x in hashtags),
        "source": source,
    }


def load_cached_posts(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return payload.get("posts", [])
    except (FileNotFoundError, ValueError, OSError):
        return []


def tag_has(post, tag):
    return any(h.casefold() == "#" + tag.casefold() for h in post.get("hashtags", []))


def image_url(post, prefix=""):
    local = post.get("local_image", "")
    if local:
        return prefix + local
    remote = post.get("image_url", "")
    parsed = urlparse(remote)
    if parsed.scheme == "https" and ((parsed.hostname or "").endswith(".fbcdn.net") or parsed.hostname == "fbcdn.net"):
        return remote
    return ""


def image_element(post, prefix="", css_class=""):
    img = image_url(post, prefix)
    if img:
        return f'<img src="{htmltxt(img)}" alt="Image for {htmltxt(post["title"])}" loading="lazy" class="{htmltxt(css_class)}">'
    return '<div class="no-photo"><span class="no-photo-word">TCS<span>.</span></span><span>THE COMMENT SECTION</span></div>'


def breadcrumb(post, prefix):
    return " · ".join(htmltxt(x) for x in post["categories"][:2]) or "News"


def caption_html(text):
    parts = []
    position = 0
    for match in re.finditer(r"\b(Full Reel)\s*:\s*(https?://[^\s<>]+)", text, re.IGNORECASE):
        url = match.group(2).rstrip(".,;!?)")
        parsed = urlparse(url)
        if not parsed.hostname or parsed.username or parsed.password:
            continue
        parts.append(htmltxt(text[position:match.start()]))
        parts.append(f'<a class="reel-link" href="{htmltxt(url)}" target="_blank" rel="noopener noreferrer">{htmltxt(match.group(1))}</a>')
        parts.append(htmltxt(match.group(2)[len(url):]))
        position = match.end()
    parts.append(htmltxt(text[position:]))
    return "".join(parts)


def story_text(post):
    title = post['title']
    lines = post.get("message", "").strip().splitlines()
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        headline = re.sub(r"\s*#[\w]+", "", line.strip()).strip()
        shortened = (headline[:118].rsplit(" ", 1)[0] + "…") if len(headline) > 118 else headline
        if title in (headline, shortened):
            # Keep the complete headline above the story, including long captions.
            title = headline
            del lines[index]
        break
    body = "\n".join(lines).strip()
    return title, body


def card(post, prefix="", featured=False):
    _, body = story_text(post)
    story = " ".join(line.strip() for line in body.splitlines() if not line.lstrip().startswith("#"))
    excerpt = story if featured else story[:180]
    preview = f"<p>{caption_html(excerpt)}</p>" if excerpt else ""
    article = prefix + "articles/" + post["slug"] + ".html"
    section = post["categories"][0] if post["categories"] else "News"
    badge = "<span class='pill-red'>TRENDING</span>" if post.get("is_trending") else ""
    return f'''<article class="story-card{' large-card' if featured else ''}">
      <a href="{htmltxt(article)}" class="story-image" aria-label="Read {htmltxt(post['title'])}">{image_element(post,prefix)}</a>
      <div class="card-body"><div class="card-meta"><span>{htmltxt(section).upper()}</span><span class="mid-dot">•</span>{publication_time(post)}{badge}</div>
      <h3><a href="{htmltxt(article)}">{htmltxt(post['title'])}</a></h3>
      {preview}
      <a class="read-link" href="{htmltxt(article)}">READ ARTICLE <span aria-hidden="true">↗</span></a></div>
    </article>'''


def category_key(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "other"


def page_shell(title, content, prefix="", navcats=None, mode="live", description="The latest stories and conversations from TCS Media Network."):
    app_id = os.getenv("FB_APP_ID", "").strip()
    app_meta = f'<meta name="facebook-app-id" content="{app_id}">' if re.fullmatch(r"[0-9]+", app_id) else ""
    uat_enabled = os.getenv("UAT_MODE", "true").strip().lower() in {"1", "true", "yes", "on"}
    uat_notice = ('<div class="uat-banner" role="note"><strong>UAT PREVIEW</strong>'
                  '<span>Website under acceptance testing · Pending final approval</span></div>'
                  '<div class="uat-watermark" aria-hidden="true">UAT PREVIEW</div>') if uat_enabled else ""
    cats = navcats or []
    links = ''.join(f'<a href="{prefix}category/{category_key(c)}.html">{htmltxt(c)}</a>' for c in cats[:4])
    demo_bar = '<div class="demo-banner">DEMO CONTENT — SAMPLE STORIES ONLY. Run the Facebook importer to see real posts.</div>' if mode == "demo" else ''
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{app_meta}<meta name="theme-color" content="#b91521"><meta name="description" content="{htmltxt(description[:160])}">
<title>{htmltxt(title)} | TCS Media Network</title><link rel="icon" href="{prefix}assets/tcs-mark.webp" type="image/webp"><script src="{prefix}theme.js"></script><link rel="stylesheet" href="{prefix}styles.css?v=uat-1"></head>
<body><a class="skip" href="#main">Skip to content</a>{uat_notice}<div class="topline"><div class="wrap"><span><span class="pulse"></span> TCS MEDIA NETWORK</span><span>WHERE EVERY <b>JUAN</b> HAS A VOICE</span></div></div>{demo_bar}
<header class="header"><div class="wrap header-inner">
<a class="brand" href="{prefix}index.html"><img src="{prefix}assets/tcs-mark.webp" alt="TCS logo"><span><strong>TCS<span class="red">.</span></strong><small>THE COMMENT SECTION<br>MEDIA NETWORK</small></span></a>
<nav class="nav" aria-label="Main navigation"><a href="{prefix}index.html">Home</a><a href="{prefix}latest.html">Latest News</a><a href="{prefix}trending.html">Trending</a>{links}</nav>
<div class="header-actions"><button class="theme-toggle" type="button" aria-label="Dark mode" aria-pressed="false" hidden><span class="theme-icon" aria-hidden="true">☾</span><span class="theme-label">Dark mode</span></button>
<button class="menu-toggle" type="button" aria-controls="mobileMenu" aria-expanded="false">☰ <span class="sr-only">Menu</span></button></div></div>
<nav class="mobile-menu" id="mobileMenu" hidden><a href="{prefix}index.html">Home</a><a href="{prefix}latest.html">Latest News</a><a href="{prefix}trending.html">Trending</a>{links}</nav></header>
<main id="main">{content}</main>
<footer class="footer"><div class="wrap foot-inner"><div><strong>TCS<span class="red">.</span></strong><p>THE COMMENT SECTION MEDIA NETWORK</p></div><div>Stories from <a href="https://www.facebook.com/TheCommentSectionMedia/" target="_blank" rel="noopener noreferrer">The Comment Section Media</a><p>All article text is republished from the original Facebook posts.</p></div></div><div class="wrap copyright">© {datetime.now(timezone.utc).year} TCS Media Network · Content source: Facebook</div></footer>
<script src="{prefix}app.js?v=uat-1" defer></script></body></html>'''


def page_head(kicker, title, subtitle=""):
    return f'<div class="section-head"><div><span class="eyebrow">{htmltxt(kicker)}</span><h1>{htmltxt(title)}<span class="red">.</span></h1></div><p>{htmltxt(subtitle)}</p></div>'


def recent_sidebar(posts, prefix=""):
    trending = [p for p in posts if p.get("is_trending")][:5]
    lines = ''.join(f'<li><span>{i:02}</span><a href="{prefix}articles/{htmltxt(p["slug"])}.html">{htmltxt(p["title"])}</a></li>' for i,p in enumerate(trending,1))
    if not lines:
        lines = '<li class="muted">No posts tagged #Trending yet. Add #Trending in Facebook captions to populate this section.</li>'
    return f'''<aside class="sidebar"><div class="sidepanel"><span class="eyebrow">THE TCS EDIT</span><h3>Trending <em>now</em><span class="red">.</span></h3><ol>{lines}</ol><a class="panel-link" href="{prefix}trending.html">All trending articles ↗</a></div><div class="sidepromo"><span>FOLLOW THE CONVERSATION</span><strong>Every JUAN<br>has a voice.</strong><p>Follow original stories from TCS on Facebook.</p><a href="https://www.facebook.com/TheCommentSectionMedia/" target="_blank" rel="noopener noreferrer">Visit Facebook Page ↗</a></div></aside>'''


def home_page(posts, categories, mode="live"):
    if not posts:
        status = '<div class="setup-empty"><h2>No articles imported yet</h2><p>Export FB_TOKEN and run <code>python3 sync_facebook.py</code> in your terminal. This homepage will fill with your Page posts.</p></div>'
    else:
        status = ""
    hero_post = next((p for p in posts if p.get("is_featured")), posts[0] if posts else None)
    featured = ''
    if hero_post:
        featured = f'''<div class="featured-wrap">{card(hero_post, featured=True)}</div>'''
    latest = ''.join(card(p) for p in posts[:12])
    category_links = ''.join(f'<a class="category-chip" href="category/{category_key(cat)}.html">{htmltxt(cat)}</a>' for cat in categories[:12])
    breaking = next((p for p in posts if p.get("is_breaking")), None)
    breaking_strip = (f'<a class="breaking-strip" href="articles/{htmltxt(breaking["slug"])}.html"><strong>BREAKING</strong><span>{htmltxt(breaking["title"])}</span><span>↗</span></a>' if breaking else '')
    stamp = f"<span>{len(posts)} {('sample' if mode=='demo' else 'imported')} articles</span>"
    return f'''<section class="hero wrap"><div class="banner"><img src="assets/tcs-cover.webp" alt="TCS The Comment Section Media Network banner"><span class="banner-label">YOUR STORIES. YOUR PERSPECTIVES. YOUR VOICE.</span></div><div class="hero-intro"><div><div class="eyebrow">THE CONVERSATION STARTS HERE</div><h1>Stories that move.<br><span class="red">Voices that matter.</span></h1></div><p>From breaking news to conversations across the Philippines — discover what's happening, directly from the TCS Facebook Page.</p></div></section>
<div class="marquee"><div class="wrap">IN THE LOOP <span>✳</span> NEWS & CURRENT AFFAIRS <span>✳</span> COMMUNITY VOICES <span>✳</span> SPORTS & CULTURE</div></div>
<div class="wrap">{breaking_strip}<section class="spotlight">{page_head("EDITOR'S SPOTLIGHT", "On the radar", "Featured Facebook stories")}{featured}</section>
<section class="news-section">{page_head("STAY IN THE KNOW", "Latest stories", "Newest Facebook posts, updated by your importer")}
<div class="category-bar"><a class="category-chip active" href="latest.html">All stories</a>{category_links}</div>
<div class="content-columns"><div><div class="search-row"><label for="filterInput">Filter visible stories</label><input id="filterInput" type="search" placeholder="Search headlines or captions…"><span class="count-badge">{stamp}</span></div><div class="card-grid" id="storyGrid">{latest}</div>{status}<p class="filter-empty" id="filterEmpty" hidden>No matching stories in this list.</p><a class="more-button" href="latest.html">View all articles <span>↗</span></a></div>{recent_sidebar(posts)}</div></section></div>'''


def listing_page(posts, categories, mode, kicker, title, intro, prefix="", allposts=None):
    cards = ''.join(card(p,prefix) for p in posts)
    if not cards:
        cards = '<div class="setup-empty"><h2>No posts found</h2><p>New posts will appear here when their Facebook caption contains the matching hashtag.</p></div>'
    chips = ''.join(f'<a class="category-chip" href="{prefix}category/{category_key(c)}.html">{htmltxt(c)}</a>' for c in categories[:12])
    return f'''<div class="wrap listing-shell">{page_head(kicker,title,intro)}<div class="category-bar"><a class="category-chip" href="{prefix}latest.html">All stories</a><a class="category-chip" href="{prefix}trending.html">#Trending</a>{chips}</div><div class="content-columns"><div><div class="search-row"><label for="filterInput">Search visible articles</label><input type="search" id="filterInput" placeholder="Search articles…"><span class="count-badge">{len(posts)} articles</span></div><div class="card-grid" id="storyGrid">{cards}</div><p class="filter-empty" id="filterEmpty" hidden>No matching stories.</p></div>{recent_sidebar(allposts if allposts is not None else posts,prefix)}</div></div>'''


def article_page(post, more_posts, categories, mode):
    category = post["categories"][0] if post["categories"] else "News"
    source = (f'<a class="fb-source" href="{htmltxt(post["permalink_url"])}" target="_blank" rel="noopener noreferrer">View original Facebook post ↗</a>' if post.get("permalink_url") else '')
    imgtag = image_element(post, "../", "article-image")
    photo = f'<div class="article-visual">{imgtag}</div>'
    title, body = story_text(post)
    paragraphs = ''.join(f'<p>{caption_html(p)}</p>' for p in re.split(r'\n\s*\n', body) if p.strip())
    tags = ''.join(f'<span class="hashtag">{htmltxt(t)}</span>' for t in post.get("hashtags", []))
    related = ''.join(card(p,"../") for p in more_posts[:3] if p["id"] != post["id"])
    return f'''<div class="wrap article-shell"><nav class="crumb" aria-label="Breadcrumb"><a href="../index.html">Home</a><span>/</span><a href="../latest.html">Latest News</a><span>/</span><span>{htmltxt(category)}</span></nav>
<header class="article-title"><span class="eyebrow">{htmltxt(category).upper()} • FACEBOOK STORY</span><h1>{htmltxt(title)}</h1><div class="article-meta-row"><div class="article-byline"><span>THE COMMENT SECTION MEDIA NETWORK</span><span>•</span>{publication_time(post)}</div>
<section class="article-share" aria-label="Share this article"><div class="share-buttons"><button class="share-button share-whatsapp" type="button" data-share="whatsapp" aria-label="Share on WhatsApp" title="Share on WhatsApp"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M20 11.5a8 8 0 0 1-12 7L3 20l1.5-5A8 8 0 1 1 20 11.5Z"/><path d="M8 7c0 5 4 8 8 8l1-2-3-1-1 1-3-3 1-1-1-3Z"/></svg></button><button class="share-button share-x" type="button" data-share="x" aria-label="Share on X" title="Share on X"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m4 3 16 18h-4L0 3h4Zm15 0L3 21"/></svg></button><button class="share-button share-messenger" type="button" data-share="messenger" aria-label="Share on Messenger" title="Share on Messenger"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M21 11a9 9 0 0 1-10 9l-5 2v-4a9 9 0 1 1 15-7Z"/><path d="m6 14 5-6 3 3 4-2-5 6-3-3Z"/></svg></button><button class="share-button share-viber" type="button" data-share="viber" aria-label="Share on Viber" title="Share on Viber"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M5 3h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-7l-5 3v-3H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/><path d="M7 7c0 5 4 9 9 9l1-3-3-1-1 1-3-3 1-1-1-3ZM14 6a4 4 0 0 1 4 4"/></svg></button><button class="share-button share-telegram" type="button" data-share="telegram" aria-label="Share on Telegram" title="Share on Telegram"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m2 10 20-8-4 20-6-6-4 3v-6L22 2 6 11Z"/></svg></button></div><p class="share-status" role="status"></p><input class="share-copy" aria-label="Article link to copy" readonly hidden><noscript>Enable JavaScript to share this article.</noscript></section></div></header>
<div class="article-columns"><div class="article-main">{photo}<div class="article-content">{paragraphs}</div><div class="article-tags">{tags}</div><div class="article-origin"><strong>Source: The Comment Section Media Network</strong><p>Article text reproduces the Facebook post as published. This page does not add unverified reporting.</p>{source}</div></div>
<aside class="article-rail"><span class="eyebrow">SHARE THE CONVERSATION</span><h3>Every JUAN<br><span class="red">has a voice.</span></h3><p>Explore more news and perspectives from TCS.</p><a href="../latest.html">Browse the latest ↗</a></aside></div>
<section class="related">{page_head('KEEP READING','More stories','Latest from TCS')}<div class="card-grid">{related}</div></section></div>'''


def build_site(posts, page_name, mode, site_dir=None):
    posts = [post for post in posts if post.get("title", "").strip().casefold() != "photo or video update from tcs"]
    posts = [post for post in posts if not re.search(
        r"loud\s*fest", " ".join(str(post.get(field, "")) for field in ("title", "message", "hashtags", "categories")), re.IGNORECASE
    )]
    site = Path(site_dir) if site_dir else (ROOT / "site")
    article_dir = site / "articles"
    cat_dir = site / "category"
    data_dir = site / "data"
    for p in (site, article_dir, cat_dir, data_dir):
        p.mkdir(parents=True, exist_ok=True)
    posts = sorted(posts, key=lambda p: parse_date(p.get("created_time")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    counter = Counter(c for p in posts for c in p.get("categories", []))
    categories = [name for name,_ in counter.most_common()]
    navcats = categories[:3]
    (site / "index.html").write_text(page_shell("Home",home_page(posts,categories,mode), navcats=navcats, mode=mode),encoding="utf-8")
    (site / "latest.html").write_text(page_shell("Latest News",listing_page(posts,categories,mode,"MOST RECENT","Latest news","All imported stories, newest first.",allposts=posts),navcats=navcats,mode=mode),encoding="utf-8")
    trending = [p for p in posts if p.get("is_trending")]
    (site / "trending.html").write_text(page_shell("Trending",listing_page(trending,categories,mode,"TAGGED ON FACEBOOK","Trending","Stories whose Facebook captions contain #Trending.",allposts=posts),navcats=navcats,mode=mode),encoding="utf-8")
    for c in categories:
        chosen = [p for p in posts if c in p.get("categories",[])]
        (cat_dir / f"{category_key(c)}.html").write_text(page_shell(c,listing_page(chosen,categories,mode,"TOPIC CATEGORY",c,f"Facebook posts tagged #{c.replace(' ','')}",prefix="../",allposts=posts),prefix="../",navcats=navcats,mode=mode),encoding="utf-8")
    for idx, p in enumerate(posts):
        others = [x for x in posts if x["id"] != p["id"]][:3]
        (article_dir / f"{p['slug']}.html").write_text(page_shell(p["title"],article_page(p,others,categories,mode),prefix="../",navcats=navcats,mode=mode,description=p.get("message") or p["title"]),encoding="utf-8")
    data = {"page_name": page_name, "source_mode": mode, "generated_at": datetime.now(timezone.utc).isoformat(), "post_count": len(posts), "posts": posts}
    (data_dir / "posts.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    # Remove stale articles when regenerating a demo or switching to live content.
    valid = {p["slug"]+".html" for p in posts}
    for old in article_dir.glob("*.html"):
        if old.name not in valid:
            old.unlink()
    valid_cats = {category_key(c)+".html" for c in categories}
    for old in cat_dir.glob("*.html"):
        if old.name not in valid_cats:
            old.unlink()
    return site
