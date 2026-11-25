"""Stage s03_highlights — Kandidaten finden.

Zwei Signale werden kombiniert:

1. **Audio-Energie** — laute, dichte Momente (Schussgefechte, Explosionen,
   Reaktionen) sind in Gameplay typischerweise die spannenden Stellen.
   Berechnet ueber die RMS-Energie des 16-kHz-Audios.
2. **Transkript-Dichte** — mehr Sprache in kurzer Zeit heisst meist
   Kommentar/Action des Spielers.

Ergebnis sind Zeitfenster zwischen ``min_clip_sec`` und ``max_clip_sec``.
Die eigentliche Bewertung (welcher Moment es wert ist) macht s04_rank.

Gate: mindestens ``min_candidates`` Kandidaten, alle Startzeiten innerhalb
der Videolänge.
"""

from __future__ import annotations

import json
import subprocess

from media_pipeline.lib import ffmpeg, log
from media_pipeline.models import Candidate, StageResult


def _audio_rms_series(audio_path, window_sec: float = 2.0) -> list[float]:
    """Berechnet die RMS-Energie pro Fenster (numpy, ohne scipy)."""
    import numpy as np

    cmd = [
        ffmpeg.FFMPEG, "-v", "error",
        "-i", str(audio_path),
        "-ac", "1",
        "-ar", "16000",
        "-f", "s16le",
        "-",
    ]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=1800).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []

    if not raw:
        return []

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    window_size = int(16000 * window_sec)
    if window_size <= 0 or samples.size < window_size:
        return []

    usable = (samples.size // window_size) * window_size
    frames = samples[:usable].reshape(-1, window_size)
    rms = np.sqrt(np.mean(np.square(frames), axis=1))

    # Normalisieren auf 0..1 — die Absolute Lautstaerke ist aus Genre
    # und Geraet abhaengig, der Verlauf ist das eigentliche Signal.
    peak = float(rms.max()) if rms.size else 0.0
    if peak <= 0:
        return [0.0] * int(rms.size)
    return [float(v) for v in (rms / peak)]


def _pick_peaks(
    series: list[float], window_sec: float, threshold: float
) -> list[tuple[float, float]]:
    """Findet Fenster ueber der Schwelle."""
    if not series:
        return []

    mean = sum(series) / len(series)
    limit = mean * threshold

    peaks: list[tuple[float, float]] = []
    start = None
    for index, value in enumerate(series):
        if value >= limit:
            if start is None:
                start = index
        elif start is not None:
            peaks.append((start * window_sec, index * window_sec))
            start = None
    if start is not None:
        peaks.append((start * window_sec, len(series) * window_sec))
    return peaks

def run(ctx) -> StageResult:
    """Fuehrt s03_highlights aus."""
    video = ctx.video_path
    if not video.is_file():
        return StageResult(ok=False, error=f"Kein Video: {video}")

    meta = ctx.video_meta()
    if meta is None:
        return StageResult(ok=False, error="Keine Video-Metadaten (s01_ingest fehlt)")
    video_duration = float(meta.duration_sec)

    min_clip = float(ctx.config.get("highlights.min_clip_sec", 30))
    max_clip = float(ctx.config.get("highlights.max_clip_sec", 90))
    window_sec = float(ctx.config.get("highlights.energy_window_sec", 2.0))
    threshold = float(ctx.config.get("highlights.energy_threshold", 1.35))
    min_candidates = int(ctx.config.get("highlights.min_candidates", 3))

    audio_path = ctx.work_dir / "energy.wav"
    try:
        ffmpeg.extract_audio(video, audio_path)
    except ffmpeg.FFmpegError as exc:
        return StageResult(ok=False, error=f"Audioextraktion: {exc}")

    try:
        series = _audio_rms_series(audio_path, window_sec)
    finally:
        try:
            audio_path.unlink(missing_ok=True)
        except OSError:
            pass

    if not series:
        return StageResult(ok=False, error="Audio-Energie konnte nicht berechnet werden")

    peaks = _pick_peaks(series, window_sec, threshold)
    log.info(f"Energieanalyse: {len(peaks)} Spitzenauschnitte in {len(series)} Fenstern")

    # Spitzen zu Clips zusammenfassen (mit Ueberlappung zusammenfuehren).
    merged: list[list[float]] = []
    for start, end in peaks:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    transcript = ctx.transcript()
    candidates: list[Candidate] = []
    for index, (start, end) in enumerate(merged, start=1):
        if end - start > max_clip:
            end = start + max_clip
        if end - start < min_clip:
            # Zu kurze Spitze: mit Kontext auf fuellen.
            pad = (min_clip - (end - start)) / 2
            start = max(0.0, start - pad)
            end = min(video_duration, end + pad)
        if end - start < min_clip * 0.6:
            continue
        if start >= video_duration:
            continue

        text = ""
        if transcript is not None:
            text = " ".join(s.text for s in transcript.segments_in(start, end)).strip()

        # Energie ueber das ganze Fenster mitteln — der Einzelwert am
        # Fensteranfang ist meist Stille und damit bedeutungslos.
        first = int(start / window_sec)
        last = max(first + 1, int(end / window_sec))
        window_values = series[first:last] if 0 <= first < len(series) else []
        energy = sum(window_values) / len(window_values) if window_values else 0.0

        candidates.append(
            Candidate(
                id=f"cand_{index:02d}",
                start=round(start, 3),
                end=round(end, 3),
                reason=f"Audio-Energie {energy:.2f} im Fenster",
                energy=round(energy, 4),
                transcript=text,
            )
        )

    # Zu wenige Kandidaten: nach Transcript-Dichte aufstocken.
    if len(candidates) < min_candidates and transcript is not None:
        log.info("Energie lieferte zu wenige Kandidaten — ergaenze nach Transcript-Dichte")
        step = max(min_clip, 60.0)
        position = 0.0
        while position + min_clip <= video_duration and len(candidates) < min_candidates:
            window_end = min(video_duration, position + step)
            text = " ".join(s.text for s in transcript.segments_in(position, window_end)).strip()
            if text:
                candidates.append(
                    Candidate(
                        id=f"cand_{len(candidates) + 1:02d}",
                        start=round(position, 3),
                        end=round(window_end, 3),
                        reason="Transkriptdichte als Auffuellung",
                        energy=0.0,
                        transcript=text,
                    )
                )
            position += step

    if len(candidates) < min_candidates:
        return StageResult(
            ok=False,
            error=(
                f"Nur {len(candidates)} Kandidaten gefunden "
                f"(gefordert: {min_candidates}) — Video zu kurz oder ohne Action?"
            ),
        )

    with ctx.candidates_path.open("w", encoding="utf-8") as fh:
        json.dump([c.to_dict() for c in candidates], fh, indent=2, ensure_ascii=False)

    gate = f"{len(candidates)} Kandidaten, Zeitraum 0-{video_duration:.0f}s"
    return StageResult(
        ok=True,
        data={"candidates": len(candidates), "duration": video_duration},
        gate=gate,
    )

