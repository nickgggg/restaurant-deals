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
    def test_visual_assets_prioritize_specials_images_and_pdfs(self) -> None:
        page = """
            <html><body>
              <img src="/logo.png" alt="Restaurant logo" width="80" height="80">
              <img src="/images/tuesday-specials.jpg" alt="Tuesday specials" width="1200" height="1600">
              <a href="/menus/happy-hour.pdf">Happy hour menu</a>
            </body></html>
        """
        assets = extract_with_gemini.visual_asset_candidates("https://example.com/specials", page)

        self.assertEqual(assets[0]["url"], "https://example.com/menus/happy-hour.pdf")
        self.assertEqual(assets[1]["url"], "https://example.com/images/tuesday-specials.jpg")
        self.assertLess(assets[-1]["score"], assets[1]["score"])

    def test_visual_cache_requires_matching_asset_hash(self) -> None:
        previous = {
            "visual_hash": "same",
            "visual_status": "verified",
            "status": "ok",
        }

        self.assertTrue(extract_with_gemini.reusable_visual_page("same", previous))
        self.assertFalse(extract_with_gemini.reusable_visual_page("changed", previous))

    def test_visual_assets_deduplicate_identical_image_variants(self) -> None:
        item = {
            "page": {"url": "https://example.com/specials"},
            "asset_candidates": [
                {"url": "https://example.com/specials.jpg"},
                {"url": "https://example.com/specials.jpg?format=100w"},
                {"url": "https://example.com/happy-hour.jpg"},
            ],
        }

        def fake_fetch(candidate, _page_url):
            duplicate = "specials.jpg" in candidate["url"]
            return {
                "url": candidate["url"],
                "mime_type": "image/jpeg",
                "byte_size": 10_000,
                "content_hash": "same" if duplicate else "different",
                "data": "encoded",
            }

        with patch.object(extract_with_gemini, "fetch_visual_asset", side_effect=fake_fetch):
            assets = extract_with_gemini.fetch_visual_assets(item)

        self.assertEqual([asset["content_hash"] for asset in assets], ["same", "different"])

    def test_visual_queue_prioritizes_strong_asset_signals(self) -> None:
        generic = {
            "restaurant": {"name": "Alpha", "city": "Huntington Beach"},
            "page": {"url": "https://example.com/menu", "confidence": "high"},
            "asset_candidates": [{"url": "https://example.com/photo.jpg", "score": 1}],
        }
        specials = {
            "restaurant": {"name": "Zulu", "city": "Huntington Beach"},
            "page": {"url": "https://example.com/specials", "confidence": "high"},
            "asset_candidates": [{"url": "https://example.com/weekday-specials.jpg", "score": 15}],
        }

        self.assertEqual(sorted([generic, specials], key=extract_with_gemini.visual_priority)[0], specials)

    def test_visual_queue_spreads_calls_across_restaurants(self) -> None:
        candidates = []
        for restaurant, score in (("Alpha", 20), ("Alpha", 19), ("Bravo", 18), ("Charlie", 17), ("Delta", 16)):
            candidates.append(
                {
                    "restaurant": {"name": restaurant, "city": "Huntington Beach"},
                    "page": {"url": f"https://example.com/{restaurant}/{score}", "confidence": "high"},
                    "asset_candidates": [{"url": f"https://example.com/{score}.jpg", "score": score}],
                }
            )

        selected = extract_with_gemini.select_visual_pages(candidates)

        self.assertEqual([item["restaurant"]["name"] for item in selected], ["Alpha", "Bravo", "Charlie", "Delta"])

    def test_visual_asset_urls_are_unescaped_and_encoded(self) -> None:
        page = '<img src="\\/\\/cdn.example.com/Happy Hour Menu.jpg" alt="Happy hour specials">'

        assets = extract_with_gemini.visual_asset_candidates("https://example.com/specials", page)

        self.assertEqual(assets[0]["url"], "https://cdn.example.com/Happy%20Hour%20Menu.jpg")

    def test_visual_deals_require_higher_confidence(self) -> None:
        raw = {
            "deals": [
                {
                    "summary": "$5 Taco Tuesday",
                    "details": ["Tacos are $5"],
                    "applies_days": ["tuesday"],
                    "applies_month_days": [],
                    "time_window": "",
                    "categories": ["food"],
                    "valid_through": "",
                    "evidence": ["Tuesday tacos are $5"],
                    "confidence": 0.88,
                }
            ]
        }
        accepted, rejected = extract_with_gemini.validate_deals(
            raw,
            "Tuesday tacos are $5",
            min_confidence=0.9,
        )

        self.assertEqual(accepted, [])
        self.assertEqual(rejected, ["$5 Taco Tuesday"])

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
