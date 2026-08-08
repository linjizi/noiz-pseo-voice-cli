"""Acceptance checks for a single voice record (no network beyond CMS/site)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional


def _url_ok(url: str, timeout: int = 15) -> bool:
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", "noiz-pseo-voice-cli/0.1")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except (urllib.error.URLError, urllib.error.HTTPError):
        return False


def run_checks(record: dict[str, Any], site_base: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    staging = record.get("pipelineStaging") or {}
    status = record.get("pipelineStatus")
    slug = record.get("canonicalSlug") or record.get("slug")

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("status_terminal", status in ("built", "live"), str(status))

    kw_raw = staging.get("keywordInputJson") or staging.get("keywordInput")
    kw: Any = kw_raw
    parse_error = ""
    if isinstance(kw_raw, str):
        try:
            kw = json.loads(kw_raw)
        except (ValueError, TypeError):
            # v0.3.2: keywordInputJson is stored as a serialized JSON string
            # (text field); unparseable input fails the keyword checks.
            kw = None
            parse_error = "keywordInputJson is not valid JSON"
    add("keywords_present", bool(kw), parse_error or "keywordInputJson/keywordInput")
    primary = ""
    if isinstance(kw, dict):
        primary = str(kw.get("primary_keyword") or "")
    add("primary_keyword", bool(primary), primary or parse_error)
    validated = bool(isinstance(kw, dict) and kw.get("validated"))
    add("keywords_validated", validated, "validated flag" + (f" ({parse_error})" if parse_error else ""))

    content_hash = staging.get("generatedContentHash") or staging.get("assetsContentHash")
    add("content_hash", bool(content_hash), str(content_hash)[:24] if content_hash else "")

    assets = staging.get("assets") or []
    add("assets_nonempty", bool(assets), f"{len(assets)} asset(s)")

    page_url = None
    if slug:
        page_path = slug if slug.startswith("voice/") else f"voice/{slug}"
        page_url = f"{site_base}/lp/{page_path}"
    if page_url:
        add("page_url_defined", True, page_url)
        add("page_url_live", _url_ok(page_url), page_url)
    else:
        add("page_url_defined", False, "no canonicalSlug/slug")
        add("page_url_live", False, "n/a")

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks, "page_url": page_url, "voice_id": record.get("voiceId")}
