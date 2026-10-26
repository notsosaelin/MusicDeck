"""Temporary UI queues must not become source-labelled Jump back in cards."""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "py_modules"))
from musicdeck.recents import Recents


class RecentsTests(unittest.TestCase):
    def test_temporary_pages_not_recorded_but_source_lists_remain(self):
        with tempfile.TemporaryDirectory() as directory:
            history = Recents(directory)
            for identifier in ("search:sukuna x crank", "section:Recently played", "tag:jazz"):
                history.add("source", {"id": identifier, "kind": "list", "title": identifier})
            history.add("source", {"id": "album", "kind": "album", "title": "Album"})
            history.add_search("source", "sukuna x crank")
            restored = Recents(directory)
            self.assertEqual([c["id"] for c in restored.for_instance("source")], ["album", "tag:jazz"])
            self.assertEqual(restored.searches("source"), ["sukuna x crank"])

    def test_old_files_filter_temporary_pages_without_changing_other_history(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory() as directory:
                collections = {"qobuz": [
                    {"id": "search:sukuna x crank", "kind": "list", "title": "sukuna x crank"},
                    {"id": "section:Recently played", "kind": "list", "title": "Recently played"},
                    {"id": "real", "kind": "album", "title": "Real"},
                ]}
                data = collections if legacy else {"collections": collections, "searches": {"youtube": ["sukuna x crank"]}}
                pathlib.Path(directory, "recents.json").write_text(json.dumps(data), encoding="utf-8")
                restored = Recents(directory)
                self.assertEqual([c["id"] for c in restored.for_instance("qobuz")], ["real"])
                if not legacy:
                    self.assertEqual(restored.searches("youtube"), ["sukuna x crank"])


if __name__ == "__main__":
    unittest.main()
