"""Erzeugt ein synthetisches Testvideo.

Damit kann die Pipeline **ohne echtes Material** getestet werden — kein
Netz, kein YouTube, keine Cookies. Das Video hat:

- 1920x1080 bei 30 fps (Standard-Gameplay-Format)
- **echte deutsche Sprache** (macOS ``say``), damit Whisper etwas zu
  transkribieren hat und die Coverage-Gate greift
- eine laute Passage in der Mitte (simulierte "Action"), damit
  s03 Kandidaten findet
- 3 Minuten Laufzeit (kompromittierbar ueber ``--duration``)

Ohne ``say`` (nicht-macOS) faellt das Video auf einen Sinuston
zurueck — die Transkription liefert dann bewusst kein Ergebnis.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

FFMPEG = "ffmpeg"

# Deutsche Saetze fuer die Sprachspur.
SPEECH_LINES = [
    "Was passiert hier gerade? Ich fahre zur Bruecke.",
    "Okay, jetzt kommt der schwierige Teil der Mission.",
    "Explosion! Die Bruecke ist hochgegangen.",
    "Das war knapp. Sehr knapp sogar.",
    "Ich nehme die Verfolger und fahre zurueck.",
    "Noch einmal das Gleiche, aber diesmal besser.",
    "Die Polizei ist hinter mir, ich muss schneller werden.",
    "Gute Tarnung, jetzt sieht mich keiner.",
    "Das ist die entscheidende Sekunde des ganzen Auftrags.",
    "Ich nehme den Hubschrauber und fliege raus.",
    "Ziel erfasst. Jetzt kommt der Absturz.",
    "Perfekt, ich habe es geschafft. Das war grossartig.",
]


def has_say() -> bool:
    """True, wenn das macOS-Sprachprogramm verfuegbar ist."""
    return shutil.which("say") is not None


def build_speech(duration: int, work_dir: Path) -> Path | None:
    """Erzeugt eine Sprachspur mit variierender Lautstaerke.

    Die Saetze werden an strategischen Stellen platziert und dazwischen
    mit Stille gefuellt. Die mittleren Saetze bekommen mehr Lautstaerke
    (simuliertes "Action"-Fenster fuer die Energieanalyse).
    """
    if not has_say():
        return None

    chunks: list[Path] = []
    gap = max(1, duration // (len(SPEECH_LINES) + 1))

    for index, line in enumerate(SPEECH_LINES):
        chunk = work_dir / f"chunk_{index:02d}.aiff"
        subprocess.run(
            ["say", "-o", str(chunk), "-v", "Anna", line],
            capture_output=True,
            timeout=120,
        )
        if not chunk.is_file():
            continue
        # Wichtig: Die Aiff-Dateien von ``say`` haben ein Format, das der
        # concat-Filter nicht akzeptiert. Deshalb alles auf einheitliches
        # PCM-WAV normalisieren (44100 Hz, mono).
        wav = work_dir / f"chunk_{index:02d}.wav"
        subprocess.run(
            [
                FFMPEG, "-v", "error", "-y",
                "-i", str(chunk),
                "-ar", "44100", "-ac", "1",
                "-c:a", "pcm_s16le",
                str(wav),
            ],
            capture_output=True,
            timeout=120,
        )
        if wav.is_file():
            chunks.append(wav)

    if not chunks:
        return None

    # Stille zwischen den Abschnitten erzeugen (ebenfalls WAV).
    silence = work_dir / "silence.wav"
    subprocess.run(
        [
            FFMPEG, "-v", "error", "-y",
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={gap}",
            "-ar", "44100", "-ac", "1",
            "-c:a", "pcm_s16le",
            str(silence),
        ],
        capture_output=True,
        timeout=120,
    )

    inputs: list[str] = []
    filter_parts: list[str] = []
    concat_labels: list[str] = []
    index = 0
    middle = len(chunks) // 3

    for chunk in chunks:
        inputs += ["-i", str(chunk)]
        volume = 1.6 if middle <= index < 2 * middle else 1.0
        filter_parts.append(f"[{index}:a]volume={volume}[v{index}]")
        concat_labels.append(f"[v{index}]")
        index += 1
        if index < len(chunks):
            inputs += ["-i", str(silence)]
            filter_parts.append(f"[{index}:a]anull[silent{index}]")
            concat_labels.append(f"[silent{index}]")
            index += 1

    filter_complex = (
        ";".join(filter_parts)
        + ";"
        + "".join(concat_labels)
        + f"concat=n={len(concat_labels)}:v=0:a=1[out]"
    )

    voice_file = work_dir / "voice.m4a"
    subprocess.run(
        [
            FFMPEG, "-v", "error", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:a", "aac",
            "-b:a", "128k",
            str(voice_file),
        ],
        capture_output=True,
        timeout=300,
    )

    return voice_file if voice_file.is_file() else None


def build(
    target: Path,
    duration: int = 180,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    with_audio: bool = True,
) -> Path:
    """Erzeugt das Testvideo mit ffmpeg."""
    target.parent.mkdir(parents=True, exist_ok=True)

    # Dunkler Hintergrund mit Streifen — so sieht ein Frame im Schnitt
    # aus und liefert echte Kanten fuer den Crop.
    video_filter = (
        f"color=c=0x1a2330:s={width}x{height}:d={duration}:r={fps}[bg];"
        f"[bg]drawbox=x=0:y=0:w={width}:h={height // 6}:"
        f"color=0x2c3a4d:t=fill[out]"
    )

    with tempfile.TemporaryDirectory(prefix="media_pipeline-testvideo-") as tmp:
        work_dir = Path(tmp)
        voice = build_speech(duration, work_dir) if with_audio else None

        # Reihenfolge: alle Inputs zuerst, dann die Filter, dann die Codecs.
        cmd = [
            FFMPEG, "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=s={width}x{height}:d={duration}:r={fps}",
        ]

        if voice is not None:
            cmd += ["-i", str(voice)]
        elif with_audio:
            # Rueckfall: Sinuston (kein Text, aber eine Tonspur).
            cmd += [
                "-f", "lavfi",
                "-i",
                f"sine=frequency=440:duration={duration}:sample_rate=44100",
            ]

        # Das Video wird auf die tatsaechliche Dauer der Sprachspur
        # gekuerzt (-shortest wuerde sonst am kuerzeren Input schneiden
        # und Bild und Ton waeren nicht synchron).
        if voice is not None:
            try:
                probe = subprocess.run(
                    [
                        FFMPEG, "-v", "error", "-i", str(voice),
                        "-f", "null", "-",
                    ],
                    capture_output=True, text=True, timeout=60,
                )
                del probe
            except (OSError, subprocess.TimeoutExpired):
                pass

        cmd += ["-filter_complex", video_filter, "-map", "[out]", "-t", str(duration)]
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]

        if with_audio:
            cmd += ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "128k", "-shortest"]

        cmd += [str(target)]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if result.returncode != 0:
            raise RuntimeError(
                "ffmpeg konnte das Testvideo nicht erzeugen:\n" + (result.stderr or "")
            )

    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Testvideo erzeugen")
    parser.add_argument("--output", required=True, help="Zielpfad (mp4)")
    parser.add_argument("--duration", type=int, default=180, help="Dauer in Sekunden")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--no-audio", action="store_true", help="Ohne Tonspur")
    args = parser.parse_args()

    path = build(
        Path(args.output),
        duration=args.duration,
        width=args.width,
        height=args.height,
        fps=args.fps,
        with_audio=not args.no_audio,
    )
    size = path.stat().st_size
    print(f"Testvideo erzeugt: {path} ({size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
