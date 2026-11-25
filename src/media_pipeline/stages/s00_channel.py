"""Stage s00_channel — den YouTube-Kanal auswerten.

Diese Stage laeuft **vor** s01_ingest. Sie beantwortet die Frage:
"Welche Videos gibt es im Kanal, und welche davon will ich verarbeiten?"

Sie laedt **nichts** herunter. ``yt-dlp --flat-playlist`` liest nur die
Videoliste. Das spart Bandbreite und Speicher — und macht den Zustand
sichtbar, bevor die grosse Arbeit beginnt.

Ergebnis: ``data/work/<video_id>/channel.json`` mit der vollstaendigen
Videoliste. s01_ingest arbeitet danach nur noch die in der Liste
ausgewaehlten Videos ab.

Gate: Es muss mindestens ein Video gefunden werden, das den Filtern
entspricht.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from media_pipeline.lib import log, youtube
from media_pipeline.models import StageResult


def _select_videos(
    listing: youtube.ChannelListing,
    archive: set[str],
    max_videos: int | None,
    only: str | None = None,
) -> tuple[list[youtube.ChannelVideo], list[str]]:
    """Waehlt die zu verarbeitenden Videos aus.

    Rueckgabe: ``(gewaehlt, uebersprungen)``.
    """
    chosen: list[youtube.ChannelVideo] = []
    skipped: list[str] = []

    for video in listing.videos:
        if only and video.video_id != only and video.title != only:
            skipped.append(f"{video.video_id} (nicht angefordert)")
            continue
        if video.video_id in archive:
            skipped.append(f"{video.video_id} (bereits geladen)")
            continue
        if max_videos is not None and len(chosen) >= max_videos:
            skipped.append(f"{video.video_id} (Limit {max_videos} erreicht)")
            continue
        chosen.append(video)

    return chosen, skipped


def run(ctx) -> StageResult:
    """Fuehrt s00_channel aus."""
    config = ctx.config

    # 1. Quelle bestimmen: explizite URL > Kanal aus der Config.
    url, source_type = youtube.detect_source(config)
    if not url and ctx.source_url:
        url, source_type = ctx.source_url, "video"

    if not url:
        return StageResult(
            ok=False,
            error=(
                "Keine YouTube-Quelle konfiguriert. Setze in "
                "config/pipeline.yaml eine der Zeilen:\n"
                "  ingest.channel_url:  https://www.youtube.com/@DEIN_KANAL/videos\n"
                "  ingest.playlist_url: https://www.youtube.com/playlist?list=...\n"
                "  ingest.video_url:    https://www.youtube.com/watch?v=..."
            ),
        )

    browser = str(config.get("ingest.cookies_from_browser", "") or "")
    require_cookies = bool(config.get("ingest.require_cookies", True))

    # 2. Cookie-Zugang pruefen — bevor Zeit vergeudet wird.
    if require_cookies and browser:
        # Bei mehreren Browser-Profilen muss das richtige gefunden werden:
        # ein Profil mit ungueltigen Cookies liefert nur Storyboards und
        # blockiert jeden Download.
        profile, hint = youtube.detect_working_profile(browser)
        if ":" in profile:
            log.info(f"Browser-Profil: {profile}")
            browser = profile
        ok, hint = youtube.check_browser_cookies(browser)
        if not ok:
            return StageResult(
                ok=False,
                error=f"Kein YouTube-Zugang: {hint}",
            )
        log.info(f"YouTube-Zugang: {hint}")

    # 3. Videos auflisten (ohne Download).
    max_videos = config.get("ingest.max_videos", 5)
    max_age_days = config.get("ingest.max_age_days", "")
    dateafter = str(config.get("ingest.dateafter", "") or "")

    try:
        listing = youtube.list_channel_videos(
            url,
            browser=browser or None,
            max_videos=None,
            dateafter=dateafter or None,
            max_age_days=int(max_age_days) if max_age_days else None,
            match_filter=str(config.get("ingest.match_filter", "") or "") or None,
        )
    except youtube.YouTubeError as exc:
        return StageResult(ok=False, error=str(exc))

    channel_name = youtube.channel_display_name(url)
    log.info(
        f"Kanal '{channel_name}' ({source_type}): "
        f"{len(listing.videos)} Videos gelistet"
        + (
            f", {listing.skipped_private} private uebersprungen"
            if listing.skipped_private
            else ""
        )
    )

    # 4. Auswahl treffen.
    archive_path = ctx.project_root / str(
        config.get("ingest.download_archive", "data/state/downloaded.txt")
    )
    archive = youtube.read_archive(archive_path)

    # 4a. Exakte Metadaten der ausgewahlten Videos holen.
    # --flat-playlist liefert kein Hochladedatum — erst der Einzelabruf
    # pro Video bringt Datum, Aufloesung und echte Laenge.
    # Der Abruf laeuft parallel, sonst dauert er bei vielen Videos
    # mehrere Minuten.
    #
    # Wichtig: Nur die **ersten** Videos werden nachgeladen. Ein Kanal
    # kann mehrere hundert Eintraege haben — deren Metadaten zu holen
    # waere minuteslang und verschwendet Bandbreite.
    max_age_days = config.get("ingest.max_age_days", "")
    cutoff = None
    if max_age_days:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=int(max_age_days))

    limit = int(max_videos) if max_videos else 5
    youtube.fetch_details_parallel(
        listing.videos[: limit * 3], browser=browser or None
    )

    enriched: list[youtube.ChannelVideo] = []
    for video in listing.videos:
        # Altersfilter erst jetzt anwenden — vorher fehlt das Datum.
        if cutoff and video.timestamp:
            uploaded = datetime.fromtimestamp(video.timestamp, tz=timezone.utc)
            if uploaded < cutoff:
                log.info(
                    f"  uebersprungen: {video.video_id} "
                    f"({video.upload_date}, aelter als {max_age_days} Tage)"
                )
                continue
        enriched.append(video)

    listing.videos = enriched

    chosen, skipped = _select_videos(
        listing,
        archive,
        int(max_videos) if max_videos else None,
        only=ctx.source_video_id if ctx.source_video_id and source_type == "video" else None,
    )

    for entry in skipped[:10]:
        log.info(f"  uebersprungen: {entry}")

    # 5. Ergebnis ablegen.
    payload = {
        "channel": channel_name,
        "source": url,
        "source_type": source_type,
        "browser": browser,
        "found": len(listing.videos),
        "selected": len(chosen),
        "skipped_private": listing.skipped_private,
        "videos": [v.to_dict() for v in chosen],
        "all_videos": [v.to_dict() for v in listing.videos],
    }

    target = ctx.work_dir / "channel.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    if not chosen:
        return StageResult(
            ok=False,
            error=(
                f"Kein passendes Video im Kanal '{channel_name}' gefunden "
                f"({len(listing.videos)} Videos gesehen, "
                f"{listing.skipped_private} privat). "
                "Pruefe die Filter in config/pipeline.yaml "
                "(max_age_days, dateafter, max_videos)."
            ),
            data=payload,
        )

    gate = (
        f"Kanal '{channel_name}': {len(chosen)} von {len(listing.videos)} "
        f"Videos ausgewaehlt"
    )
    return StageResult(ok=True, data=payload, gate=gate)
