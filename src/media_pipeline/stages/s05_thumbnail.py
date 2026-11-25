"""Stage s05_thumbnail — Thumbnail rendern.

Ablauf:
1. Antigravity erzeugt Headline + Subline (JSON-Schema).
2. ffmpeg extrahiert den Frame an der besten Stelle des Clips.
3. Pillow rendert den Text **als PNG** (dieses ffmpeg hat kein drawtext).
4. Pillow legt den Text auf das Bild und speichert das fertige Thumbnail.

Gate: PNG >= 1080x1920, Text vorhanden, Kontrast >= 4.5:1.
"""

from __future__ import annotations

import json

from media_pipeline.lib import agy, ffmpeg, log, render
from media_pipeline.models import RankedClip, StageResult


def _generate_text(ctx, clip: RankedClip) -> dict:
    """Fragt Antigravity nach Headline und Subline."""
    prompt_file = ctx.prompts_dir / "thumbnail_text.md"
    schema_file = ctx.schemas_dir / "thumbnail_text.schema.json"
    if not prompt_file.is_file() or not schema_file.is_file():
        raise FileNotFoundError("Prompt oder JSON-Schema fuer Thumbnail fehlt")

    payload = {
        "title": clip.title,
        "reason": clip.reason,
        "keywords": clip.keywords,
        "start_sec": clip.start,
        "end_sec": clip.end,
    }
    prompt = (
        prompt_file.read_text(encoding="utf-8")
        .replace("{{CONTENT_JSON}}", json.dumps(payload, ensure_ascii=False, indent=2))
    )

    return agy.json_out(
        prompt=prompt,
        schema_path=schema_file,
        model=str(ctx.config.get("thumbnail.model", agy.MODEL_THUMBNAIL)),
        timeout=int(ctx.config.get("thumbnail.timeout_sec", 180)),
    )


def _fallback_text(clip: RankedClip, max_chars: int) -> dict:
    """Notfall-Text, falls der LLM-Aufruf scheitert."""
    headline = (clip.title or clip.reason or "Highlight").strip()[:max_chars]
    return {
        "headline": headline,
        "subline": "",
        "emoji": "",
        "overlay_position": "top",
    }


def run(ctx) -> StageResult:
    """Fuehrt s05_thumbnail aus."""
    clips = ctx.ranked()
    if not clips:
        return StageResult(ok=False, error="Keine bewerteten Clips (s04_rank fehlt)")

    video = ctx.video_path
    if not video.is_file():
        return StageResult(ok=False, error=f"Kein Video: {video}")

    width = int(ctx.config.get("thumbnail.width", 1080))
    height = int(ctx.config.get("thumbnail.height", 1920))
    max_chars = int(ctx.config.get("thumbnail.max_title_chars", 42))
    font_path = str(ctx.config.get("thumbnail.font_path", render.DEFAULT_FONT))
    font_outline = str(ctx.config.get("thumbnail.font_outline_path", render.DEFAULT_FONT_BOLD))
    color_text = str(ctx.config.get("thumbnail.color_text", "#FFFFFFFF"))
    color_outline = str(ctx.config.get("thumbnail.color_outline", "#E6000000"))

    produced: list[str] = []
    contrasts: list[float] = []

    for clip in clips:
        try:
            text_data = _generate_text(ctx, clip)
        except (agy.AgyError, FileNotFoundError) as exc:
            log.warn(f"Thumbnail-Text fuer {clip.id} nicht per LLM: {exc}")
            text_data = _fallback_text(clip, max_chars)

        headline = str(text_data.get("headline", "") or "").strip()
        if not headline:
            headline = (clip.title or clip.reason or "Highlight").strip()[:max_chars]
        subline = str(text_data.get("subline", "") or "").strip()[:28]
        position = str(text_data.get("overlay_position", "top") or "top")

        # Frame an der besten Stelle des Clips extrahieren.
        timestamp = clip.start + clip.best_moment_sec
        frame_path = ctx.thumbs_dir / f"{clip.id}_frame.jpg"
        try:
            ffmpeg.extract_frame(video, frame_path, timestamp)
        except ffmpeg.FFmpegError as exc:
            return StageResult(ok=False, error=f"Frame-Extraktion {clip.id}: {exc}")

        from PIL import Image

        with Image.open(frame_path) as raw:
            source = raw.convert("RGB")
            source_ratio = source.width / source.height
            target_ratio = width / height

            if source_ratio > target_ratio:
                new_width = int(source.height * target_ratio)
                left = (source.width - new_width) // 2
                source = source.crop((left, 0, left + new_width, source.height))
            else:
                new_height = int(source.width / target_ratio)
                top = int((source.height - new_height) * 0.4)
                source = source.crop((0, top, source.width, top + new_height))

            frame = source.resize((width, height), Image.LANCZOS).convert("RGBA")

        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        overlay, contrast = render.draw_headline(
            overlay,
            headline,
            subline,
            position,
            font_path=font_path,
            font_outline_path=font_outline,
            color_text=color_text,
            color_outline=color_outline,
            max_chars=max_chars,
        )

        combined = Image.alpha_composite(frame, overlay).convert("RGB")
        thumb_path = ctx.thumbs_dir / f"{clip.id}.png"
        combined.save(thumb_path, "PNG")

        contrasts.append(contrast)
        produced.append(str(thumb_path))

        clip.thumb_path = str(thumb_path)
        log.info(f"Thumbnail {clip.id}: '{headline}' (Kontrast {contrast:.1f}:1)")

    # Bewertete Clips mit den Thumbnail-Pfaden zurueckschreiben.
    with ctx.ranked_path.open("w", encoding="utf-8") as fh:
        json.dump([c.to_dict() for c in clips], fh, indent=2, ensure_ascii=False)

    if not produced:
        return StageResult(ok=False, error="Kein Thumbnail erzeugt")

    min_contrast = min(contrasts) if contrasts else 0.0
    if min_contrast < 4.5:
        return StageResult(
            ok=False,
            error=f"Text-Kontrast zu gering ({min_contrast:.1f}:1, gefordert 4.5:1)",
            data={"thumbs": len(produced)},
        )

    gate = f"{len(produced)} Thumbnails, {width}x{height}, min. Kontrast {min_contrast:.1f}:1"
    return StageResult(ok=True, data={"thumbs": len(produced)}, gate=gate)
