"""Die sieben Pipeline-Stages.

Jede Stage ist ein eigenes Modul mit einer ``run(ctx) -> StageResult``-
Funktion. Die Reihenfolge steht in ``media_pipeline.pipeline.STAGE_ORDER``.
"""

from __future__ import annotations

__all__ = [
    "s01_ingest",
    "s02_transcribe",
    "s03_highlights",
    "s04_rank",
    "s05_thumbnail",
    "s06_vertical",
    "s07_longform",
]
