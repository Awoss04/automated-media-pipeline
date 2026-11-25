"""Tests der Konfiguration."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from media_pipeline.config import Config, ConfigError, load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestConfig(unittest.TestCase):
    def test_laden_der_projektconfig(self):
        config = Config.load(PROJECT_ROOT)
        self.assertEqual(config.get("vertical.width"), 1080)
        self.assertEqual(config.get("vertical.height"), 1920)
        self.assertGreater(float(config.get("rank.min_score")), 0)

    def test_punktpfad(self):
        config = Config.load(PROJECT_ROOT)
        self.assertIsNotNone(config.get("transcribe.model"))
        self.assertIsNone(config.get("gibt.es.nicht"))

    def test_require_wirft_bei_fehlendem_wert(self):
        config = Config.load(PROJECT_ROOT)
        with self.assertRaises(ConfigError):
            config.require("gibt.es.nicht")

    def test_kanalprofil(self):
        config = Config.load(PROJECT_ROOT, channel="shorts")
        self.assertEqual(config.get("vertical.width"), 1080)

    def test_unbekannter_kanal(self):
        with self.assertRaises(ConfigError):
            Config.load(PROJECT_ROOT, channel="gibtsnicht")

    def test_fehlende_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError):
                Config.load(Path(tmp))

    def test_pfade_sind_absolut(self):
        config = Config.load(PROJECT_ROOT)
        self.assertTrue(config.path("data_dir").is_absolute())
        self.assertTrue(str(config.path("data_dir")).startswith(str(PROJECT_ROOT)))

    def test_validierung_meldung(self):
        data = {
            "paths": {"data_dir": "data"},
            "transcribe": {"model": "small", "min_coverage": 0.95},
            "rank": {"model": "m", "min_score": 6.0},
            "vertical": {"width": 1080, "height": 1920, "min_clip_sec": 30, "max_clip_sec": 90},
        }
        config = Config(data, PROJECT_ROOT)
        config.validate()  # darf nicht werfen

    def test_validierung_erkennt_ungueltigen_score(self):
        data = {
            "paths": {"data_dir": "data"},
            "transcribe": {"model": "small", "min_coverage": 0.95},
            "rank": {"model": "m", "min_score": 99.0, "max_score": 10},
            "vertical": {"width": 1080, "height": 1920, "min_clip_sec": 30, "max_clip_sec": 90},
        }
        with self.assertRaises(ConfigError):
            Config(data, PROJECT_ROOT).validate()

    def test_validierung_erkennt_ungueltige_clipdauer(self):
        data = {
            "paths": {"data_dir": "data"},
            "transcribe": {"model": "small", "min_coverage": 0.95},
            "rank": {"model": "m", "min_score": 6.0},
            "vertical": {"width": 1080, "height": 1920, "min_clip_sec": 90, "max_clip_sec": 30},
        }
        with self.assertRaises(ConfigError):
            Config(data, PROJECT_ROOT).validate()


class TestDotenv(unittest.TestCase):
    def test_lesen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# Kommentar\n"
                "AGY_BIN=/usr/local/bin/agy\n"
                "export OTHER='wert mit leerzeichen'\n"
                "\n"
                "ohne_gleichheitszeichen\n",
                encoding="utf-8",
            )
            values = load_dotenv(path)
            self.assertEqual(values["AGY_BIN"], "/usr/local/bin/agy")
            self.assertEqual(values["OTHER"], "wert mit leerzeichen")
            self.assertNotIn("ohne_gleichheitszeichen", values)

    def test_fehlende_datei(self):
        self.assertEqual(load_dotenv(Path("/gibt/es/nicht/.env")), {})


if __name__ == "__main__":
    unittest.main()
