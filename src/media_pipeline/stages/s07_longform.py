"""Stage s07_longform — Highlights zu einem Langvideo zusammenschneiden.

Nutzt die bereits erzeugten 9:16-Clips aus s06, schneidet sie auf 16:9
und fuegt sie mit dem concat-Demuxer zusammen.

Zusaetzlich erzeugt Antigravity Metadaten (Titel, Beschreibung, Tags,
Kapitelmarken) fuer die Veroeffentlichung.

Gate: Dauer >= ``min_duration_sec``, alle Segmente enthalten.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from media_pipeline.lib import agy, ffmpeg, log
from media_pipeline.models import RankedClip, StageResult


def _generate_metadata(ctx, clips: list[RankedClip], durations: list[float]) -> dict | None:
    """Fragt Antigravity nach Titel, Beschreibung, Tags und Kapitelmarken."""
    prompt_file = ctx.prompts_dir / "longform_plan.md"
    schema_file = ctx.schemas_dir / "longform_plan.schema.json"
    if not prompt_file.is_file() or not schema_file.is_file():
        return None

    segments = []
    position = 0.0
    for clip, duration in zip(clips, durations):
        segments.append(
            {
                "id": clip.id,
                "title": clip.title,
                "score": clip.score,
                "duration_sec": round(duration, 1),
                "start_sec_in_longform": round(position, 1),
            }
        )
        position += duration

    payload = {"segments": segments, "total_duration_sec": round(position, 1)}
    prompt = (
        prompt_file.read_text(encoding="utf-8")
        .replace("{{CONTENT_JSON}}", json.dumps(payload, ensure_ascii=False, indent=2))
    )

    try:
        return agy.json_out(
            prompt=prompt,
            schema_path=schema_file,
            model=str(ctx.config.get("longform.model", agy.MODEL_LONGFORM)),
            timeout=int(ctx.config.get("longform.timeout_sec", 240)),
        )
    except (agy.AgyError, FileNotFoundError) as exc:
        log.warn(f"Longform-Metadaten nicht erzeugt: {exc}")
        return None


def run(ctx) -> StageResult:
    """Fuehrt s07_longform aus."""
    clips = [c for c in ctx.ranked() if c.clip_path and Path(c.clip_path).is_file()]
    if not clips:
        return StageResult(
            ok=False,
            error="Keine fertigen Clips vorhanden (s06_vertical fehlt oder leer)",
        )

    out_width = 1920
    out_height = 1080
    encoder = str(ctx.config.get("longform.encoder", "h264_videotoolbox"))
    video_bitrate = str(ctx.config.get("longform.video_bitrate", "8M"))
    audio_bitrate = str(ctx.config.get("longform.audio_bitrate", "192k"))
    min_duration = float(ctx.config.get("longform.min_duration_sec", 300))

    # Jeden 9:16-Clip auf 16:9 croppen (Hintergrund wird schwarz gefuellt).
    parts: list[Path] = []
    durations: list[float] = []
    work_dir = ctx.work_dir / "longform"
    work_dir.mkdir(parents=True, exist_ok=True)

    for index, clip in enumerate(clips):
        part_path = work_dir / f"part_{index:02d}.mp4"
        filter_chain = (
            f"scale={out_width}:{out_height}:force_original_aspect_ratio=increase,"
            f"crop={out_width}:{out_height},"
            f"pad={out_width}:{out_height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
        cmd = [
            ffmpeg.FFMPEG, "-v", "error", "-y",
            "-i", str(Path(clip.clip_path)),
            "-vf", filter_chain,
            *ffmpeg.encoder_args(encoder, video_bitrate, audio_bitrate),
            str(part_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        except subprocess.TimeoutExpired:
            return StageResult(ok=False, error=f"Longform-Teil {clip.id}: Timeout")
        if result.returncode != 0:
            tail = (result.stderr or "").strip().splitlines()[-6:]
            return StageResult(
                ok=False,
                error=f"Longform-Teil {clip.id} fehlgeschlagen:\n" + "\n".join(tail),
            )

        try:
            summary = ffmpeg.probe_summary(part_path)
        except ffmpeg.FFmpegError as exc:
            return StageResult(ok=False, error=f"Longform-Teil {clip.id}: {exc}")

        parts.append(part_path)
        durations.append(float(summary.get("duration_sec") or 0))

    if not parts:
        return StageResult(ok=False, error="Keine Longform-Teile erzeugt")

    output_path = ctx.clips_dir / "longform.mp4"
    try:
        ffmpeg.concat(parts, output_path, work_dir)
    except ffmpeg.FFmpegError as exc:
        return StageResult(ok=False, error=f"Concat: {exc}")

    try:
        summary = ffmpeg.probe_summary(output_path)
    except ffmpeg.FFmpegError as exc:
        return StageResult(ok=False, error=f"Longform nicht lesbar: {exc}")

    total_duration = float(summary.get("duration_sec") or 0)
    if total_duration < min_duration:
        return StageResult(
            ok=False,
            error=(
                f"Longform nur {total_duration:.0f}s "
                f"(gefordert: {min_duration:.0f}s) — zu wenige Clips?"
            ),
            data={"duration": total_duration},
        )

    # Metadaten erzeugen (optional — fehlt der LLM, ist es kein Fehler).
    metadata = _generate_metadata(ctx, clips, durations)
    if metadata:
        meta_path = ctx.clips_dir / "longform_metadata.json"
        with meta_path.open("w", encoding="utf-8") as fh:
            json.dump(metadata, fh, indent=2, ensure_ascii=False)
        log.info(f"Longform-Metadaten: '{metadata.get('title', '')}'")

    # Zwischenprodukte loeschen.
    for part in parts:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass

    gate = (
        f"{len(parts)} Segmente, {total_duration:.0f}s, "
        f"{summary.get('width')}x{summary.get('height')}"
    )
    return StageResult(
        ok=True,
        data={
            "segments": len(parts),
            "duration": total_duration,
            "path": str(output_path),
        },
        gate=gate,
    )
