"""Text-Rendering mit Pillow.

Dieses ffmpeg hat **kein** ``drawtext``/``subtitles``/``ass`` (kein
libfreetype). Deshalb wird jeder Text **hier** als PNG gerendert und
spaeter per ffmpeg-``overlay`` eingebrannt.

Regeln aus ``.agents/rules/pipeline-architecture.md``:

- Thumbnail immer 1080x1920
- Systemschriften (reproduzierbar, kein Download)
- Text immer mit Outline/Schlagschatten — auf hellem Gameplay ist Text
  sonst unlesbar
- Kontrast gegen den Hintergrund >= 4.5:1
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Systemschriften (macOS). Arial Black fuer Headlines, Arial Bold fuer Rest.
DEFAULT_FONT = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
DEFAULT_FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)


class RenderError(Exception):
    """Fehler beim Rendern von Text oder Bildern."""


def hex_to_rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    """Wandelt ``#RRGGBB`` oder ``#AARRGGBB`` in ein RGBA-Tupel."""
    raw = value.strip().lstrip("#")
    if len(raw) not in (6, 8):
        raise RenderError(f"Ungueltiger Hex-Farbwert: {value}")
    try:
        if len(raw) == 6:
            return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16), alpha)
        # 8-stellig: #AARRGGBB
        return (
            int(raw[2:4], 16),
            int(raw[4:6], 16),
            int(raw[6:8], 16),
            int(raw[0:2], 16),
        )
    except ValueError as exc:
        raise RenderError(f"Ungueltiger Hex-Farbwert: {value}") from exc


def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    """Laedt eine TrueType-Schrift, mit Fallback auf die Default-Schrift."""
    font_path = Path(path)
    if not font_path.is_file():
        fallback = Path(DEFAULT_FONT)
        if not fallback.is_file():
            raise RenderError(f"Keine Schrift gefunden: {path} (Fallback fehlt auch)")
        font_path = fallback
    try:
        return ImageFont.truetype(str(font_path), size)
    except OSError as exc:
        raise RenderError(f"Schrift nicht ladbar: {font_path}: {exc}") from exc


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    """Relative Leuchtdichte nach WCAG."""
    channels = []
    for value in rgb[:3]:
        c = value / 255.0
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    """Kontrastverhaeltnis nach WCAG (1.0 bis 21.0)."""
    l1 = relative_luminance(fg)
    l2 = relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
              max_width: int) -> list[str]:
    """Bricht Text auf eine maximale Breite um (wortweise)."""
    words = text.split()
    if not words:
        return []

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        width = draw.textlength(candidate, font=font)
        if width <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def draw_headline(
    image: Image.Image,
    headline: str,
    subline: str = "",
    position: str = "top",
    font_path: str = DEFAULT_FONT,
    font_outline_path: str = DEFAULT_FONT_BOLD,
    color_text: str = "#FFFFFFFF",
    color_outline: str = "#E6000000",
    max_chars: int = 42,
) -> tuple[Image.Image, float]:
    """Rendert Headline (und Subline) auf ein Bild.

    Returns:
        (Bild, Kontrastverhaeltnis) — der Kontrast wird gegen den
        Bildbereich unter dem Text gemessen.
    """
    if not headline.strip():
        raise RenderError("Leere Headline")

    headline = headline.strip()[:max_chars]
    draw = ImageDraw.Draw(image)
    width, height = image.size

    # Schriftgroesse an die Breite anpassen.
    size = int(width * 0.115)
    font = load_font(font_path, size)
    while size > 24 and draw.textlength(headline, font=font) > width * 0.92:
        size -= 4
        font = load_font(font_path, size)

    sub_font = load_font(font_outline_path, int(size * 0.52))
    outline = max(3, size // 12)

    lines = wrap_text(draw, headline, font, int(width * 0.9))
    line_height = int(size * 1.18)
    block_height = line_height * len(lines)

    margin = int(width * 0.06)
    if position == "bottom":
        y = height - margin - block_height
    elif position == "middle":
        y = (height - block_height) // 2
    else:
        y = margin

    text_rgba = hex_to_rgba(color_text)
    outline_rgba = hex_to_rgba(color_outline)

    # Schlagschatten zuerst (dunkel, leicht versetzt) — dann Outline, dann Text.
    for line_index, line in enumerate(lines):
        line_y = y + line_index * line_height
        draw.text(
            (margin + 4, line_y + 4), line, font=font,
            fill=(0, 0, 0, 160), stroke_width=outline, stroke_fill=(0, 0, 0, 160),
        )
        draw.text(
            (margin, line_y), line, font=font,
            fill=text_rgba, stroke_width=outline, stroke_fill=outline_rgba,
        )

    if subline.strip():
        sub = subline.strip()
        sub_y = y + block_height + int(size * 0.18)
        draw.text(
            (margin + 3, sub_y + 3), sub, font=sub_font,
            fill=(0, 0, 0, 160), stroke_width=max(2, outline // 2),
            stroke_fill=(0, 0, 0, 160),
        )
        draw.text(
            (margin, sub_y), sub, font=sub_font,
            fill=text_rgba, stroke_width=max(2, outline // 2), stroke_fill=outline_rgba,
        )

    # Kontrast messen: mittlerer Pixel unter dem Textbereich.
    region = image.crop((0, max(0, y - 10), width, min(height, y + block_height + 10)))
    pixels = list(region.convert("RGB").getdata())
    if pixels:
        avg = tuple(sum(c[i] for c in pixels) // len(pixels) for i in range(3))
    else:
        avg = (0, 0, 0)

    return image, contrast_ratio(text_rgba[:3], avg)


def make_gradient_overlay(
    width: int,
    height: int,
    color_top: str = "#B3000000",
    color_bottom: str = "#00000000",
) -> Image.Image:
    """Erzeugt einen vertikalen Verlauf als RGBA-Overlay."""
    top = hex_to_rgba(color_top)
    bottom = hex_to_rgba(color_bottom)

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for y in range(height):
        t = y / max(1, height - 1)
        color = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
            int(top[3] + (bottom[3] - top[3]) * t),
        )
        draw.line([(0, y), (width, y)], fill=color)
    return overlay


def render_text_png(
    dst: Path,
    width: int,
    height: int,
    headline: str,
    subline: str = "",
    position: str = "top",
    font_path: str = DEFAULT_FONT,
    font_outline_path: str = DEFAULT_FONT_BOLD,
    color_text: str = "#FFFFFFFF",
    color_outline: str = "#E6000000",
    max_chars: int = 42,
) -> tuple[Path, float]:
    """Rendert eine transparente Text-Ebene als PNG.

    Wird per ffmpeg-``overlay`` ueber das Video gelegt.
    """
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    image, contrast = draw_headline(
        canvas, headline, subline, position,
        font_path=font_path, font_outline_path=font_outline_path,
        color_text=color_text, color_outline=color_outline,
        max_chars=max_chars,
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    image.save(dst, "PNG")
    return dst, contrast

    lines.append(current)
    return lines
