"""Stage s01_ingest — Video beschaffen und Metadaten erfassen.

Zwei Wege:
- **URL-Modus:** ``yt-dlp`` laedt ein privates YouTube-Video herunter
  (Cookies aus dem Browser, nie exportiert).
- **Datei-Modus:** Eine lokale Datei aus ``data/inbox/`` wird uebernommen
  (Capturecard -> Mac).

Gate: Datei lesbar, ausreichend gross, ``ffprobe`` liefert fps/dauer/
aufloesung.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from media_pipeline.lib import ffmpeg, log
from media_pipeline.models import StageResult


def _ytdlp_command(url: str, fmt: str, outtmpl: str, browser: str) -> list[str]:
    """Baut den yt-dlp-Aufruf als Argumentliste (ohne Shell)."""
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "--no-progress",
        "--no-warnings",
        "--restrict-filenames",
        "-f", fmt,
        "-o", outtmpl,
    ]
    if browser:
        # Cookies werden direkt aus dem Browser gelesen — nie exportiert.
        cmd += ["--cookies-from-browser", browser]
    cmd.append(url)
    return cmd


def _download(url: str, fmt: str, target: Path, browser: str) -> None:
    """Laedt die Video-Datei herunter."""
    target.parent.mkdir(parents=True, exist_ok=True)
    outtmpl = str(target.parent / "%(id)s.%(ext)s")
    cmd = _ytdlp_command(url, fmt, outtmpl, browser)
    log.info(f"yt-dlp: lade herunter (Browser-Cookies: {browser or 'keine'})")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("yt-dlp Timeout nach 2h") from exc

    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-8:]
        raise RuntimeError("yt-dlp fehlgeschlagen:\n" + "\n".join(tail))


def _find_downloaded(target: Path) -> Path:
    """Findet die heruntergeladene Datei (Endung kann variieren)."""
    candidates = sorted(target.parent.glob(f"{target.stem}.*"))
    files = [c for c in candidates if c.is_file() and c.suffix.lower() != ".part"]
    if not files:
        raise RuntimeError(f"Keine heruntergeladene Datei fuer {target.stem} gefunden")
    return files[0]


def run(ctx) -> StageResult:
    """Fuehrt s01_ingest aus."""
    inbox = ctx.inbox_path

    # Der Titel aus s00_channel (Kanalliste) ist verlaesslicher als ein
    # URL-Raten — er kommt direkt von YouTube.
    title = ctx.title
    channel_info = ctx.work_dir / "channel.json"
    if channel_info.is_file():
        try:
            data = json.loads(channel_info.read_text(encoding="utf-8"))
            for entry in data.get("videos", []):
                if entry.get("video_id") == ctx.video_id:
                    title = entry.get("title") or title
                    break
        except (OSError, ValueError):
            pass

    if ctx.source_url:
        fmt = str(ctx.config.get("ingest.format", "bv*[height<=1080]+ba/b[height<=1080]"))
        browser = str(ctx.config.get("ingest.cookies_from_browser", "") or "")
        _download(ctx.source_url, fmt, inbox, browser)
        downloaded = _find_downloaded(inbox)
        if downloaded != inbox:
            shutil.move(str(downloaded), str(inbox))
    else:
        if not inbox.is_file():
            return StageResult(
                ok=False,
                error=f"Keine Quelldatei: {inbox} (weder URL noch Datei vorhanden)",
            )

    min_bytes = int(ctx.config.get("ingest.min_file_bytes", 1_048_576))
    size = inbox.stat().st_size
    if size < min_bytes:
        return StageResult(
            ok=False,
            error=f"Datei zu klein ({size} Bytes < {min_bytes}) — Download unvollstaendig?",
        )

    try:
        summary = ffmpeg.probe_summary(inbox)
    except ffmpeg.FFmpegError as exc:
        return StageResult(ok=False, error=f"ffprobe: {exc}")

    duration = float(summary.get("duration_sec") or 0)
    width = int(summary.get("width") or 0)
    height = int(summary.get("height") or 0)
    fps = float(summary.get("fps") or 0)

    if duration <= 0 or width <= 0 or height <= 0:
        return StageResult(
            ok=False,
            error=(
                f"ffprobe lieferte unvollstaendige Daten "
                f"(dauer={duration}s, {width}x{height})"
            ),
        )

    # Normalisierte Kopie in den Arbeitsordner — die Inbox bleibt unberuehrt.
    work_video = ctx.video_path
    if inbox.resolve() != work_video.resolve():
        shutil.copy2(inbox, work_video)

    meta = {
        "path": str(work_video),
        "duration_sec": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "codec": summary.get("codec", ""),
        "size_bytes": size,
        "title": title,
        "source_url": ctx.source_url,
    }
    with ctx.meta_path.open("w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)

    gate = (
        f"ffprobe: {width}x{height} @ {fps}fps, {duration:.1f}s, "
        f"{size / 1_048_576:.1f} MB"
    )
    return StageResult(ok=True, data=meta, gate=gate)
