"""Kommandozeile der Pipeline.

    python -m media_pipeline run --url "<youtube-url>"
    python -m media_pipeline run --video-id mission_01 --stage s04_rank
    python -m media_pipeline status
    python -m media_pipeline reset --video-id mission_01

Der Einstieg erfolgt ueber ``python -m media_pipeline`` — siehe
``src/media_pipeline/__main__.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from media_pipeline.config import Config, ConfigError
from media_pipeline.lib import log
from media_pipeline.pipeline import STAGE_ORDER, Pipeline, PipelineContext, slugify
from media_pipeline.state import PipelineState

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_GATE_FAILED = 2
EXIT_CONFIG_ERROR = 3


def _project_root() -> Path:
    """Projekt-Root: drei Ebenen ueber ``src/media_pipeline/cli.py``."""
    return Path(__file__).resolve().parents[2]


def _make_context(
    config: Config, video_id: str, source_url: str = "", title: str = ""
) -> PipelineContext:
    """Baut den Pipeline-Kontext und laedt den Zustand."""
    state_dir = config.path("state_dir")
    state = PipelineState.load(state_dir, video_id)
    if source_url:
        state.source_url = source_url
    if title:
        state.title = title
    state.save()
    return PipelineContext(config, state, video_id, source_url, title)


def cmd_run(args: argparse.Namespace) -> int:
    """Fuehrt die Pipeline aus."""
    from media_pipeline.lib import youtube

    root = _project_root()
    try:
        config = Config.load(root, channel=args.channel)
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return EXIT_ERROR

    config.ensure_dirs()
    log.configure(config.path("logs_dir"), level=args.log_level)

    # Kanal-Modus: mehrere Videos nacheinander verarbeiten.
    channel_url, source_type = youtube.detect_source(config)
    if args.channel and not args.url and not args.video_id:
        # --channel ist hier der KANAL (nicht das Kanalprofil). Das
        # Kanalprofil kommt aus --profile.
        pass

    if source_type in ("channel", "playlist") and not args.url and not args.video_id:
        return _run_channel_mode(args, config, root, channel_url, source_type)

    # Video-ID: aus URL (YouTube-ID) oder explizit.
    video_id = args.video_id
    if not video_id:
        if not args.url:
            print("Fehler: --url oder --video-id noetig", file=sys.stderr)
            return EXIT_ERROR
        video_id = slugify(args.url.rsplit("/", 1)[-1].split("v=")[-1][:20])

    ctx = _make_context(config, video_id, args.url or "", args.title or "")

    pipeline = Pipeline(ctx)
    try:
        results = pipeline.run(only=args.stage, force=args.force, from_stage=args.from_stage)
    except ValueError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(pipeline.summary(results))

    failed = [name for name, result in results.items() if not result.ok]
    if failed:
        print(
            f"\nFehlgeschlagen: {', '.join(failed)}\n"
            f"Erneut starten mit: python -m media_pipeline run --video-id {video_id} "
            f"--stage {failed[0]} --force",
            file=sys.stderr,
        )
        return EXIT_GATE_FAILED

    return EXIT_OK


def _run_channel_mode(
    args: argparse.Namespace,
    config: Config,
    root: Path,
    channel_url: str,
    source_type: str,
) -> int:
    """Verarbeitet alle Videos des konfigurierten Kanals nacheinander."""
    from media_pipeline.lib import youtube

    print(f"Kanal-Modus: {youtube.channel_display_name(channel_url)}")

    # 1. Kanal auflisten.
    browser = str(config.get("ingest.cookies_from_browser", "") or "")

    # Bei mehreren Browser-Profilen das funktionierende waehlen — sonst
    # liefert der Download nur Storyboards.
    if browser:
        profile, hint = youtube.detect_working_profile(browser)
        if ":" in profile:
            print(f"Browser-Profil: {profile} ({hint})")
            browser = profile

    max_videos = config.get("ingest.max_videos", 5)
    max_age_days = config.get("ingest.max_age_days", "")
    dateafter = str(config.get("ingest.dateafter", "") or "")

    try:
        listing = youtube.list_channel_videos(
            channel_url,
            browser=browser or None,
            dateafter=dateafter or None,
            max_age_days=int(max_age_days) if max_age_days else None,
            match_filter=str(config.get("ingest.match_filter", "") or "") or None,
        )
    except youtube.YouTubeError as exc:
        print(f"\nFEHLER: {exc}", file=sys.stderr)
        return EXIT_GATE_FAILED

    archive_path = root / str(
        config.get("ingest.download_archive", "data/state/downloaded.txt")
    )
    archive = youtube.read_archive(archive_path)

    queue = [
        v
        for v in listing.videos
        if v.video_id not in archive
    ][: int(max_videos or 0)]

    if not queue:
        print("\nKeine neuen Videos im Kanal — nichts zu tun.")
        return EXIT_OK

    print(f"{len(queue)} Video(s) in der Warteschlange:\n")
    for video in queue:
        print(f"  {video.video_id}  {video.title[:60]}")

    # 2. Jedes Video einzeln durch die Pipeline schicken.
    exit_code = EXIT_OK
    for index, video in enumerate(queue, start=1):
        print()
        print("=" * 60)
        print(f" Video {index}/{len(queue)}: {video.title[:60]}")
        print("=" * 60)

        ctx = _make_context(config, video.video_id, video.url, video.title)
        pipeline = Pipeline(ctx)
        try:
            results = pipeline.run(
                only=args.stage,
                force=args.force,
                from_stage=args.from_stage,
            )
        except ValueError as exc:
            print(f"Fehler: {exc}", file=sys.stderr)
            return EXIT_ERROR

        print(pipeline.summary(results))

        failed = [name for name, result in results.items() if not result.ok]
        if failed:
            exit_code = EXIT_GATE_FAILED
            print(
                f"\nVideo {video.video_id}: {', '.join(failed)} fehlgeschlagen.",
                file=sys.stderr,
            )
            # Das Video wird NICHT ins Archiv geschrieben — es kann neu
            # versucht werden.
            continue

        # Erfolgreich verarbeitet -> ins Archiv, damit es nicht noch
        # einmal geladen wird.
        youtube.append_archive(archive_path, video.video_id)
        print(f"Archiviert: {video.video_id}")

    return exit_code


def cmd_channels(args: argparse.Namespace) -> int:
    """Kanal-Kommando: Kanal setzen, Videos auflisten, Zugang pruefen."""
    from media_pipeline.lib import youtube

    root = _project_root()

    try:
        config = Config.load(root)
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    # ---------------------------------------------------------- Kanal setzen
    if args.set_channel:
        url = args.set_channel.strip()
        if not url:
            print("FEHLER: --set braucht eine URL", file=sys.stderr)
            return EXIT_CONFIG_ERROR

        if not url.startswith("http"):
            url = youtube.build_channel_url(url)

        path = root / "config" / "pipeline.yaml"
        text = path.read_text(encoding="utf-8")
        lines = text.split("\n")
        replaced = False
        for index, line in enumerate(lines):
            if line.strip().startswith("channel_url:"):
                indent = line[: len(line) - len(line.lstrip())]
                lines[index] = f'{indent}channel_url: "{url}"'
                replaced = True
                break
        if not replaced:
            print("FEHLER: channel_url nicht in der Config gefunden", file=sys.stderr)
            return EXIT_CONFIG_ERROR

        path.write_text("\n".join(lines), encoding="utf-8")
        name = youtube.channel_display_name(url)
        print(f"Kanal gesetzt: {name}")
        print(f"  {url}")
        print(f"  Gespeichert in: {path}")
        return EXIT_OK

    # ------------------------------------------------------ Zugang pruefen
    # Das kommt VOR der Quellen-Pruefung: Der Cookie-Test ist auch ohne
    # konfigurierten Kanal sinnvoll (er prueft den Login).
    if args.check_cookies:
        browser = str(config.get("ingest.cookies_from_browser", "") or "")
        print(f"Browser: {browser}")
        print("Pruefe Profile …")
        profile, hint = youtube.detect_working_profile(browser)
        print(f"  {hint}")
        print(f"  Verwende: {profile}")
        ok, hint2 = youtube.check_browser_cookies(profile)
        print(("  OK      " if ok else "  FEHLER  ") + hint2)
        return EXIT_OK if ok else EXIT_GATE_FAILED

    # ------------------------------------------------------ Quelle bestimmen
    url, source_type = youtube.detect_source(config)
    if args.channel:
        url = youtube.build_channel_url(args.channel)
        source_type = "channel"

    if not url:
        print(
            "Kein Kanal konfiguriert.\n"
            "\n"
            "Optionen:\n"
            "  1. Kanal speichern (dauerhaft):\n"
            "     python -m media_pipeline channels --set @deinKanal\n"
            "  2. Nur fuer dieses Mal:\n"
            "     python -m media_pipeline channels --channel @deinKanal\n",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR

    browser = str(config.get("ingest.cookies_from_browser", "") or "")

    # Das funktionierende Browser-Profil ermitteln. Ein Profil mit
    # ungueltigen Cookies liefert nur Storyboards — dann liefert auch
    # der Metadaten-Abruf kein Hochladedatum.
    if browser:
        profile, hint = youtube.detect_working_profile(browser)
        if ":" in profile:
            print(f"Browser-Profil: {profile}")
            browser = profile

    # ------------------------------------------------------ Videos auflisten
    max_videos = args.max_videos or config.get("ingest.max_videos", 5)
    max_age_days = config.get("ingest.max_age_days", "")
    dateafter = str(config.get("ingest.dateafter", "") or "")

    print(f"Kanal: {youtube.channel_display_name(url)}")
    print(f"Typ:   {source_type}")
    print(f"Browser: {browser or 'keiner (oeffentliche Videos)'}")
    print("Liste Videos … (kein Download)")

    try:
        listing = youtube.list_channel_videos(
            url,
            browser=browser or None,
            dateafter=dateafter or None,
            max_age_days=int(max_age_days) if max_age_days else None,
            match_filter=str(config.get("ingest.match_filter", "") or "") or None,
        )
    except youtube.YouTubeError as exc:
        print(f"\nFEHLER: {exc}", file=sys.stderr)
        return EXIT_GATE_FAILED

    archive_path = root / str(
        config.get("ingest.download_archive", "data/state/downloaded.txt")
    )
    archive = youtube.read_archive(archive_path)

    print()
    print(f"{len(listing.videos)} Videos gefunden")
    if listing.skipped_private:
        print(f"{listing.skipped_private} private Videos uebersprungen")
    print()

    # Metadaten der Kandidaten nachladen (Datum, Laenge) — parallel.
    # Nur die ersten Eintraege: ein Kanal kann hunderte Videos haben.
    print("Lade Metadaten (Hochladedatum, Laenge) …")
    youtube.fetch_details_parallel(
        listing.videos[: (int(max_videos or 5) + 2) * 2], browser=browser or None
    )

    chosen: list = []
    for video in listing.videos:
        already = " (bereits geladen)" if video.video_id in archive else ""
        selected = len(chosen) < int(max_videos or 0) and not already
        if selected:
            chosen.append(video)
        mark = ">" if selected else " "
        duration = f"{video.duration:>6}s" if video.duration else "     ?"
        views = f"{video.view_count:>10}" if video.view_count else "         ?"
        print(
            f" {mark} {video.upload_date or '????-??-??'}  {duration}  "
            f"{views} Aufrufe  {video.video_id}  {video.title[:44]}{already}"
        )

    print()
    print(f"{len(chosen)} Video(s) zum Verarbeiten ausgewaehlt (Limit {max_videos})")
    print()
    print("Verarbeiten mit:")
    print(f"  PYTHONPATH=src ./venv/bin/python -m media_pipeline run --channel {url}")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    """Zeigt den Zustand aller (oder eines) Videos."""
    root = _project_root()
    try:
        config = Config.load(root)
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return EXIT_ERROR

    state_dir = config.path("state_dir")
    if not state_dir.is_dir():
        print("Noch keine Videos verarbeitet.")
        return EXIT_OK

    files = sorted(state_dir.glob("*.json"))
    if args.video_id:
        files = [f for f in files if f.stem == args.video_id]
    if not files:
        print("Keine Videos gefunden.")
        return EXIT_OK

    for path in files:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        print(f"\n=== {data.get('video_id')} ===")
        print(f"  Quelle: {data.get('source_url') or '(lokale Datei)'}")
        stages = data.get("stages") or {}
        for name in STAGE_ORDER:
            entry = stages.get(name)
            if not entry:
                print(f"  {name:<16} -")
                continue
            mark = {
                "done": "OK  ",
                "failed": "FAIL",
                "skipped": "SKIP",
                "running": "RUN ",
                "pending": "    ",
            }.get(entry.get("status", "pending"), "    ")
            detail = entry.get("gate") or entry.get("error") or ""
            print(f"  {mark} {name:<16} {detail}")

    return EXIT_OK


def cmd_reset(args: argparse.Namespace) -> int:
    """Setzt den Zustand eines Videos zurueck."""
    root = _project_root()
    try:
        config = Config.load(root)
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if not args.video_id:
        print("Fehler: --video-id noetig", file=sys.stderr)
        return EXIT_ERROR

    state_dir = config.path("state_dir")
    state = PipelineState.load(state_dir, args.video_id)
    state.reset([args.stage] if args.stage else None)
    print(f"Zustand fuer '{args.video_id}' zurueckgesetzt.")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Baut den Argument-Parser."""
    parser = argparse.ArgumentParser(
        prog="media_pipeline", description="Automated Media Pipeline — Gameplay zu vertikalen Clips"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Pipeline ausfuehren")
    run_parser.add_argument("--url", help="YouTube-URL des privaten Videos")
    run_parser.add_argument("--video-id", help="Eigene Video-ID (z. B. mission_01)")
    run_parser.add_argument("--title", default="", help="Titel des Videos")
    run_parser.add_argument(
        "--stage", choices=STAGE_ORDER, help="Nur diese Stage ausfuehren"
    )
    run_parser.add_argument(
        "--from-stage", choices=STAGE_ORDER, help="Ab dieser Stage starten"
    )
    run_parser.add_argument(
        "--force", action="store_true", help="Fertige Stages erneut ausfuehren"
    )
    run_parser.add_argument(
        "--channel", help="Kanal-Profil anwenden (shorts, longform)"
    )
    run_parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warn", "error"],
        help="Ausgabedetailgrad",
    )
    run_parser.set_defaults(func=cmd_run)

    # ------------------------------------------------------------- channels
    channel_parser = sub.add_parser(
        "channels", help="YouTube-Kanaele auflisten und verwalten"
    )
    channel_parser.add_argument(
        "--channel",
        help=(
            "Kanal-Handle (@name, name oder UC...) — ueberschreibt "
            "ingest.channel_url aus der Config"
        ),
    )
    channel_parser.add_argument(
        "--set",
        dest="set_channel",
        metavar="URL",
        help="Kanal-URL in config/pipeline.yaml speichern",
    )
    channel_parser.add_argument(
        "--list-only",
        action="store_true",
        help="Nur auflisten, nichts herunterladen",
    )
    channel_parser.add_argument(
        "--max-videos",
        type=int,
        help="Hoechstes Videoanzahl, das ausgewaehlt wird",
    )
    channel_parser.add_argument(
        "--check-cookies",
        action="store_true",
        help="Nur pruefen, ob der Browser-Login funktioniert",
    )
    channel_parser.set_defaults(func=cmd_channels)

    status_parser = sub.add_parser("status", help="Zustand anzeigen")
    status_parser.add_argument("--video-id", help="Nur dieses Video zeigen")
    status_parser.set_defaults(func=cmd_status)

    reset_parser = sub.add_parser("reset", help="Zustand zuruecksetzen")
    reset_parser.add_argument("--video-id", required=True, help="Video-ID")
    reset_parser.add_argument("--stage", choices=STAGE_ORDER, help="Nur diese Stage")
    reset_parser.set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI-Einstieg."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
