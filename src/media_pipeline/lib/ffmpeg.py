"""ffmpeg- und ffprobe-Wrapper.

Alle Aufrufe gehen ueber ``subprocess`` mit **Argumentlisten** — nie ueber
Shell-Interpolation. Das ist die Basis dafuer, dass ein fremder Dateiname
(Video-Titel, YouTube-Metadaten) keinen Shell-Befehl einschleusen kann.

**Wichtig:** Dieses ffmpeg hat kein ``drawtext``/``subtitles``/``ass``
(kein libfreetype). Text wird mit Pillow als PNG gerendert und per
``overlay`` eingebrannt.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

# Filter, die in diesem ffmpeg-Build fehlen.
UNSUPPORTED_TEXT_FILTERS = ("drawtext", "subtitles", "ass")


class FFmpegError(Exception):
    """Fehler bei einer ffmpeg-/ffprobe-Ausfuehrung."""


def available() -> bool:
    """True, wenn ffmpeg und ffprobe im PATH liegen."""
    return shutil.which(FFMPEG) is not None and shutil.which(FFPROBE) is not None


def has_text_filters() -> bool:
    """Prueft, ob drawtext/subtitles/ass verfuegbar sind.

    In diesem Build nicht — der Text muss ueber Pillow+overlay laufen.
    """
    try:
        result = subprocess.run(
            [FFFMPEG, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    out = result.stdout
    return all(
        any(f" {name} " in line for line in out.splitlines())
        for name in UNSUPPORTED_TEXT_FILTERS
    )


def _run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    """Fuehrt ein Kommando aus und wirft bei Fehler eine klare Exception."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise FFmpegError(f"{cmd[0]} nicht gefunden: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"{cmd[0]} Timeout nach {timeout}s") from exc
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-12:]
        raise FFmpegError(
            f"{cmd[0]} fehlgeschlagen (rc={result.returncode}):\n" + "\n".join(tail)
        )
    return result


def probe(path: Path) -> dict[str, Any]:
    """Liest Video-Metadaten mit ffprobe (JSON)."""
    cmd = [
        FFPROBE,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = _run(cmd, timeout=120)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FFmpegError(f"ffprobe lieferte kein gueltiges JSON: {exc}") from exc


def probe_summary(path: Path) -> dict[str, Any]:
    """Kurzfassung der Video-Metadaten: Dauer, Aufloesung, fps, Codec."""
    data = probe(path)
    streams = data.get("streams") or []
    fmt = data.get("format") or {}

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = 0.0
    for source in (fmt.get("duration"), (video or {}).get("duration")):
        try:
            duration = float(source)
            break
        except (TypeError, ValueError):
            continue

    width = int((video or {}).get("width") or 0)
    height = int((video or {}).get("height") or 0)
    fps = 0.0
    raw_fps = (video or {}).get("avg_frame_rate") or (video or {}).get("r_frame_rate") or "0/1"
    try:
        num, _, den = str(raw_fps).partition("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        fps = 0.0

    return {
        "duration_sec": round(duration, 3),
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "codec": (video or {}).get("codec_name", ""),
        "has_audio": audio is not None,
        "size_bytes": int(fmt.get("size") or 0),
    }


def extract_audio(src: Path, dst: Path, sample_rate: int = 16000) -> Path:
    """Extrahiert eine mono-Audiospur (16 kHz) fuer Whisper."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        FFMPEG, "-v", "error", "-y",
        "-i", str(src),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", "1",
        str(dst),
    ])
    return dst


def crop_filter(width: int, height: int, focus_y: float = 0.5) -> str:
    """Baut den crop-Filter, um eine Quelle auf 9:16 (Hochformat) zu schneiden.

    ``width``/``height`` sind die **Quell**-Dimensionen. Geschnitten wird
    auf die Breite, die bei voller Quellhoehe das Zielformat 9:16 ergibt:
    ``crop_w = height * 9 / 16``. Ist die Quelle schmaler als das
    Zielformat verlangt, wird stattdessen in der Hoehe beschnitten.

    Der vertikale Fokus (``focus_y``) verschiebt den Ausschnitt — bei
    Gameplay liegt die Action meist in der oberen Bildhaelfte.
    """
    if width <= 0 or height <= 0:
        raise FFmpegError("Ungueltige Dimensionen fuer crop-Filter")

    target_ratio = 9.0 / 16.0

    if width / height > target_ratio:
        # Quelle ist breiter als 9:16 -> in der Breite schneiden.
        crop_h = height - (height % 2)
        crop_w = int(crop_h * target_ratio)
        crop_w = min(crop_w, width)
        crop_w -= crop_w % 2
        x = (width - crop_w) // 2
        y = int((height - crop_h) * max(0.0, min(1.0, focus_y)))
    else:
        # Quelle ist bereits schmaler als 9:16 -> in der Hoehe schneiden.
        crop_w = width - (width % 2)
        crop_h = int(crop_w / target_ratio)
        crop_h = min(crop_h, height)
        crop_h -= crop_h % 2
        x = 0
        y = int((height - crop_h) * max(0.0, min(1.0, focus_y)))

    return f"crop={crop_w}:{crop_h}:{x}:{y}"


def encoder_args(encoder: str, video_bitrate: str, audio_bitrate: str) -> list[str]:
    """Baut die Encoder-Argumente fuer die Ausgabe."""
    if encoder == "h264_videotoolbox":
        return [
            "-c:v", "h264_videotoolbox",
            "-b:v", video_bitrate,
            "-c:a", "aac",
            "-b:a", audio_bitrate,
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
        ]
    return [
        "-c:v", "libx264",
        "-preset", "medium",
        "-b:v", video_bitrate,
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]


def cut(src: Path, dst: Path, start: float, duration: float) -> Path:
    """Schneidet ein Segment ohne Neuencodieren (``-c copy``)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        FFMPEG, "-v", "error", "-y",
        "-ss", f"{start:.3f}",
        "-i", str(src),
        "-t", f"{duration:.3f}",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        str(dst),
    ])
    return dst


def concat(segments: list[Path], dst: Path, work_dir: Path) -> Path:
    """Fuegt Segmente per concat-Demuxer zusammen (ohne Neuencodierung).

    Die Segmente muessen identische Codec-Parameter haben — dafuer sorgt
    die Pipeline (alle Segmente werden mit demselben Encoder erzeugt).
    """
    if not segments:
        raise FFmpegError("Keine Segmente zum Zusammenfuehren")

    work_dir.mkdir(parents=True, exist_ok=True)
    list_file = work_dir / "concat.txt"
    with list_file.open("w", encoding="utf-8") as fh:
        for segment in segments:
            # Pfade mit Leerzeichen/Quotes korrekt fuer concat escapes.
            safe = str(segment.resolve()).replace("'", "'\\''")
            fh.write(f"file '{safe}'\n")

    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        FFMPEG, "-v", "error", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(dst),
    ])
    return dst


def extract_frame(src: Path, dst: Path, timestamp: float, width: int = 0, height: int = 0) -> Path:
    """Extrahiert ein Einzelbild als JPEG (fuer die Thumbnail-Analyse)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG, "-v", "error", "-y",
        "-ss", f"{timestamp:.3f}",
        "-i", str(src),
        "-frames:v", "1",
    ]
    if width and height:
        cmd += ["-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease"]
    cmd += ["-q:v", "3", str(dst)]
    _run(cmd, timeout=300)
    return dst
