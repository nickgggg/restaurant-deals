from __future__ import annotations

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
RESTAURANTS_PATH = ROOT / "docs" / "data" / "restaurants.json"
SOURCES_PATH = ROOT / "crawler" / "sources.json"
DEALS_PATH = ROOT / "docs" / "data" / "deals.json"
OUTPUT_PATH = ROOT / "docs" / "data" / "logos.json"
LOGO_DIR = ROOT / "docs" / "logos"
MAX_SITES_PER_RUN = 20
REFRESH_AFTER_DAYS = 90
RETRY_MISSING_AFTER_DAYS = 14
REQUEST_TIMEOUT = 15
MAX_HTML_BYTES = 1_500_000
MAX_IMAGE_BYTES = 600_000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def host_key(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def homepage(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme or 'https'}://{parsed.netloc}/"


class IconParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.icons: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "link":
            return
        values = {key.lower(): (value or "") for key, value in attrs}
        rel = values.get("rel", "").lower()
        href = values.get("href", "").strip()
        if "icon" not in rel or not href or href.startswith("data:"):
            return
        sizes = [int(value) for value in re.findall(r"(\d+)\s*x\s*\d+", values.get("sizes", ""), re.I)]
        score = 300 if "apple-touch-icon" in rel else 200
        score += min(max(sizes, default=0), 512)
        if "mask-icon" in rel:
            score -= 150
        self.icons.append({"url": urljoin(self.page_url, href), "score": score})


def icon_candidates(page_url: str, page_html: str) -> list[str]:
    parser = IconParser(page_url)
    parser.feed(page_html)
    ranked = sorted(parser.icons, key=lambda item: item["score"], reverse=True)
    urls = [item["url"] for item in ranked]
    urls.append(urljoin(page_url, "/favicon.ico"))
    return list(dict.fromkeys(urls))[:5]


def request_bytes(url: str, accept: str, limit: int) -> tuple[bytes, str, str]:
    request = Request(
        url,
        headers={
            "User-Agent": "restaurant-deals-bot/1.0 (+https://github.com/nickgggg/restaurant-deals)",
            "Accept": accept,
        },
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        length = int(response.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("asset is too large")
        content = response.read(limit + 1)
        if len(content) > limit:
            raise ValueError("asset is too large")
        return content, response.headers.get_content_type(), response.geturl()


def image_extension(content: bytes, content_type: str) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if content.startswith(b"\x00\x00\x01\x00"):
        return "ico"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "webp"
    return {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico", "image/webp": "webp"}.get(content_type)


def safe_filename(host: str, extension: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-") or "restaurant"
    return f"{stem}.{extension}"


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def website_inventory() -> dict[str, str]:
    websites: dict[str, str] = {}
    restaurants = load_json(RESTAURANTS_PATH, {}).get("restaurants", [])
    sources = load_json(SOURCES_PATH, [])
    for item in [*restaurants, *sources]:
        url = item.get("website_url") or item.get("url") or ""
        host = host_key(url)
        if host and host not in websites:
            websites[host] = homepage(url)
    return websites


def active_deal_hosts() -> set[str]:
    restaurants = load_json(RESTAURANTS_PATH, {}).get("restaurants", [])
    official_by_restaurant = {
        (item.get("name", "").lower(), item.get("city", "").lower()): host_key(item.get("website_url", ""))
        for item in restaurants
    }
    hosts: set[str] = set()
    for deal in load_json(DEALS_PATH, {}).get("deals", []):
        if deal.get("status") != "active":
            continue
        key = (deal.get("restaurant", "").lower(), deal.get("city", "").lower())
        host = official_by_restaurant.get(key) or host_key(deal.get("source_url", ""))
        if host:
            hosts.add(host)
    return hosts


def fetch_logo(host: str, page_url: str) -> dict[str, Any]:
    html_bytes, _, final_url = request_bytes(page_url, "text/html,application/xhtml+xml", MAX_HTML_BYTES)
    page_html = html_bytes.decode("utf-8", errors="replace")
    errors: list[str] = []
    for icon_url in icon_candidates(final_url, page_html):
        try:
            content, content_type, resolved_url = request_bytes(icon_url, "image/png,image/webp,image/jpeg,image/gif,image/x-icon,*/*;q=0.2", MAX_IMAGE_BYTES)
            extension = image_extension(content, content_type)
            if extension and len(content) >= 32:
                return {"content": content, "extension": extension, "source_url": resolved_url}
        except (HTTPError, URLError, OSError, ValueError) as exc:
            errors.append(str(getattr(exc, "reason", exc)))
    raise RuntimeError(errors[-1] if errors else "no supported site icon found")


def main() -> int:
    now = utc_now()
    websites = website_inventory()
    active_hosts = active_deal_hosts()
    previous = load_json(OUTPUT_PATH, {})
    logos = previous.get("logos", {})
    checks = previous.get("checks", {})
    force = os.environ.get("LOGO_CACHE_FORCE") == "1"

    def needs_check(host: str) -> bool:
        if force:
            return True
        check = checks.get(host, {})
        days = RETRY_MISSING_AFTER_DAYS if check.get("status") == "missing" else REFRESH_AFTER_DAYS
        return parse_iso(check.get("checked_at")) < now - timedelta(days=days)

    queue = sorted(
        websites.items(),
        key=lambda item: (item[0] not in active_hosts, item[0] in logos, parse_iso(checks.get(item[0], {}).get("checked_at"))),
    )
    queue = [item for item in queue if needs_check(item[0])][:MAX_SITES_PER_RUN]

    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, Any] | Exception] = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_logo, host, url): host for host, url in queue}
        for future in as_completed(futures):
            host = futures[future]
            try:
                results[host] = future.result()
            except Exception as exc:
                results[host] = exc

    successes = 0
    for host, _ in queue:
        result = results.get(host)
        if isinstance(result, dict):
            filename = safe_filename(host, result["extension"])
            (LOGO_DIR / filename).write_bytes(result["content"])
            logos[host] = {
                "path": f"logos/{filename}",
                "source_url": result["source_url"],
                "checked_at": iso(now),
            }
            checks[host] = {"checked_at": iso(now), "status": "ok"}
            successes += 1
        else:
            checks[host] = {
                "checked_at": iso(now),
                "status": "missing",
                "error": str(result or "unknown error")[:180],
            }

    payload = {
        "generated_at": iso(now),
        "sites_per_run": MAX_SITES_PER_RUN,
        "logos": {host: logos[host] for host in sorted(logos) if host in websites},
        "checks": {host: checks[host] for host in sorted(checks) if host in websites},
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"Checked {len(queue)} sites; cached {successes} icons; {len(payload['logos'])} available total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
