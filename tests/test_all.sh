#!/usr/bin/env bash
#
# Gesamtlauf der Pipeline-Tests.
#
# Jeder Fehler bricht den Lauf ab — kein Weiterlauf mit defekten Daten.
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VENV="$PROJECT_ROOT/venv"
PYTHON="$VENV/bin/python"

cd "$PROJECT_ROOT"

if [ ! -x "$PYTHON" ]; then
  echo "FEHLER: venv nicht gefunden: $PYTHON"
  echo "  Erstellen mit: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
  exit 1
fi

export PYTHONPATH="$PROJECT_ROOT/src"

echo "=============================================="
echo " Automated Media Pipeline — Testlauf"
echo "=============================================="
echo "Python:  $($PYTHON --version 2>&1)"
echo "Projekt: $PROJECT_ROOT"
echo

echo ">>> 1. Abhaengigkeiten pruefen"
"$PYTHON" - << 'PYEOF'
import importlib
import sys

fehlend = []
for modul, paket in [
    ("yaml", "PyYAML"),
    ("PIL", "Pillow"),
    ("numpy", "numpy"),
    ("faster_whisper", "faster-whisper"),
    ("yt_dlp", "yt-dlp"),
]:
    try:
        importlib.import_module(modul)
        print(f"  OK      {paket}")
    except ImportError:
        fehlend.append(paket)
        print(f"  FEHLT   {paket}")

if fehlend:
    print(f"\nFEHLER: fehlende Pakete: {', '.join(fehlend)}")
    sys.exit(1)
PYEOF
echo

echo ">>> 2. Import aller Module"
"$PYTHON" - << 'PYEOF'
from media_pipeline import cli, config, models, pipeline, state
from media_pipeline.lib import agy, ffmpeg, log, render
from media_pipeline.stages import (
    s01_ingest, s02_transcribe, s03_highlights, s04_rank,
    s05_thumbnail, s06_vertical, s07_longform,
)
print("  Alle Module importierbar.")
PYEOF
echo

echo ">>> 3. Werkzeuge pruefen"
"$PYTHON" - << 'PYEOF'
import shutil
import sys

from media_pipeline.lib import agy, ffmpeg

fehlend = []
if not ffmpeg.available():
    fehlend.append("ffmpeg/ffprobe")
else:
    print("  OK      ffmpeg + ffprobe")

if not agy.available():
    fehlend.append("agy (Antigravity CLI)")
else:
    print(f"  OK      agy ({agy.binary_path()})")

if fehlend:
    print(f"\nHINWEIS: nicht gefunden: {', '.join(fehlend)}")
    print("  Die LLM-Stufen s04/s05 brauchen agy, der Rest laeuft ohne.")
PYEOF
echo

echo ">>> 4. Unit-Tests"
"$PYTHON" -m unittest discover -s "$PROJECT_ROOT/tests" -p 'test_*.py' -v
echo

echo ">>> 5. CLI-Funktionspruefung"
"$PYTHON" -m media_pipeline --help > /dev/null
echo "  OK      media_pipeline --help"
"$PYTHON" -m media_pipeline status > /dev/null
echo "  OK      media_pipeline status"
echo

echo "=============================================="
echo " Alle Pruefungen bestanden."
echo "=============================================="
