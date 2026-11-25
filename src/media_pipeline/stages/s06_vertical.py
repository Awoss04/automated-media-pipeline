"""Stage s06_vertical — Clips auf 9:16 zuschneiden.

Ablauf je Clip:
1. Segment schneiden (ohne Neuencodierung).
2. Auf 9:16 croppen (Fokus konfigurierbar).
3. Skalieren auf die Zielaufloesung.
4. Text-Overlay (PNG aus s05) mit ``overlay`` einbrennen.

Gate: 1080x1920, Dauer im erlaubten Bereich, Tonspur vorhanden, Overlay
eingebrannt.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from media_pipeline.lib import ffmpeg, log
from media_pipeline.models import RankedClip, StageResult


def _build_filter(meta: dict, out_width: int, out_height: int, focus_y: float,
                  has_overlay: bool) -> str:
    """Baut die ffmpeg-Filterkette aus crop + scale (+ overlay).

    Wichtig: In ffmpeg 9 kennt der ``overlay``-Filter **keine**
    Datei-Option mehr. Die Text-Ebene muss als zweiter Input-Stream
    ueber ``-i`` uebergeben werden und wird ueber
    ``[base][overlay]overlay=…`` referenziert.
    """
    source_w = int(meta.get("width") or 0)
    source_h = int(meta.get("height") or 0)

    # Video-Branch: crop -> scale -> crop.
    # Wichtig: Das Komma trennt die Filter, das Input-Label steht direkt
    # vor dem ersten Filter ("[0:v]crop=..."), NICHT danach.
    base = "[0:v]"
    if source_w and source_h:
        base += ffmpeg.crop_filter(source_w, source_h, focus_y)
    base += f",scale={out_width}:{out_height}:force_original_aspect_ratio=increase"
    base += f",crop={out_width}:{out_height}[base]"
    parts: list[str] = [base]

    if has_overlay:
        # Overlay-Branch: zweite Eingabe (PNG mit Text) unveraendert.
        parts.append("[1:v]format=rgba[ovl]")
        parts.append("[base][ovl]overlay=0:0:format=auto[out]")
    else:
        parts.append("[base]null[out]")

    return ";".join(parts)


def _has_audio(path: Path) -> bool:
    """Prueft, ob die Datei eine Tonspur enthaelt."""
    try:
        summary = ffmpeg.probe_summary(path)
    except ffmpeg.FFmpegError:
        return False
    return bool(summary.get("has_audio"))


def run(ctx) -> StageResult:
    """Fuehrt s06_vertical aus."""
    clips = ctx.ranked()
    if not clips:
        return StageResult(ok=False, error="Keine bewerteten Clips (s04_rank fehlt)")

    video = ctx.video_path
    if not video.is_file():
        return StageResult(ok=False, error=f"Kein Video: {video}")

    meta = ctx.video_meta()
    if meta is None:
        return StageResult(ok=False, error="Keine Video-Metadaten (s01_ingest fehlt)")

    out_width = int(ctx.config.get("vertical.width", 1080))
    out_height = int(ctx.config.get("vertical.height", 1920))
    focus_y = float(ctx.config.get("vertical.focus_y", 0.4))
    encoder = str(ctx.config.get("vertical.encoder", "h264_videotoolbox"))
    video_bitrate = str(ctx.config.get("vertical.video_bitrate", "6M"))
    audio_bitrate = str(ctx.config.get("vertical.audio_bitrate", "192k"))
    min_clip = float(ctx.config.get("vertical.min_clip_sec", 30))
    max_clip = float(ctx.config.get("vertical.max_clip_sec", 90))

    produced: list[str] = []
    problems: list[str] = []

    video_duration = float(meta.duration_sec)

    for clip in clips:
        duration = clip.end - clip.start

        # Zu kurze Clips werden auf Mindestlaenge erweitert: der Start
        # wandert nach hinten, sodass die Action im Bild bleibt. Ohne
        # das wuerde s05 ein Thumbnail fuer einen Clip bauen, den s06
        # danach verwirft.
        if duration < min_clip and video_duration > 0:
            wanted = min(min_clip, video_duration)
            new_start = max(0.0, clip.end - wanted)
            new_end = min(video_duration, new_start + wanted)
            new_start = max(0.0, new_end - wanted)
            if new_end - new_start > duration:
                log.info(
                    f"{clip.id}: Clip von {duration:.1f}s auf "
                    f"{new_end - new_start:.1f}s erweitert"
                )
                clip.start = round(new_start, 3)
                clip.end = round(new_end, 3)
                duration = clip.end - clip.start

        if duration < 1.0:
            problems.append(f"{clip.id}: Dauer {duration:.1f}s zu kurz")
            continue

        # 1. Segment schneiden
        segment_path = ctx.work_dir / f"{clip.id}_seg.mp4"
        try:
            ffmpeg.cut(video, segment_path, clip.start, duration)
        except ffmpeg.FFmpegError as exc:
            return StageResult(ok=False, error=f"Cut {clip.id}: {exc}")

        # 2./3./4. Crop + Scale + Overlay
        overlay = Path(clip.thumb_path) if clip.thumb_path and Path(clip.thumb_path).is_file() else None
        filter_chain = _build_filter(
            meta.to_dict(), out_width, out_height, focus_y, overlay is not None
        )

        output_path = ctx.clips_dir / f"{clip.id}.mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            ffmpeg.FFMPEG, "-v", "error", "-y",
            "-i", str(segment_path),
        ]
        if overlay is not None:
            # Die Text-Ebene ist ein eigener Input-Stream.
            cmd += ["-i", str(overlay)]
        cmd += [
            "-filter_complex", filter_chain,
            "-map", "[out]",
            "-map", "0:a?",
            *ffmpeg.encoder_args(encoder, video_bitrate, audio_bitrate),
            str(output_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        except subprocess.TimeoutExpired:
            return StageResult(ok=False, error=f"Clip {clip.id}: ffmpeg Timeout")
        if result.returncode != 0:
            tail = (result.stderr or "").strip().splitlines()[-8:]
            return StageResult(
                ok=False,
                error=f"Clip {clip.id} fehlgeschlagen:\n" + "\n".join(tail),
            )

        # Gate: Ausgabe pruefen
        try:
            summary = ffmpeg.probe_summary(output_path)
        except ffmpeg.FFmpegError as exc:
            problems.append(f"{clip.id}: Ausgabe nicht lesbar ({exc})")
            continue

        actual_w = int(summary.get("width") or 0)
        actual_h = int(summary.get("height") or 0)
        actual_dur = float(summary.get("duration_sec") or 0)

        if (actual_w, actual_h) != (out_width, out_height):
            problems.append(
                f"{clip.id}: {actual_w}x{actual_h} statt {out_width}x{out_height}"
            )
            continue
        if actual_dur < min_clip or actual_dur > max_clip:
            problems.append(
                f"{clip.id}: Dauer {actual_dur:.1f}s ausserhalb {min_clip}-{max_clip}s"
            )
            continue
        if not summary.get("has_audio"):
            problems.append(f"{clip.id}: keine Tonspur")
            continue

        clip.clip_path = str(output_path)
        produced.append(str(output_path))
        log.info(f"Clip {clip.id}: {actual_w}x{actual_h}, {actual_dur:.1f}s, Overlay={overlay is not None}")

        # Zwischenprodukt loeschen — Platz ist knapp.
        try:
            segment_path.unlink(missing_ok=True)
        except OSError:
            pass

    if not produced:
        return StageResult(
            ok=False,
            error="Kein Clip erzeugt" + (f": {'; '.join(problems[:3])}" if problems else ""),
        )

    with ctx.ranked_path.open("w", encoding="utf-8") as fh:
        json.dump([c.to_dict() for c in clips], fh, indent=2, ensure_ascii=False)

    gate = f"{len(produced)} Clips, {out_width}x{out_height}"
    if problems:
        log.warn(f"{len(problems)} Befunde: {'; '.join(problems[:3])}")
    return StageResult(ok=True, data={"clips": len(produced)}, gate=gate)
