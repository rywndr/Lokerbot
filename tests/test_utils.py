from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from lokerbot.utils import (
    clean_string,
    dedupe_list,
    humanize_label,
    is_recent_job_post,
    normalize_description_text,
    parse_iso_datetime,
)

SCRAPED_AT = datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc)


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

    def test_parse_iso_datetime_supports_z_timestamps(self) -> None:
        self.assertEqual(
            parse_iso_datetime("2026-03-16T12:00:00Z"),
            datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc),
        )

    def test_parse_iso_datetime_preserves_offset_timestamps(self) -> None:
        self.assertEqual(
            parse_iso_datetime("2026-03-16T19:00:00+07:00"),
            datetime(2026, 3, 16, 19, 0, 0, tzinfo=timezone(timedelta(hours=7))),
        )

    def test_parse_iso_datetime_assumes_utc_for_naive_timestamps(self) -> None:
        self.assertEqual(
            parse_iso_datetime("2026-03-16T12:00:00"),
            datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc),
        )

    def test_parse_iso_datetime_returns_none_for_malformed_or_non_string_values(self) -> None:
        for value in ("not-a-date", 123, None):
            with self.subTest(value=value):
                self.assertIsNone(parse_iso_datetime(value))

    def test_is_recent_job_post_accepts_today_and_exact_30_day_boundary(self) -> None:
        self.assertTrue(is_recent_job_post("2026-03-16T11:59:59Z", SCRAPED_AT))
        self.assertTrue(is_recent_job_post("2026-02-14T12:00:00Z", SCRAPED_AT))

    def test_is_recent_job_post_rejects_out_of_window_values(self) -> None:
        self.assertFalse(is_recent_job_post("2026-02-14T11:59:59Z", SCRAPED_AT))
        self.assertFalse(is_recent_job_post("2026-03-16T12:00:01Z", SCRAPED_AT))
        self.assertFalse(is_recent_job_post(None, SCRAPED_AT))

    def test_dedupe_list_preserves_first_seen_order(self) -> None:
        self.assertEqual(
            dedupe_list(["Hybrid", "Python", "Hybrid", "Remote", "Python"]),
            ["Hybrid", "Python", "Remote"],
        )

    def test_normalize_description_text_strips_html_and_blank_lines(self) -> None:
        self.assertEqual(
            normalize_description_text("<p>Hello&nbsp;world</p><p>Second line</p><br><div>Third line</div>"),
            "Hello world\nSecond line\nThird line",
        )


if __name__ == "__main__":
    unittest.main()
