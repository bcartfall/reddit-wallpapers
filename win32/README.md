# Reddit Wallpaper — Win32 System Tray App

A lightweight Win32 C++ application that rotates your desktop wallpaper from
a local SQLite database of Reddit wallpapers, with smart least-viewed weighting.

---

## Features

| Feature | Detail |
|---|---|
| **Smart selection** | 75% chance → random from least-viewed wallpapers; 25% → fully random |
| **View tracking** | `views` incremented in DB on every selection |
| **Multi-monitor** | Uses `SPI_SETDESKWALLPAPER` with `SPIF_SENDCHANGE` to update all monitors |
| **System tray** | Hidden window, no taskbar entry, tray icon with context menu |
| **Configurable** | Interval and path set via `config.json` next to the `.exe` |
| **On launch** | Immediately selects a random wallpaper before the first timer fires |

---

## Directory Layout (runtime)

```
RedditWallpaper.exe        ← the built executable
config.json                ← configuration (edit this)

<reddit_wallpaper_path>\
    db\
        wallpapers.db      ← SQLite3 database
    wallpapers\
        image1.jpg         ← wallpaper image files
        image2.png
        ...
```

---

## Database Schema

```sql
CREATE TABLE wallpapers (
    id        INTEGER PRIMARY KEY,
    filename  TEXT NOT NULL,   -- just the filename, e.g. "abc123.jpg"
    subreddit TEXT,
    width     INTEGER,
    height    INTEGER,
    views     INTEGER NOT NULL DEFAULT 0
);
```

---

## Configuration

`config.json` lives in the same folder as the `.exe`:

```json
{
    "reddit_wallpaper_path": "\\\\nas\\media\\Wallpapers\\cats",
    "minutes": 30
}
```

| Key | Type | Description |
|---|---|---|
| `reddit_wallpaper_path` | string | UNC or local path to the wallpaper root |
| `minutes` | integer | How often to rotate (minimum 1) |

> **Tip:** In JSON, backslashes must be doubled. A UNC path like `\\nas\share`
> becomes `"\\\\nas\\share"` in the file.

---

## Building

### Prerequisites

- **Visual Studio 2019 / 2022** (Desktop C++ workload)
- **CMake 3.16+** (included with VS or install separately)
- **Python 3 + Pillow** *(optional, for icon generation)*

### Quick Build (PowerShell)

Open a **"Developer PowerShell for VS 20xx"** (or run `vcvarsall.bat x64`),
then:

```powershell
cd path\to\reddit_wallpaper
.\build.ps1
```

The script will:
1. Download **nlohmann/json** single-header automatically
2. Download **SQLite3 amalgamation** automatically  
3. Generate `resources/wallpaper.ico` via Python (or use a stub)
4. Configure and build via CMake
5. Copy `RedditWallpaper.exe` + `config.json` to `dist\`

### Manual Build (MSVC command line)

```bat
:: Download SQLite3 amalgamation into src\ and nlohmann/json into third_party\
cmake -S . -B build
cmake --build build --config Release
```

---

## Tray Menu

Right-click the tray icon to access:

| Menu Item | Action |
|---|---|
| **Select Random Wallpaper** | Immediately pick and apply a new wallpaper |
| **Exit** | Close the application |

---

## Auto-start with Windows

To launch on login, place a shortcut to `RedditWallpaper.exe` in:

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
```

Or add a registry entry:

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
  "RedditWallpaper" = "C:\path\to\RedditWallpaper.exe"
```

---

## Notes

- The app uses `HWND_MESSAGE` so the window is truly invisible — no Alt+Tab, 
  no taskbar button, no visible window at all.
- `SystemParametersInfoW(SPI_SETDESKWALLPAPER, ...)` writes to the registry and
  broadcasts a `WM_SETTINGCHANGE`, which Windows uses to update all monitors.
- For per-monitor different wallpapers the Windows API requires the
  `IDesktopWallpaper` COM interface; this app sets a single wallpaper across
  all monitors (standard behaviour).
