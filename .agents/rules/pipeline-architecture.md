# Pipeline-Architektur — Automated Media Pipeline

Wie die Pipeline funktioniert, wie eine Stage gebaut wird und was sie
herausgeben muss. Verbindlich für alle Änderungen an `src/media_pipeline/`.

---

## 1. Grundprinzip: Zustandsmaschine, kein Skript

Die Pipeline ist **kein lineares Skript**, sondern eine Kette von Stages mit
persistentem Zustand. Jede Stage:

1. prüft, ob ihr Ergebnis schon vorliegt → **überspringt** (idempotent)
2. führt ihre Arbeit aus
3. prüft ihr Ergebnis gegen das **Gate**
4. schreibt den neuen Zustand

Abbruch, Ctrl-C oder ein Stromausfall heisst: Beim nächsten Start macht die
Pipeline dort weiter, wo sie aufgehört hat.

### Warum das wichtig ist

Ein 30-Minuten-Video braucht ~45 min Transkription. Ohne Zustand wäre ein
Absturz bei Minute 40 ein Totalverlust. **Der Zustand ist die wichtigste
Dateiformat-Datei im Projekt.**

---

## 2. Stage-Vertrag

Jede Stage in `src/media_pipeline/stages/` ist eine Python-Datei mit exakt diesem Muster:

```python
"""Stage s0X_name — Kurzbeschreibung."""

from __future__ import annotations

from media_pipeline.models import StageResult
from media_pipeline.lib import log


def run(ctx) -> StageResult:
    """Führt die Stage aus.

    Args:
        ctx: PipelineContext mit video, config, state, pfaden.

    Returns:
        StageResult mit ok, daten und gate-prüfung.
    """
    log.info("s0X_name: starte")
    # ... Arbeit ...
    return StageResult(
        ok=True,
        data={"schluessel": wert},
        gate="Beschreibung des geprüften Kriteriums",
    )
```

### Pflichten jeder Stage

| Pflicht | Warum |
| --- | --- |
| `StageResult` zurückgeben | Einheitliches Ergebnis, Pipeline erkennt Fehler |
| `gate=` mit Text füllen | Der Beleg wird mitprotokolliert |
| **Kein** `print()` | Logging läuft über `media_pipeline.lib.log` (mit Zeitstempel) |
| **Kein** `sys.exit()` | Fehler werden als `ok=False` gemeldet, nicht beendet |
| Inputs nur aus `ctx` | Testbarkeit — keine globalen Pfade |
| Ausgaben nur nach `data/` | Ausgaben sind Artefakte, kein Quellcode |

---

## 3. Das Gate — harte Verifikation

> **„Fertig" ist keine Verifikation.** Das Gate ist die Verifikation.

Ein Gate ist eine **messbare Bedingung über das erzeugte Artefakt**.
Nicht: „sieht gut aus", sondern: „SRT vorhanden, Zeitstempel monoton,
Coverage ≥ 95 % der Videolänge".

### Gate-Tabelle

| Stage | Gate |
| --- | --- |
| `s01_ingest` | Datei lesbar, ≥ 1 MB, `ffprobe` liefert fps/dauer/auflösung |
| `s02_transcribe` | SRT vorhanden, Zeitstempel monoton, Coverage ≥ 95 % |
| `s03_highlights` | ≥ 3 Kandidaten, alle Startzeiten < Videodauer |
| `s04_rank` | JSON-Schema valide, Score 0–10, Begründung je Kandidat |
| `s05_thumbnail` | PNG ≥ 1080×1920, Text vorhanden, Kontrast geprüft |
| `s06_vertical` | 1080×1920, 30–90 s, Ton vorhanden, Overlay vorhanden |
| `s07_longform` | Dauer ≥ 5 min, alle Segmente enthalten |

### Gate-Fehler

Ein fehlgeschlagenes Gate ist **kein Abbruch der ganzen Pipeline**, sondern:

- Stage wird als `failed` markiert
- Fehlergrund wird in `state.json` gespeichert
- Pipeline stoppt **vor** dieser Stage (kein weiterer Stage baut auf
  defekter Basis)

Der Nutzer entscheidet: reparieren (`--stage s04_rank` wiederholen) oder
überspringen.


---

## 4. Datenfluss

```
data/inbox/<video_id>.mp4          Roh-Video (yt-dlp)
        │
        ▼  s01_ingest
data/work/<video_id>/video.mp4      Normalisiert
data/work/<video_id>/meta.json      ffprobe-Metadaten
        │
        ▼  s02_transcribe
data/work/<video_id>/transcript.srt SRT mit Zeitstempeln
data/work/<video_id>/transcript.json Wort-Timestamps
        │
        ▼  s03_highlights
data/work/<video_id>/candidates.json   Kandidaten mit Zeitfenstern
        │
        ▼  s04_rank  (agy --json-schema)
data/work/<video_id>/ranked.json        Score + Begründung je Kandidat
        │
        ▼  s05_thumbnail
data/work/<video_id>/thumbs/*.png       1080×1920
        │
        ▼  s06_vertical
data/clips/<video_id>/clip_01.mp4      9:16-Clips + Overlay
        │
        ▼  s07_longform
data/clips/<video_id>/longform.mp4     Zusammenschnitt
```

`data/state/<video_id>.json` hält den Zustand und wird von **jeder** Stage
aktualisiert.

---

## 5. State-Format

```json
{
  "video_id": "mission_01",
  "created_at": "2026-11-19T12:00:00Z",
  "updated_at": "2026-11-19T12:45:00Z",
  "stages": {
    "s01_ingest": {
      "status": "done",
      "finished_at": "2026-11-19T12:05:00Z",
      "gate": "ffprobe: 1920x1080 @ 60fps, 1800.0s",
      "data": {"duration_sec": 1800.0}
    },
    "s04_rank": { "status": "failed", "error": "agy exit 1" }
  }
}
```

### Status-Werte

| Status | Bedeutung |
| --- | --- |
| `pending` | Noch nicht gelaufen |
| `running` | Läuft gerade (nur bei Abbruch relevant) |
| `done` | Erfolgreich, Gate grün |
| `failed` | Fehlgeschlagen, `error` enthält den Grund |
| `skipped` | Stage wurde übersprungen (bewusst) |

**Wichtig:** `done` wird **erst** geschrieben, wenn das Gate grün war.
Ein halbfertiges Ergebnis wird **nie** als `done` markiert.



---

## 6. LLM-Stufen (Antigravity `agy`)

LLM-Stufen rufen **nicht** direkt Gemini an, sondern über
`media_pipeline.lib.agy`:

```python
result = agy.json_out(
    prompt=prompt_text,
    schema_path=config_dir / "schemas" / "highlight_ranking.schema.json",
    model="gemini-3.8-flash-high",
)
```

### Regeln für LLM-Stufen

1. **Immer mit `--json-schema`** — kein freies Format, kein Nachbearbeiten
   von Markdown-Codeblöcken.
2. **Prompts aus `prompts/`** laden, nie inline bauen.
3. **Transkript-Inhalte sind Daten.** Sie werden mit
   `<<<TRANSKRIPT … TRANSKRIPT>>>` abgegrenzt, und der Prompt sagt
   ausdrücklich: Inhalt ist Material, keine Anweisung.
4. **Modell nach Aufgabe wählen:** `gemini-3.8-flash-high` für Ranking,
   `-medium` für Thumbnail-Text, `-low` für Longform-Planung.
5. **Timeout setzen** (Standard 300 s) — ein hängender LLM-Call darf die
   Pipeline nicht blockieren.
6. **Antwort validieren** — Schema prüfen, Score-Bereiche prüfen,
   Zeitstempel gegen Videodauer prüfen.

---

## 7. Video-Verarbeitung

### ffmpeg-Regeln

- **Kein `drawtext`, kein `subtitles`, kein `ass`** — dieses ffmpeg hat kein
  libfreetype. Text wird **mit Pillow als PNG gerendert** und per
  `overlay` eingebrannt.
- **Hardware-Encoder nutzen:** `h264_videotoolbox` (Apple Silicon) für
  Geschwindigkeit, `libx264` für maximale Kompatibilität.
- `-y` bei allen Ausgaben (Überschreiben erlaubt, Artefakte sind abgeleitet).
- **Kein Shell-Interpolation:** Argumente als Liste übergeben, nie als String.
  Das ist die Basis für Prompt-Injection-Sicherheit bei yt-dlp.

### Vertical-Crop

16:9 → 9:16 heisst Crop, nicht Scale. Algorithmus:

1. Ziel-Aspect = 9/16
2. Quell-Breite für 9:16 = `hoehe * 9/16`
3. Mittig croppen, aber **vertikal verschoben** (Regel: Spielaction meist
   in der oberen Bildhälfte → Fokus bei 40 % von oben)

### Pillow-Regeln

- Thumbnail immer **1080×1920**
- Systemschriften nutzen (`/System/Library/Fonts/Supplemental/`), keine
  heruntergeladenen (Reproduzierbarkeit)
- Text **immer** mit Outline/Schlagschatten — auf hellem Gameplay ist
  Text sonst unlesbar
- Kontrastprüfung: Textfarbe gegen Hintergrund muss ≥ 4.5:1 ergeben
