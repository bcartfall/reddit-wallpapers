# Reddit Wallpaper Fetcher

Fetches high-resolution wallpapers from configured subreddits using the
public Reddit JSON API — **no API key required**.

---

## Requirements

- Python 3.10+ (uses `tuple[int, int] | None` union syntax)
- No third-party packages — only the standard library

---

## Quick Start

```bash
# 1. Place wallpaper_fetcher.py and config.json in the same folder
# 2. Edit config.json to your liking
# 3. Run:
python wallpaper_fetcher.py
```

---

## config.json

```json
{
  "subreddits": ["EarthPorn", "Wallpapers"],
  "save_location": "./wallpapers",
  "num_posts": 50,
  "min_megapixels": 2,
  "sort": "top",
  "time_filter": "month"
}
```

| Key | Type | Description |
|---|---|---|
| `subreddits` | list[str] | Subreddits to scrape (without the `r/` prefix) |
| `save_location` | string | Directory where wallpapers are saved (created if missing) |
| `num_posts` | int | How many posts to examine per subreddit |
| `min_megapixels` | float | Minimum resolution in megapixels (e.g. `2` = 2 MP) |
| `sort` | string | Reddit sort mode: `top`, `hot`, `new`, `rising` |
| `time_filter` | string | Time window for `top`: `hour`, `day`, `week`, `month`, `year`, `all` |

---

## How It Works

1. **Fetches** posts from each subreddit via `reddit.com/r/<sub>/<sort>.json`
2. **Filters** — only keeps posts whose title contains a dimension tag like `[3840x2160]`
3. **Checks resolution** — skips images below `min_megapixels`
4. **Deduplicates** — a `.seen_urls.json` file in the save directory tracks
   every downloaded URL hash; repeat runs skip already-saved images
5. **Downloads** the image and names it after the post title, e.g.:
   `Morning_fog_in_the_Alps_3840x2160.jpg`
6. **Logs** everything — console shows INFO level; a timestamped `.log` file
   inside `<save_location>/logs/` captures full DEBUG output

---

## Output Structure

```
wallpapers/
├── logs/
│   └── wallpaper_fetcher_20250504_143021.log
├── .seen_urls.json          ← duplicate tracker (auto-managed)
├── EarthPorn/
│   ├── Morning_fog_in_the_Alps_3840x2160.jpg
│   └── ...
└── Wallpapers/
    ├── Neon_City_Rainy_Night_2560x1440.png
    └── ...
```

---

## Notes

- The fetcher uses Reddit's **public JSON API** (no OAuth needed) with a
  `User-Agent` header to comply with Reddit's API rules.
- A small delay (`0.5 s`) is inserted between downloads and `1 s` between
  paginated API requests to avoid rate-limiting.
- If Reddit returns a `429 Too Many Requests`, the script waits 60 seconds
  and retries automatically.
- Only posts with **direct image links** (`.jpg`, `.jpeg`, `.png`, `.webp`)
  or Reddit-hosted previews (`i.redd.it`) are downloaded; links to external
  galleries (Imgur albums, Flickr, etc.) without a resolvable direct URL
  are skipped.
