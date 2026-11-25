# Automated Media Pipeline

Automatische Pipeline: private YouTube-Gameplay-Videos werden erkannt,
heruntergeladen, transkribiert, auf Highlights analysiert, als 9:16-Clips
zugeschnitten und mit generiertem Thumbnail versehen.

## Zweck

Aus einem langen Gameplay-Recording (PS5 → HDMI-Capture → privates
YouTube-Video) erzeugt die Pipeline automatisch:

1. **Highlight-Kandidaten** —momente mit Spannung, Actions oder Komik
2. **Vertikale Clips** (1080×1920, 30–90 s) für Shorts/Reels/TikTok
3. **Thumbnails** (1080×1920) mit generiertem Kurztitel
4. **Long-Form-Zusammenschnitte** aus mehreren Highlights

Die Pipeline **bereitet vor**. Veröffentlichen entscheidet immer der Mensch.

## Architektur-Überblick

| Pfad | Inhalt |
| --- | --- |
| `src/media_pipeline/` | **Quelle.** Package mit `pipeline.py` (Orchestrator) und `stages/` |
| `config/` | Schwellwerte, Kanal-Profile, JSON-Schemas für LLM-Ausgaben |
| `prompts/` | LLM-Prompts als versionierte Dateien |
| `data/` | **Arbeitsdaten** (gitignored): `inbox/`, `work/`, `clips/`, `state/` |
| `.agents/rules/` | Verbindliche Regeln: Struktur, Security, Test-Pflichten |
| `.agents/skills/pipeline/` | Der Pipeline-Workflow als Skill |

## Pipeline-Stages

Jede Stage ist einzeln ausführbar und hat ein **hartes Gate** — „fertig"
ist keine Verifikation, das Gate prüft das Artefakt real.

| Stage | Aufgabe | Gate |
| --- | --- | --- |
| `s01_ingest` | `yt-dlp` zieht vom privaten YouTube-Video | `ffprobe` liefert fps/dauer/auflösung |
| `s02_transcribe` | faster-whisper (int8) → SRT + Wort-Timestamps | Zeitstempel monoton, Coverage ≥ 95 % |
| `s03_highlights` | Audio-Energy + Satz-Segmentierung → Kandidaten | ≥ 3 Kandidaten, Startzeiten gültig |
| `s04_rank` | Antigravity (`agy`) bewertet Kandidaten | JSON-Schema valide, Score 0–10 |
| `s05_thumbnail` | Pillow rendert 9:16-Thumbnail | PNG ≥ 1080×1920, Text lesbar |
| `s06_vertical` | ffmpeg crop/scale/overlay → 9:16 | 1080×1920, 30–90 s, Ton vorhanden |
| `s07_longform` | ffmpeg concat → Long-Form | ≥ 5 min, alle Segmente enthalten |

## Werkzeuge (alle lokal vorhanden, verifiziert)

| Werkzeug | Zweck |
| --- | --- |
| `agy` (Antigravity CLI) | LLM-Stufen — **kein API-Key**, OAuth über Antigravity |
| `faster-whisper` (venv) | Transkription auf CPU, CTranslate2 int8 |
| `ffmpeg` / `ffprobe` | Schnitt, Crop, Overlay, Metadaten |
| `yt-dlp` (venv) | Download privater YouTube-Videos |
| `Pillow` (venv) | Thumbnail- und Text-Rendering |

> `ffmpeg` dieses Systems hat **kein `drawtext`** (kein libfreetype).
> Text wird deshalb **immer mit Pillow als PNG gerendert** und per
> `overlay` eingebrannt — niemals `drawtext`/`subtitles` verwenden.

## Grenzen

- **Rechtlich:** GTA-6-/Rockstar-Material ist urheberrechtlich geschützt.
  Monetarisierte Clips und Thumbnails sind gewerbliche Nutzung und **nicht
  erlaubt**. Fan-Videos sind Grauzone. Die Pipeline baut vor, **die
  Veröffentlichung entscheidet der Mensch**.
- **Speicher:** Auf 256-GB-SSDs ist Platz knapp. Das Quellvideo wird nach
  `s06` gelöscht (Stage `cleanup`).
## YouTube-Zugriff

- **Quelle:** `config/pipeline.yaml` unter `ingest:` — `channel_url`
  (empfohlen), `playlist_url` oder `video_url`. Es gilt: die **erste
  gesetzte** Quelle gewinnt.
- **Befehle:**
  - `python -m media_pipeline channels --set @handle` — Kanal dauerhaft speichern
  - `python -m media_pipeline channels --list-only` — Videos ansehen, kein Download
  - `python -m media_pipeline channels --check-cookies` — Browser-Login pruefen
  - `python -m media_pipeline run` — Kanal komplett verarbeiten
- **Browser-Profile:** Auf macOS hat ein Browser oft mehrere Profile.
  Nur eines liefert echte Video-Formate — ein Profil mit ungueltigen
  Cookies liefert nur Storyboards. Die Pipeline prueft jedes Profil
  automatisch (`detect_working_profile`) und nimmt das funktionierende.
  Man kann es auch fest setzen: `cookies_from_browser: "brave:Profile 1"`.
- **Cookies:** werden direkt aus dem Browser gelesen, **nie** exportiert
  oder gespeichert. Keine Credentials im Repository.
- **Reihenfolge:** YouTube liefert Kanalvideos chronologisch aufsteigend.
  Ohne `--playlist-reverse` stehen die **neuesten** zuerst.
- **Archiv:** Erfolgreich verarbeitete Videos landen in
  `data/state/downloaded.txt` und werden nicht erneut geladen.

## Nutzung

```bash
# Kompletter Lauf
./venv/bin/python -m media_pipeline run --url "<youtube-url>"

# Nur einzelne Stage wiederholen
./venv/bin/python -m media_pipeline run --video-id mission_01 --stage s04_rank

# Status anzeigen
./venv/bin/python -m media_pipeline status
```

## Detail-Regeln (modularer Kontext)

| Thema | Datei |
| --- | --- |
| Struktur, Python-Konventionen, Security, Test-Pflichten | `.agents/rules/coding-standards.md` |
| Zustandsmaschine, Stage-Verträge, Gates, Datenfluss | `.agents/rules/pipeline-architecture.md` |
| Verbindlicher Arbeitsablauf (Ablauf-Skill) | `.agents/skills/pipeline/SKILL.md` |

Die Dateien unter `.gemini/` sind Symlink-Spiegel auf `.agents/` — Antigravity
liest `.agents/`, die Ordnerstruktur bleibt unter `.gemini/` sichtbar.
**Beide Pfade nie unabhängig voneinander bearbeiten.**
