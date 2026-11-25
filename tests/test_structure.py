"""Tests der Projektstruktur und der Agent-Konventionen.

Diese Tests sichern die Struktur ab: wenn jemand eine Datei verschiebt
oder eine Konvention bricht, faellt es hier auf — nicht erst im Betrieb.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestProjektstruktur(unittest.TestCase):
    def test_ordner_existieren(self):
        expected = [
            "src/media_pipeline",
            "src/media_pipeline/stages",
            "src/media_pipeline/lib",
            "config",
            "config/schemas",
            "prompts",
            "data",
            "logs",
            "tests",
            ".agents/rules",
            ".agents/skills/pipeline",
        ]
        for rel in expected:
            self.assertTrue((PROJECT_ROOT / rel).is_dir(), f"{rel} fehlt")

    def test_alle_stages_vorhanden(self):
        expected = [
            "s01_ingest",
            "s02_transcribe",
            "s03_highlights",
            "s04_rank",
            "s05_thumbnail",
            "s06_vertical",
            "s07_longform",
        ]
        for name in expected:
            path = PROJECT_ROOT / "src" / "media_pipeline" / "stages" / f"{name}.py"
            self.assertTrue(path.is_file(), f"{name}.py fehlt")

    def test_agent_kontext_vorhanden(self):
        for name in ["GEMINI.md", "AGENTS.md", ".gitignore"]:
            self.assertTrue((PROJECT_ROOT / name).is_file(), f"{name} fehlt")

    def test_gemini_symlinks_zeigen_auf_agents(self):
        """Die .gemini/-Pfade sind Symlink-Spiegel auf .agents/."""
        gemini = PROJECT_ROOT / ".gemini"
        if not gemini.is_dir():
            self.skipTest(".gemini/ nicht vorhanden")
        for link_name, target in [("rules", "../.agents/rules"), ("workflows", "../.agents/skills")]:
            link = gemini / link_name
            self.assertTrue(link.is_symlink(), f".gemini/{link_name} ist kein Symlink")
            self.assertEqual(str(link.readlink()), target)
            self.assertTrue(link.exists(), f".gemini/{link_name} zeigt ins Leere")

class TestPrompts(unittest.TestCase):
    """Prompts muessen Dateien sein und den Injection-Schutz enthalten."""

    def setUp(self):
        self.prompts_dir = PROJECT_ROOT / "prompts"

    def test_prompt_dateien_vorhanden(self):
        for name in ["highlight_ranking.md", "thumbnail_text.md", "longform_plan.md"]:
            self.assertTrue((self.prompts_dir / name).is_file(), f"{name} fehlt")

    def test_kein_prompt_inline_im_code(self):
        """Prompts gehoeren in Dateien, nicht in Python-Code."""
        pattern = re.compile(r"(Du bist|Create a|You are)\s", re.IGNORECASE)
        for path in (PROJECT_ROOT / "src").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(
                pattern.search(text),
                f"{path.name} enthaelt einen inline-Prompt",
            )

    def test_injection_schutz_vorhanden(self):
        """Jeder Prompt mit Fremdinhalten braucht die Injection-Warnung."""
        for path in self.prompts_dir.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            self.assertIn(
                "Injection", text, f"{path.name} hat keine Injection-Warnung"
            )

    def test_platzhalter_verwendet(self):
        text = (self.prompts_dir / "highlight_ranking.md").read_text(encoding="utf-8")
        self.assertIn("{{CANDIDATES_JSON}}", text)
        self.assertIn("{{TRANSCRIPT_EXCERPT}}", text)

    def test_transkript_abgegrenzt(self):
        text = (self.prompts_dir / "highlight_ranking.md").read_text(encoding="utf-8")
        self.assertIn("<<<TRANSKRIPT", text)
        self.assertIn("TRANSKRIPT>>>", text)


class TestKeineDrawtext(unittest.TestCase):
    """Dieses ffmpeg hat kein drawtext — die Regel darf nicht gebrochen werden."""

    def test_kein_drawtext_im_code(self):
        for path in (PROJECT_ROOT / "src").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "drawtext=", text, f"{path.name} verwendet drawtext (nicht gebaut)"
            )


class TestRegeln(unittest.TestCase):
    def test_regel_dateien_vorhanden(self):
        rules = PROJECT_ROOT / ".agents" / "rules"
        for name in ["coding-standards.md", "pipeline-architecture.md"]:
            self.assertTrue((rules / name).is_file(), f"{name} fehlt")

    def test_skill_hat_frontmatter(self):
        skill = PROJECT_ROOT / ".agents" / "skills" / "pipeline" / "SKILL.md"
        text = skill.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"), "SKILL.md braucht YAML-Frontmatter")
        self.assertIn("name:", text)
        self.assertIn("description:", text)


class TestKeineSecrets(unittest.TestCase):
    """Keine Credentials im versionierten Code."""

    def test_keine_secret_patterns(self):
        """Echte Zuweisungen mit Wert suchen — keine blossen Wortnennungen.

        Ein Kommentar wie "kein API-Key noetig" ist kein Leak. Ein
        ``password = "geheim1234"`` ist einer.
        """
        pattern = re.compile(
            r"(api[_-]?key|secret[_-]?key|access[_-]?token|password)"
            r"\s*[:=]\s*['\"][^'\"]{8,}['\"]",
            re.IGNORECASE,
        )
        for path in list((PROJECT_ROOT / "src").rglob("*.py")) + list(
            (PROJECT_ROOT / "config").rglob("*.yaml")
        ):
            text = path.read_text(encoding="utf-8")
            match = pattern.search(text)
            if match is not None:
                self.fail(f"{path.name}: moegliches Secret '{match.group(0)[:40]}'")

    def test_keine_env_werte_als_klartext(self):
        """Es darf keine fest verdrahteten YouTube-Cookies geben."""
        pattern = re.compile(r"(cookies?[-_]?(file|path|data)?\s*[:=]\s*['\"][^'\"]{12,})", re.IGNORECASE)
        for path in (PROJECT_ROOT / "src").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            match = pattern.search(text)
            if match is not None:
                self.fail(f"{path.name}: moeglicher Cookie-Wert '{match.group(0)[:40]}'")

    def test_gitignore_deckt_daten_und_env(self):
        text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", text)
        self.assertIn("data/", text)
        self.assertIn("venv/", text)


class TestStateVerzeichnis(unittest.TestCase):
    def test_state_ist_ignoriert(self):
        text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("data/state/", text)

    def test_gitkeep_vorhanden(self):
        for rel in ["data/inbox", "data/work", "data/clips", "data/state", "logs"]:
            self.assertTrue(
                (PROJECT_ROOT / rel / ".gitkeep").is_file(), f"{rel}/.gitkeep fehlt"
            )


if __name__ == "__main__":
    unittest.main()
