#define WIN32_LEAN_AND_MEAN
#define UNICODE
#define _UNICODE

#include <windows.h>
#include <shellapi.h>
#include <shlobj.h>
#include <commctrl.h>
#include <string>
#include <vector>
#include <random>
#include <chrono>
#include <fstream>
#include <sstream>
#include <algorithm>
#include <stdexcept>

// SQLite3 - single header amalgamation assumed compiled in
#include "sqlite3.h"

// nlohmann/json - single header
#include "json.hpp"

#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "comctl32.lib")

using json = nlohmann::json;

// ─── Constants ──────────────────────────────────────────────────────────────
static const UINT WM_TRAYICON      = WM_APP + 1;
static const UINT ID_TRAY_SELECT   = WM_APP + 2;
static const UINT ID_TRAY_EXIT     = WM_APP + 3;
static const UINT ID_TIMER         = WM_APP + 4;
static const wchar_t* APP_CLASS    = L"RedditWallpaperClass";
static const wchar_t* APP_TITLE    = L"Reddit Wallpapers";

// ─── Config ─────────────────────────────────────────────────────────────────
struct Config {
    std::wstring reddit_wallpaper_path;
    int          minutes = 30;
};

// ─── Wallpaper entry ─────────────────────────────────────────────────────────
struct Wallpaper {
    int64_t     id;
    std::wstring filename;
    std::wstring subreddit;
    int          width;
    int          height;
    int          views;
};

// ─── Globals ─────────────────────────────────────────────────────────────────
static Config        g_config;
static HWND          g_hwnd   = nullptr;
static NOTIFYICONDATA g_nid   = {};
static sqlite3*      g_db     = nullptr;
static std::mt19937  g_rng(std::random_device{}());

// ─── Helpers ─────────────────────────────────────────────────────────────────
static std::wstring Utf8ToWide(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
    std::wstring w(n - 1, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, &w[0], n);
    return w;
}

static std::string WideToUtf8(const std::wstring& w) {
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), -1, nullptr, 0, nullptr, nullptr);
    std::string s(n - 1, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.c_str(), -1, &s[0], n, nullptr, nullptr);
    return s;
}

// ─── Config loading ──────────────────────────────────────────────────────────
static std::wstring GetExeDir() {
    wchar_t buf[MAX_PATH];
    GetModuleFileNameW(nullptr, buf, MAX_PATH);
    std::wstring p(buf);
    auto pos = p.rfind(L'\\');
    return (pos != std::wstring::npos) ? p.substr(0, pos) : p;
}

static bool LoadConfig(const std::wstring& configPath) {
    std::ifstream f(configPath);
    if (!f.is_open()) return false;
    try {
        json j;
        f >> j;
        std::string raw = j.value("reddit_wallpaper_path", "");
        g_config.reddit_wallpaper_path = Utf8ToWide(raw);
        g_config.minutes = j.value("minutes", 30);
        if (g_config.minutes < 1) g_config.minutes = 1;
        return !g_config.reddit_wallpaper_path.empty();
    } catch (...) {
        return false;
    }
}

// ─── Database ────────────────────────────────────────────────────────────────
static bool OpenDatabase() {
    std::wstring dbPath = g_config.reddit_wallpaper_path + L"\\db\\wallpapers.db";
    std::string  dbPathU8 = WideToUtf8(dbPath);
    int rc = sqlite3_open(dbPathU8.c_str(), &g_db);
    return (rc == SQLITE_OK);
}

static void CloseDatabase() {
    if (g_db) { sqlite3_close(g_db); g_db = nullptr; }
}

// Returns a wallpaper using the weighted selection logic:
//   75% → pick randomly among rows with the minimum view count
//   25% → pick randomly from the entire table
static bool SelectWallpaper(Wallpaper& out) {
    if (!g_db) return false;

    std::uniform_real_distribution<double> coin(0.0, 1.0);
    bool useLeastViewed = (coin(g_rng) < 0.75);

    std::string sql;
    if (useLeastViewed) {
        // All rows sharing the minimum views value
        sql = "SELECT id, filename, subreddit, width, height, views "
              "FROM wallpapers "
              "WHERE active = 1 "
              "ORDER BY views ASC, RANDOM() LIMIT 1;";
    } else {
        sql = "SELECT id, filename, subreddit, width, height, views "
              "FROM wallpapers "
              "WHERE active = 1 "
              "ORDER BY RANDOM() LIMIT 1;";
    }

    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(g_db, sql.c_str(), -1, &stmt, nullptr) != SQLITE_OK)
        return false;

    bool found = false;
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        out.id       = sqlite3_column_int64(stmt, 0);
        auto fn      = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
        auto sr      = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2));
        out.filename = fn ? Utf8ToWide(fn) : L"";
        out.subreddit= sr ? Utf8ToWide(sr) : L"";
        out.width    = sqlite3_column_int(stmt, 3);
        out.height   = sqlite3_column_int(stmt, 4);
        out.views    = sqlite3_column_int(stmt, 5);
        found = true;
    }
    sqlite3_finalize(stmt);

    if (found) {
        // Increment views
        std::string upd = "UPDATE wallpapers SET views = views + 1 WHERE id = ?;";
        sqlite3_stmt* upd_stmt = nullptr;
        if (sqlite3_prepare_v2(g_db, upd.c_str(), -1, &upd_stmt, nullptr) == SQLITE_OK) {
            sqlite3_bind_int64(upd_stmt, 1, out.id);
            sqlite3_step(upd_stmt);
            sqlite3_finalize(upd_stmt);
        }
    }
    return found;
}

// ─── Wallpaper application ───────────────────────────────────────────────────

// Marks a wallpaper row inactive (file missing / inaccessible).
static void DeactivateWallpaper(int64_t id) {
    if (!g_db) return;
    const char* sql = "UPDATE wallpapers SET active = 0 WHERE id = ?;";
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(g_db, sql, -1, &stmt, nullptr) == SQLITE_OK) {
        sqlite3_bind_int64(stmt, 1, id);
        sqlite3_step(stmt);
        sqlite3_finalize(stmt);
    }
}

// Returns false (and deactivates the row) if the file does not exist on disk.
static bool ApplyWallpaper(const Wallpaper& wp) {
    std::wstring path = g_config.reddit_wallpaper_path + L"\\wallpapers\\" + wp.filename;

    // Check the file actually exists before handing it to the shell.
    if (GetFileAttributesW(path.c_str()) == INVALID_FILE_ATTRIBUTES) {
        DeactivateWallpaper(wp.id);
        return false;
    }

    // SystemParametersInfo requires the path to be stored persistently.
    // Use SPIF_UPDATEINIFILE | SPIF_SENDCHANGE so all monitors pick it up
    // (Windows spans the wallpaper or tiles per monitor based on personalisation settings).
    BOOL ok = SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0,
        const_cast<wchar_t*>(path.c_str()),
        SPIF_UPDATEINIFILE | SPIF_SENDCHANGE);
    return ok != FALSE;
}

// ─── Core action ─────────────────────────────────────────────────────────────

// Returns the count of active wallpapers remaining in the DB.
static int ActiveWallpaperCount() {
    if (!g_db) return 0;
    const char* sql = "SELECT COUNT(*) FROM wallpapers WHERE active = 1;";
    sqlite3_stmt* stmt = nullptr;
    int count = 0;
    if (sqlite3_prepare_v2(g_db, sql, -1, &stmt, nullptr) == SQLITE_OK) {
        if (sqlite3_step(stmt) == SQLITE_ROW)
            count = sqlite3_column_int(stmt, 0);
        sqlite3_finalize(stmt);
    }
    return count;
}

// Keeps picking candidates until one is successfully applied or no active
// wallpapers remain.  Files that are missing are deactivated on the fly so
// they are never tried again.
static void PickAndApplyRandom() {
    while (ActiveWallpaperCount() > 0) {
        Wallpaper wp{};
        if (!SelectWallpaper(wp)) break;        // DB error or truly empty
        if (ApplyWallpaper(wp)) break;          // success
        // ApplyWallpaper returned false → file was missing, row deactivated;
        // loop and try the next candidate.
    }
}

// ─── Tray icon ───────────────────────────────────────────────────────────────
// Embedded minimal 32x32 wallpaper icon (generated programmatically as HICON)
static HICON CreateTrayIcon() {
    // Create a simple 32x32 icon depicting a picture frame / landscape
    int sz = 32;
    HDC hdc = GetDC(nullptr);
    HDC memDC = CreateCompatibleDC(hdc);

    BITMAPINFO bmi = {};
    bmi.bmiHeader.biSize        = sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth       = sz;
    bmi.bmiHeader.biHeight      = -sz;
    bmi.bmiHeader.biPlanes      = 1;
    bmi.bmiHeader.biBitCount    = 32;
    bmi.bmiHeader.biCompression = BI_RGB;

    void* pixels = nullptr;
    HBITMAP hbm = CreateDIBSection(memDC, &bmi, DIB_RGB_COLORS, &pixels, nullptr, 0);
    HBITMAP oldBm = (HBITMAP)SelectObject(memDC, hbm);

    // Draw icon: sky gradient + ground + sun
    uint32_t* px = (uint32_t*)pixels;
    for (int y = 0; y < sz; y++) {
        for (int x = 0; x < sz; x++) {
            uint32_t c;
            // Outer border / frame (dark)
            if (x < 2 || x >= sz-2 || y < 2 || y >= sz-2) {
                c = 0xFF2C3E50; // dark blue-gray frame
            }
            // Sky (top 60%)
            else if (y < sz * 0.6) {
                uint8_t b = (uint8_t)(180 + (75 * y) / (sz * 6 / 10));
                uint8_t g = (uint8_t)(200 + (55 * y) / (sz * 6 / 10));
                c = 0xFF000000 | (0x87 << 16) | (g << 8) | b; // sky blue gradient
            }
            // Ground (bottom 40%)
            else {
                c = 0xFF4A7C4E; // green
            }
            // Sun
            int dx = x - 22, dy = y - 10;
            if (dx*dx + dy*dy <= 16) c = 0xFFFFC200;

            px[y * sz + x] = c;
        }
    }

    // Create mask bitmap (all black = opaque)
    HBITMAP hMask = CreateCompatibleBitmap(hdc, sz, sz);
    HDC maskDC = CreateCompatibleDC(hdc);
    HBITMAP oldMask = (HBITMAP)SelectObject(maskDC, hMask);
    PatBlt(maskDC, 0, 0, sz, sz, BLACKNESS);
    SelectObject(maskDC, oldMask);
    DeleteDC(maskDC);

    SelectObject(memDC, oldBm);
    DeleteDC(memDC);
    ReleaseDC(nullptr, hdc);

    ICONINFO ii = {};
    ii.fIcon    = TRUE;
    ii.hbmColor = hbm;
    ii.hbmMask  = hMask;
    HICON icon = CreateIconIndirect(&ii);

    DeleteObject(hbm);
    DeleteObject(hMask);
    return icon;
}

static void AddTrayIcon(HWND hwnd, HICON hIcon) {
    g_nid.cbSize           = sizeof(g_nid);
    g_nid.hWnd             = hwnd;
    g_nid.uID              = 1;
    g_nid.uFlags           = NIF_ICON | NIF_MESSAGE | NIF_TIP;
    g_nid.uCallbackMessage = WM_TRAYICON;
    g_nid.hIcon            = hIcon;
    wcscpy_s(g_nid.szTip, APP_TITLE);
    Shell_NotifyIconW(NIM_ADD, &g_nid);
}

static void RemoveTrayIcon() {
    Shell_NotifyIconW(NIM_DELETE, &g_nid);
}

static void ShowTrayMenu(HWND hwnd) {
    HMENU hMenu = CreatePopupMenu();
    AppendMenuW(hMenu, MF_STRING, ID_TRAY_SELECT, L"Select Random Wallpaper");
    AppendMenuW(hMenu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(hMenu, MF_STRING, ID_TRAY_EXIT,   L"Exit");

    // Must set foreground window to make menu dismiss properly
    SetForegroundWindow(hwnd);

    POINT pt;
    GetCursorPos(&pt);
    TrackPopupMenu(hMenu, TPM_RIGHTBUTTON | TPM_BOTTOMALIGN,
                   pt.x, pt.y, 0, hwnd, nullptr);
    DestroyMenu(hMenu);
}

// ─── Window procedure ─────────────────────────────────────────────────────────
static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_CREATE:
        // Timer: milliseconds
        SetTimer(hwnd, ID_TIMER, g_config.minutes * 60 * 1000, nullptr);
        return 0;

    case WM_TIMER:
        if (wp == ID_TIMER) {
            PickAndApplyRandom();
        }
        return 0;

    case WM_TRAYICON:
        if (lp == WM_RBUTTONUP || lp == WM_LBUTTONUP) {
            ShowTrayMenu(hwnd);
        }
        return 0;

    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case ID_TRAY_SELECT:
            PickAndApplyRandom();
            break;
        case ID_TRAY_EXIT:
            DestroyWindow(hwnd);
            break;
        }
        return 0;

    case WM_DESTROY:
        KillTimer(hwnd, ID_TIMER);
        RemoveTrayIcon();
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

// ─── Entry point ─────────────────────────────────────────────────────────────
int WINAPI wWinMain(HINSTANCE hInst, HINSTANCE, LPWSTR, int) {
    // Load config from same directory as exe
    std::wstring cfgPath = GetExeDir() + L"\\config.json";
    if (!LoadConfig(cfgPath)) {
        MessageBoxW(nullptr,
            L"Failed to load config.json.\n\n"
            L"Expected format:\n"
            L"{\n"
            L"  \"reddit_wallpaper_path\": \"\\\\\\\\nas\\\\media\\\\Wallpapers\\\\cats\",\n"
            L"  \"minutes\": 30\n"
            L"}",
            L"Reddit Wallpapers - Config Error",
            MB_ICONERROR | MB_OK);
        return 1;
    }

    if (!OpenDatabase()) {
        std::wstring msg = L"Failed to open database:\n" +
            g_config.reddit_wallpaper_path + L"\\db\\wallpapers.db";
        MessageBoxW(nullptr, msg.c_str(), L"Reddit Wallpapers - DB Error",
                    MB_ICONERROR | MB_OK);
        return 1;
    }

    // Register window class
    WNDCLASSEXW wc = {};
    wc.cbSize        = sizeof(wc);
    wc.lpfnWndProc   = WndProc;
    wc.hInstance     = hInst;
    wc.lpszClassName = APP_CLASS;
    RegisterClassExW(&wc);

    // Create hidden message-only window (no taskbar entry)
    g_hwnd = CreateWindowExW(
        0, APP_CLASS, APP_TITLE,
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, 0, 0,
        HWND_MESSAGE,   // message-only: never shown, never in taskbar
        nullptr, hInst, nullptr);

    if (!g_hwnd) return 1;

    // Tray icon
    HICON hIcon = CreateTrayIcon();
    AddTrayIcon(g_hwnd, hIcon);

    // Apply wallpaper immediately on launch
    PickAndApplyRandom();

    // Message loop
    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }

    CloseDatabase();
    DestroyIcon(hIcon);
    return (int)msg.wParam;
}