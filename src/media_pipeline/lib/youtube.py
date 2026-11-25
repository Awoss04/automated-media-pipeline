"""YouTube-Zugriff: Kanal-Erkennung und Video-Auflistung.

Dieses Modul kapselt alles, was mit dem YouTube-Kanal zu tun hat:

- **Kanal erkennen:** Welche Quelle ist gesetzt (Kanal, Playlist, Video)?
- **Videos auflisten:** ``yt-dlp --flat-playlist`` liest nur die Metadaten
  eines Kanals — **ohne** etwas herunterzuladen. So sieht man sofort,
  welche Videos es gibt, bevor Bandbreite und Speicher belastet werden.
- **Cookies:** Private Videos brauchen die Cookies des eingeloggten
  Browsers. Sie werden **gelesen**, nie exportiert, nie gespeichert.

Sicherheitsregel: Cookies sind ein Geheimnis. Sie verlassen den Browser
nie — ``yt-dlp`` liest sie direkt aus dem Cookie-Jar.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path


class YouTubeError(Exception):
    """Fehler beim Zugriff auf YouTube."""


@dataclass
class ChannelVideo:
    """Ein Video aus dem Kanal (Metadaten, noch nicht heruntergeladen)."""

    video_id: str
    title: str = ""
    url: str = ""
    duration: int | None = None
    timestamp: int | None = None
    view_count: int | None = None
    is_private: bool = False

    @property
    def upload_date(self) -> str:
        """Hochladedatum als YYYY-MM-DD (leer, wenn unbekannt)."""
        if not self.timestamp:
            return ""
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).strftime("%Y-%m-%d")

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "url": self.url,
            "duration": self.duration,
            "timestamp": self.timestamp,
            "view_count": self.view_count,
            "is_private": self.is_private,
            "upload_date": self.upload_date,
        }


@dataclass
class ChannelListing:
    """Ergebnis der Kanal-Sichtung."""

    source: str = ""
    source_type: str = ""
    videos: list[ChannelVideo] = field(default_factory=list)
    skipped_private: int = 0

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "source_type": self.source_type,
            "video_count": len(self.videos),
            "skipped_private": self.skipped_private,
            "videos": [v.to_dict() for v in self.videos],
        }



def detect_source(config) -> tuple[str, str]:
    """Ermittelt, welche Quelle konfiguriert ist.

    Rueckgabe: ``(url, typ)`` mit typ in
    ``channel`` / ``playlist`` / ``video`` / ``none``.
    """
    channel_url = str(config.get("ingest.channel_url", "") or "").strip()
    playlist_url = str(config.get("ingest.playlist_url", "") or "").strip()
    video_url = str(config.get("ingest.video_url", "") or "").strip()

    if channel_url:
        return channel_url, "channel"
    if playlist_url:
        return playlist_url, "playlist"
    if video_url:
        return video_url, "video"
    return "", "none"


def _ytdlp_base(browser: str | None) -> list[str]:
    """Gemeinsame yt-dlp-Argumente (ohne URL)."""
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-progress",
        "--no-warnings",
        "--ignore-errors",
        "--ignore-config",
    ]
    if browser:
        cmd += ["--cookies-from-browser", browser]
    return cmd


def build_channel_url(handle: str) -> str:
    """Baut eine Kanal-URL aus einem Handle oder einer URL.

    Akzeptiert ``@handle``, ``handle``, ``UC...`` (Kanal-ID) oder eine
    vollstaendige URL.
    """
    handle = (handle or "").strip()
    if not handle:
        raise YouTubeError("Kanal-Handle leer")

    if handle.startswith("http://") or handle.startswith("https://"):
        return handle
    if handle.startswith("@"):
        return f"https://www.youtube.com/{handle}/videos"
    if handle.startswith("UC"):
        return f"https://www.youtube.com/channel/{handle}/videos"
    return f"https://www.youtube.com/@{handle}/videos"


def channel_display_name(url: str) -> str:
    """Lesbarer Kanalname aus einer URL (fuer Logs und Status)."""
    trimmed = url.rstrip("/").removesuffix("/videos")
    tail = trimmed.rsplit("/", 1)[-1]
    return tail or trimmed



def list_channel_videos(
    url: str,
    browser: str | None = None,
    max_videos: int | None = None,
    dateafter: str | None = None,
    max_age_days: int | None = None,
    match_filter: str | None = None,
    timeout: int = 300,
) -> ChannelListing:
    """Listet die Videos eines Kanals — **ohne** Download.

    Nutzt ``--flat-playlist``: yt-dlp liest nur die Videoliste. Das ist
    schnell und kostet keinen Speicher.
    """
    if not url:
        raise YouTubeError("Keine Quelle angegeben")

    cmd = _ytdlp_base(browser)
    cmd += ["--flat-playlist", "--dump-single-json"]
    # KEIN --playlist-reverse: YouTube liefert die Videos des Kanals
    # chronologisch aufsteigend (aelteste zuerst). Ohne Reverse stehen
    # die NEUSTEN zuerst — das ist, was wir wollen.

    if dateafter:
        cmd += ["--dateafter", dateafter]
    if match_filter:
        cmd += ["--match-filter", match_filter]
    if max_videos:
        cmd += ["--playlist-end", str(int(max_videos))]

    cmd.append(url)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise YouTubeError(
            f"yt-dlp Timeout nach {timeout}s beim Auflisten von {url}"
        ) from exc

    stdout = (result.stdout or "").strip()
    if not stdout:
        tail = (result.stderr or "").strip().splitlines()[-6:]
        raise YouTubeError(
            "yt-dlp lieferte keine Kanaldaten.\n"
            + "\n".join(tail)
            + "\n\nHinweis: Private Videos brauchen die Cookies des "
            "eingeloggten Browsers (ingest.cookies_from_browser)."
        )

    payload = _parse_listing(stdout)
    if payload is None:
        raise YouTubeError("yt-dlp-Ausgabe konnte nicht als JSON gelesen werden")

    entries = payload.get("entries") or []
    if isinstance(entries, dict):
        entries = [entries]

    listing = ChannelListing(
        source=url,
        source_type=str(payload.get("_type") or "channel"),
    )

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        # Private Videos erscheinen mit is_private=True und ohne Metadaten.
        if entry.get("is_private") or entry.get("availability") == "private":
            listing.skipped_private += 1
            continue

        video_id = str(entry.get("id") or "")
        if not video_id:
            continue

        timestamp = entry.get("timestamp")
        listing.videos.append(
            ChannelVideo(
                video_id=video_id,
                title=str(entry.get("title") or ""),
                url=str(
                    entry.get("url")
                    or f"https://www.youtube.com/watch?v={video_id}"
                ),
                duration=entry.get("duration"),
                timestamp=int(timestamp) if timestamp else None,
                view_count=entry.get("view_count"),
                is_private=bool(entry.get("is_private")),
            )
        )

    return listing


def fetch_video_details(
    video_url: str,
    browser: str | None = None,
    timeout: int = 180,
) -> dict:
    """Holt die genauen Metadaten **eines** Videos (inkl. Hochladedatum).

    Benoetigt, weil ``--flat-playlist`` kein Hochladedatum liefert
    (yt-dlp gibt da ``NA`` zurueck).

    Wichtig: ``--skip-download`` allein reicht nicht — yt-dlp wertet dann
    die Format-Auswahl aus und meldet "Requested format is not
    available". Deshalb wird eine explizite, weite Format-Angabe
    mitgegeben.
    """
    cmd = _ytdlp_base(browser)
    cmd += [
        "--skip-download",
        "--dump-json",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
    ]
    cmd.append(video_url)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise YouTubeError("yt-dlp Timeout bei den Metadaten") from exc

    stdout = (result.stdout or "").strip()
    if not stdout:
        return {}

    for line in stdout.split("\n"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {}


def _parse_listing(stdout: str) -> dict | None:
    """Liest die JSON-Ausgabe von yt-dlp.

    ``--dump-single-json`` liefert ein Objekt, aber defensiv werden auch
    mehrere Zeilen mit je einem Objekt akzeptiert.
    """
    payload: dict | None = None

    try:
        candidate = json.loads(stdout)
        if isinstance(candidate, dict):
            if "entries" in candidate:
                return candidate
            payload = candidate
    except json.JSONDecodeError:
        pass

    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            if "entries" in candidate:
                return candidate
            if payload is None:
                payload = candidate

    return payload



def browser_profiles(browser: str) -> list[str]:
    """Findet die Browser-Profile, die eine Cookie-Datenbank besitzen.

    Auf macOS kann ein Browser mehrere Profile haben. Nur eines davon
    enthaelt meist den eingeloggten YouTube-Account — und ein Profil
    mit kaputten Cookies blockiert den Zugriff.

    Rueckgabe: Liste wie ``["brave:Profile 1", "brave:Default"]``.
    """
    base = Path.home() / "Library" / "Application Support"
    folders = {
        "brave": base / "BraveSoftware" / "Brave-Browser",
        "chrome": base / "Google" / "Chrome",
        "chromium": base / "Chromium",
        "edge": base / "Microsoft Edge",
        "brave-beta": base / "BraveSoftware" / "Brave-Browser-Beta",
    }
    root = folders.get(browser.lower())
    if root is None or not root.is_dir():
        return [browser]

    found: list[str] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / "Cookies").is_file() and not (entry / "Network" / "Cookies").is_file():
            continue
        found.append(f"{browser}:{entry.name}")

    return found or [browser]


def fetch_details_parallel(
    videos: list[ChannelVideo],
    browser: str | None = None,
    workers: int = 4,
    timeout: int = 180,
) -> None:
    """Holt die Metadaten mehrerer Videos **parallel** und ergaenzt sie in-place.

    Ohne Parallelisierung dauert der Abruf bei 10 Videos rund 200 Sekunden
    (YouTube antwortet auf Einzelabfragen nur mit Verzoegerung). Mit
    Threads sinkt das auf einen Bruchteil.

    Fehler werden ignoriert — ein Video ohne Metadaten darf die
    gesamte Liste nicht verhindern.
    """
    if not videos:
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _one(video: ChannelVideo) -> dict:
        try:
            return fetch_video_details(video.url, browser=browser, timeout=timeout)
        except YouTubeError:
            return {}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(_one, video): video for video in videos}
        for future in as_completed(futures):
            video = futures[future]
            try:
                details = future.result()
            except Exception:  # noqa: BLE001 — ein Fehler darf nicht stoppen
                continue
            if not details:
                continue

            if details.get("timestamp"):
                video.timestamp = int(details["timestamp"])
            if details.get("upload_date") and not video.timestamp:
                try:
                    video.timestamp = int(
                        datetime.strptime(
                            str(details["upload_date"]), "%Y%m%d"
                        )
                        .replace(tzinfo=timezone.utc)
                        .timestamp()
                    )
                except ValueError:
                    pass
            if details.get("duration"):
                video.duration = details["duration"]
            if details.get("view_count"):
                video.view_count = details["view_count"]
            if details.get("title"):
                video.title = str(details["title"])


def detect_working_profile(
    browser: str,
    probe_url: str = "https://www.youtube.com/watch?v=jNQXAC9IVRw",
) -> tuple[str, str]:
    """Findet das Browser-Profil, mit dem YouTube wirklich funktioniert.

    Getestet wird, ob yt-dlp ueber dieses Profil **Video-Formate** sieht.
    Ein Profil mit ungueltigen Cookies liefert nur Storyboards — das
    ist das Erkennungsmerkmal.

    Wichtig: Als Probe dient ein **bekanntes, dauerhaft verfuegbares
    Video**. Die YouTube-Startseite bzw. die recommended-Feed-Seite
    liefert auch mit kaputten Cookies ein Ergebnis — nur keine
    Video-Formate. Damit waere der Test aussagelos.

    Rueckgabe: ``(profil, hinweis)``. profil ist der beste gefundene
    Wert (oder der Browser-Name, wenn nichts funktioniert).
    """
    candidates = browser_profiles(browser)
    if len(candidates) == 1:
        return candidates[0], f"Profil '{candidates[0]}' (einziges vorhandenes)"

    best = candidates[0]
    best_count = -1

    for candidate in candidates:
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--cookies-from-browser", candidate,
            "--list-formats",
            "--no-warnings",
            probe_url,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )
        except subprocess.TimeoutExpired:
            continue

        lines = [l for l in (result.stdout or "").splitlines() if l.strip()]
        videos = [
            l
            for l in lines
            if "video" in l and "storyboard" not in l and "mhtml" not in l
        ]
        count = len(videos)
        if count > best_count:
            best_count = count
            best = candidate

        if count > 0:
            return candidate, (
                f"Profil '{candidate}' funktioniert ({count} Video-Formate)."
            )

    if best_count <= 0:
        return best, (
            f"Kein Profil von '{browser}' liefert Video-Formate. "
            "Haeufigste Ursache: der Browser ist nicht auf YouTube "
            "eingeloggt, oder macOS verweigert den Keychain-Zugriff."
        )

    return best, f"Profil '{best}' (beste Teilauswahl: {best_count} Formate)"


def check_browser_cookies(browser: str, timeout: int = 180) -> tuple[bool, str]:
    """Prueft, ob der Browser ueberhaupt Cookies liefern kann.

    Rueckgabe: ``(ok, hinweis)``. Der Hinweis sagt, was zu tun ist,
    wenn es nicht klappt.

    Wichtig: Es wird **nur** die Cookie-Extraktion geprueft, nicht die
    Startseite geladen. Sonst zieht yt-dlp die komplette
    "recommended"-Playlist — das dauert 20 Sekunden und testet nichts.
    """
    if not browser:
        return False, (
            "Kein Browser konfiguriert. Setze ingest.cookies_from_browser "
            "in config/pipeline.yaml."
        )

    # "Extracting cookies from <browser>" ist die relevante Zeile.
    # Mit --simulate und einer existierenden Video-URL passiert nichts
    # weiter als die Cookie-Extraktion.
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--cookies-from-browser", browser,
        "--simulate",
        "--no-playlist",
        "--playlist-items", "0",
        "--no-progress",
        "--no-warnings",
        "https://www.youtube.com/feed/recommended",
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return False, (
            f"Timeout ({timeout}s) beim Zugriff auf die Cookies von "
            f"'{browser}'. Browser einmal schliessen und erneut versuchen."
        )

    combined = ((result.stdout or "") + (result.stderr or "")).lower()

    if "extracted" in combined and "cookies" in combined:
        # Zahl der Cookies herausfiltern, falls vorhanden.
        import re

        match = re.search(r"extracted (\d+) cookies", combined)
        count = match.group(1) if match else "?"
        return True, f"Cookies aus '{browser}' lesbar ({count} Cookies)."

    if "could not find" in combined or "unable to extract" in combined:
        return False, (
            f"Cookies aus '{browser}' nicht lesbar. Bitte den Browser "
            "einmal schliessen und neu starten, dann den Keychain-Zugriff "
            "erlauben."
        )
    if "private" in combined and "sign in" in combined:
        return False, (
            "YouTube verweigert den Zugriff. Browser einloggen und "
            "erneut versuchen."
        )

    return False, (
        f"Cookies aus '{browser}' nicht bestaetigt. "
        + "\n".join((result.stderr or "").strip().splitlines()[-4:])
    )


def read_archive(path: Path) -> set[str]:
    """Liest die Download-Archivdatei (bereits verarbeitete Video-IDs)."""
    if not path.is_file():
        return set()
    done: set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Format: "youtube VIDEOID" — die ID ist das letzte Feld.
            done.add(line.split()[-1])
    except OSError:
        return set()
    return done


def append_archive(path: Path, video_id: str) -> None:
    """Traegt eine Video-ID in die Archivdatei ein (verhindert Re-Download)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_archive(path)
    if video_id in existing:
        return
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"youtube {video_id}\n")
