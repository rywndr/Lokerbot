from __future__ import annotations

from pathlib import Path
import unittest

from lokerbot.nextjs import extract_next_data

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "dealls_listing.html"


class NextJsTests(unittest.TestCase):
    def test_extract_next_data_from_fixture(self) -> None:
        html = FIXTURE_PATH.read_text(encoding="utf-8")

        next_data = extract_next_data(html)

        self.assertIsInstance(next_data, dict)
        queries = next_data["props"]["pageProps"]["dehydratedState"]["queries"]
        self.assertIsInstance(queries, list)
        self.assertGreater(len(queries), 0)

    def test_extract_next_data_raises_when_script_missing(self) -> None:
        html = "<html><body><script id='not-next-data'>{}</script></body></html>"

        with self.assertRaisesRegex(ValueError, "Could not find __NEXT_DATA__"):
            extract_next_data(html)

    def test_extract_next_data_raises_when_payload_empty(self) -> None:
        html = '<html><body><script id="__NEXT_DATA__" type="application/json"></script></body></html>'

        with self.assertRaisesRegex(ValueError, "script was empty"):
            extract_next_data(html)


if __name__ == "__main__":
    unittest.main()
