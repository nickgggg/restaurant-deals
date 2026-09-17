from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "crawler" / "places_config.json"
OUTPUT_PATH = ROOT / "docs" / "data" / "restaurants.json"
PLACES_URL = "https://places.googleapis.com/v1/places:searchNearby"
REQUEST_TIMEOUT = 15
DISCOVERY_VERSION = 4
RENDER_TIMEOUT = 25
MAX_DISCOVERY_RENDERS_PER_RUN = 8
RENDER_SLOTS = threading.Semaphore(2)
RENDER_BUDGET_LOCK = threading.Lock()
RENDER_ATTEMPTS = 0

SPECIAL_LINK = re.compile(
    r"\b(?:happy[\s_-]*hour|daily[\s_-]*specials?|weekday[\s_-]*specials?|"
    r"weekly[\s_-]*specials?|food[\s_-]*specials?|drink[\s_-]*specials?|"
    r"restaurant[\s_-]*specials?|lunch[\s_-]*specials?|weekday[\s_-]*(?:lunch|dinner)|"
    r"team[\s_-]*specials?|promotions?|coupons?|deals?)\b",
    re.I,
)
WEAK_SPECIAL_LINK = re.compile(r"\bspecials?\b", re.I)
PAGE_PROMO_SIGNAL = re.compile(
    r"(?:\d{1,3}%\s*off|\b(?:happy\s*hour|daily\s*specials?|weekday\s*specials?|"
    r"bogo|buy\s+one|get\s+one|half\s*price|kids\s+eat\s+free)\b)",
    re.I,
)
KNOWN_SPECIAL_PATHS = (
    "/happy-hour",
    "/specials",
    "/daily-specials",
    "/dailyspecials",
    "/weekday-specials",
    "/weekday-lunch",
    "/deals",
    "/promotions",
)
BLOCKED_HOSTS = {
    "facebook.com",
    "instagram.com",
    "linktr.ee",
    "opentable.com",
    "toasttab.com",
    "yelp.com",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def axis_values(start: float, end: float, step: float) -> Iterable[float]:
    current = start
    while current <= end + 0.000001:
        yield round(current, 6)
        current += step


def grid_points(config: dict[str, Any], areas: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    lat_step = config["grid"]["latitude_step"]
    lon_step = config["grid"]["longitude_step"]
    points: list[dict[str, Any]] = []
    for area in areas or config["areas"]:
        bounds = area["bounds"]
        for latitude in axis_values(bounds["south"], bounds["north"], lat_step):
            for longitude in axis_values(bounds["west"], bounds["east"], lon_step):
                points.append({"city": area["city"], "latitude": latitude, "longitude": longitude})
    return points


def api_request(api_key: str, point: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    body = json.dumps(
        {
            "includedPrimaryTypes": config["included_types"],
            "maxResultCount": 20,
            "rankPreference": "DISTANCE",
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": point["latitude"], "longitude": point["longitude"]},
                    "radius": config["grid"]["radius_meters"],
                }
            },
        }
    ).encode("utf-8")
    field_mask = ",".join(
        [
            "places.id",
            "places.displayName",
            "places.formattedAddress",
            "places.location",
            "places.types",
            "places.primaryType",
            "places.businessStatus",
            "places.websiteUri",
            "places.googleMapsUri",
            "places.nationalPhoneNumber",
            "places.regularOpeningHours",
        ]
    )
    request = Request(
        PLACES_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": field_mask,
            "User-Agent": "deal-radar/2.0 (+https://github.com/nickgggg/restaurant-deals)",
        },
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return json.loads(response.read()).get("places", [])
        except HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")[:500]
            last_error = RuntimeError(f"Places API HTTP {exc.code}: {message}")
            if exc.code not in {429, 500, 502, 503, 504} or (exc.code == 429 and "per day" in message.lower()):
                break
        except (OSError, URLError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(2**attempt)
    raise RuntimeError(str(last_error or "Places API request failed"))


def city_from_address(address: str, expected: str) -> str | None:
    if re.search(rf"\b{re.escape(expected)}\s*,\s*CA\b", address, re.I):
        return expected
    return expected if not address else None


def normalize_hours(raw: dict[str, Any] | None) -> dict[str, list[dict[str, str]]]:
    if not raw:
        return {}
    days = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
    result: dict[str, list[dict[str, str]]] = {}
    for period in raw.get("periods", []):
        opened = period.get("open", {})
        closed = period.get("close", {})
        if "day" not in opened or "hour" not in opened or "hour" not in closed:
            continue
        day = days[int(opened["day"])]
        result.setdefault(day, []).append(
            {
                "open": f'{int(opened["hour"]):02d}:{int(opened.get("minute", 0)):02d}',
                "close": f'{int(closed["hour"]):02d}:{int(closed.get("minute", 0)):02d}',
            }
        )
    return result


def normalize_place(place: dict[str, Any], fallback_city: str) -> dict[str, Any] | None:
    address = place.get("formattedAddress", "")
    city = city_from_address(address, fallback_city)
    name = place.get("displayName", {}).get("text")
    location = place.get("location", {})
    if not city or not name or not place.get("id"):
        return None
    return {
        "place_id": place["id"],
        "name": name,
        "city": city,
        "address": address,
        "phone": place.get("nationalPhoneNumber", ""),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "website_url": place.get("websiteUri", ""),
        "google_maps_url": place.get("googleMapsUri", ""),
        "business_status": place.get("businessStatus", "BUSINESS_STATUS_UNSPECIFIED"),
        "primary_type": place.get("primaryType", ""),
        "types": place.get("types", []),
        "hours": normalize_hours(place.get("regularOpeningHours")),
        "specials_pages": [],
    }


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.text: list[str] = []
        self._href: str | None = None
        self._anchor_parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._hidden += 1
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._anchor_parts)))
            self._href = None
            self._anchor_parts = []
        if tag in {"script", "style", "noscript", "svg"} and self._hidden:
            self._hidden -= 1

    def handle_data(self, data: str) -> None:
        if self._hidden:
            return
        clean = re.sub(r"\s+", " ", html.unescape(data)).strip()
        if clean:
            self.text.append(clean)
            if self._href:
                self._anchor_parts.append(clean)


def host_key(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme or "https", parsed.netloc, parsed.path or "/", "", parsed.query, ""))


def blocked_website(url: str) -> bool:
    host = host_key(url)
    return any(host == blocked or host.endswith(f".{blocked}") for blocked in BLOCKED_HOSTS)


def fetch_homepage(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "deal-radar/2.0 (+https://github.com/nickgggg/restaurant-deals)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        content_type = response.headers.get("Content-Type", "")
        if not any(kind in content_type for kind in ("html", "xml", "text/plain")):
            return ""
        return response.read(2_000_000).decode(response.headers.get_content_charset() or "utf-8", errors="replace")


def browser_executable() -> str | None:
    configured = os.environ.get("CHROME_PATH", "").strip()
    if configured and Path(configured).exists():
        return configured
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    for path in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ):
        if Path(path).exists():
            return path
    return None


def render_page(url: str) -> str:
    global RENDER_ATTEMPTS
    browser = browser_executable()
    if not browser:
        return ""
    with RENDER_BUDGET_LOCK:
        if RENDER_ATTEMPTS >= MAX_DISCOVERY_RENDERS_PER_RUN:
            return ""
        RENDER_ATTEMPTS += 1
    with RENDER_SLOTS, tempfile.TemporaryDirectory(prefix="restaurant-deals-chrome-") as profile:
        command = [
            browser,
            "--headless=new",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-extensions",
            "--disable-sync",
            "--hide-scrollbars",
            "--ignore-certificate-errors",
            "--virtual-time-budget=8000",
            f"--user-data-dir={profile}",
            "--dump-dom",
            url,
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=RENDER_TIMEOUT, check=False)
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            return output if "<" in output else ""
    return result.stdout if result.returncode == 0 and "<" in result.stdout else ""


def sitemap_urls(homepage: str) -> list[str]:
    root = f"{urlparse(homepage).scheme or 'https'}://{urlparse(homepage).netloc}"
    sitemap_locations = [urljoin(root, "/sitemap.xml")]
    try:
        robots = fetch_homepage(urljoin(root, "/robots.txt"))
        sitemap_locations.extend(
            match.group(1).strip()
            for match in re.finditer(r"^\s*Sitemap:\s*(\S+)", robots, re.I | re.M)
        )
    except Exception:
        pass

    found: list[str] = []
    seen_sitemaps: set[str] = set()
    queue = list(dict.fromkeys(sitemap_locations))[:3]
    while queue and len(seen_sitemaps) < 3:
        location = queue.pop(0)
        if location in seen_sitemaps or host_key(location) != host_key(homepage):
            continue
        seen_sitemaps.add(location)
        try:
            document = fetch_homepage(location)
            root_node = ET.fromstring(document)
        except Exception:
            continue
        locations = [(node.text or "").strip() for node in root_node.iter() if node.tag.rsplit("}", 1)[-1] == "loc"]
        for url in locations:
            if not url or host_key(url) != host_key(homepage):
                continue
            if url.lower().endswith((".xml", ".xml.gz")):
                if len(queue) + len(seen_sitemaps) < 3 and not url.lower().endswith(".gz"):
                    queue.append(url)
                continue
            if SPECIAL_LINK.search(urlparse(url).path.replace("-", " ").replace("_", " ")):
                found.append(canonical_url(url))
    return list(dict.fromkeys(found))[:12]


def discover_specials_pages(restaurant: dict[str, Any]) -> list[dict[str, Any]]:
    homepage = restaurant.get("website_url", "")
    if not homepage or blocked_website(homepage):
        return []
    try:
        page = fetch_homepage(homepage)
    except Exception:
        page = ""
    parser = LinkParser()
    parser.feed(page)
    home_host = host_key(homepage)
    candidates: dict[str, dict[str, Any]] = {}

    def collect_links(links: list[tuple[str, str]], base_url: str) -> None:
        for href, label in links:
            absolute = canonical_url(urljoin(base_url, href))
            if host_key(absolute) != home_host or urlparse(absolute).scheme not in {"http", "https"}:
                continue
            evidence = f"{label} {urlparse(absolute).path.replace('-', ' ').replace('_', ' ')}"
            if SPECIAL_LINK.search(evidence):
                candidates[absolute] = {
                    "url": absolute,
                    "label": label.strip() or "Specials",
                    "confidence": "high",
                    "discovered_via": "site_link",
                }
            elif WEAK_SPECIAL_LINK.search(evidence) and not re.search(r"menu|catering|gift", evidence, re.I):
                candidates[absolute] = {
                    "url": absolute,
                    "label": label.strip() or "Specials",
                    "confidence": "medium",
                    "discovered_via": "site_link",
                }

    collect_links(parser.links, homepage)
    for sitemap_url in sitemap_urls(homepage):
        candidates.setdefault(
            sitemap_url,
            {"url": sitemap_url, "label": "Specials", "confidence": "high", "discovered_via": "sitemap"},
        )

    thin_homepage = not page or len(parser.links) < 4 or len(" ".join(parser.text)) < 200
    if thin_homepage and not any(item["confidence"] == "high" for item in candidates.values()):
        rendered = render_page(homepage)
        if rendered:
            rendered_parser = LinkParser()
            rendered_parser.feed(rendered)
            collect_links(rendered_parser.links, homepage)
            parser.text.extend(rendered_parser.text)

    if not any(item["confidence"] == "high" for item in candidates.values()):
        root = f"{urlparse(homepage).scheme or 'https'}://{urlparse(homepage).netloc}"
        for path in KNOWN_SPECIAL_PATHS:
            probe_url = canonical_url(urljoin(root, path))
            if probe_url in candidates:
                continue
            try:
                probe_page = fetch_homepage(probe_url)
            except Exception:
                continue
            probe_parser = LinkParser()
            probe_parser.feed(probe_page)
            probe_text = " ".join(probe_parser.text)
            if PAGE_PROMO_SIGNAL.search(probe_text):
                candidates[probe_url] = {
                    "url": probe_url,
                    "label": path.strip("/").replace("-", " ").title(),
                    "confidence": "high",
                    "discovered_via": "known_path",
                }

    hub_urls = [
        item["url"]
        for item in candidates.values()
        if re.search(r"menus?[\s_-]*(?:and|&)?[\s_-]*specials?|specials?[\s_-]*menu", f'{item["label"]} {item["url"]}', re.I)
    ][:2]
    for hub_url in hub_urls:
        try:
            hub_page = fetch_homepage(hub_url)
        except Exception:
            continue
        hub_parser = LinkParser()
        hub_parser.feed(hub_page)
        collect_links(hub_parser.links, hub_url)

    visible_text = " ".join(parser.text)
    if SPECIAL_LINK.search(visible_text):
        absolute = canonical_url(homepage)
        candidates.setdefault(
            absolute,
            {"url": absolute, "label": "Website specials", "confidence": "medium", "discovered_via": "page_text"},
        )
    return sorted(candidates.values(), key=lambda item: (item["confidence"] != "high", item["url"]))[:8]


def add_specials_pages(restaurants: list[dict[str, Any]], limit: int | None = None) -> int:
    operational = [
        item
        for item in restaurants
        if item["business_status"] == "OPERATIONAL" and item.get("website_url") and not blocked_website(item["website_url"])
    ]
    operational.sort(
        key=lambda item: (
            parse_iso(item.get("specials_checked_at")) or datetime.min.replace(tzinfo=timezone.utc),
            item["name"].casefold(),
        )
    )
    if limit is not None:
        operational = operational[: max(0, limit)]
    checked_at = iso(utc_now())
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(discover_specials_pages, item): item for item in operational}
        for future in as_completed(futures):
            restaurant = futures[future]
            try:
                pages = future.result()
            except Exception as exc:
                restaurant["specials_error"] = f"{type(exc).__name__}: {exc}"
                continue
            if pages:
                restaurant["specials_pages"] = pages
                restaurant.pop("specials_error", None)
            restaurant["specials_checked_at"] = checked_at
            restaurant["specials_discovery_version"] = DISCOVERY_VERSION
    return len(operational)


def refresh_existing_sources(config: dict[str, Any], existing: dict[str, Any], area_status: dict[str, dict[str, Any]]) -> int:
    restaurants = existing.get("restaurants", [])
    if not restaurants:
        return 0
    checked = add_specials_pages(restaurants, int(config.get("max_source_sites_per_run", 12)))
    now = utc_now()
    payload = dict(existing)
    coverage = dict(existing.get("coverage", {}))
    coverage["official_websites"] = sum(bool(item.get("website_url")) for item in restaurants)
    coverage["specials_pages_found"] = sum(bool(item.get("specials_pages")) for item in restaurants)
    coverage["source_sites_checked"] = checked
    coverage["last_source_scan_at"] = iso(now)
    payload.update(
        {
            "discovery_version": DISCOVERY_VERSION,
            "generated_at": iso(now),
            "refresh_after": iso(next_refresh_at(config, area_status, now)),
            "area_status": area_status,
            "coverage": coverage,
            "restaurants": restaurants,
        }
    )
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return checked


def build_area_status(config: dict[str, Any], existing: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], bool]:
    saved = existing.get("area_status")
    migration = not isinstance(saved, dict)
    statuses = dict(saved or {})
    previous_cities = set(existing.get("coverage", {}).get("cities", []))
    previous_cities.update(item.get("city") for item in existing.get("restaurants", []) if item.get("city"))
    previous_generated = existing.get("generated_at")
    for area in config["areas"]:
        city = area["city"]
        if city in statuses:
            continue
        if city in previous_cities:
            statuses[city] = {
                "status": "active",
                "activated_at": previous_generated,
                "last_refreshed": previous_generated,
            }
        else:
            statuses[city] = {"status": "queued", "activated_at": None, "last_refreshed": None}
    return statuses, migration


def select_areas(config: dict[str, Any], existing: dict[str, Any], force: bool) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    statuses, migration = build_area_status(config, existing)
    limit = max(1, int(config.get("max_areas_per_run", 1)))
    queued = [area for area in config["areas"] if statuses[area["city"]]["status"] == "queued"]
    active = [area for area in config["areas"] if statuses[area["city"]]["status"] == "active"]
    if force:
        return (queued or active)[:limit], statuses
    if migration and queued:
        return queued[:limit], statuses

    now = utc_now()
    activation_interval = timedelta(days=int(config.get("queue_activation_days", 7)))
    activation_dates = [
        parse_iso(status.get("activated_at"))
        for status in statuses.values()
        if status.get("status") == "active"
    ]
    latest_activation = max((value for value in activation_dates if value), default=None)
    activation_due = latest_activation + activation_interval if latest_activation else None
    if queued and (not activation_due or now.date() >= activation_due.date()):
        return queued[:limit], statuses

    refresh_interval = timedelta(days=int(config.get("area_refresh_days", 35)))
    due = sorted(
        active,
        key=lambda area: parse_iso(statuses[area["city"]].get("last_refreshed")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    due = [
        area
        for area in due
        if not parse_iso(statuses[area["city"]].get("last_refreshed"))
        or now - parse_iso(statuses[area["city"]]["last_refreshed"]) >= refresh_interval
    ]
    return due[:limit], statuses


def next_refresh_at(config: dict[str, Any], statuses: dict[str, dict[str, Any]], now: datetime) -> datetime:
    candidates: list[datetime] = []
    queued = any(status.get("status") == "queued" for status in statuses.values())
    activation_dates = [parse_iso(status.get("activated_at")) for status in statuses.values() if status.get("status") == "active"]
    latest_activation = max((value for value in activation_dates if value), default=None)
    if queued:
        candidates.append((latest_activation or now) + timedelta(days=int(config.get("queue_activation_days", 7))))
    for status in statuses.values():
        refreshed = parse_iso(status.get("last_refreshed"))
        if status.get("status") == "active" and refreshed:
            candidates.append(refreshed + timedelta(days=int(config.get("area_refresh_days", 35))))
    return min(candidates, default=now + timedelta(days=1))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    config = load_json(CONFIG_PATH, {})
    if not config:
        raise RuntimeError(f"Missing discovery configuration: {CONFIG_PATH}")
    existing = load_json(OUTPUT_PATH, {})
    selected_areas, area_status = select_areas(config, existing, args.force)
    if not selected_areas:
        checked = refresh_existing_sources(config, existing, area_status)
        print(f"No city is due for Places refresh; checked {checked} restaurant websites instead")
        return 0

    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GOOGLE_PLACES_API_KEY is not available to this GitHub Actions workflow")

    points = grid_points(config, selected_areas)
    cell_limit = int(config.get("max_map_cells_per_area", 180))
    for area in selected_areas:
        area_cells = len(grid_points(config, [area]))
        if area_cells > cell_limit:
            raise RuntimeError(f'{area["city"]} requires {area_cells} map cells, above the configured limit of {cell_limit}')
    found: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for index, point in enumerate(points, start=1):
        try:
            for raw in api_request(api_key, point, config):
                normalized = normalize_place(raw, point["city"])
                if normalized:
                    found[normalized["place_id"]] = normalized
        except Exception as exc:
            failures.append(f'{point["latitude"]},{point["longitude"]}: {exc}')
            if "per day" in str(exc).lower() and "429" in str(exc):
                print("Daily Places quota is exhausted; deferring this city without changing restaurant data")
                return 0
        if index % 25 == 0:
            print(f"Scanned {index}/{len(points)} map cells; found {len(found)} restaurants")

    success_count = len(points) - len(failures)
    if success_count < max(1, int(len(points) * 0.8)):
        sample = failures[0] if failures else "unknown error"
        raise RuntimeError(f"Places discovery failed for too many map cells ({success_count}/{len(points)} succeeded): {sample}")

    refreshed_restaurants = list(found.values())
    checked_sites = add_specials_pages(refreshed_restaurants)
    now = utc_now()
    selected_cities = {area["city"] for area in selected_areas}
    retained = [item for item in existing.get("restaurants", []) if item.get("city") not in selected_cities]
    restaurants = sorted([*retained, *refreshed_restaurants], key=lambda item: (item["name"].casefold(), item["address"].casefold()))
    for city in selected_cities:
        status = area_status[city]
        status["status"] = "active"
        status["activated_at"] = status.get("activated_at") or iso(now)
        status["last_refreshed"] = iso(now)
        status["restaurant_count"] = sum(item.get("city") == city for item in restaurants)
    active_cities = [area["city"] for area in config["areas"] if area_status[area["city"]]["status"] == "active"]
    queued_cities = [area["city"] for area in config["areas"] if area_status[area["city"]]["status"] == "queued"]
    active_areas = [area for area in config["areas"] if area["city"] in active_cities]
    payload = {
        "discovery_version": DISCOVERY_VERSION,
        "generated_at": iso(now),
        "refresh_after": iso(next_refresh_at(config, area_status, now)),
        "area_status": area_status,
        "coverage": {
            "cities": active_cities,
            "queued_cities": queued_cities,
            "last_scanned_cities": sorted(selected_cities),
            "map_cells": len(grid_points(config, active_areas)),
            "last_scan_map_cells": len(points),
            "successful_map_cells": success_count,
            "failed_map_cells": len(failures),
            "restaurant_count": len(restaurants),
            "operational_count": sum(item["business_status"] == "OPERATIONAL" for item in restaurants),
            "closed_count": sum(item["business_status"] in {"CLOSED_TEMPORARILY", "CLOSED_PERMANENTLY"} for item in restaurants),
            "official_websites": sum(bool(item.get("website_url")) for item in restaurants),
            "specials_pages_found": sum(bool(item.get("specials_pages")) for item in restaurants),
            "source_sites_checked": checked_sites,
            "last_source_scan_at": iso(now),
        },
        "restaurants": restaurants,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f'Wrote {len(restaurants)} restaurants after scanning {", ".join(sorted(selected_cities))}')
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Restaurant discovery failed: {exc}", file=sys.stderr)
        sys.exit(1)
