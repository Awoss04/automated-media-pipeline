"""Konfiguration laden und validieren.

Quelle der Wahrheit ist ``config/pipeline.yaml``. Kanal-Profile aus
``config/channels.yaml`` ueberschreiben Teile davon.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Fehler beim Laden oder Pruefen der Konfiguration."""


class Config:
    """Zugriff auf die Pipeline-Konfiguration.

    ``config.get("rank.min_score")`` liest verschachtelte Werte per
    Punktpfad. Fehlende Pfade liefern ``None`` statt einer Exception —
    der Aufrufer entscheidet, ob das ein Fehler ist.
    """

    def __init__(self, data: dict[str, Any], project_root: Path) -> None:
        self._data = data
        self.project_root = project_root

    # ---------------------------------------------------------------- laden

    @classmethod
    def load(
        cls,
        project_root: Path | None = None,
        channel: str | None = None,
    ) -> "Config":
        """Laedt ``config/pipeline.yaml`` und optional ein Kanal-Profil."""
        root = Path(project_root or Path.cwd()).resolve()
        config_dir = root / "config"

        pipeline_file = config_dir / "pipeline.yaml"
        if not pipeline_file.is_file():
            raise ConfigError(f"Konfiguration nicht gefunden: {pipeline_file}")

        with pipeline_file.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

        if not isinstance(data, dict):
            raise ConfigError(f"{pipeline_file} enthaelt kein Mapping")

        if channel:
            data = cls._apply_channel(data, config_dir / "channels.yaml", channel)

        cfg = cls(data, root)
        cfg.validate()
        return cfg

    @staticmethod
    def _apply_channel(
        data: dict[str, Any], channels_file: Path, channel: str
    ) -> dict[str, Any]:
        """Ueberschreibt Config-Werte mit einem Kanal-Profil."""
        if not channels_file.is_file():
            raise ConfigError(f"Kanal-Konfiguration nicht gefunden: {channels_file}")

        with channels_file.open("r", encoding="utf-8") as fh:
            channels = (yaml.safe_load(fh) or {}).get("channels", {})

        if channel not in channels:
            known = ", ".join(sorted(channels)) or "(keine)"
            raise ConfigError(f"Unbekannter Kanal '{channel}'. Verfuegbar: {known}")

        merged = dict(data)
        for section, values in channels[channel].items():
            if isinstance(values, dict) and isinstance(merged.get(section), dict):
                combined = dict(merged[section])
                combined.update(values)
                merged[section] = combined
            else:
                merged[section] = values
        return merged

    # --------------------------------------------------------------- zugriff

    def get(self, path: str, default: Any = None) -> Any:
        """Liest einen Wert ueber einen Punktpfad, z. B. ``rank.min_score``."""
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, path: str) -> Any:
        """Wie ``get``, aber fehlende Werte sind ein Fehler."""
        value = self.get(path)
        if value is None:
            raise ConfigError(f"Pflichtwert fehlt in der Konfiguration: {path}")
        return value

    def as_dict(self) -> dict[str, Any]:
        return self._data

    # ------------------------------------------------------------ validierung

    def validate(self) -> None:
        """Prueft die Pflichtwerte und plausible Bereiche."""
        self.require("paths.data_dir")
        self.require("transcribe.model")
        self.require("rank.model")
        self.require("vertical.width")
        self.require("vertical.height")

        min_score = float(self.get("rank.min_score", 0))
        max_score = float(self.get("rank.max_score", 10))
        if not 0 <= min_score <= max_score:
            raise ConfigError(
                f"rank.min_score ({min_score}) muss zwischen 0 und {max_score} liegen"
            )

        min_clip = float(self.get("vertical.min_clip_sec", 0))
        max_clip = float(self.get("vertical.max_clip_sec", 0))
        if min_clip <= 0 or max_clip <= 0 or min_clip > max_clip:
            raise ConfigError(
                f"vertical.min_clip_sec ({min_clip}) muss > 0 und <= max_clip_sec ({max_clip}) sein"
            )

        coverage = float(self.get("transcribe.min_coverage", 0))
        if not 0 < coverage <= 1.5:
            raise ConfigError(f"transcribe.min_coverage ({coverage}) muss in (0, 1.5] liegen")

        width = int(self.get("vertical.width", 0))
        height = int(self.get("vertical.height", 0))
        if width <= 0 or height <= 0:
            raise ConfigError("vertical.width und vertical.height muessen positiv sein")

    # ---------------------------------------------------------------- pfade

    def path(self, key: str) -> Path:
        """Absoluter Pfad aus ``paths.<key>``, relativ zum Projekt-Root."""
        rel = self.require(f"paths.{key}")
        return (self.project_root / rel).resolve()

    def ensure_dirs(self) -> None:
        """Legt alle Arbeitsverzeichnisse an."""
        for key in ("data_dir", "inbox_dir", "work_dir", "clips_dir", "state_dir", "logs_dir"):
            try:
                self.path(key).mkdir(parents=True, exist_ok=True)
            except ConfigError:
                continue


def load_dotenv(path: Path) -> dict[str, str]:
    """Liest eine einfache ``.env``-Datei ohne externe Abhaengigkeit.

    Nur ``KEY=WERT``-Zeilen, ``#``-Kommentare, optionale ``export``-Zeilen.
    Gibt die Werte als Dict zurueck — **ohne** sie zu loggen.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("'\"")
    return values


def env_value(name: str, default: str | None = None) -> str | None:
    """Liest eine Umgebungsvariable — bevorzugt aus der lokalen ``.env``."""
    if name in os.environ:
        return os.environ[name]
    dotenv = load_dotenv(Path.cwd() / ".env")
    if name in dotenv:
        return dotenv[name]
    return default
