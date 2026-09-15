# Restaurant Deals

A lightweight, serverless tracker for local restaurant specials, beginning with Huntington Beach and Fountain Valley and designed to expand.

The project runs entirely on GitHub:

- GitHub Actions runs the crawler every day at 14:37 UTC, plus a 16:17 UTC fallback that exits when the feed is less than eight hours old and manual `workflow_dispatch` runs.
- The crawler writes normalized JSON to `docs/data/deals.json`.
- GitHub Pages serves the static frontend from `docs/`.
- Google Places builds a staggered nearby-restaurant inventory and supplies current business details.
- Navigation links, sitemaps, and a small set of common specials routes discover candidate pages.
- Headless Chrome renders a bounded batch of JavaScript-heavy pages only when ordinary HTML is insufficient.
- Likely specials images and PDFs get a bounded Gemini vision fallback only when text extraction remains weak; shared schedules and related price tiers are normalized into coherent offers.
- Twelve existing restaurant websites rotate through source discovery each run without making additional Places requests.
- Gemini extracts structured offers from likely specials pages; source-evidence checks keep uncertain results unpublished.
- The crawler uses only the Python standard library, so there are no package installs.
- MapLibre and OpenFreeMap provide the optional custom map without an API key.
- A service worker and web app manifest make the site installable and keep the latest successful deal feed available offline.

## Current Scope

Curated sources live in `crawler/sources.json`. Discovery scans one configured city at a time, finds official websites and likely specials pages, and writes the merged restaurant inventory to `docs/data/restaurants.json`. A queued city activates every seven days; active cities are rescanned after 35 days. Each run is capped at one city and 180 Places map cells. Gemini extracts structured offers from candidate pages; exact source-evidence checks, schedule validation, and value-signal rules keep generic menu content and unsupported claims out of the feed.

The discovery job requires the repository Actions secret `GOOGLE_PLACES_API_KEY`, with Places API (New) enabled. Its map bounds and refresh interval live in `crawler/places_config.json`.

AI extraction requires the Actions secret `GEMINI_API_KEY`. It processes at most 30 changed text pages and four visually weak pages per daily run. It checks no more than 16 visual candidates to find those four uncached pages; cache hits do not consume Gemini calls. Each visual page is capped at three assets and 11 MB total. Text and visual content fingerprints are stored in `docs/data/ai_extractions.json`, so unchanged pages, images, and PDFs reuse cached results. City activation does not raise those daily caps.

## What It Finds

The candidate detector intentionally stays broad instead of hard-coding exact offers. It tags text matching patterns such as:

- Percent discounts like `20% off`, `1/2 off`, or `% off`
- Dollar amounts like `$5`, `$10 lunch`, `$2 off`, or `2 for $12`
- BOGO and buy-one-get-one phrasing
- Happy hour and late-night specials
- Weekday specials like Taco Tuesday, Wine Wednesday, weekend brunch, daily specials, and all-day offers
- Free item, combo, discount, coupon, promo, special, offer, and rewards language

## Data Shape

Each deal includes:

- `restaurant`
- `city`
- `source_url`
- `candidate_text`
- `tags`
- `first_seen`
- `last_seen`
- `status`
- `is_stale`
- `days_since_seen`

## Staleness Handling

A deal gets a stable ID from its source URL and normalized candidate text. When the same candidate is found again, `first_seen` is preserved and `last_seen` updates.

If a previously seen deal is missing from a later crawl, it stays in the JSON as `status: "stale"` instead of disappearing immediately. Stale deals are retained for up to 90 days, then dropped.

## GitHub Pages

Enable Pages in the repo settings:

1. Go to Settings -> Pages.
2. Set source to `Deploy from a branch`.
3. Choose branch `main` and folder `/docs`.
4. Save.

Once enabled, the frontend will read the latest `docs/data/deals.json` and show active/stale filters, city filtering, search, source links, and failed-source notices.

The frontend also supports current-time filtering, device-only favorites, restorable filter URLs, restaurant sharing, and list/map views. Favorites use browser storage and may disappear when a private-browsing session closes.

The site can be installed from supported browser menus or added to an iPhone home screen from Safari's Share menu. Its interface and latest successful JSON feeds are cached on the device. When the network is unavailable, the site labels the cached feed with its saved timestamp and refreshes automatically after connectivity returns.

## Analytics

The site includes an optional Umami Cloud integration and useful event names for filters, sorting, map use, favorites, sharing, and restaurant actions. Analytics stays completely off while `docs/analytics-config.js` has an empty `websiteId`.

To enable it, create a website for `nickgggg.github.io` in Umami, then put the public website ID in `docs/analytics-config.js`. No Umami API key belongs in the repository.

## Deal Reports

Each restaurant has a report action that opens a prefilled GitHub issue. `.github/workflows/triage-deal-report.yml` validates structured reports, labels the issue, and replies automatically. Trusted reporter URLs move to the front of an immediate evidence-checked crawl, which posts a verified or needs-review result back to the issue. Public reports stay in a review-only source queue so an anonymous visitor cannot spend API quota or change published data. Evidence-backed code or data fixes remain visible in the issue before it is closed.

## Running Manually

From the Actions tab, run the `Update deals` workflow manually whenever you want an immediate refresh.
