"""Tests der Datenmodelle."""

from __future__ import annotations

import unittest

from media_pipeline.models import (
    Candidate,
    RankedClip,
    StageResult,
    Transcript,
    TranscriptSegment,
    VideoMeta,
)


class TestVideoMeta(unittest.TestCase):
    def test_is_vertical(self):
        self.assertTrue(VideoMeta("a.mp4", 10, 1080, 1920, 60).is_vertical)
        self.assertFalse(VideoMeta("a.mp4", 10, 1920, 1080, 60).is_vertical)

    def test_roundtrip(self):
        meta = VideoMeta("a.mp4", 12.5, 1920, 1080, 59.94, "h264", 1024)
        restored = VideoMeta.from_dict(meta.to_dict())
        self.assertEqual(restored.duration_sec, 12.5)
        self.assertEqual(restored.codec, "h264")
        self.assertEqual(restored.size_bytes, 1024)


class TestTranscript(unittest.TestCase):
    def setUp(self):
        self.transcript = Transcript(
            segments=[
                TranscriptSegment(0.0, 5.0, "Hallo"),
                TranscriptSegment(5.0, 10.0, "Welt"),
                TranscriptSegment(20.0, 25.0, "Später"),
            ],
            language="de",
            model="small",
        )

    def test_duration(self):
        self.assertEqual(self.transcript.duration, 25.0)

    def test_full_text(self):
        self.assertEqual(self.transcript.full_text, "Hallo Welt Später")

    def test_segments_in(self):
        found = self.transcript.segments_in(4.0, 6.0)
        self.assertEqual(len(found), 2)

    def test_segments_in_empty(self):
        self.assertEqual(self.transcript.segments_in(100.0, 200.0), [])

    def test_roundtrip(self):
        restored = Transcript.from_dict(self.transcript.to_dict())
        self.assertEqual(len(restored.segments), 3)
        self.assertEqual(restored.segments[2].text, "Später")


class TestCandidate(unittest.TestCase):
    def test_duration_and_roundtrip(self):
        candidate = Candidate(id="c1", start=10.0, end=70.0, reason="test", energy=0.8)
        self.assertEqual(candidate.duration, 60.0)
        restored = Candidate.from_dict(candidate.to_dict())
        self.assertEqual(restored.id, "c1")
        self.assertEqual(restored.energy, 0.8)


class TestRankedClip(unittest.TestCase):
    def test_roundtrip(self):
        clip = RankedClip(
            id="c1",
            start=1.0,
            end=61.0,
            score=8.5,
            reason="toller Moment",
            title="Titel",
            keywords=["a", "b"],
            best_moment_sec=12.0,
        )
        restored = RankedClip.from_dict(clip.to_dict())
        self.assertEqual(restored.score, 8.5)
        self.assertEqual(restored.duration, 60.0)
        self.assertEqual(restored.keywords, ["a", "b"])


class TestStageResult(unittest.TestCase):
    def test_defaults(self):
        result = StageResult(ok=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {})
        self.assertEqual(result.gate, "")

    def test_to_dict(self):
        result = StageResult(ok=False, error="Fehler")
        payload = result.to_dict()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "Fehler")


if __name__ == "__main__":
    unittest.main()
