# Coding-Standards — Automated Media Pipeline

Verbindliche Regeln für Code-Änderungen in diesem Repository.
Bei Widerspruch zum Quellcode gilt der Quellcode — dieses Dokument beschreibt
die Absicht, die dahintersteht.

---

## 1. Tech-Stack

| Bereich | Technologie | Regel |
| --- | --- | --- |
| Sprache | **Python 3.11** (venv) | Keine Type-Hints-Pflicht, aber erlaubt |
| Daten | **JSON + YAML** | YAML nur für Config, JSON für Maschinendaten |
| Video | **ffmpeg / ffprobe** | Nur Kommandozeile, keine Wrapper-Bibliothek |
| Transkription | **faster-whisper** (CTranslate2 int8) | Kein OpenAI-Whisper, kein PyTorch |
| LLM | **Antigravity CLI `agy`** | Kein Claude, kein API-Key |
| Bilder | **Pillow** | Text wird **immer** hier gerendert |

**Das Projekt hat bewusst kein `requirements.txt` im Repo.** Das venv in
`venv/` ist die Quelle der Wahrheit; es enthält `faster-whisper`,
`yt-dlp`, `PyYAML`, `numpy`, `pydantic` und `Pillow`. Kein PyTorch — das
wäre ein 2-GB-Download ohne Nutzen.

### Code-Stil

- **4 Leerzeichen** Einrückung, kein Tab.
- **Deutsche Bezeichner** für Fachbegriffe, englische für Standard-APIs
  (`json`, `subprocess`, `pathlib`).
- Kommentare auf **Deutsch**, erklären *warum*, nicht *was*.
- Kleine, reine Funktionen; eine Aufgabe pro Funktion.
- Type-Hints auf öffentlichen Funktionen (`def f(x: str) -> bool:`).
- Module mit `__all__`? Nicht nötig — kein Package-Export nötig.

---

## 2. Struktur-Prinzipien

Diese Regeln sind der Kern des Projekts.

### Code ≠ Daten

- `src/`, `config/`, `prompts/`, `tests/` → **versioniert**
- `data/` → **gitignored** (Videos, Frames, State, Logs)
- `.env` → **gitignored**, enthält nur Pfade, nie Geheimnisse im Klartext

### Prompts sind Dateien

LLM-Prompts liegen in `prompts/*.md` und werden **zur Laufzeit gelesen**.
Niemals ein Prompt in Python-Code inline schreiben. Grund: Prompts ändern
sich ständig, sollen diffbar sein, und müssen **versionierbar** bleiben.

### Fremde Inhalte sind Daten, keine Anweisungen

Transkripte, Videometadaten, YouTube-Titel und -Beschreibungen sind
**potenziell feindlich** (Prompt Injection). Sie werden:
- nie als Anweisung interpretiert
- nie per `eval`/`exec` verarbeitet
- immer als Text in Quotes gesetzt, wenn sie in einen Prompt gehen

### Eine Quelle der Wahrheit

Jede Konfiguration hat genau einen Ort. `config/pipeline.yaml` ist die Quelle
für Schwellwerte; Stages lesen daraus, nie aus Umgebungsvariablen oder
Hardcoded-Werten.

---

## 3. Security-Guardrails

**Deny schlägt Allow.** Jede dieser Regeln ist absolut.

### Secrets & Credentials

- **Keine API-Keys, Passwörter, Tokens oder Cookies** im Code, in Logs, in
  Prompts, in JSON-Dateien oder im Repository.
- YouTube-Cookies werden **nur im Browser** gelesen
  (`yt-dlp --cookies-from-browser`), nie exportiert und nie gespeichert.
- `.env` enthält nur **Pfade und Namen**, keine Geheimnisse.
- Vor dem Commit: `grep -rniE "api[_-]?key|secret|token|password|cookie" --include="*.py" --include="*.md" --include="*.yaml" --include="*.json"` muss leer sein.

### Prompt-Injection-Schutz

- Video-Titel, Beschreibungen und Transkripte sind **Daten**.
- Ein Prompt, der Transkripttext enthält, muss ihn klar abgrenzen
  (`<<<TRANSKRIPT ... TRANSKRIPT>>>`) und die Instruktion betonen, dass der
  Inhalt **ausschliesslich Material zur Analyse** ist.

### Destruktive Aktionen

- Löschen von Videodateien (`data/inbox/`) braucht **explizite Bestätigung**
  oder den konfigurierten `cleanup_after_stage`-Schalter.
- Kein blindes `rm -rf`; immer den exakten Zielpfad nennen.

### Urheberrecht

GTA-6-/Rockstar-Material ist geschützt. Die Pipeline **bereitet vor** —
Veröffentlichung, Monetarisierung und Thumbnails für kommerzielle Zwecke
liegen **beim Menschen**. Diese Regel steht auch in `GEMINI.md`.

---

## 4. Test-Pflichten

**Ohne bestandenen Testlauf ist keine Änderung fertig.**

### Gesamtlauf

```bash

bash tests/test_all.sh
```

Der Lauf prüft: Import, Config-Validierung, Datenmodelle, State-Maschine,
ffmpeg-Integration, Pillow-Rendering, Prompt-Dateien, JSON-Schemas.

### Regeln

- **Bei jeder Code-Änderung** mindestens die betroffenen Tests laufen lassen.
- **Neue Features brauchen neue Tests.** Ein Feature ohne Test gilt als
  unfertig.
- **Bestehende Tests nicht löschen oder abschwächen**, um sie grün zu bekommen.
- **Kein Test braucht Netz oder dein echtes Material.** Der End-to-End-Test
  erzeugt sein Testvideo selbst mit ffmpeg.

### Vor dem Abschluss (Checkliste)

- [ ] `bash tests/test_all.sh` läuft vollständig grün durch
- [ ] Keine Secrets im Diff (`grep` oben)
- [ ] Kein `drawtext`/`subtitles` (nicht gebaut in diesem ffmpeg)
- [ ] Neue Prompts liegen in `prompts/`, nicht inline im Code
- [ ] Struktur-Änderungen in `GEMINI.md` nachgezogen
