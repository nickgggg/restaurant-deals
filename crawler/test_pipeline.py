from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from crawler import discover_restaurants, extract_with_gemini


class SourceDiscoveryTests(unittest.TestCase):
    def test_rendered_navigation_recovers_specials_routes(self) -> None:
        rendered = """
            <html><body>
              <a href="/weekday-lunch">Weekday Lunch</a>
              <a href="/dailyspecials">Daily Specials</a>
            </body></html>
        """
        with (
            patch.object(discover_restaurants, "fetch_homepage", return_value="<html></html>"),
            patch.object(discover_restaurants, "render_page", return_value=rendered),
            patch.object(discover_restaurants, "sitemap_urls", return_value=[]),
        ):
            pages = discover_restaurants.discover_specials_pages({"website_url": "https://example.com/"})

        by_url = {page["url"]: page for page in pages}
        expected = {"https://example.com/weekday-lunch", "https://example.com/dailyspecials"}
        self.assertTrue(expected.issubset(by_url))
        self.assertTrue(all(by_url[url]["discovered_via"] == "site_link" for url in expected))

    def test_sitemap_finds_specials_pages(self) -> None:
        sitemap = """
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://example.com/menu</loc></url>
              <url><loc>https://example.com/happy-hour</loc></url>
              <url><loc>https://example.com/weekday-specials</loc></url>
            </urlset>
        """
        with patch.object(discover_restaurants, "fetch_homepage", side_effect=lambda url: sitemap if "sitemap" in url else ""):
            urls = discover_restaurants.sitemap_urls("https://example.com/")

        self.assertEqual(urls, ["https://example.com/happy-hour", "https://example.com/weekday-specials"])


class ExtractionTests(unittest.TestCase):
    def test_report_fields_remove_placeholder_comments(self) -> None:
        body = """### Restaurant
Example Grill

### City
Huntington Beach

### Details
<!-- Placeholder --> Actual weekday price is $8.
"""
        fields = extract_with_gemini.report_fields(body)
        self.assertEqual(fields["restaurant"], "Example Grill")
        self.assertEqual(fields["details"], "Actual weekday price is $8.")

    def test_source_url_rejects_local_addresses(self) -> None:
        self.assertTrue(extract_with_gemini.safe_source_url("https://example.com/specials"))
        self.assertFalse(extract_with_gemini.safe_source_url("http://localhost/specials"))
        self.assertFalse(extract_with_gemini.safe_source_url("file:///tmp/specials"))

    def test_expiration_dates_are_parsed(self) -> None:
        self.assertEqual(extract_with_gemini.parse_date("2026-09-14"), date(2026, 9, 14))
        self.assertIsNone(extract_with_gemini.parse_date("not-a-date"))

    def test_reported_pages_are_first_in_candidate_queue(self) -> None:
        inventory = {
            "restaurants": [
                {
                    "place_id": "place-1",
                    "name": "Example Grill",
                    "city": "Huntington Beach",
                    "business_status": "OPERATIONAL",
                    "specials_pages": [{"url": "https://example.com/specials", "confidence": "high"}],
                }
            ]
        }
        reported = {
            "restaurant": inventory["restaurants"][0],
            "page": {"url": "https://example.com/reported", "confidence": "reported", "report_issue": 12},
        }
        with patch.object(extract_with_gemini, "reported_pages", return_value=[reported]):
            candidates = extract_with_gemini.candidate_pages(inventory)

        candidates.sort(key=extract_with_gemini.priority)
        self.assertEqual(candidates[0]["page"]["url"], "https://example.com/reported")

    def test_new_report_rechecks_an_unchanged_cached_page(self) -> None:
        item = {"content_hash": "same", "page": {"report_issue": 12}}
        cached = {"content_hash": "same", "status": "no_deals", "page": {}}
        processed = {"content_hash": "same", "status": "no_deals", "page": {"report_issue": 12}}

        self.assertFalse(extract_with_gemini.reusable_page(item, cached))
        self.assertTrue(extract_with_gemini.reusable_page(item, processed))


if __name__ == "__main__":
    unittest.main()
