"""Tests der YouTube-Anbindung (Kanal, Cookies, Archiv)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from media_pipeline.lib import youtube


class TestBuildChannelUrl(unittest.TestCase):
    def test_handle_mit_at(self):
        self.assertEqual(
            youtube.build_channel_url("@meinKanal"),
            "https://www.youtube.com/@meinKanal/videos",
        )

    def test_handle_ohne_at(self):
        self.assertEqual(
            youtube.build_channel_url("meinKanal"),
            "https://www.youtube.com/@meinKanal/videos",
        )

    def test_kanal_id(self):
        self.assertEqual(
            youtube.build_channel_url("UCabc123"),
            "https://www.youtube.com/channel/UCabc123/videos",
        )

    def test_vollstaendige_url(self):
        url = "https://www.youtube.com/@x/videos"
        self.assertEqual(youtube.build_channel_url(url), url)

    def test_leer_wirft(self):
        with self.assertRaises(youtube.YouTubeError):
            youtube.build_channel_url("   ")


class TestDisplayName(unittest.TestCase):
    def test_name_aus_url(self):
        self.assertEqual(
            youtube.channel_display_name("https://www.youtube.com/@meinKanal/videos"),
            "@meinKanal",
        )

    def test_leere_url(self):
        self.assertEqual(youtube.channel_display_name(""), "")


class TestDetectSource(unittest.TestCase):
    class _Cfg:
        def __init__(self, **values):
            self._values = values

        def get(self, key, default=None):
            return self._values.get(key, default)

    def test_kanal_hat_vorrang(self):
        cfg = self._Cfg(
            **{
                "ingest.channel_url": "https://c/",
                "ingest.playlist_url": "https://p/",
                "ingest.video_url": "https://v/",
            }
        )
        url, kind = youtube.detect_source(cfg)
        self.assertEqual(url, "https://c/")
        self.assertEqual(kind, "channel")

    def test_playlist_wenn_kein_kanal(self):
        cfg = self._Cfg(**{"ingest.playlist_url": "https://p/"})
        self.assertEqual(youtube.detect_source(cfg)[1], "playlist")

    def test_video_wenn_alles_leer(self):
        cfg = self._Cfg(**{"ingest.video_url": "https://v/"})
        self.assertEqual(youtube.detect_source(cfg)[1], "video")

    def test_keine_quelle(self):
        self.assertEqual(youtube.detect_source(self._Cfg())[1], "none")


class TestParseListing(unittest.TestCase):
    def test_objekt_mit_entries(self):
        payload = youtube._parse_listing('{"entries": [{"id": "a"}]}')
        self.assertIsNotNone(payload)
        self.assertEqual(payload["entries"][0]["id"], "a")

    def test_kein_json(self):
        self.assertIsNone(youtube._parse_listing("kein json"))

    def test_leer(self):
        self.assertIsNone(youtube._parse_listing(""))


class TestChannelVideo(unittest.TestCase):
    def test_upload_date_leer_ohne_timestamp(self):
        self.assertEqual(youtube.ChannelVideo("abc").upload_date, "")

    def test_upload_date(self):
        video = youtube.ChannelVideo("abc", timestamp=1_700_000_000)
        self.assertRegex(video.upload_date, r"^\d{4}-\d{2}-\d{2}$")

    def test_to_dict(self):
        video = youtube.ChannelVideo("abc", title="Titel", duration=42)
        payload = video.to_dict()
        self.assertEqual(payload["video_id"], "abc")
        self.assertEqual(payload["duration"], 42)


class TestArchive(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "downloaded.txt"

    def tearDown(self):
        self._tmp.cleanup()

    def test_leere_datei(self):
        self.assertEqual(youtube.read_archive(self.path), set())

    def test_schreiben_und_lesen(self):
        youtube.append_archive(self.path, "vid_001")
        youtube.append_archive(self.path, "vid_002")
        self.assertEqual(youtube.read_archive(self.path), {"vid_001", "vid_002"})

    def test_kein_doppelte_eintraege(self):
        youtube.append_archive(self.path, "vid_001")
        youtube.append_archive(self.path, "vid_001")
        content = self.path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(content), 1)

    def test_kommentar_zeilen_werden_ignoriert(self):
        self.path.write_text("# Kommentar\nyoutube abc\n", encoding="utf-8")
        self.assertEqual(youtube.read_archive(self.path), {"abc"})


class TestBrowserProfiles(unittest.TestCase):
    def test_unbekannter_browser_liefert_namen(self):
        profiles = youtube.browser_profiles("unbekannt123")
        self.assertEqual(profiles, ["unbekannt123"])

    def test_kein_leerer_browser(self):
        self.assertTrue(youtube.browser_profiles("brave"))


class TestKeineCookiesImCode(unittest.TestCase):
    """Die Cookie-Logik darf nie Cookie-Werte im Code enthalten."""

    def test_kein_harter_cookie_pfad(self):
        source = Path(youtube.__file__).read_text(encoding="utf-8")
        self.assertNotIn("Netscape HTTP Cookie File", source)


class TestStageLoader(unittest.TestCase):
    """Jede Stage in STAGE_ORDER muss auch ladbar sein.

    Das faengt einen echten Bug: s00_channel war in STAGE_ORDER, aber
    nicht in der Modulliste des Loaders — der Lauf brach mit
    ``KeyError: 's00_channel'`` ab.
    """

    def test_alle_stages_ladbar(self):
        from media_pipeline.pipeline import STAGE_ORDER, Pipeline

        stages = Pipeline._load_stages()
        for name in STAGE_ORDER:
            self.assertIn(name, stages, f"{name} nicht ladbar")
            self.assertTrue(callable(stages[name]))

    def test_stage_module_hat_run(self):
        import importlib

        from media_pipeline.pipeline import STAGE_ORDER

        for name in STAGE_ORDER:
            module = importlib.import_module(f"media_pipeline.stages.{name}")
            self.assertTrue(
                hasattr(module, "run"), f"{name} hat keine run()-Funktion"
            )

    def test_stage_reihenfolge_beginnt_mit_channel(self):
        from media_pipeline.pipeline import STAGE_ORDER

        self.assertEqual(STAGE_ORDER[0], "s00_channel")
        self.assertEqual(len(STAGE_ORDER), 8)


class TestPipelineContext(unittest.TestCase):
    """Der Context braucht die Attribute, die die Stages benutzen."""

    def test_source_video_id_existiert(self):
        import inspect

        from media_pipeline.pipeline import PipelineContext

        params = inspect.signature(PipelineContext.__init__).parameters
        self.assertIn("source_video_id", params)

    def test_kontext_hat_alle_pfadattribute(self):
        import tempfile
        from pathlib import Path

        from media_pipeline.config import Config
        from media_pipeline.pipeline import PipelineContext
        from media_pipeline.state import PipelineState

        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            config = Config.load(project_root)
            state = PipelineState.load(Path(tmp), "x")
            ctx = PipelineContext(config, state, "x")
            for attr in (
                "work_dir",
                "inbox_path",
                "clips_dir",
                "thumbs_dir",
                "meta_path",
                "transcript_json",
                "transcript_srt",
                "candidates_path",
                "ranked_path",
                "prompts_dir",
                "schemas_dir",
            ):
                self.assertTrue(hasattr(ctx, attr), f"{attr} fehlt")


if __name__ == "__main__":
    unittest.main()
