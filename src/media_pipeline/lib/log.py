"""Logging mit Zeitstempel.

Kein ``print()`` im Code — alles laeuft hier durch, damit die Ausgabe
einheitlich ist und in die Logdatei wandert.

**Sicherheitsregel:** Es werden **niemals** Credentials geloggt. Funktionen
mit sensiblen Argumenten (z. B. Cookies) uebergeben nur Pfade oder Namen.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}

_current_level = _LEVELS["info"]
_log_file: Path | None = None


def configure(log_dir: Path | None = None, level: str = "info") -> None:
    """Setzt Log-Level und optional die Logdatei."""
    global _current_level, _log_file
    _current_level = _LEVELS.get(level, _LEVELS["info"])
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")
        _log_file = log_dir / f"pipeline-{stamp}.log"


def _emit(level: str, message: str) -> None:
    if _LEVELS.get(level, 20) < _current_level:
        return
    stamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] {level.upper():<5} {message}"
    print(line, file=sys.stderr)
    if _log_file is not None:
        try:
            with _log_file.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def debug(message: str) -> None:
    _emit("debug", message)


def info(message: str) -> None:
    _emit("info", message)


def warn(message: str) -> None:
    _emit("warn", message)


def error(message: str) -> None:
    _emit("error", message)


def stage_start(name: str) -> None:
    """Meldet den Beginn einer Stage."""
    _emit("info", f"=== {name} ===")


def stage_done(name: str, gate: str) -> None:
    """Meldet eine erfolgreich abgeschlossene Stage samt Gate-Beleg."""
    _emit("info", f"    OK {name}: {gate}")


def stage_failed(name: str, error_msg: str) -> None:
    """Meldet eine fehlgeschlagene Stage."""
    _emit("error", f"    FEHLER {name}: {error_msg}")


def stage_skipped(name: str, reason: str = "bereits erledigt") -> None:
    """Meldet eine uebersprungene Stage."""
    _emit("info", f"    SKIP {name}: {reason}")
