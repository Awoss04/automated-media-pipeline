"""Orchestrator der Pipeline.

Die Pipeline ist eine Zustandsmaschine: sie fuehrt die sieben Stages in
fester Reihenfolge aus und ueberspringt, was bereits gruen war.

Reihenfolge (Datenfluss):

    s01_ingest -> s02_transcribe -> s03_highlights -> s04_rank
                -> s05_thumbnail -> s06_vertical    -> s07_longform
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from media_pipeline.config import Config
from media_pipeline.lib import log
from media_pipeline.models import Candidate, RankedClip, StageResult, Transcript, VideoMeta
from media_pipeline.state import PipelineState

STAGE_ORDER = [
    "s00_channel",
    "s01_ingest",
    "s02_transcribe",
    "s03_highlights",
    "s04_rank",
    "s05_thumbnail",
    "s06_vertical",
    "s07_longform",
]


def slugify(text: str, max_len: int = 40) -> str:
    """Erzeugt aus einem Titel einen sicheren Verzeichnisnamen.

    Nur alphanumerische Zeichen, Bindestriche und Unterstriche. Fremde
    Zeichen (Shell-Metazeichen, Pfadtrenner) werden entfernt — der Name
    landet in Pfaden und wird an yt-dlp uebergeben.
    """
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-_")
    return (cleaned or "video")[:max_len]


class PipelineContext:
    """Kontext, den jede Stage bekommt.

    Bestaetigt die Regel „Inputs nur aus ctx" — keine globalen Pfade,
    keine versteckten Abhaengigkeiten.
    """

    def __init__(
        self,
        config: Config,
        state: PipelineState,
        video_id: str,
        source_url: str = "",
        title: str = "",
        source_video_id: str = "",
    ) -> None:
        self.config = config
        self.state = state
        self.video_id = video_id
        self.source_url = source_url
        self.title = title
        # Die YouTube-Video-ID (falls bekannt). Wird von s00_channel
        # benutzt, um gezielt ein einzelnes Video aus dem Kanal zu waehlen.
        self.source_video_id = source_video_id

    # ------------------------------------------------------------- pfade

    @property
    def work_dir(self) -> Path:
        path = self.config.path("work_dir") / self.video_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def inbox_path(self) -> Path:
        return self.config.path("inbox_dir") / f"{self.video_id}.mp4"

    @property
    def clips_dir(self) -> Path:
        path = self.config.path("clips_dir") / self.video_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def thumbs_dir(self) -> Path:
        path = self.work_dir / "thumbs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def project_root(self) -> Path:
        return self.config.project_root

    @property
    def prompts_dir(self) -> Path:
        return self.project_root / "prompts"

    @property
    def schemas_dir(self) -> Path:
        return self.project_root / "config" / "schemas"

    # ---------------------------------------------------------- artefakte

    @property
    def video_path(self) -> Path:
        return self.work_dir / "video.mp4"

    @property
    def meta_path(self) -> Path:
        return self.work_dir / "meta.json"

    @property
    def transcript_srt(self) -> Path:
        return self.work_dir / "transcript.srt"

    @property
    def transcript_json(self) -> Path:
        return self.work_dir / "transcript.json"

    @property
    def candidates_path(self) -> Path:
        return self.work_dir / "candidates.json"

    @property
    def ranked_path(self) -> Path:
        return self.work_dir / "ranked.json"

    # --------------------------------------------------------- datenzugriff

    def video_meta(self) -> VideoMeta | None:
        """Liest die Video-Metadaten, falls vorhanden."""
        if not self.meta_path.is_file():
            return None
        import json

        try:
            with self.meta_path.open("r", encoding="utf-8") as fh:
                return VideoMeta.from_dict(json.load(fh))
        except (OSError, ValueError):
            return None

    def transcript(self) -> Transcript | None:
        """Liest das Transkript, falls vorhanden."""
        if not self.transcript_json.is_file():
            return None
        import json

        try:
            with self.transcript_json.open("r", encoding="utf-8") as fh:
                return Transcript.from_dict(json.load(fh))
        except (OSError, ValueError):
            return None

    def candidates(self) -> list[Candidate]:
        """Liest die Highlight-Kandidaten, falls vorhanden."""
        if not self.candidates_path.is_file():
            return []
        import json

        try:
            with self.candidates_path.open("r", encoding="utf-8") as fh:
                return [Candidate.from_dict(c) for c in json.load(fh)]
        except (OSError, ValueError, KeyError):
            return []

    def ranked(self) -> list[RankedClip]:
        """Liest die bewerteten Clips, falls vorhanden."""
        if not self.ranked_path.is_file():
            return []
        import json

        try:
            with self.ranked_path.open("r", encoding="utf-8") as fh:
                return [RankedClip.from_dict(c) for c in json.load(fh)]
        except (OSError, ValueError, KeyError):
            return []

class Pipeline:
    """Fuehrt die Stage-Kette aus."""

    def __init__(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        self._stages = self._load_stages()

    @staticmethod
    def _load_stages() -> dict[str, Callable]:
        """Laedt die Stage-Module und ihre ``run``-Funktion."""
        import importlib

        modules = {
            "s00_channel": "media_pipeline.stages.s00_channel",
            "s01_ingest": "media_pipeline.stages.s01_ingest",
            "s02_transcribe": "media_pipeline.stages.s02_transcribe",
            "s03_highlights": "media_pipeline.stages.s03_highlights",
            "s04_rank": "media_pipeline.stages.s04_rank",
            "s05_thumbnail": "media_pipeline.stages.s05_thumbnail",
            "s06_vertical": "media_pipeline.stages.s06_vertical",
            "s07_longform": "media_pipeline.stages.s07_longform",
        }
        loaded: dict[str, Callable] = {}
        for name, module_path in modules.items():
            module = importlib.import_module(module_path)
            loaded[name] = getattr(module, "run")
        return loaded

    def run(
        self,
        only: str | None = None,
        force: bool = False,
        from_stage: str | None = None,
    ) -> dict[str, StageResult]:
        """Fuehrt die Pipeline aus.

        Args:
            only: Nur diese Stage ausfuehren.
            force: Fertige Stages erneut ausfuehren.
            from_stage: Ab dieser Stage starten (inklusive).

        Returns:
            Mapping Stage-Name -> StageResult.
        """
        results: dict[str, StageResult] = {}
        order = list(STAGE_ORDER)

        if only:
            if only not in order:
                raise ValueError(
                    f"Unbekannte Stage '{only}'. Verfuegbar: {', '.join(order)}"
                )
            order = [only]
        elif from_stage:
            if from_stage not in STAGE_ORDER:
                raise ValueError(
                    f"Unbekannte Stage '{from_stage}'. Verfuegbar: {', '.join(STAGE_ORDER)}"
                )
            order = order[order.index(from_stage) :]

        for name in order:
            stage = self._stages[name]

            if not force and self.ctx.state.is_done(name) and not only:
                log.stage_skipped(name)
                results[name] = StageResult(
                    ok=True,
                    gate=self.ctx.state.data_of(name).get("_gate", ""),
                    data=self.ctx.state.data_of(name),
                )
                continue

            log.stage_start(name)
            self.ctx.state.mark_running(name)

            try:
                result = stage(self.ctx)
            except Exception as exc:  # noqa: BLE001 — Stage-Fehler duerfen nicht stoppen
                message = f"{type(exc).__name__}: {exc}"
                log.stage_failed(name, message)
                self.ctx.state.mark_failed(name, message)
                results[name] = StageResult(ok=False, error=message)
                # Die Kette stoppt: keine Stage baut auf defekter Basis auf.
                break

            results[name] = result
            if not result.ok:
                log.stage_failed(name, result.error or "Gate nicht erfuellt")
                self.ctx.state.mark_failed(name, result.error or "Gate nicht erfuellt")
                break

            data = dict(result.data)
            data["_gate"] = result.gate
            log.stage_done(name, result.gate)
            self.ctx.state.mark_done(name, result.gate, data)

        return results

    def summary(self, results: dict[str, StageResult]) -> str:
        """Erzeugt einen kompakten Abschlussbericht."""
        lines = ["", "=" * 46, " Pipeline-Ergebnis", "=" * 46]
        for name in STAGE_ORDER:
            if name not in results:
                continue
            result = results[name]
            mark = "OK  " if result.ok else "FAIL"
            detail = result.gate if result.ok else (result.error or "unbekannt")
            lines.append(f" {mark} {name:<16} {detail}")
        lines.append("=" * 46)
        return "\n".join(lines)
