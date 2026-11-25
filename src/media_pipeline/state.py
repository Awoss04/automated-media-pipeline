"""Persistenter Pipeline-Zustand.

Der Zustand liegt als JSON unter ``data/state/<video_id>.json``. Er ist die
wichtigste Datei im Projekt: Er ermoeglicht den Wiedereinstieg nach einem
Abbruch — jede Stage ueberspringt, was bereits gruen war.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from media_pipeline.models import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SKIPPED,
)


def _now() -> str:
    """Aktueller Zeitstempel in UTC (ISO-8601)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StageState:
    """Zustand einer einzelnen Stage."""

    def __init__(self, name: str, status: str = STATUS_PENDING) -> None:
        self.name = name
        self.status = status
        self.gate = ""
        self.error = ""
        self.data: dict[str, Any] = {}
        self.finished_at = ""

    @property
    def is_done(self) -> bool:
        """True, wenn die Stage erfolgreich **und** mit gruenem Gate endete."""
        return self.status == STATUS_DONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "gate": self.gate,
            "error": self.error,
            "data": self.data,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "StageState":
        stage = cls(name, data.get("status", STATUS_PENDING))
        stage.gate = data.get("gate", "")
        stage.error = data.get("error", "")
        stage.data = data.get("data", {}) or {}
        stage.finished_at = data.get("finished_at", "")
        return stage

class PipelineState:
    """Zustand einer Video-Verarbeitung."""

    def __init__(self, video_id: str, path: Path) -> None:
        self.video_id = video_id
        self.path = path
        self.created_at = _now()
        self.updated_at = self.created_at
        self.stages: dict[str, StageState] = {}
        self.source_url = ""
        self.title = ""

    # ----------------------------------------------------------- erzeugen

    @classmethod
    def load(cls, state_dir: Path, video_id: str) -> "PipelineState":
        """Laedt den Zustand oder erzeugt einen neuen (leeren)."""
        path = state_dir / f"{video_id}.json"
        state = cls(video_id, path)
        if not path.is_file():
            return state

        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            # Beschaedigte State-Datei: neu anlegen statt abstuerzen.
            return state

        state.created_at = data.get("created_at", state.created_at)
        state.updated_at = data.get("updated_at", state.updated_at)
        state.source_url = data.get("source_url", "")
        state.title = data.get("title", "")
        for name, sdata in (data.get("stages") or {}).items():
            state.stages[name] = StageState.from_dict(name, sdata)
        return state

    # -------------------------------------------------------------- zugriff

    def stage(self, name: str) -> StageState:
        """Liefert (und erzeugt bei Bedarf) den Zustand einer Stage."""
        if name not in self.stages:
            self.stages[name] = StageState(name)
        return self.stages[name]

    def is_done(self, name: str) -> bool:
        """True, wenn die Stage bereits gruen war."""
        return name in self.stages and self.stages[name].is_done

    def data_of(self, name: str) -> dict[str, Any]:
        """Die Ergebnis-Daten einer bereits gelaufenen Stage."""
        if name not in self.stages:
            return {}
        return self.stages[name].data

        stage = cls(name, data.get("status", STATUS_PENDING))
        stage.gate = data.get("gate", "")
        stage.error = data.get("error", "")
        stage.data = data.get("data", {}) or {}
        stage.finished_at = data.get("finished_at", "")
        return stage


    # -------------------------------------------------------------- schreiben

    def mark_running(self, name: str) -> None:
        self.stage(name).status = STATUS_RUNNING
        self.save()

    def mark_done(self, name: str, gate: str, data: dict[str, Any] | None = None) -> None:
        """Markiert eine Stage als erfolgreich — **nur** bei gruenem Gate."""
        stage = self.stage(name)
        stage.status = STATUS_DONE
        stage.gate = gate
        stage.error = ""
        stage.data = data or {}
        stage.finished_at = _now()
        self.save()

    def mark_failed(self, name: str, error: str) -> None:
        """Markiert eine Stage als fehlgeschlagen."""
        stage = self.stage(name)
        stage.status = STATUS_FAILED
        stage.error = error
        stage.gate = ""
        stage.finished_at = _now()
        self.save()

    def mark_skipped(self, name: str, reason: str = "") -> None:
        stage = self.stage(name)
        stage.status = STATUS_SKIPPED
        stage.gate = reason
        stage.finished_at = _now()
        self.save()

    def reset(self, names: list[str] | None = None) -> None:
        """Setzt die angegebenen Stages (oder alle) auf ``pending`` zurueck."""
        targets = names if names else list(self.stages)
        for name in targets:
            self.stages[name] = StageState(name)
        self.save()

    # --------------------------------------------------------------- speichern

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "created_at": self.created_at,
            "updated_at": _now(),
            "source_url": self.source_url,
            "title": self.title,
            "stages": {name: s.to_dict() for name, s in self.stages.items()},
        }
    def save(self) -> None:
        """Schreibt den Zustand atomar (tempfile + rename)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=f".{self.path.stem}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp_name, self.path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        self.updated_at = _now()
