from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "crawler" / "sources.json"
RESTAURANTS_PATH = ROOT / "docs" / "data" / "restaurants.json"
AI_EXTRACTIONS_PATH = ROOT / "docs" / "data" / "ai_extractions.json"
OUTPUT_PATH = ROOT / "docs" / "data" / "deals.json"
STALE_AFTER_DAYS = 21
DROP_AFTER_DAYS = 90
REQUEST_TIMEOUT = 25
CRAWLER_VERSION = 18

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_LABELS = {
    "monday": "Mon",
    "tuesday": "Tue",
    "wednesday": "Wed",
    "thursday": "Thu",
    "friday": "Fri",
    "saturday": "Sat",
    "sunday": "Sun",
}

TAG_PATTERNS = {
    "percent_off": re.compile(r"\b(?:\d{1,3}\s*)?%\s*off\b|\b\d{1,3}\s*percent\s*off\b|\bhalf\s+price\b|\b1/2\s*off\b", re.I),
    "dollar_amount": re.compile(r"\$\s?\d+(?:\.\d{2})?|\b\d+(?:\.\d{2})?\s*dollars?\b", re.I),
    "bogo": re.compile(r"\b(?:bogo|buy\s+one(?:,?\s+get\s+one)?|two\s+for|2\s+for|2-4-1)\b", re.I),
    "happy_hour": re.compile(r"\b(?:happy|social)\s+hour\b|\blate\s+night\b", re.I),
    "weekday_special": re.compile(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekday|weekend|daily|all\s+day|taco\s+tuesday|wine\s+wednesday|brunch)\b", re.I),
    "free": re.compile(r"\bfree\b|\bcomplimentary\b", re.I),
    "deal_language": re.compile(r"\b(?:deal|deals|special|specials|discount|coupon|promo|promotion|offer|reward|rewards|limited\s+time|save|savings|starting\s+at)\b", re.I),
}

NOISE_PATTERNS = [
    re.compile(r"^(skip to|copyright|privacy policy|terms|accessibility|do not sell)", re.I),
    re.compile(r"^(facebook|instagram|twitter|x|youtube|tiktok|home|menu|menus|order|order online|reserve a table|book a table|contact us|careers|gallery|directions)$", re.I),
    re.compile(r"\b(?:cookie preferences|privacy policy|powered by|yelp rating|read more|grubhub|doordash|uber eats|postmates|nutritional information|party trays|at your grocer|your cart is empty)\b", re.I),
    re.compile(r"^[A-Z][a-z]+\s+[A-Z]\.$"),
]
LOW_VALUE_SUMMARY = re.compile(
    r"^(?:fountain valley coupon!?|happy hour specials|lunch specials|dinner specialties|all dinners include|our entire menu is available|corkage fee|add chicken|add salad|add soup|book mini golf|food & drinks|chef-driven|playground menus)",
    re.I,
)
PROMO_CONTEXT = re.compile(
    r"\b(?:off|free|bogo|2-4-1|buy one|half price|happy hour|social hour|special|specials|deal|deals|coupon|limited time|starting at|all day|weekday|weekend|taco tuesday|wine wednesday|kids eat free|with purchase|not valid|valid with coupon|available carry out|dine in only|each)\b",
    re.I,
)
VALIDITY_CONTEXT = re.compile(
    r"\b(?:happy hour|social hour|daily|every day|all day|weekday|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun|am|pm|valid|dine in|carry out|coupon|limited time|after|before|holidays)\b",
    re.I,
)
TIME_RANGE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|to|thru|through|until|\u2013|\u2014)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm|close)\b",
    re.I,
)
FOOD_TERMS = re.compile(r"\b(?:app|apps|appetizer|bite|brunch|breakfast|lunch|dinner|meal|taco|tacos|pizza|wing|wings|burger|sandwich|salad|pasta|chicken|steak|fish|seafood|soup|dessert|fries|entree|platter|combo|soda|meatloaf|spaghetti|prime rib|nachos|calamari|sliders)\b", re.I)
DRINK_TERMS = re.compile(r"\b(?:beer|beers|wine|wines|vino|cocktail|cocktails|margarita|margaritas|martini|martinis|drink|drinks|bar|well|draft|pint|pints|mug|mugs|pitcher|pitchers|sangria|tequila|vodka|whiskey|bourbon|beverage)\b", re.I)


class Candidate(TypedDict):
    text: str
    summary: str
    details: list[str]


@dataclass(frozen=True)
class Source:
    name: str
    city: str
    url: str
    notes: str = ""
    location: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg", "iframe"}:
            self._hidden_depth += 1
        if tag in {"br", "p", "div", "li", "section", "article", "h1", "h2", "h3", "h4", "tr"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "iframe"} and self._hidden_depth:
            self._hidden_depth -= 1
        if tag in {"p", "div", "li", "section", "article", "h1", "h2", "h3", "h4", "tr"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self._parts.append(data)

    def lines(self) -> list[str]:
        return [normalize_line(line) for line in re.split(r"[\n\r]+", html.unescape("\n".join(self._parts))) if normalize_line(line)]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return fallback


def normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(line)).strip(" -|\u2022\t")


def restaurant_key(name: str, city: str) -> str:
    clean_name = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    clean_name = re.sub(r"\b(?:restaurant|bar|grill|cafe|the)\b", "", clean_name)
    return f'{re.sub(r"\s+", " ", clean_name).strip()}|{city.lower()}'


def load_restaurant_inventory() -> list[dict[str, Any]]:
    try:
        payload = json.loads(RESTAURANTS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return payload.get("restaurants", [])


def load_ai_sources() -> list[dict[str, Any]]:
    try:
        payload = json.loads(AI_EXTRACTIONS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return payload.get("sources", [])


def load_ai_summary() -> dict[str, Any]:
    try:
        payload = json.loads(AI_EXTRACTIONS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload.get("summary", {})


def inventory_location(restaurant: dict[str, Any], fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    location = dict(fallback or {})
    for key in ("address", "phone", "latitude", "longitude", "hours", "google_maps_url", "business_status", "place_id"):
        source_key = "place_id" if key == "place_id" else key
        value = restaurant.get(source_key)
        if value not in (None, "", {}, []):
            location[key] = value
    return location


def website_host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def load_sources() -> list[Source]:
    raw_sources = [raw for raw in json.loads(SOURCES_PATH.read_text()) if not raw.get("retired")]
    inventory = load_restaurant_inventory()
    inventory_by_key = {restaurant_key(item["name"], item["city"]): item for item in inventory}
    inventory_by_host = {
        website_host(item.get("website_url", "")): item
        for item in inventory
        if website_host(item.get("website_url", ""))
    }
    sources: list[Source] = []
    manual_keys: set[str] = set()
    seen_urls: set[str] = set()

    for raw in raw_sources:
        key = restaurant_key(raw["name"], raw["city"])
        manual_keys.add(key)
        seen_urls.add(raw["url"].rstrip("/"))
        matched = inventory_by_key.get(key) or inventory_by_host.get(website_host(raw["url"]))
        if matched:
            raw = dict(raw)
            raw["location"] = inventory_location(matched, raw.get("location"))
        sources.append(Source(**raw))

    for raw in load_ai_sources():
        key = restaurant_key(raw["name"], raw["city"])
        url = raw["url"].rstrip("/")
        if key in manual_keys or url in seen_urls:
            continue
        seen_urls.add(url)
        sources.append(Source(**raw))

    for restaurant in inventory:
        key = restaurant_key(restaurant["name"], restaurant["city"])
        if key in manual_keys or restaurant.get("business_status") != "OPERATIONAL":
            continue
        for page in restaurant.get("specials_pages", []):
            url = page.get("url", "").rstrip("/")
            if not page.get("publish") or page.get("confidence") != "high" or not url or url in seen_urls:
                continue
            seen_urls.add(url)
            sources.append(
                Source(
                    name=restaurant["name"],
                    city=restaurant["city"],
                    url=page["url"],
                    notes="Official specials page found during restaurant discovery",
                    location=inventory_location(restaurant),
                    options={"discovered": True, "strict": True, "exclude_menu_prices": True, "max_deals": 8},
                )
            )
    return sources


def load_existing() -> dict[str, dict]:
    if not OUTPUT_PATH.exists():
        return {}
    try:
        raw = json.loads(OUTPUT_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    if raw.get("crawler_version") != CRAWLER_VERSION:
        return {}
    return {deal["id"]: deal for deal in raw.get("deals", []) if "id" in deal}


def fetch_html(source: Source) -> str:
    request = Request(
        source.url,
        headers={
            "User-Agent": "restaurant-deals-bot/1.0 (+https://github.com/nickgggg/restaurant-deals)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except HTTPError as exc:
            if exc.code == 403:
                raise RuntimeError(f"HTTP {exc.code}") from exc
            last_error = exc
        except (OSError, URLError) as exc:
            last_error = exc
        if attempt < 2:
            time.sleep(2**attempt)
    raise RuntimeError(str(getattr(last_error, "reason", last_error)))


def clean_text(page_html: str) -> list[str]:
    parser = VisibleTextParser()
    parser.feed(page_html)
    return [line for line in parser.lines() if len(line) >= 3 and not any(pattern.search(line) for pattern in NOISE_PATTERNS)]


def detect_tags(text: str) -> list[str]:
    return [tag for tag, pattern in TAG_PATTERNS.items() if pattern.search(text)]


def dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = normalize_line(str(item))
        key = re.sub(r"\W+", " ", clean.lower()).strip()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return result


def extract_days(text: str) -> list[str]:
    lower = text.lower()
    aliases = {
        "mon": "monday", "monday": "monday", "tue": "tuesday", "tues": "tuesday", "tuesday": "tuesday",
        "wed": "wednesday", "wednesday": "wednesday", "thu": "thursday", "thur": "thursday", "thurs": "thursday", "thursday": "thursday",
        "fri": "friday", "friday": "friday", "sat": "saturday", "saturday": "saturday", "sun": "sunday", "sunday": "sunday",
    }
    if re.search(r"\b(?:daily|every day|everyday)\b", lower):
        return DAYS
    if re.search(r"\b(?:weekday|weekdays|monday\s*(?:-|to|thru|through|\u2013|\u2014)\s*friday|mon\s*(?:-|to|thru|through|\u2013|\u2014)\s*fri|m\s*-\s*f)\b", lower):
        return DAYS[:5]
    if re.search(r"\b(?:weekend|weekends)\b", lower):
        return DAYS[5:]
    found: list[str] = []
    day_pattern = r"(mon(?:day)?|tues?|tuesday|wed(?:nesday)?|thu(?:r|rs|rsday)?|thursday|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"
    range_pattern = re.compile(rf"\b{day_pattern}\s*(?:-|to|thru|through|\u2013|\u2014)\s*{day_pattern}\b", re.I)
    for match in range_pattern.finditer(lower):
        start = aliases.get(match.group(1))
        end = aliases.get(match.group(2))
        if start and end:
            si, ei = DAYS.index(start), DAYS.index(end)
            span = DAYS[si : ei + 1] if si <= ei else DAYS[si:] + DAYS[: ei + 1]
            found.extend(day for day in span if day not in found)
    for match in re.finditer(rf"\b{day_pattern}\b", lower, re.I):
        day = aliases.get(match.group(1))
        if day and day not in found:
            found.append(day)
    return found


def normalize_time(hour: str, minute: str | None, period: str | None) -> str:
    if period and period.lower() == "close":
        return "close"
    suffix = f" {period.upper()}" if period else ""
    return f"{int(hour)}:{minute}{suffix}" if minute else f"{int(hour)}{suffix}"


def extract_time_window(text: str) -> str | None:
    match = TIME_RANGE.search(text)
    if not match:
        if re.search(r"\bafter\s+5\s*pm\b", text, re.I):
            return "After 5 PM"
        return None
    start_period = match.group(3) or (match.group(6) if match.group(6).lower() != "close" else None)
    return f"{normalize_time(match.group(1), match.group(2), start_period)} - {normalize_time(match.group(4), match.group(5), match.group(6))}"


def day_label(days: list[str]) -> str | None:
    if not days:
        return None
    if days == DAYS:
        return "Every day"
    if days == DAYS[:5]:
        return "Mon-Fri"
    if days == DAYS[5:]:
        return "Weekend"
    return ", ".join(DAY_LABELS[day] for day in days)


def category_for(text: str) -> list[str]:
    categories: list[str] = []
    if FOOD_TERMS.search(text):
        categories.append("food")
    if DRINK_TERMS.search(text):
        categories.append("drink")
    return categories or ["general"]


def extract_month_days(text: str) -> list[int]:
    found = {
        int(value)
        for value in re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)\s+of\s+(?:each|every|the)\s+month\b", text, re.I)
        if 1 <= int(value) <= 31
    }
    found.update(
        int(value)
        for value in re.findall(r"\b(?:every|each)\s+(\d{1,2})(?:st|nd|rd|th)(?:\s+of\s+(?:the\s+)?month)?\b", text, re.I)
        if 1 <= int(value) <= 31
    )
    return sorted(found)


def ordinal(value: int) -> str:
    suffix = "th" if 10 <= value % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def validity_for(days: list[str], time_window: str | None, month_days: list[int] | None = None) -> str:
    monthly = f"Every month on the {', '.join(ordinal(value) for value in month_days)}" if month_days else None
    schedule_includes_days = bool(time_window and extract_days(time_window))
    parts = [part for part in [monthly, None if schedule_includes_days else day_label(days), time_window] if part]
    return ", ".join(parts) if parts else "Check source"


def deal_id(source: Source, text: str) -> str:
    stable = re.sub(r"\s+", " ", text.lower()).strip()
    return hashlib.sha1(f"{source.name}|{source.url}|{stable}".encode("utf-8")).hexdigest()[:16]


def build_deal(source: Source, candidate: Candidate, now: datetime, existing: dict[str, dict]) -> dict:
    text = candidate["text"]
    tags = detect_tags(text)
    days = extract_days(text)
    month_days = extract_month_days(text)
    time_window = extract_time_window(text)
    did = deal_id(source, text)
    previous = existing.get(did, {})
    return {
        "id": did,
        "restaurant": source.name,
        "city": source.city,
        "source_url": source.url,
        "source_notes": source.notes,
        "location": source.location,
        "candidate_text": text,
        "summary": candidate["summary"],
        "details": candidate["details"],
        "tags": tags,
        "categories": category_for(text),
        "applies_days": days,
        "applies_month_days": month_days,
        "time_window": time_window,
        "validity": validity_for(days, time_window, month_days),
        "first_seen": previous.get("first_seen") or iso(now),
        "last_seen": iso(now),
        "status": "active",
        "is_stale": False,
        "days_since_seen": 0,
    }


def build_static_deal(source: Source, raw: dict[str, Any], now: datetime, existing: dict[str, dict]) -> dict:
    details = dedupe(raw.get("details", []))
    text = normalize_line(" ".join([raw["summary"], *details]))
    did = deal_id(source, text)
    previous = existing.get(did, {})
    days = raw["applies_days"] if "applies_days" in raw else extract_days(text)
    month_days = raw.get("applies_month_days") or extract_month_days(text)
    time_window = raw["time_window"] if "time_window" in raw else extract_time_window(text)
    tags = dedupe([*raw.get("tags", []), *detect_tags(text)])
    categories = raw.get("categories") or category_for(text)
    return {
        "id": did,
        "restaurant": source.name,
        "city": source.city,
        "source_url": source.url,
        "source_notes": source.notes,
        "location": source.location,
        "candidate_text": text,
        "summary": raw["summary"],
        "details": details,
        "tags": tags,
        "categories": categories,
        "applies_days": days,
        "applies_month_days": month_days,
        "time_window": time_window,
        "validity": raw.get("validity") or validity_for(days, time_window, month_days),
        "first_seen": previous.get("first_seen") or iso(now),
        "last_seen": iso(now),
        "status": "active",
        "is_stale": False,
        "days_since_seen": 0,
        "valid_through": raw.get("valid_through"),
        "source_evidence": raw.get("source_evidence", []),
        "ai_confidence": raw.get("ai_confidence"),
    }


def starts_new_deal(line: str) -> bool:
    return bool(
        re.match(r"^\$ ?\d", line)
        or re.match(r"^(?:free|happy hour|taco tuesday|wine wednesday|kids eat free|meal deals|lunch specials|daily specials)\b", line, re.I)
        or TAG_PATTERNS["percent_off"].search(line)
        or TAG_PATTERNS["bogo"].search(line)
    )


def candidate_windows(lines: list[str]) -> Iterable[Candidate]:
    seen: set[str] = set()
    for index, line in enumerate(lines):
        summary = normalize_line(line)[:180]
        if not (4 <= len(summary) <= 180):
            continue
        details: list[str] = []
        for neighbor in lines[max(0, index - 3) : index] + lines[index + 1 : min(len(lines), index + 6)]:
            clean = normalize_line(neighbor)
            if clean == summary or any(pattern.search(clean) for pattern in NOISE_PATTERNS):
                continue
            if starts_new_deal(clean) and details:
                break
            if len(clean) <= 140 and (VALIDITY_CONTEXT.search(clean) or PROMO_CONTEXT.search(clean) or len(clean.split()) <= 10):
                details.append(clean)
        text = normalize_line(" ".join([summary, *details]))
        tags = detect_tags(text)
        key = re.sub(r"\W+", " ", text.lower()).strip()
        if key in seen or not tags or not is_quality_candidate(text, summary, tags):
            continue
        seen.add(key)
        yield {"text": text, "summary": summary, "details": dedupe(details)}


def is_quality_candidate(text: str, summary: str, tags: list[str]) -> bool:
    lower = text.lower()
    if any(pattern.search(text) for pattern in NOISE_PATTERNS):
        return False
    if LOW_VALUE_SUMMARY.search(summary):
        return False
    if len(summary.split()) < 2 and not summary.startswith("$") and not extract_days(summary):
        return False
    if len(text) > 320:
        return False
    if "dollar_amount" in tags and not PROMO_CONTEXT.search(text):
        return False
    if tags == ["dollar_amount"] and not re.search(r"\b(?:off|each|starting at|special|deal|coupon|happy hour)\b", lower):
        return False
    return True


def has_explicit_discount(deal: dict) -> bool:
    text = deal.get("candidate_text", "")
    tags = set(deal.get("tags", []))
    return bool(tags.intersection({"percent_off", "bogo", "happy_hour", "free"}) or re.search(r"\b(?:off|coupon|limited time|starting at|happy hour|2-4-1|half price|with purchase|kids eat free)\b", text, re.I))


def is_publishable_discovered(deal: dict) -> bool:
    if has_explicit_discount(deal):
        return True
    tags = set(deal.get("tags", []))
    categories = set(deal.get("categories", []))
    return bool(
        deal.get("applies_days")
        and "dollar_amount" in tags
        and categories.intersection({"food", "drink"})
        and PROMO_CONTEXT.search(deal.get("candidate_text", ""))
    )


def aggregate_deals(source: Source, deals: list[dict], now: datetime, existing: dict[str, dict]) -> list[dict]:
    if source.options.get("exclude_menu_prices"):
        deals = [
            deal
            for deal in deals
            if has_explicit_discount(deal) or (source.options.get("discovered") and is_publishable_discovered(deal))
        ]
    compact: list[dict] = []
    seen: set[str] = set()
    for deal in deals:
        if source.options.get("discovered") and not is_publishable_discovered(deal):
            continue
        if source.options.get("strict") and not has_explicit_discount(deal) and not is_publishable_discovered(deal):
            continue
        key = re.sub(r"\W+", " ", deal["summary"].lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        compact.append(deal)
    return compact[: source.options.get("max_deals", 20)]


def crawl_source(source: Source, now: datetime, existing: dict[str, dict]) -> tuple[list[dict], dict]:
    if source.options.get("static_deals"):
        deals = [build_static_deal(source, deal, now, existing) for deal in source.options["static_deals"]]
        mode = "ai_extracted" if source.options.get("ai_extracted") else "curated"
        return deals, source_status(source, True, len(deals), now, mode=mode)

    page_html = fetch_html(source)
    lines = clean_text(page_html)
    deals = [build_deal(source, candidate, now, existing) for candidate in candidate_windows(lines)]
    deals = aggregate_deals(source, deals, now, existing)
    return deals, source_status(source, True, len(deals), now, mode="crawled")


def source_status(source: Source, ok: bool, count: int, now: datetime, mode: str, error: str | None = None) -> dict:
    status = {
        "name": source.name,
        "city": source.city,
        "url": source.url,
        "notes": source.notes,
        "location": source.location,
        "ok": ok,
        "candidate_count": count,
        "mode": mode,
        "checked_at": iso(now),
    }
    if error:
        status["error"] = error
    return status


def carry_forward_stale(existing: dict[str, dict], seen_ids: set[str], now: datetime) -> list[dict]:
    stale_deals: list[dict] = []
    for did, deal in existing.items():
        if did in seen_ids:
            continue
        days_since_seen = max(0, (now - parse_iso(deal.get("last_seen"), now)).days)
        if days_since_seen > DROP_AFTER_DAYS:
            continue
        carried = dict(deal)
        carried["status"] = "stale"
        carried["is_stale"] = True
        carried["days_since_seen"] = days_since_seen
        carried["stale_after_days"] = STALE_AFTER_DAYS
        stale_deals.append(carried)
    return stale_deals


def sort_deals(deals: list[dict]) -> list[dict]:
    return sorted(deals, key=lambda deal: (deal.get("status") != "active", deal.get("restaurant", ""), deal.get("validity", ""), deal.get("summary", "")))


def main() -> int:
    now = utc_now()
    sources = load_sources()
    existing = load_existing()
    all_deals: list[dict] = []
    source_statuses: list[dict] = []

    results: list[tuple[list[dict], dict] | None] = [None] * len(sources)
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(crawl_source, source, now, existing): (index, source) for index, source in enumerate(sources)}
        for future in as_completed(futures):
            index, source = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                results[index] = ([], source_status(source, False, 0, now, mode="failed", error=f"{type(exc).__name__}: {exc}"))

    for result in results:
        if result is None:
            continue
        deals, status = result
        all_deals.extend(deals)
        source_statuses.append(status)

    seen_ids = {deal["id"] for deal in all_deals}
    all_deals.extend(carry_forward_stale(existing, seen_ids, now))
    active_count = sum(1 for deal in all_deals if deal.get("status") == "active")
    stale_count = sum(1 for deal in all_deals if deal.get("status") == "stale")
    payload = {
        "crawler_version": CRAWLER_VERSION,
        "generated_at": iso(now),
        "scope": {
            "cities": sorted({source.city for source in sources}),
            "source_count": len(sources),
            "stale_after_days": STALE_AFTER_DAYS,
            "drop_after_days": DROP_AFTER_DAYS,
            "restaurant_count": len(load_restaurant_inventory()),
        },
        "summary": {
            "active_deals": active_count,
            "stale_deals": stale_count,
            "total_deals": len(all_deals),
            "healthy_sources": sum(1 for item in source_statuses if item["ok"]),
            "failed_sources": sum(1 for item in source_statuses if not item["ok"]),
        },
        "pipeline": load_ai_summary(),
        "sources": source_statuses,
        "deals": sort_deals(all_deals),
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {len(all_deals)} deals to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
