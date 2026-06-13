"""Generate vendor/icons/voxnote.ico — multi-res Windows app icon.

Run from repo root:
    python scripts/gen_icon.py

Output: vendor/icons/voxnote.ico (sizes 16, 32, 48, 64, 128, 256)
        Each resolution rendered independently for crisp small-icon text.

Design: blue rounded-square background + bold white «АТ» (VoxNote)
monogram in Russian Cyrillic. Placeholder — replace with real brand asset
when the visual identity for v0.2 is ready.

Why per-size rendering instead of save(sizes=[...]) downscale: text glyphs
at 16×16 look smeared on naive bilinear/lanczos downscale from 256. Drawing
the rounded-square + text directly at each target size keeps strokes
pixel-aligned. Slightly slower (~6 PIL calls vs 1) but trivial for a
build-time one-shot.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Brand-ish palette. Matches the existing CustomTkinter blue accent across
# the dark-theme UI (BLUE_DIM in theme.py is similar). RGBA for clean alpha
# at the rounded corners.
BG_COLOR = (74, 144, 226, 255)
FG_COLOR = (255, 255, 255, 255)
LETTERS = "АТ"

# Arial Bold ships with every Windows 10/11 install and has full Cyrillic
# coverage. Segoe UI Bold is more "Windows native" but its Cyrillic strokes
# look thinner at small sizes — Arial Bold gives a punchier monogram.
FONT_PATH = "C:/Windows/Fonts/arialbd.ttf"


def _font_size_for_icon(icon_size: int) -> int:
    """Pick a font size that visually fills ~55% of the icon side.

    At 16px the «АТ» is too tiny to be legible — we drop letters and just
    render the background colour. The caller checks `_should_render_text`.
    """
    return max(8, int(icon_size * 0.55))


def _should_render_text(icon_size: int) -> bool:
    """Below 24px the Cyrillic letters smear into illegible mush — show
    the background colour only at very small sizes so users see a clean
    blue square in the Alt-Tab thumbnail rather than smudged text."""
    return icon_size >= 24


def make_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded square — radius scales with size so the curvature looks
    # consistent across resolutions.
    radius = max(2, int(size * 0.20))
    draw.rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=BG_COLOR,
    )

    if _should_render_text(size):
        font_size = _font_size_for_icon(size)
        font = ImageFont.truetype(FONT_PATH, font_size)
        bbox = draw.textbbox((0, 0), LETTERS, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        # Subtract bbox offsets to recentre — PIL's textbbox reports the
        # glyph's actual ink bounds, not the typographic em box, so the
        # text would land off-centre without this correction.
        x = (size - text_w) // 2 - bbox[0]
        y = (size - text_h) // 2 - bbox[1] - int(size * 0.04)
        draw.text((x, y), LETTERS, font=font, fill=FG_COLOR)

    return img


def main() -> None:
    out_path = Path("vendor/icons/voxnote.ico")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sizes = [256, 128, 64, 48, 32, 16]
    images = [make_icon(s) for s in sizes]

    # PIL ICO writer: save the largest image, attach the rest via
    # append_images. Each becomes a separate resolution inside the .ico
    # container. Windows picks the right size based on context (Explorer
    # detail view = 16, Taskbar = 32, Alt-Tab = 32-48, Desktop large = 96+).
    images[0].save(
        out_path,
        format="ICO",
        sizes=[(img.width, img.height) for img in images],
        append_images=images[1:],
    )
    print(f"Wrote {out_path} with sizes {sizes}")


if __name__ == "__main__":
    main()
