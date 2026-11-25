"""Stage s02_transcribe — Audio transkribieren.

Nutzt **faster-whisper** (CTranslate2, int8) — rund 4-8x schneller als
OpenAI-Whisper auf CPU. Kein PyTorch, kein 2-GB-Download.

Gate: SRT vorhanden, Zeitstempel monoton, Coverage >= konfigurierter
Anteil der Videolänge.
"""

from __future__ import annotations

import json
from pathlib import Path

from media_pipeline.lib import ffmpeg, log
from media_pipeline.models import StageResult, Transcript, TranscriptSegment


def _format_timestamp(seconds: float) -> str:
    """Formatiert Sekunden als SRT-Zeitstempel (HH:MM:SS,mmm)."""
    if seconds < 0:
        seconds = 0.0
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _write_srt(path: Path, segments: list[TranscriptSegment]) -> None:
    """Schreibt das Transkript als SRT-Datei."""
    lines: list[str] = []
    for index, segment in enumerate(segments, start=1):
        lines.append(str(index))
        lines.append(f"{_format_timestamp(segment.start)} --> {_format_timestamp(segment.end)}")
        lines.append(segment.text.strip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _is_monotonic(segments: list[TranscriptSegment]) -> bool:
    """True, wenn die Zeitstempel monoton steigen."""
    previous = -1.0
    for segment in segments:
        if segment.start < previous - 0.001:
            return False
        previous = segment.start
    return True


def run(ctx) -> StageResult:
    """Fuehrt s02_transcribe aus."""
    video = ctx.video_path
    if not video.is_file():
        return StageResult(ok=False, error=f"Kein Video: {video}")

    meta = ctx.video_meta()
    video_duration = float(meta.duration_sec) if meta else 0.0

    model_name = str(ctx.config.get("transcribe.model", "small"))
    compute_type = str(ctx.config.get("transcribe.compute_type", "int8"))
    device = str(ctx.config.get("transcribe.device", "cpu"))
    language = str(ctx.config.get("transcribe.language", "de"))
    beam_size = int(ctx.config.get("transcribe.beam_size", 5))
    vad_filter = bool(ctx.config.get("transcribe.vad_filter", True))
    want_words = bool(ctx.config.get("transcribe.word_timestamps", True))
    min_coverage = float(ctx.config.get("transcribe.min_coverage", 0.95))

    audio_path = ctx.work_dir / "audio.wav"
    try:
        ffmpeg.extract_audio(video, audio_path)
    except ffmpeg.FFmpegError as exc:
        return StageResult(ok=False, error=f"Audioextraktion: {exc}")

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        return StageResult(ok=False, error=f"faster-whisper nicht installiert: {exc}")

    log.info(f"Whisper: Modell '{model_name}' ({compute_type}, {device}) — lade")
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as exc:  # noqa: BLE001 — Modellladung kann viele Fehler werfen
        return StageResult(ok=False, error=f"Modell konnte nicht geladen werden: {exc}")

    log.info("Whisper: transkribiere — 30 min Video dauern ca. 5-10 min")
    try:
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=None if language == "auto" else language,
            beam_size=beam_size,
            vad_filter=vad_filter,
            word_timestamps=want_words,
        )
        segments: list[TranscriptSegment] = []
        words: list[dict] = []
        for seg in segments_iter:
            segments.append(
                TranscriptSegment(start=float(seg.start), end=float(seg.end), text=seg.text)
            )
            if want_words and getattr(seg, "words", None):
                for word in seg.words:
                    words.append(
                        {
                            "start": float(word.start),
                            "end": float(word.end),
                            "word": word.word,
                            "probability": float(getattr(word, "probability", 0.0) or 0.0),
                        }
                    )
    except Exception as exc:  # noqa: BLE001
        return StageResult(ok=False, error=f"Transkription: {type(exc).__name__}: {exc}")
    finally:
        # Das WAV ist Zwischenprodukt und kann gross sein — direkt loeschen.
        try:
            audio_path.unlink(missing_ok=True)
        except OSError:
            pass

    if not segments:
        return StageResult(ok=False, error="Transkript ist leer — kein Audio erkannt?")

    if not _is_monotonic(segments):
        return StageResult(
            ok=False,
            error="Transkript-Zeitstempel sind nicht monoton — Transkription ungueltig",
        )

    transcript = Transcript(
        segments=segments,
        language=str(getattr(info, "language", language) or language),
        model=model_name,
        words=words,
    )

    _write_srt(ctx.transcript_srt, segments)
    with ctx.transcript_json.open("w", encoding="utf-8") as fh:
        json.dump(transcript.to_dict(), fh, indent=2, ensure_ascii=False)

    coverage = transcript.duration / video_duration if video_duration > 0 else 1.0
    if coverage < min_coverage:
        return StageResult(
            ok=False,
            error=(
                f"Transkript deckt nur {coverage * 100:.1f}% des Videos ab "
                f"(gefordert: {min_coverage * 100:.0f}%)"
            ),
            data={"coverage": coverage},
        )

    gate = (
        f"{len(segments)} Segmente, {len(words)} Woerter, "
        f"Sprache={transcript.language}, Coverage {coverage * 100:.1f}%"
    )
    return StageResult(
        ok=True,
        data={
            "segments": len(segments),
            "words": len(words),
            "language": transcript.language,
            "coverage": round(coverage, 4),
            "duration": transcript.duration,
        },
        gate=gate,
    )
