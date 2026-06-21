import hashlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

try:
    from rdflib import RDF
except ImportError:
    RDF = None

from empower_hri.ollama_client import MockLlmClient
from empower_hri.rdf_child_memory import (
    HRI,
    RdfChildMemoryStore,
    generate_child_memory_report,
)


@unittest.skipIf(RDF is None, "rdflib is not installed")
class RdfChildMemoryStoreTest(unittest.TestCase):
    def test_seed_file_is_created_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rdf_path = Path(tmpdir) / "children_memory.ttl"

            RdfChildMemoryStore(str(rdf_path), seed_if_missing=True)

            self.assertTrue(rdf_path.exists())
            self.assertIn("Luca", rdf_path.read_text(encoding="utf-8"))

    def test_seed_contains_at_least_two_children(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RdfChildMemoryStore(
                str(Path(tmpdir) / "children_memory.ttl"),
                seed_if_missing=True,
            )

            children = list(store.graph.subjects(RDF.type, HRI.Child))

            self.assertGreaterEqual(len(children), 2)

    def test_luca_storytelling_preferences_are_returned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RdfChildMemoryStore(
                str(Path(tmpdir) / "children_memory.ttl"),
                seed_if_missing=True,
            )

            memory = store.get_child_memory_context("Luca", "storytelling")

            self.assertTrue(memory["found"])
            self.assertIn("dinosaurs", memory["story_preferences"])
            self.assertIn("space", memory["story_preferences"])
            self.assertIn("friendly robot", memory["story_preferences"])
            self.assertIn("happy ending", memory["story_preferences"])

    def test_sofia_mood_preferences_are_returned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RdfChildMemoryStore(
                str(Path(tmpdir) / "children_memory.ttl"),
                seed_if_missing=True,
            )

            memory = store.get_child_memory_context("Sofia", "mood_check_teddy")

            self.assertTrue(memory["found"])
            self.assertIn("calm", memory["emotion_preferences"])
            self.assertIn("proud", memory["emotion_preferences"])
            self.assertIn("mood_check_teddy", memory["preferred_activity_types"])

    def test_query_is_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RdfChildMemoryStore(
                str(Path(tmpdir) / "children_memory.ttl"),
                seed_if_missing=True,
            )

            memory = store.get_child_memory_context("LUCA", "storytelling")

            self.assertTrue(memory["found"])
            self.assertEqual(memory["child_name"], "Luca")

    def test_unknown_child_returns_found_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RdfChildMemoryStore(
                str(Path(tmpdir) / "children_memory.ttl"),
                seed_if_missing=True,
            )

            memory = store.get_child_memory_context("Marco", "storytelling")

            self.assertFalse(memory["found"])
            self.assertIn("No stored memory", memory["prompt_text"])

    def test_report_is_created_without_modifying_rdf_graph(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rdf_path = Path(tmpdir) / "children_memory.ttl"
            report_dir = Path(tmpdir) / "reports"
            store = RdfChildMemoryStore(str(rdf_path), seed_if_missing=True)
            before = hashlib.sha256(rdf_path.read_bytes()).hexdigest()
            memory = store.get_child_memory_context("Luca", "storytelling")

            report_path = generate_child_memory_report(
                report_dir=str(report_dir),
                context={
                    "child_name": "Luca",
                    "activity_type": "storytelling",
                    "task": "bring the bottle",
                    "activity_description": "tell a story",
                },
                transcript=[
                    {"role": "assistant", "content": "Hi Luca, let's begin."},
                    {"role": "child", "content": "I like the robot dinosaur."},
                ],
                memory_context=memory,
                completion_reason="max_turns",
                turns=1,
                llm_client=MockLlmClient(),
                now=datetime(2026, 6, 21, 12, 30, 0),
            )

            self.assertTrue(Path(report_path).exists())
            report_text = Path(report_path).read_text(encoding="utf-8")
            self.assertIn("# Child Memory Session Report", report_text)
            self.assertIn("These are suggestions only", report_text)
            after = hashlib.sha256(rdf_path.read_bytes()).hexdigest()
            self.assertEqual(before, after)


class ChildMemoryReportTest(unittest.TestCase):
    def test_report_markdown_is_created_without_rdflib_runtime(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = generate_child_memory_report(
                report_dir=str(Path(tmpdir) / "reports"),
                context={
                    "child_name": "Luca",
                    "activity_type": "storytelling",
                    "task": "bring the bottle",
                    "activity_description": "tell a story",
                },
                transcript=[
                    {"role": "assistant", "content": "Hi Luca, let's begin."},
                    {"role": "child", "content": "I like space."},
                ],
                memory_context={
                    "child_name": "Luca",
                    "found": True,
                    "prompt_text": "The child is Luca.\n\nKnown story preferences:\n- space",
                },
                completion_reason="max_turns",
                turns=1,
                llm_client=MockLlmClient(),
                now=datetime(2026, 6, 21, 12, 30, 0),
            )

            report_text = Path(report_path).read_text(encoding="utf-8")
            self.assertIn("# Child Memory Session Report", report_text)
            self.assertIn("Child name: Luca", report_text)
            self.assertIn("These are suggestions only", report_text)


class VoicePromptCompatibilityTest(unittest.TestCase):
    def test_prompt_without_memory_has_no_memory_section(self):
        try:
            from empower_hri.voice_llm_node import build_child_activity_system_prompt
        except ImportError as exc:
            self.skipTest(f"ROS 2 imports unavailable: {exc}")

        prompt = build_child_activity_system_prompt(
            "storytelling",
            {"activity_description": "tell a story"},
        )

        self.assertNotIn("Child personalization memory", prompt)
        self.assertIn("Tell one short", prompt)


if __name__ == "__main__":
    unittest.main()
