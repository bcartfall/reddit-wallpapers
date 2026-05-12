#!/usr/bin/env python3
"""
generate_icon.py  –  Creates a wallpaper.ico file for RedditWallpaper.
Run this once; the output goes to  resources/wallpaper.ico
Requires: Pillow  (pip install Pillow)
"""

from PIL import Image, ImageDraw
import math, os

def make_frame(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    s   = size

    # Outer frame (rounded-rect feel via filled rect)
    border = max(2, s // 10)
    frame_color = (44, 62, 80, 255)        # dark slate
    d.rectangle([0, 0, s-1, s-1], fill=frame_color, outline=frame_color)

    # Inner picture area
    inner = [border, border, s-border-1, s-border-1]
    # Sky gradient (simple: two rects)
    sky_top    = (100, 160, 220, 255)
    sky_bottom = (160, 210, 240, 255)
    mid_y = inner[1] + (inner[3] - inner[1]) * 60 // 100
    d.rectangle([inner[0], inner[1], inner[2], mid_y], fill=sky_bottom)
    d.rectangle([inner[0], inner[1], inner[2], inner[1] + (mid_y - inner[1])//2], fill=sky_top)

    # Ground
    ground_color = (74, 124, 78, 255)
    d.rectangle([inner[0], mid_y, inner[2], inner[3]], fill=ground_color)

    # Sun
    sun_r  = max(2, s // 8)
    sun_cx = inner[0] + (inner[2] - inner[0]) * 72 // 100
    sun_cy = inner[1] + (mid_y - inner[1]) * 35 // 100
    sun_color = (255, 194, 0, 255)
    d.ellipse([sun_cx - sun_r, sun_cy - sun_r,
               sun_cx + sun_r, sun_cy + sun_r], fill=sun_color)

    # Horizon line
    horizon_color = (80, 140, 90, 255)
    d.rectangle([inner[0], mid_y - max(1, s//32), inner[2], mid_y + max(1, s//32)],
                fill=horizon_color)

    return img


def main():
    sizes  = [16, 24, 32, 48, 64, 128, 256]
    frames = [make_frame(s) for s in sizes]

    out_dir = os.path.join(os.path.dirname(__file__), "resources")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "wallpaper.ico")

    frames[0].save(
        out_path,
        format="ICO",
        append_images=frames[1:],
        sizes=[(s, s) for s in sizes],
    )
    print(f"Icon saved to: {out_path}")


if __name__ == "__main__":
    main()
