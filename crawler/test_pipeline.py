from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from crawler import cache_logos, crawl_deals, discover_restaurants, extract_with_gemini


class LogoCacheTests(unittest.TestCase):
    def test_icon_candidates_prefer_semantic_logo_over_touch_icon(self) -> None:
        page = """
            <link rel="icon" href="/favicon-32.png" sizes="32x32">
            <link rel="apple-touch-icon" href="/touch.png" sizes="180x180">
            <header><img class="site-logo" src="/actual-logo.png" alt="Example logo"></header>
        """

        candidates = cache_logos.icon_candidates("https://example.com/menu", page)

        self.assertEqual(candidates[0], "https://example.com/actual-logo.png")
        self.assertEqual(candidates[-1], "https://example.com/favicon.ico")

    def test_icon_candidates_reject_obvious_utility_artwork(self) -> None:
        page = """
            <meta property="og:image" content="/table-setting.jpg">
            <img class="logo" src="/images/qrcode-logo.png" alt="Order QR logo">
            <img class="brand-logo" src="/images/restaurant-logo.png" alt="Restaurant logo">
        """

        candidates = cache_logos.icon_candidates("https://example.com/", page)

        self.assertEqual(candidates[0], "https://example.com/images/restaurant-logo.png")
        self.assertFalse(any("qr" in url or "table-setting" in url for url in candidates))

    def test_image_extension_uses_file_signature(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"x" * 40

        self.assertEqual(cache_logos.image_extension(png, "application/octet-stream"), "png")
        self.assertIsNone(cache_logos.image_extension(b"not an image", "text/html"))


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
            "extraction_version": extract_with_gemini.EXTRACTION_VERSION,
        }

        self.assertTrue(extract_with_gemini.reusable_visual_page("same", previous))
        self.assertFalse(extract_with_gemini.reusable_visual_page("changed", previous))
        self.assertFalse(
            extract_with_gemini.reusable_visual_page(
                "same",
                {**previous, "extraction_version": extract_with_gemini.EXTRACTION_VERSION - 1},
            )
        )

    def test_market_broiler_price_tiers_share_one_schedule(self) -> None:
        schedule = "MON-FRI 3-6:30 SAT + SUN 1-5"
        deals = []
        for price, categories, details in (
            ("$8", ["food", "drink"], ["Sashimi Scallops", "Parmesan Garlic Fries"]),
            ("$10", ["drink"], ["Classic Margarita", "American Mule", "Offered in designated bar areas only"]),
            ("$11", ["food", "drink"], ["Coconut Shrimp", "Lemon Drop"]),
            ("$13", ["food", "drink"], ["California Roll", "Smokin' Old Fashioned"]),
        ):
            deals.append(
                {
                    "summary": f"{price} Social Hour Food and Drinks",
                    "details": details,
                    "applies_days": extract_with_gemini.DAYS if price == "$8" else [],
                    "applies_month_days": [],
                    "time_window": None,
                    "categories": categories,
                    "valid_through": None,
                    "source_evidence": [schedule] if price == "$8" else [price.removeprefix("$"), details[0]],
                    "ai_confidence": 0.95,
                }
            )

        grouped = extract_with_gemini.consolidate_related_deals(deals)

        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["summary"], "$8-$13 Social Hour")
        self.assertEqual(grouped[0]["applies_days"], extract_with_gemini.DAYS)
        self.assertEqual(grouped[0]["time_window"], "Mon-Fri 3pm-6:30pm; Sat-Sun 1pm-5pm")
        self.assertEqual(len(grouped[0]["details"]), 5)
        self.assertTrue(grouped[0]["details"][0].startswith("$8:"))
        self.assertEqual(grouped[0]["details"][-1], "Offered in designated bar areas only")

    def test_bare_hour_range_counts_as_time_when_attached_to_days(self) -> None:
        self.assertTrue(extract_with_gemini.evidence_has_time(["MON-FRI 3-6:30"]))
        self.assertFalse(extract_with_gemini.evidence_has_time(["Choose any 3-6 appetizers"]))

    def test_social_hour_uses_the_happy_hour_filter(self) -> None:
        self.assertIn("happy_hour", crawl_deals.detect_tags("$8-$13 Social Hour"))

    def test_visual_queue_rotates_oldest_checked_pages_first(self) -> None:
        old = {
            "restaurant": {"name": "Old", "city": "Huntington Beach"},
            "page": {"url": "https://example.com/old", "confidence": "high"},
            "asset_candidates": [{"url": "https://example.com/old.jpg", "score": 5}],
            "last_visual_check": "2026-09-01T12:00:00Z",
        }
        recent = {
            "restaurant": {"name": "Recent", "city": "Huntington Beach"},
            "page": {"url": "https://example.com/recent", "confidence": "high"},
            "asset_candidates": [{"url": "https://example.com/recent.jpg", "score": 20}],
            "last_visual_check": "2026-09-15T12:00:00Z",
        }

        self.assertEqual(sorted([recent, old], key=extract_with_gemini.visual_priority)[0], old)

    def test_grouped_tier_details_remove_repeated_prices(self) -> None:
        deal = {
            "summary": "$8-$13 Social Hour",
            "details": ["$8: $8 Parmesan Garlic Fries; $8 Buffalo Cauliflower"],
            "applies_days": [],
            "source_evidence": [],
        }

        self.assertEqual(
            extract_with_gemini.consolidate_related_deals([deal])[0]["details"],
            ["$8: Parmesan Garlic Fries; Buffalo Cauliflower"],
        )

    def test_validity_does_not_repeat_days_already_in_schedule(self) -> None:
        schedule = "Mon-Fri 3pm-6:30pm; Sat-Sun 1pm-5pm"
        self.assertEqual(crawl_deals.validity_for(crawl_deals.DAYS, schedule), schedule)

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

        selected = extract_with_gemini.select_visual_pages(candidates)[:4]

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


class DealHistoryTests(unittest.TestCase):
    def deal(self, deal_id: str, summary: str, status: str, source: str = "https://example.com/specials") -> dict:
        return {
            "id": deal_id,
            "restaurant": "Example Grill",
            "city": "Huntington Beach",
            "source_url": source,
            "summary": summary,
            "status": status,
            "first_seen": "2026-09-01T12:00:00Z",
            "last_seen": "2026-09-14T12:00:00Z",
        }

    def test_price_tiers_share_a_stable_promotion_signature(self) -> None:
        grouped = self.deal("new", "$8-$13 Social Hour", "active")
        tier = self.deal("old", "$10 Social Hour Food and Drinks", "stale")

        self.assertEqual(crawl_deals.promotion_signature(grouped), "social hour")
        self.assertEqual(crawl_deals.promotion_signature(tier), "social hour")

    def test_replaced_stale_variants_leave_the_public_feed(self) -> None:
        now = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
        active = self.deal("new", "$8-$13 Social Hour", "active")
        stale = self.deal("old", "$10 Social Hour Food and Drinks", "stale")

        public, archived = crawl_deals.archive_replaced_variants([active, stale], now)

        self.assertEqual([item["id"] for item in public], ["new"])
        self.assertEqual(archived[0]["canonical_id"], "new")
        self.assertEqual(archived[0]["retired_reason"], "replaced_by_canonical_offer")

    def test_ambiguous_active_offers_do_not_retire_history(self) -> None:
        now = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
        first = self.deal("active-food", "$8 Happy Hour Food", "active")
        second = self.deal("active-drink", "$10 Happy Hour Drinks", "active")
        stale = self.deal("old", "$9 Happy Hour Special", "stale")

        public, archived = crawl_deals.archive_replaced_variants([first, second, stale], now)

        self.assertEqual({item["id"] for item in public}, {"active-food", "active-drink", "old"})
        self.assertEqual(archived, [])

    def test_only_newest_unmatched_stale_variant_stays_public(self) -> None:
        now = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
        older = self.deal("older", "Weekday Happy Hour", "stale")
        older["last_seen"] = "2026-09-10T12:00:00Z"
        newer = self.deal("newer", "Weekday Happy Hour Special", "stale")

        public, archived = crawl_deals.archive_replaced_variants([older, newer], now)

        self.assertEqual([item["id"] for item in public], ["newer"])
        self.assertEqual(archived[0]["canonical_id"], "newer")


if __name__ == "__main__":
    unittest.main()
