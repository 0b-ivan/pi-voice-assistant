import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import llm  # noqa: E402
import memory  # noqa: E402


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "stick" / "proximus"
        self.device = Path(self.tmp.name) / "device"
        self.root.mkdir(parents=True)
        self.device.touch()
        self.core = memory.MemoryCore(self.root, self.device, clock=lambda: 1000)

    def test_store_survives_reload_and_pulling_the_stick_hides_it(self):
        self.assertTrue(self.core.add("fact", "Bediener heißt Ivan."))
        self.assertTrue(self.core.add("directive", "Städte nur noch Makropolen nennen"))
        self.assertTrue(self.core.add("fact", "bediener heißt ivan"))  # duplicate refreshed
        self.core.remember_turn("wie hoch ist der eiffelturm", "330 Meter.")
        fresh = memory.MemoryCore(self.root, self.device)
        self.assertEqual(fresh.counts(), dict(facts=1, directives=1))
        self.device.unlink()                          # stick pulled
        self.assertIsNone(fresh.context())
        self.assertFalse(fresh.add("fact", "geht nicht"))
        self.device.touch()                           # plugged back in
        context = fresh.context()
        self.assertEqual(context["facts"], ["bediener heißt ivan"])
        self.assertEqual(context["history"], [dict(q="wie hoch ist der eiffelturm", a="330 Meter")])

    def test_forget_matches_other_word_forms(self):
        self.core.add("fact", "Bediener heißt Ivan")
        self.core.add("directive", "Humor-Erweiterung installiert")
        self.assertEqual(self.core.forget("ich ivan heiße"), 1)
        self.assertEqual(self.core.forget("humor"), 1)
        self.assertEqual(self.core.counts(), dict(facts=0, directives=0))

    def test_damaged_file_is_kept_aside(self):
        (self.root / memory.FILE).write_text("{kaputt")
        self.assertEqual(self.core.load()["facts"], [])
        self.assertTrue(list(self.root.glob("memory.broken-*")))

    def test_history_and_budget_limits(self):
        for i in range(10):
            self.core.remember_turn(f"frage {i}", f"antwort {i}")
        for i in range(150):
            self.core.add("fact", f"tatsache nummer {i} " + "x" * 50)
        context = self.core.context()
        self.assertEqual(len(context["history"]), 4)
        self.assertLess(sum(map(len, context["facts"])), memory.CONTEXT_BUDGET)
        self.assertTrue(context["facts"][-1].startswith("tatsache nummer 149"))
        self.assertEqual(context["total_facts"], 150)
        self.assertIsNotNone(memory.encode_header(context))


class CommandTests(unittest.TestCase):
    def test_commands(self):
        cases = {
            "merk dir dass ich ivan heiße": ("add_fact", "ich ivan heiße"),
            "nenne städte in zukunft nur noch makropolen":
                ("add_directive", "nenne städte in zukunft nur noch makropolen"),
            "ab jetzt antworte kürzer": ("add_directive", "ab jetzt antworte kürzer"),
            "installiere humor erweiterung": ("add_directive", "Humor-Erweiterung installiert"),
            "installiere die humor-erweiterung": ("add_directive", "Humor-Erweiterung installiert"),
            "deinstalliere humor erweiterung": ("forget", "humor"),
            "was weißt du über mich": ("recall", ""),
            "welche erweiterungen hast du": ("list_directives", ""),
            "wie spät ist es": None,
            "vergiss es": None,
            "erzähl mir was über makropolen": None,
        }
        for text, expected in cases.items():
            self.assertEqual(memory.command(text), expected, text)

    def test_replies(self):
        context = dict(facts=["Bediener heißt Ivan"], directives=["Humor-Erweiterung installiert"],
                       total_facts=1)
        self.assertIn("fehlt", memory.reply("add_fact", "x", None, "light"))
        self.assertIn("Ivan", memory.reply("recall", "", context))
        self.assertIn("1 Direktiven aktiv", memory.reply("list_directives", "", context))
        self.assertEqual(memory.reply("forget", "humor", context), "1 Eintrag gelöscht.")
        self.assertIn("Kein passender", memory.reply("forget", "pizza", context))


class HeaderAndPromptTests(unittest.TestCase):
    def test_header_round_trip_drops_junk(self):
        copy = dict(facts=["a fact", 3, ""], directives=["d" * 500], history=[dict(q="q", a="a"), "x"],
                    total_facts=-5, extra="ignored")
        decoded = memory.decode_header(memory.encode_header(copy))
        self.assertEqual(decoded, dict(facts=["a fact"], directives=["d" * memory.MAX_TEXT],
                                       history=[dict(q="q", a="a")], total_facts=0))
        self.assertIsNone(memory.decode_header("not base64!"))
        self.assertIsNone(memory.decode_header("A" * (memory.HEADER_LIMIT + 4)))

    def test_prompt_contains_directives_facts_and_history(self):
        copy = dict(facts=["Bediener heißt Ivan"], directives=["Städte heißen Makropolen"],
                    history=[dict(q="hallo", a="Gruß")])
        sent = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "Ok."}}]}).encode()

        def urlopen(request, timeout):
            sent.update(json.loads(request.data))
            return Response()

        with patch.dict(os.environ, {"LOCAL_LLM_URL": "http://127.0.0.1:1/v1/chat/completions"}), \
                patch("urllib.request.urlopen", urlopen):
            llm.generate_local_reply("wie heißt meine stadt", memory=copy)
        system = sent["messages"][0]["content"]
        self.assertIn("Städte heißen Makropolen", system)
        self.assertIn("Bediener heißt Ivan", system)
        self.assertIn("MERKE", system)
        self.assertEqual([m["role"] for m in sent["messages"]], ["system", "user", "assistant", "user"])
        self.assertIn("Kein Gedächtniskern", llm.system_prompt(memory=None))
        self.assertNotIn("Gedächtnis", llm.system_prompt())

    def test_learned_lines_are_split_off(self):
        self.assertEqual(memory.split_learned("Gut. MERKE: Bediener heißt Ivan. DIREKTIVE: kurz."),
                         ("Gut.", [("add_fact", "Bediener heißt Ivan"), ("add_directive", "kurz")]))


if __name__ == "__main__":
    unittest.main()
