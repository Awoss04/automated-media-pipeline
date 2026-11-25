"""Datenmodelle der Pipeline.

Alle Modelle sind ``dataclass``-basiert und serialisierbar über
``to_dict``/``from_dict`` — der State auf der Platte ist JSON.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VideoMeta:
    """Metadaten eines Videos, aus ``ffprobe``."""

    path: str
    duration_sec: float
    width: int
    height: int
    fps: float
    codec: str = ""
    size_bytes: int = 0

    @property
    def is_vertical(self) -> bool:
        return self.height > self.width

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VideoMeta":
        return cls(**{k: v for k, v in data.items() if k in {f.name for f in dataclasses.fields(cls)}})


@dataclass
class TranscriptSegment:
    """Ein transkribiertes Segment mit Zeitstempeln."""

    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class Transcript:
    """Das vollständige Transkript eines Videos."""

    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str = ""
    model: str = ""
    words: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        """Gedeckte Zeit spanne in Sekunden."""
        if not self.segments:
            return 0.0
        return max(s.end for s in self.segments) - min(s.start for s in self.segments)

    @property
    def full_text(self) -> str:
        return " ".join(s.text for s in self.segments if s.text)

    def segments_in(self, start: float, end: float) -> list[TranscriptSegment]:
        """Alle Segmente, die das Zeitfenster überschneiden."""
        return [s for s in self.segments if s.end > start and s.start < end]

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "model": self.model,
            "segments": [s.to_dict() for s in self.segments],
            "words": self.words,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Transcript":
        segs = [
            TranscriptSegment(start=float(s["start"]), end=float(s["end"]), text=s.get("text", ""))
            for s in data.get("segments", [])
        ]
        return cls(
            segments=segs,
            language=data.get("language", ""),
            model=data.get("model", ""),
            words=data.get("words", []),
        )


@dataclass
class Candidate:
    """Ein Highlight-Kandidat aus ``s03_highlights``."""

    id: str
    start: float
    end: float
    reason: str = ""
    energy: float = 0.0
    transcript: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Candidate":
        return cls(
            id=str(data["id"]),
            start=float(data["start"]),
            end=float(data["end"]),
            reason=data.get("reason", ""),
            energy=float(data.get("energy", 0.0)),
            transcript=data.get("transcript", ""),
        )


@dataclass
class RankedClip:
    """Ein bewerteter Kandidat aus ``s04_rank``."""

    id: str
    start: float
    end: float
    score: float
    reason: str = ""
    title: str = ""
    keywords: list[str] = field(default_factory=list)
    best_moment_sec: float = 0.0
    thumb_path: str = ""
    clip_path: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str[str, Any]]) -> "RankedClip":
        return cls(
            id=str(data["id"]),
            start=float(data["start"]),
            end=float(data["end"]),
            score=float(data.get("score", 0.0)),
            reason=data.get("reason", ""),
            title=data.get("title", ""),
            keywords=list(data.get("keywords", [])),
            best_moment_sec=float(data.get("best_moment_sec", 0.0)),
            thumb_path=data.get("thumb_path", ""),
            clip_path=data.get("clip_path", ""),
        )


@dataclass
class StageResult:
    """Ergebnis einer Stage.

    ``ok`` ist das Gate-Ergebnis: ``True`` bedeutet, das Artefakt wurde
    real geprüft und erfüllt die Kriterien. ``gate`` beschreibt, **was**
    geprüft wurde — das ist der Beleg.
    """

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    gate: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "gate": self.gate,
            "error": self.error,
        }


# Status-Werte für Stage-Einträge im State.
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
