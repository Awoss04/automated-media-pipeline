"""Wrapper fuer die Antigravity-CLI (``agy``).

Die Pipeline spricht **ausschliesslich** ueber Antigravity — nie direkt
mit der Gemini-API, nie mit einem API-Key, nie mit Claude.

Warum Antigravity statt eigenstaendiger API-Aufrufe:

- kein API-Key noetig (OAuth ueber Antigravity)
- `--json-schema` erzwingt strukturiertes JSON — kein Nachbearbeiten
- dasselbe Modell, das auch in der IDE neben dem Nutzer laeuft

Der Aufruf nutzt ``--print`` (non-interaktiv) und
``--output-format json``. Die Antwort wird als JSON-Objekt erwartet;
bei ``stream-json`` wird die letzte ``agent_response``-Zeile genommen.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

# Standard-Binary. Kann ueber .env (AGY_BIN) ueberschrieben werden.
DEFAULT_AGY = "agy"

# Modelle, die Antigravity bereitstellt (Stand 2026-11).
MODEL_RANK = "gemini-3.8-flash-high"
MODEL_THUMBNAIL = "gemini-3.8-flash-medium"
MODEL_LONGFORM = "gemini-3.8-flash-low"


class AgyError(Exception):
    """Fehler beim Aufruf der Antigravity-CLI."""


# Bekannte Installationsorte, newest first. Die Version in
# ~/.gemini/bin ist die Antigravity-CLI (1.5.x) — sie kennt
# --print, --output-format und --json-schema. Aeltere Kopien in
# ~/.local/bin (1.0.x) kennen diese Flags NICHT und wuerden mit
# "flags provided but not defined" abbrechen.
CANDIDATE_PATHS = [
    Path.home() / ".gemini" / "bin" / DEFAULT_AGY,
    Path.home() / ".local" / "bin" / DEFAULT_AGY,
]


def _supports_required_flags(path: str) -> bool:
    """Prueft, ob die CLI die noetigen Flags (--output-format, --json-schema) kennt."""
    try:
        result = subprocess.run(
            [path, "--help"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    help_text = (result.stdout or "") + (result.stderr or "")
    return "--output-format" in help_text and "--json-schema" in help_text


def binary_path() -> str:
    """Pfad zur Antigravity-CLI mit den noetigen Flags.

    Reihenfolge: explizite Konfiguration (``AGY_BIN``) → bekannte
    Installationsorte → PATH. Jede Kandidat wird geprueft, ob er
    ``--output-format`` und ``--json-schema`` unterstuetzt.
    """
    from media_pipeline.config import env_value

    configured = env_value("AGY_BIN")
    if configured:
        return configured

    for candidate in CANDIDATE_PATHS:
        if candidate.is_file() and _supports_required_flags(str(candidate)):
            return str(candidate)

    found = shutil.which(DEFAULT_AGY)
    if found and _supports_required_flags(found):
        return found

    # Nichts Passendes gefunden — den wahrscheinlichsten Pfad zurueckgeben,
    # damit die Fehlermeldung den echten Grund nennt.
    for candidate in CANDIDATE_PATHS:
        if candidate.is_file():
            return str(candidate)
    return DEFAULT_AGY


def available() -> bool:
    """True, wenn die Antigravity-CLI gefunden wurde."""
    return shutil.which(binary_path()) is not None or Path(binary_path()).is_file()


def _extract_json(text: str) -> Any:
    """Holt JSON aus der Antwort.

    Erlaubt ist ein reines JSON-Objekt oder ein JSON in einem
    Markdown-Codeblock — Letzteres kommt vor, wenn das Modell sich nicht
    strikt an die Vorgabe haelt.
    """
    stripped = text.strip()
    if not stripped:
        raise AgyError("Leere Antwort von Antigravity")

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # JSON aus einem Codeblock extrahieren.
    if "```" in stripped:
        chunks = stripped.split("```")
        for chunk in chunks:
            candidate = chunk.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if not candidate.startswith("{"):
                continue
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    raise AgyError("Antwort von Antigravity enthaelt kein gueltiges JSON")


def json_out(
    prompt: str,
    schema_path: Path | None = None,
    model: str = MODEL_RANK,
    timeout: int = 300,
    extra_args: list[str] | None = None,
) -> Any:
    """Ruft Antigravity auf und gibt das geparste JSON zurueck.

    Args:
        prompt: Der vollstaendige Prompt (inkl. Instruktionen).
        schema_path: Optionaler Pfad zu einem JSON-Schema. Wird ueber
            ``--json-schema`` durchgesetzt — dadurch ist die Antwort
            garantiert strukturiert.
        model: Modellname, z. B. ``gemini-3.8-flash-high``.
        timeout: Timeout in Sekunden.
        extra_args: Zusaetzliche CLI-Argumente.

    Returns:
        Das geparste JSON (dict oder list).
    """
    if not prompt.strip():
        raise AgyError("Leerer Prompt")

    cmd = [
        binary_path(),
        "--print", prompt,
        "--output-format", "json",
        "--model", model,
    ]
    if schema_path is not None:
        if not Path(schema_path).is_file():
            raise AgyError(f"JSON-Schema nicht gefunden: {schema_path}")
        cmd += ["--json-schema", str(schema_path)]
    if extra_args:
        cmd += list(extra_args)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise AgyError(f"Antigravity-Timeout nach {timeout}s") from exc
    except OSError as exc:
        raise AgyError(f"Antigravity-CLI nicht startbar: {exc}") from exc

    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-8:]
        raise AgyError(
            f"Antigravity fehlgeschlagen (rc={result.returncode}):\n" + "\n".join(tail)
        )

    stdout = (result.stdout or "").strip()
    if not stdout:
        raise AgyError("Antigravity lieferte keine Ausgabe")

    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return _extract_json(stdout)

    if isinstance(envelope, dict):
        if envelope.get("status") not in (None, "SUCCESS"):
            raise AgyError(
                f"Antigravity meldet Status {envelope.get('status')}: "
                f"{str(envelope.get('response', ''))[:300]}"
            )

        # Bei --json-schema liefert Antigravity das Ergebnis im Feld
        # "structured_output" — nicht in "response".
        structured = envelope.get("structured_output")
        if structured is not None:
            return structured

        response = envelope.get("response")
        if isinstance(response, str) and response.strip():
            return _extract_json(response)
        return envelope

    return envelope
