"""Tests der Bibliotheks-Module: ffmpeg, Rendering, Antigravity-Wrapper."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from media_pipeline.lib import agy, ffmpeg, render


class TestFFmpegCrop(unittest.TestCase):
    def test_crop_filter_16_9_zu_9_16(self):
        # 1920x1080 auf 1080x1920 -> Breite = 1080*9/16 = 607
        result = ffmpeg.crop_filter(1920, 1080, 0.4)
        self.assertTrue(result.startswith("crop="))
        width = int(result.split(":")[0].split("=")[1])
        height = int(result.split(":")[1])
        self.assertEqual(width, 606)  # gerade Zahl
        self.assertEqual(height, 1080)

    def test_crop_filter_ungueltig(self):
        with self.assertRaises(ffmpeg.FFmpegError):
            ffmpeg.crop_filter(0, 0)

    def test_crop_filter_beruehrt_quelle_nicht(self):
        """Bei 4:3-Quelle darf die Breite nie groesser als die Quelle werden."""
        result = ffmpeg.crop_filter(640, 480, 0.5)
        width = int(result.split(":")[0].split("=")[1])
        self.assertLessEqual(width, 640)

    def test_encoder_args_videotoolbox(self):
        args = ffmpeg.encoder_args("h264_videotoolbox", "6M", "192k")
        self.assertIn("h264_videotoolbox", args)
        self.assertIn("yuv420p", args)

    def test_encoder_args_libx264(self):
        args = ffmpeg.encoder_args("libx264", "6M", "192k")
        self.assertIn("libx264", args)


class TestRender(unittest.TestCase):
    def test_hex_to_rgba(self):
        # 6-stellig: #RRGGBB
        self.assertEqual(render.hex_to_rgba("#FFFFFF"), (255, 255, 255, 255))
        self.assertEqual(render.hex_to_rgba("#000000"), (0, 0, 0, 255))
        # 8-stellig: #AARRGGBB -> Alpha zuerst
        rgba = render.hex_to_rgba("#B3000000")
        self.assertEqual(rgba[:3], (0, 0, 0))
        self.assertEqual(rgba[3], 179)

    def test_hex_to_rgba_ungueltig(self):
        with self.assertRaises(render.RenderError):
            render.hex_to_rgba("#GGGGGG")

    def test_kontrast_extreme(self):
        weiss = render.contrast_ratio((255, 255, 255), (255, 255, 255))
        schwarz = render.contrast_ratio((0, 0, 0), (0, 0, 0))
        maximum = render.contrast_ratio((255, 255, 255), (0, 0, 0))
        self.assertLess(weiss, 1.1)
        self.assertLess(schwarz, 1.1)
        self.assertGreater(maximum, 20.0)

    def test_text_png_wird_erzeugt(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "text.png"
            path, contrast = render.render_text_png(
                target, 1080, 1920, "TEST TITEL", "Unterzeile", "top"
            )
            self.assertTrue(path.is_file())
            with Image.open(path) as image:
                self.assertEqual(image.size, (1080, 1920))

    def test_leere_headline_wirft(self):
        from PIL import Image

        image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        with self.assertRaises(render.RenderError):
            render.draw_headline(image, "   ")


class TestAgy(unittest.TestCase):
    def test_json_aus_codeblock(self):
        """Antwort mit Markdown-Codeblock muss trotzdem parsebar sein."""
        text = 'Hier das Ergebnis:\n```json\n{"rankings": [{"id": "c1"}]}\n```\n'
        data = agy._extract_json(text)
        self.assertEqual(data["rankings"][0]["id"], "c1")

    def test_reines_json(self):
        data = agy._extract_json('{"a": 1}')
        self.assertEqual(data["a"], 1)

    def test_ungueltiges_json(self):
        with self.assertRaises(agy.AgyError):
            agy._extract_json("kein json hier")

    def test_leere_antwort(self):
        with self.assertRaises(agy.AgyError):
            agy._extract_json("   ")

    def test_leerer_prompt(self):
        with self.assertRaises(agy.AgyError):
            agy.json_out(prompt="  ")

    def test_fehlendes_schema(self):
        with self.assertRaises(agy.AgyError):
            agy.json_out(
                prompt="test", schema_path=Path("/gibt/es/nicht.json")
            )


class TestSchemaFiles(unittest.TestCase):
    """Die JSON-Schemas sind der Vertrag mit dem LLM — sie muessen gueltig sein."""

    def setUp(self):
        self.schema_dir = Path(__file__).resolve().parents[1] / "config" / "schemas"

    def test_schemas_vorhanden_und_gueltig(self):
        expected = [
            "highlight_ranking.schema.json",
            "thumbnail_text.schema.json",
            "longform_plan.schema.json",
        ]
        for name in expected:
            path = self.schema_dir / name
            self.assertTrue(path.is_file(), f"{name} fehlt")
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data.get("type"), "object")
            self.assertIn("required", data)


if __name__ == "__main__":
    unittest.main()
