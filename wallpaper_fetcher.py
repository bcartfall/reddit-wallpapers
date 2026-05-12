#!/usr/bin/env python3
"""
Reddit Wallpaper Fetcher
Fetches top wallpapers from configured subreddits using the Reddit JSON API.
"""

import json
import logging
import os
import re
import sqlite3
import struct
import sys
import time
import urllib.request
import urllib.error
import hashlib
from datetime import datetime
from pathlib import Path


# ─────────────────────────── Logging Setup ───────────────────────────────────

def setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"wallpaper_fetcher_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logger = logging.getLogger("WallpaperFetcher")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler – full debug output
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    # Console handler – info and above
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)

    logger.info(f"Logging to: {log_file}")

    cleanup_old_logs(log_dir, logger, days=30)
    return logger

def cleanup_old_logs(log_dir: Path, logger: logging.Logger, days: int = 30):
    """Deletes log files in log_dir older than the specified number of days."""
    if not log_dir.exists():
        return

    now = time.time()
    cutoff = now - (days * 86400)  # 86400 seconds in a day
    count = 0

    try:
        # Iterate through files matching the pattern
        for log_file in log_dir.glob("wallpaper_fetcher_*.log"):
            if log_file.is_file():
                file_mtime = log_file.stat().st_mtime
                if file_mtime < cutoff:
                    log_file.unlink()
                    count += 1
        
        if count > 0:
            logger.info(f"Cleaned up {count} log files older than {days} days.")
    except Exception as e:
        logger.error(f"Error during log cleanup: {e}")


# ─────────────────────────── Config Loading ──────────────────────────────────

def load_config(config_path: str = "config.json") -> dict:
    defaults = {
        "subreddits": ["EarthPorn", "Wallpapers"],
        "save_location": "./wallpapers",
        "num_posts": 50,
        "min_megapixels": 2,
        "sort": "top",
        "time_filter": "month",
    }

    if not os.path.exists(config_path):
        print(f"[WARN] '{config_path}' not found — using defaults.")
        return defaults

    with open(config_path, "r", encoding="utf-8") as f:
        user_cfg = json.load(f)

    merged = {**defaults, **user_cfg}
    return merged


# ─────────────────────────── Dimension Parsing ───────────────────────────────

DIMENSION_RE = re.compile(r"\[(\d{3,5})\s*[xX×]\s*(\d{3,5})\]")


def parse_dimensions(title: str) -> tuple[int, int] | None:
    """Return (width, height) from a title like 'Alps [3840x2160]', or None."""
    match = DIMENSION_RE.search(title)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def megapixels(width: int, height: int) -> float:
    return (width * height) / 1_000_000


# ─────────────────────────── Image Dimension Reading ─────────────────────────

def read_image_dimensions(path: Path) -> tuple[int, int] | None:
    """
    Read (width, height) from the raw bytes of a JPEG, PNG, or WebP file.
    Returns None if the format is unrecognised or the file is truncated.
    No third-party libraries required.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None

    # ── PNG ──────────────────────────────────────────────────────────────────
    # Signature: 8 bytes, then IHDR chunk (4 len + 4 "IHDR" + 4 w + 4 h + …)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        if len(data) >= 24:
            w, h = struct.unpack(">II", data[16:24])
            return w, h
        return None

    # ── JPEG ─────────────────────────────────────────────────────────────────
    # Walk SOF markers (0xFFC0 / FFC1 / FFC2) which contain dimensions.
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 4 <= len(data):
            if data[i] != 0xFF:
                break
            marker = data[i + 1]
            # SOF0 / SOF1 / SOF2 (baseline, extended, progressive)
            if marker in (0xC0, 0xC1, 0xC2):
                if i + 9 <= len(data):
                    h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                    return w, h
                break
            # Skip this segment
            if i + 4 > len(data):
                break
            length = struct.unpack(">H", data[i + 2 : i + 4])[0]
            i += 2 + length
        return None

    # ── WebP ─────────────────────────────────────────────────────────────────
    # RIFF????WEBP  VP8  / VP8L / VP8X
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8 " and len(data) >= 30:
            # Lossy: width/height stored as little-endian 14-bit values at offsets 26/28
            w = (struct.unpack("<H", data[26:28])[0] & 0x3FFF) + 1
            h = (struct.unpack("<H", data[28:30])[0] & 0x3FFF) + 1
            return w, h
        if chunk == b"VP8L" and len(data) >= 25:
            # Lossless: packed bits starting at byte 21
            bits = struct.unpack("<I", data[21:25])[0]
            w = (bits & 0x3FFF) + 1
            h = ((bits >> 14) & 0x3FFF) + 1
            return w, h
        if chunk == b"VP8X" and len(data) >= 30:
            # Extended: canvas width/height as 24-bit LE at offsets 24/27
            w = struct.unpack("<I", data[24:27] + b"\x00")[0] + 1
            h = struct.unpack("<I", data[27:30] + b"\x00")[0] + 1
            return w, h

    return None


# ─────────────────────────── Filename Sanitisation ───────────────────────────

def safe_filename(title: str, ext: str, subreddit: str, dims: tuple[int, int] | None = None) -> str:
    """
    Convert a post title to a safe filename.
    If `dims` is given it is used for the dimension suffix; otherwise the
    dimension tag already present in the title (if any) is extracted.
    """
    # Prefer caller-supplied dims; fall back to parsing the title.
    if dims:
        w, h = dims
        dim_str = f"_{w}x{h}"
        # Still strip any tag that happens to be in the title.
        title = DIMENSION_RE.sub("", title)
    else:
        dim_match = DIMENSION_RE.search(title)
        if dim_match:
            w, h = dim_match.group(1), dim_match.group(2)
            dim_str = f"_{w}x{h}"
            title = title[: dim_match.start()] + title[dim_match.end() :]
        else:
            dim_str = ""

    # Remove characters unsafe for filenames
    clean = re.sub(r'[\\/:*?"<>|]', "", title)
    # Collapse whitespace / underscores
    clean = re.sub(r"[\s_]+", "_", clean).strip("_")
    # Limit length
    if len(clean) > 180:
        clean = clean[:180]

    return f"{clean}{dim_str}_{subreddit}{ext}"


# ─────────────────────────── Reddit API ──────────────────────────────────────

HEADERS = {
    "User-Agent": "WallpaperFetcher/1.0 (python; educational use)"
}


def reddit_request(url: str, logger: logging.Logger) -> dict | None:
    """Perform a GET request to the Reddit JSON API with basic rate-limit handling."""
    logger.debug(f"GET {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
            logger.warning(f"HTTP {resp.status} for {url}")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            logger.warning("Rate-limited by Reddit — sleeping 5 s …")
            time.sleep(5)
        else:
            logger.error(f"HTTP error {e.code}: {e.reason} — {url}")
    except urllib.error.URLError as e:
        logger.error(f"URL error: {e.reason} — {url}")
    except Exception as e:
        logger.error(f"Unexpected error fetching {url}: {e}")
    return None


def fetch_posts(subreddit: str, sort: str, time_filter: str,
                limit: int, logger: logging.Logger) -> list[dict]:
    """
    Fetch up to `limit` posts from a subreddit using Reddit's JSON API,
    paginating with 'after' tokens as needed (max 100 per request).
    """
    posts = []
    after = None
    page = 0

    while len(posts) < limit:
        batch = min(100, limit - len(posts))
        url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit={batch}&t={time_filter}"
        if after:
            url += f"&after={after}"

        page += 1
        logger.info(f"  Fetching page {page} from r/{subreddit} ({len(posts)}/{limit} so far) …")
        data = reddit_request(url, logger)

        if not data:
            logger.error(f"  Failed to fetch page {page} from r/{subreddit} — aborting subreddit.")
            break

        children = data.get("data", {}).get("children", [])
        if not children:
            logger.info(f"  No more posts available in r/{subreddit}.")
            break

        for child in children:
            posts.append(child.get("data", {}))

        after = data.get("data", {}).get("after")
        if not after:
            break

        # Be polite to Reddit's API
        # time.sleep(1)

    logger.info(f"  Retrieved {len(posts)} raw posts from r/{subreddit}.")
    return posts


# ─────────────────────────── Image Download ──────────────────────────────────

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def is_direct_image(url: str) -> str | None:
    """Return the file extension if the URL is a direct image link, else None."""
    path = url.split("?")[0].lower()
    for ext in IMAGE_EXTENSIONS:
        if path.endswith(ext):
            return os.path.splitext(url.split("?")[0])[1]
    return None


def resolve_gallery_urls(post: dict, logger: logging.Logger) -> list[tuple[str, str]]:
    """
    For reddit.com/gallery/… posts, return a list of (url, ext) pairs — one
    per gallery item — using the highest-resolution i.redd.it source available.
    Returns an empty list if the post is not a gallery or has no media metadata.
    """
    if not post.get("is_gallery"):
        return []

    media_metadata = post.get("media_metadata", {})
    gallery_data = post.get("gallery_data", {})
    # gallery_data.items preserves the display order; fall back to metadata keys.
    item_ids: list[str] = [
        item["media_id"]
        for item in gallery_data.get("items", [])
        if "media_id" in item
    ] or list(media_metadata.keys())

    results: list[tuple[str, str]] = []
    for media_id in item_ids:
        meta = media_metadata.get(media_id, {})
        status = meta.get("status", "")
        if status != "valid":
            logger.debug(f"    Gallery item {media_id} skipped (status={status})")
            continue

        mime = meta.get("m", "image/jpeg")
        ext = "." + mime.split("/")[-1]  # e.g. "image/png" → ".png"
        if ext not in IMAGE_EXTENSIONS:
            ext = ".jpg"

        # Prefer the full-resolution 's' source; fall back to largest 'p' preview.
        src = meta.get("s", {})
        url = src.get("u", "") or src.get("gif", "")
        if not url:
            previews = meta.get("p", [])
            if previews:
                url = previews[-1].get("u", "")
        url = url.replace("&amp;", "&")
        if url:
            results.append((url, ext))
            logger.debug(f"    Gallery item {media_id}: {url[:80]}…")

    logger.info(f"  Gallery: resolved {len(results)} image(s).")
    return results


def resolve_image_url(post: dict, logger: logging.Logger) -> tuple[str, str] | None:
    """
    Resolve the best direct image URL from a post dict.
    Returns (url, extension) or None.
    Does NOT handle galleries (use resolve_gallery_urls for those).
    """
    url = post.get("url", "")

    # Direct image link
    ext = is_direct_image(url)
    if ext:
        return url, ext

    # Reddit-hosted image (i.redd.it) – may lack a recognised extension
    if "i.redd.it" in url:
        ext = os.path.splitext(url)[1] or ".jpg"
        return url, ext

    # Preview image (covers many cross-posts and external links)
    preview = post.get("preview", {})
    images = preview.get("images", [])
    if images:
        source = images[0].get("source", {})
        src_url = source.get("url", "").replace("&amp;", "&")
        if src_url:
            ext = os.path.splitext(src_url.split("?")[0])[1] or ".jpg"
            logger.debug(f"    Using preview source URL: {src_url[:80]}…")
            return src_url, ext

    logger.debug(f"    Cannot resolve image URL from: {url}")
    return None


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def download_image(url: str, dest_path: Path, filename: str, logger: logging.Logger) -> bool:
    """Download a URL to dest_path. Returns True on success."""
    logger.debug(f"  Downloading: {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        dest_path.write_bytes(data)
        size_kb = len(data) / 1024
        logger.info(f"  ✓ Saved ({size_kb:.0f} KB): {filename}")
        return True
    except Exception as e:
        logger.error(f"  ✗ Download failed for {url}: {e}")
        return False


# ─────────────────────────── Seen-URL Tracking ───────────────────────────────

SEEN_FILE = ".seen_urls.json"


def load_seen(save_dir: Path) -> set[str]:
    path = save_dir / SEEN_FILE
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_seen(save_dir: Path, seen: set[str]) -> None:
    path = save_dir / SEEN_FILE
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, indent=2)


# ─────────────────────────── SQLite Database ─────────────────────────────────

DB_FILE = "wallpapers.db"


def init_db(save_dir: Path) -> sqlite3.Connection:
    """Open (or create) the SQLite database and ensure the wallpapers table exists."""
    db_path = save_dir / DB_FILE
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wallpapers (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            filename  TEXT    NOT NULL,
            subreddit TEXT    NOT NULL,
            width     INTEGER NOT NULL,
            height    INTEGER NOT NULL,
            views     INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def db_insert(conn: sqlite3.Connection, filename: str, subreddit: str,
              width: int, height: int, views: int, logger: logging.Logger) -> None:
    """Insert a downloaded wallpaper record into the database."""
    try:
        conn.execute(
            "INSERT INTO wallpapers (filename, subreddit, width, height, views) "
            "VALUES (?, ?, ?, ?, ?)",
            (filename, subreddit, width, height, views),
        )
        conn.commit()
        logger.debug(f"  DB insert: {filename} ({subreddit} {width}x{height} views={views})")
    except sqlite3.Error as e:
        logger.error(f"  DB insert failed for '{filename}': {e}")


# ─────────────────────────── Main Logic ──────────────────────────────────────

def _download_one(
    img_url: str,
    ext: str,
    title: str,
    post_id: str,
    subreddit: str,
    title_dims: tuple[int, int] | None,
    min_megapixels: float,
    save_dir: Path,
    seen_urls: set[str],
    db_conn: sqlite3.Connection,
    logger: logging.Logger,
    stats: dict,
    index: int | None = None,
) -> None:
    """Download a single image URL and record it.  `index` is used for gallery items."""
    uid = url_hash(img_url)
    if uid in seen_urls:
        logger.info(f"  Skip — already downloaded: {title[:60]}")
        stats["duplicate"] += 1
        return
    seen_urls.add(uid)

    # Build a unique suffix for gallery items
    gallery_suffix = f"_p{index}" if index is not None else ""

    filename = safe_filename(title + gallery_suffix, ext, subreddit, dims=title_dims)
    dest = save_dir / f"_tmp.{ext}" # save as working file
    dest.unlink(missing_ok=True)
    if dest.exists():
        base, extension = os.path.splitext(filename)
        filename = f"{base}_{post_id}{extension}"
        dest = save_dir / filename

    success = download_image(img_url, dest, filename, logger)
    if not success:
        stats["failed"] += 1
        return

    # ── Determine actual resolution from the downloaded file ──────────────
    actual_dims = read_image_dimensions(dest)
    if actual_dims:
        w, h = actual_dims
        logger.debug(f"    Measured resolution: {w}×{h}")

        mp = megapixels(w, h)
        if mp < min_megapixels:
            logger.info(f"    Skip — too small ({w}×{h} = {mp:.2f} MP < {min_megapixels} MP): {title[:60]}")
            stats["too_small"] += 1
            return

        # Rename file to include correct dimensions if they differ from title
        new_filename = safe_filename(title + gallery_suffix, ext, subreddit, dims=actual_dims)
        new_dest = save_dir / new_filename
        if new_dest.exists() and new_dest != dest:
            base, extension = os.path.splitext(new_filename)
            new_filename = f"{base}_{post_id}{extension}"
            new_dest = save_dir / new_filename
        new_dest.unlink(missing_ok=True)
        dest.rename(new_dest)
        filename = new_filename
        logger.debug(f"    Renamed to: {filename}")

    else:
        logger.warning(f"    Could not determine resolution for {filename} — skipping DB insert")
        seen_urls.add(uid)
        stats["downloaded"] += 1
        return

    logger.info(f"    ✓ {w}×{h} ({mp:.1f} MP): {filename}")
    stats["downloaded"] += 1
    db_insert(db_conn, filename, subreddit, w, h, 0, logger)


def process_subreddit(subreddit: str, cfg: dict, save_dir: Path,
                      seen_urls: set[str], db_conn: sqlite3.Connection,
                      logger: logging.Logger) -> dict:
    """Fetch, filter, and download wallpapers from one subreddit."""
    stats = {
        "fetched": 0,
        "no_dimensions": 0,  # kept for summary compat (now informational only)
        "highlight": 0,
        "too_small": 0,
        "not_image": 0,
        "duplicate": 0,
        "downloaded": 0,
        "failed": 0,
    }

    logger.info(f"━━━ Processing r/{subreddit} ━━━")
    posts = fetch_posts(
        subreddit=subreddit,
        sort=cfg["sort"],
        time_filter=cfg["time_filter"],
        limit=cfg["num_posts"],
        logger=logger,
    )
    stats["fetched"] = len(posts)
    min_mp = cfg["min_megapixels"]

    for post in posts:
        title = post.get("title", "")
        post_id = post.get("id", "?")
        logger.debug(f"Post [{post_id}]: {title[:80]}")

        # Skip Reddit community highlight posts
        if post.get("community_highlight"):
            logger.info(f"  Skip — community highlight: {title[:60]}")
            stats["highlight"] += 1
            continue

        # Dimensions from title are optional — used as a fallback only.
        title_dims = parse_dimensions(title)
        if title_dims is None:
            logger.debug(f"  No dimensions in title (will measure after download): '{title[:60]}'")
            stats["no_dimensions"] += 1
            # Do NOT skip — carry on.

        common = dict(
            title=title,
            post_id=post_id,
            subreddit=subreddit,
            title_dims=title_dims,
            min_megapixels=min_mp,
            save_dir=save_dir,
            seen_urls=seen_urls,
            db_conn=db_conn,
            logger=logger,
            stats=stats,
        )

        # ── Gallery posts ────────────────────────────────────────────────────
        if post.get("is_gallery"):
            gallery_items = resolve_gallery_urls(post, logger)
            if not gallery_items:
                logger.info(f"  Skip — gallery with no resolvable images: {title[:60]}")
                stats["not_image"] += 1
                continue
            for idx, (img_url, ext) in enumerate(gallery_items):
                _download_one(img_url=img_url, ext=ext, index=idx, **common)
                time.sleep(0.01)
            continue

        # ── Single-image posts ───────────────────────────────────────────────
        result = resolve_image_url(post, logger)
        if result is None:
            logger.info(f"  Skip — not a direct image: {post.get('url', '')[:80]}")
            stats["not_image"] += 1
            continue

        img_url, ext = result
        _download_one(img_url=img_url, ext=ext, index=None, **common)
        time.sleep(0.01)

    return stats


def main():
    cfg = load_config("config.json")

    save_dir = Path(cfg["save_location"]).expanduser().resolve()
    save_dir.mkdir(parents=True, exist_ok=True)

    wallpaper_dir = save_dir / "wallpapers"
    wallpaper_dir.mkdir(parents=True, exist_ok=True)

    db_dir = save_dir / "db"
    db_dir.mkdir(parents=True, exist_ok=True)

    log_dir = save_dir / "logs"
    logger = setup_logging(log_dir)

    logger.info("═" * 60)
    logger.info("Reddit Wallpaper Fetcher — starting")
    logger.info(f"Subreddits : {cfg['subreddits']}")
    logger.info(f"Posts/sub  : {cfg['num_posts']}")
    logger.info(f"Min MP     : {cfg['min_megapixels']}")
    logger.info(f"Sort       : {cfg['sort']} / {cfg['time_filter']}")
    logger.info(f"Save dir   : {save_dir}")
    logger.info("═" * 60)

    seen_urls = load_seen(db_dir)
    logger.info(f"Loaded {len(seen_urls)} previously-seen URLs.")

    db_conn = init_db(db_dir)
    logger.info(f"Database   : {save_dir / DB_FILE}")

    totals = {
        "fetched": 0,
        "no_dimensions": 0,
        "highlight": 0,
        "too_small": 0,
        "not_image": 0,
        "duplicate": 0,
        "downloaded": 0,
        "failed": 0,
    }

    for subreddit in cfg["subreddits"]:
        stats = process_subreddit(subreddit, cfg, wallpaper_dir, seen_urls, db_conn, logger)
        for k in totals:
            totals[k] += stats[k]
        save_seen(db_dir, seen_urls)  # Persist after each subreddit

    db_conn.close()

    logger.info("═" * 60)
    logger.info("Summary")
    logger.info(f"  Posts fetched    : {totals['fetched']}")
    logger.info(f"  No title dims    : {totals['no_dimensions']} (measured from file)")
    logger.info(f"  Highlights skip  : {totals['highlight']}")
    logger.info(f"  Too small        : {totals['too_small']}")
    logger.info(f"  Not a direct img : {totals['not_image']}")
    logger.info(f"  Duplicates       : {totals['duplicate']}")
    logger.info(f"  Downloaded ✓     : {totals['downloaded']}")
    logger.info(f"  Failed ✗         : {totals['failed']}")
    logger.info("═" * 60)
    logger.info("Done.")

    # clear tmp files
    for tmp_file in wallpaper_dir.glob("_tmp.*"):
        logger.info(f"Clearing tmp file {tmp_file}.")
        tmp_file.unlink(missing_ok=True)

if __name__ == "__main__":
    main()
