# build.ps1 - Build RedditWallpaper using CMake + MSVC
# Run from the project root in a Visual Studio Developer PowerShell

param(
    [string]$Config = "Release",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$root   = $PSScriptRoot
$build  = Join-Path $root "build"
$out    = Join-Path $root "dist"

Write-Host "=== Reddit Wallpaper Builder ===" -ForegroundColor Cyan

# ── 1. Download dependencies if missing ─────────────────────────────────────
$thirdParty = Join-Path $root "third_party"
$srcDir     = Join-Path $root "src"
New-Item -ItemType Directory -Force -Path $thirdParty | Out-Null

$jsonHpp = Join-Path $thirdParty "json.hpp"
if (-not (Test-Path $jsonHpp)) {
    Write-Host "Downloading nlohmann/json..." -ForegroundColor Yellow
    $url = "https://github.com/nlohmann/json/releases/download/v3.11.3/json.hpp"
    Invoke-WebRequest -Uri $url -OutFile $jsonHpp
}

$sqliteH = Join-Path $srcDir "sqlite3.h"
$sqliteC = Join-Path $srcDir "sqlite3.c"
if (-not (Test-Path $sqliteH) -or -not (Test-Path $sqliteC)) {
    Write-Host "Downloading SQLite3 amalgamation..." -ForegroundColor Yellow
    $zipUrl  = "https://www.sqlite.org/2024/sqlite-amalgamation-3450200.zip"
    $zipFile = Join-Path $env:TEMP "sqlite3.zip"
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipFile
    $extracted = Join-Path $env:TEMP "sqlite3_extracted"
    Expand-Archive -Path $zipFile -DestinationPath $extracted -Force
    $srcFolder = Get-ChildItem $extracted -Directory | Select-Object -First 1
    Copy-Item (Join-Path $srcFolder.FullName "sqlite3.h") $sqliteH -Force
    Copy-Item (Join-Path $srcFolder.FullName "sqlite3.c") $sqliteC -Force
    Remove-Item $zipFile, $extracted -Recurse -Force
}

# ── 2. Generate icon (requires Python + Pillow) ──────────────────────────────
$iconPath = Join-Path $root "resources\wallpaper.ico"
if (-not (Test-Path $iconPath)) {
    Write-Host "Generating icon..." -ForegroundColor Yellow
    python (Join-Path $root "generate_icon.py") 2>$null
    if (-not (Test-Path $iconPath)) {
        Write-Host "  [warn] Icon generation failed (Pillow not installed?). Using stub." -ForegroundColor DarkYellow
        # Create a minimal 1x1 stub so the RC compiler doesn't error
        [byte[]]$stub = @(
            0,0,1,0,1,0,1,1,0,0,1,0,32,0,40,0,0,0,22,0,0,0,40,0,0,0,
            1,0,0,0,2,0,0,0,1,0,32,0,0,0,0,0,4,0,0,0,0,0,0,0,0,0,0,0,
            0,0,0,0,0,0,0,0,0x2C,0x3E,0x50,0xFF,0,0,0,0
        )
        [System.IO.File]::WriteAllBytes($iconPath, $stub)
    }
}

# ── 3. CMake configure ───────────────────────────────────────────────────────
if ($Clean -and (Test-Path $build)) {
    Write-Host "Cleaning build directory..." -ForegroundColor Yellow
    Remove-Item $build -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $build | Out-Null

Write-Host "Configuring CMake..." -ForegroundColor Yellow
cmake -S $root -B $build -DCMAKE_BUILD_TYPE=$Config 2>&1
if ($LASTEXITCODE -ne 0) { throw "CMake configuration failed" }

# ── 4. Build ─────────────────────────────────────────────────────────────────
Write-Host "Building ($Config)..." -ForegroundColor Yellow
cmake --build $build --config $Config --parallel 2>&1
if ($LASTEXITCODE -ne 0) { throw "Build failed" }

# ── 5. Copy to dist ──────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $out | Out-Null
$exeSource = Get-ChildItem -Path $build -Filter "RedditWallpaper.exe" -Recurse | Select-Object -First 1
if ($exeSource) {
    Copy-Item $exeSource.FullName (Join-Path $out "RedditWallpaper.exe") -Force
    Copy-Item (Join-Path $root "config.json") (Join-Path $out "config.json") -Force
    Write-Host ""
    Write-Host "=== Build complete! ===" -ForegroundColor Green
    Write-Host "Output: $out\RedditWallpaper.exe" -ForegroundColor Green
    Write-Host ""
    Write-Host "Before running, edit dist\config.json:" -ForegroundColor Cyan
    Write-Host '  reddit_wallpaper_path - path to your wallpaper folder' -ForegroundColor Cyan
    Write-Host '  minutes               - rotation interval' -ForegroundColor Cyan
} else {
    throw "Built executable not found"
}
