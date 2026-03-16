from __future__ import annotations

import unittest

from lokerbot.utils import clean_string, humanize_label


class UtilsTests(unittest.TestCase):
    def test_clean_string_trims_whitespace(self) -> None:
        self.assertEqual(clean_string("  Presales Darwinbox \n"), "Presales Darwinbox")

    def test_clean_string_returns_none_for_empty_or_non_string_values(self) -> None:
        for value in (None, 123, "", "   \t"):
            with self.subTest(value=value):
                self.assertIsNone(clean_string(value))

    def test_humanize_label_normalizes_common_label_formats(self) -> None:
        cases = {
            "fullTime": "Full Time",
            "full_time": "Full Time",
            "full-time": "Full Time",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(humanize_label(value), expected)


if __name__ == "__main__":
    unittest.main()
