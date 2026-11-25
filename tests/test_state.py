"""Tests der Zustandsmaschine — inklusive Wiedereinstieg."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from media_pipeline.models import STATUS_DONE, STATUS_FAILED, STATUS_PENDING
from media_pipeline.state import PipelineState


class TestPipelineState(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self._tmp.name)
        self.state = PipelineState.load(self.state_dir, "test_video")

    def tearDown(self):
        self._tmp.cleanup()

    def test_neuer_state_ist_leer(self):
        self.assertEqual(self.state.stages, {})
        self.assertFalse(self.state.is_done("s01_ingest"))

    def test_mark_done_und_wiedereinstieg(self):
        self.state.mark_done("s01_ingest", "ffprobe: 1920x1080", {"duration_sec": 10.0})
        self.assertTrue(self.state.is_done("s01_ingest"))

        # Neu laden — der Zustand muss persistiert sein.
        restored = PipelineState.load(self.state_dir, "test_video")
        self.assertTrue(restored.is_done("s01_ingest"))
        self.assertEqual(restored.data_of("s01_ingest")["duration_sec"], 10.0)
        self.assertIn("ffprobe", restored.stage("s01_ingest").gate)

    def test_failed_stage(self):
        self.state.mark_failed("s04_rank", "agy exit 1")
        restored = PipelineState.load(self.state_dir, "test_video")
        self.assertEqual(restored.stage("s04_rank").status, STATUS_FAILED)
        self.assertFalse(restored.is_done("s04_rank"))
        self.assertEqual(restored.stage("s04_rank").error, "agy exit 1")

    def test_failed_ist_nicht_done(self):
        """Ein fehlgeschlagenes Gate darf nie als 'done' gelten."""
        self.state.mark_failed("s02_transcribe", "kein Audio")
        self.assertFalse(self.state.is_done("s02_transcribe"))

    def test_reset(self):
        self.state.mark_done("s01_ingest", "ok")
        self.state.mark_done("s02_transcribe", "ok")
        self.state.reset(["s01_ingest"])
        self.assertFalse(self.state.is_done("s01_ingest"))
        self.assertTrue(self.state.is_done("s02_transcribe"))

    def test_reset_alles(self):
        self.state.mark_done("s01_ingest", "ok")
        self.state.reset()
        self.assertFalse(self.state.is_done("s01_ingest"))

    def test_state_ist_gueltiges_json(self):
        self.state.mark_done("s01_ingest", "ok")
        with self.state.path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["video_id"], "test_video")
        self.assertIn("stages", data)
        self.assertEqual(data["stages"]["s01_ingest"]["status"], STATUS_DONE)

    def test_beschaedigte_state_datei(self):
        """Eine kaputte State-Datei darf nicht abstuerzen."""
        self.state.path.write_text("{ kaputt", encoding="utf-8")
        restored = PipelineState.load(self.state_dir, "test_video")
        self.assertEqual(restored.stages, {})
        self.assertEqual(restored.stage("s01_ingest").status, STATUS_PENDING)

    def test_atomic_save_leave_keine_tempdateien(self):
        self.state.mark_done("s01_ingest", "ok")
        leftovers = list(self.state_dir.glob("*.tmp"))
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
