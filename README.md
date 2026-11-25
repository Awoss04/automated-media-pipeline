# Automated Media Pipeline

> Modulare Python-Pipeline zur automatisierten Erfassung, Transkription, Highlight-Analyse und Ausspielung von Videoinhalten via CLI.  
> Entwickelt nach klaren DevSecOps- und Clean-Architecture-Prinzipien.

---

## Architektur & Funktionsweise

Die Pipeline verarbeitet Rohvideos in einer deterministischen Stage-Architektur:

1. **Stage 00 – Channel & Source Resolution:** Identifikation verfuegbarer Quellvideos via yt-dlp.
2. **Stage 01 – Ingest:** Zuverlaessiger Download von Video- und Metadaten-Streams.
3. **Stage 02 – Transcribe:** Spracherkennung und Generierung synchronisierter Untertitel.
4. **Stage 03 – Highlight Extraction:** Semantische Analyse relevanter Segmente basierend auf definierten JSON-Schemas.
5. **Stage 04 – Rendering & Formatting:** Verlustfreier Zuschnitt auf Hochformat (9:16) inklusive Untertiteln und Thumbnail-Generierung via FFMPEG.

---

## Technische Highlights

- **Dependency Injection:** Zentral gesteuerter `PipelineContext` ermoeglicht isolierte Tests fuer jede einzelne Verarbeitungsstufe.
- **Validierung via Schemas:** Alle KI-gestuetzten Zwischenergebnisse werden gegen strikte JSON-Schemas (`config/schemas/`) validiert.
- **Human-in-the-Loop:** Die Pipeline bereitet Inhalte autonom vor – die Freigabe und Veroeffentlichung erfolgt immer durch den Menschen.
- **Datenschutz & Hygiene:** Authentifizierungs-Cookies und Tokens werden ausschliesslich fluechtig im Arbeitsspeicher gehalten und niemals persistiert oder versioniert.

---

## Schnellstart

```bash
# Virtuelle Umgebung einrichten & Abhaengigkeiten installieren
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Pipeline-Status pruefen
PYTHONPATH=src python -m media_pipeline status

# Testlauf ausfuehren
PYTHONPATH=src python -m media_pipeline run --dry-run
```

## Konfiguration

Kopieren Sie die Vorlage `.env.example` nach `.env`:

```bash
cp .env.example .env
```

Saemtliche systemspezifischen Pfade und Einstellungen verbleiben ausschliesslich in der lokalen `.env` (via `.gitignore` geschuetzt).

---

## Lizenz

MIT License
