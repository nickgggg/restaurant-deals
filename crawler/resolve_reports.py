from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
EXTRACTIONS_PATH = ROOT / "docs" / "data" / "ai_extractions.json"
DEALS_PATH = ROOT / "docs" / "data" / "deals.json"
REQUEST_TIMEOUT = 25


def normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def canonical_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return ""
    host = (parsed.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    if not host or parsed.scheme.casefold() not in {"http", "https"}:
        return ""
    port = f":{parsed.port}" if parsed.port else ""
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(("https", f"{host}{port}", path, parsed.query, ""))


def report_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(r"^###\s+(.+?)\s*$\n([\s\S]*?)(?=^###\s+|\Z)", body or "", re.M):
        key = normalized_text(match.group(1))
        value = re.sub(r"<!--.*?-->", "", match.group(2), flags=re.S).strip()
        fields[key] = value
    return fields


def published_resolutions(extractions: dict[str, Any], feed: dict[str, Any]) -> list[dict[str, Any]]:
    active_deals = [deal for deal in feed.get("deals", []) if deal.get("status") == "active"]
    resolutions: list[dict[str, Any]] = []
    seen: set[int] = set()
    for page in extractions.get("pages", []):
        issue_number = page.get("page", {}).get("report_issue")
        if not issue_number or issue_number in seen:
            continue
        source_url = page.get("page", {}).get("url", "")
        restaurant = page.get("restaurant", {}).get("name", "")
        city = page.get("restaurant", {}).get("city", "")
        if page.get("status") != "ok" or not page.get("deals"):
            continue
        matches = [
            deal
            for deal in active_deals
            if canonical_url(deal.get("source_url", "")) == canonical_url(source_url)
            and normalized_text(deal.get("restaurant")) == normalized_text(restaurant)
            and normalized_text(deal.get("city")) == normalized_text(city)
        ]
        if not matches:
            continue
        seen.add(issue_number)
        resolutions.append(
            {
                "issue_number": issue_number,
                "source_url": source_url,
                "restaurant": restaurant,
                "city": city,
                "deal_count": len(matches),
            }
        )
    return resolutions


def github_request(repository: str, token: str, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    request = Request(
        f"https://api.github.com/repos/{repository}{path}",
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "restaurant-deals-report-resolver",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method=method,
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        body = response.read()
    return json.loads(body) if body else None


def ensure_label(repository: str, token: str) -> None:
    try:
        github_request(
            repository,
            token,
            "POST",
            "/labels",
            {
                "name": "report-resolved",
                "color": "5319e7",
                "description": "Reported missing deal is verified in the published feed",
            },
        )
    except HTTPError as exc:
        if exc.code != 422:
            raise


def close_resolution(repository: str, token: str, resolution: dict[str, Any]) -> bool:
    issue_number = resolution["issue_number"]
    issue = github_request(repository, token, "GET", f"/issues/{issue_number}")
    if issue.get("state") != "open":
        return False
    fields = report_fields(issue.get("body") or "")
    if normalized_text(fields.get("what needs attention?")) != "missing deal":
        return False

    count = resolution["deal_count"]
    noun = "deal" if count == 1 else "deals"
    github_request(
        repository,
        token,
        "POST",
        f"/issues/{issue_number}/comments",
        {
            "body": (
                f"Resolved automatically: {count} evidence-backed {noun} from "
                f"{resolution['source_url']} are now present in the published feed for "
                f"{resolution['restaurant']} ({resolution['city']})."
            )
        },
    )
    github_request(repository, token, "POST", f"/issues/{issue_number}/labels", {"labels": ["report-resolved"]})
    for label in ("report-ready", "source-candidate", "source-needs-review"):
        try:
            github_request(repository, token, "DELETE", f"/issues/{issue_number}/labels/{label}")
        except HTTPError as exc:
            if exc.code != 404:
                raise
    github_request(
        repository,
        token,
        "PATCH",
        f"/issues/{issue_number}",
        {"state": "closed", "state_reason": "completed"},
    )
    return True


def main() -> None:
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not repository or not token:
        print("Report resolution skipped: GitHub repository or token is unavailable")
        return

    extractions = json.loads(EXTRACTIONS_PATH.read_text())
    feed = json.loads(DEALS_PATH.read_text())
    resolutions = published_resolutions(extractions, feed)
    if not resolutions:
        print("No verified missing-deal reports are ready to close")
        return

    ensure_label(repository, token)
    closed = sum(close_resolution(repository, token, resolution) for resolution in resolutions)
    print(f"Closed {closed} verified missing-deal report(s)")


if __name__ == "__main__":
    main()
