from __future__ import annotations

import unittest

from skyshard_tracker.form_ids import RuntimeFormIds, from_save_tables, plugin_local_id


class FormIdTests(unittest.TestCase):
    def test_master_relative_plugin_ids_and_runtime_ids(self):
        self.assertEqual(plugin_local_id(0x0102C037, self_index=1, kind="full"), 0x02C037)
        self.assertEqual(plugin_local_id(0x01000881, self_index=1, kind="light"), 0x881)
        with self.assertRaises(ValueError):
            plugin_local_id(0x0000003C, self_index=1, kind="full")
        with self.assertRaises(ValueError):
            plugin_local_id(0x0102C037, self_index=1, kind="light")
        self.assertEqual(RuntimeFormIds("full", 0x48).resolve(0x02C037), 0x4802C037)
        self.assertEqual(RuntimeFormIds("light", 0x838).resolve(0x881), 0xFE838881)

    def test_save_tables_are_resolved_separately_by_plugin_kind(self):
        self.assertEqual(from_save_tables("full", ["Skyrim.esm", "Skyshards.esp"], [])[0],
                         RuntimeFormIds("full", 1))
        self.assertEqual(from_save_tables("light", ["Skyrim.esm"], ["Other.esl", "Skyshards.esp"])[0],
                         RuntimeFormIds("light", 1))
        resolver, warning = from_save_tables("full", ["Skyrim.esm"], ["Skyshards.esp"])
        self.assertIsNone(resolver)
        self.assertIn("imported plugin is full", warning)


if __name__ == "__main__":
    unittest.main()
