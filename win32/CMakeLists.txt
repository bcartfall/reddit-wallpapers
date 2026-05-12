cmake_minimum_required(VERSION 3.16)
project(RedditWallpaper LANGUAGES CXX C)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Win32 GUI application (no console window)
add_executable(RedditWallpaper WIN32
    src/main.cpp
    src/sqlite3.c        # SQLite3 amalgamation
    resources/app.rc     # Resource file (icon)
)

target_include_directories(RedditWallpaper PRIVATE
    src
    third_party
)

target_link_libraries(RedditWallpaper PRIVATE
    shell32
    user32
    gdi32
    comctl32
    comdlg32
)

# Link SQLite3 dependencies
target_link_libraries(RedditWallpaper PRIVATE)

# MSVC-specific flags
if(MSVC)
    target_compile_options(RedditWallpaper PRIVATE /W3 /MP)
    # Silence SQLite3 warnings
    set_source_files_properties(src/sqlite3.c PROPERTIES
        COMPILE_FLAGS "/wd4996 /wd4244"
    )
    # Static CRT
    set_property(TARGET RedditWallpaper PROPERTY
        MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>"
    )
endif()

# Output name
set_target_properties(RedditWallpaper PROPERTIES
    OUTPUT_NAME "RedditWallpaper"
)
