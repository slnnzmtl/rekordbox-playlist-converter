#!/usr/bin/env python3
"""Check GitHub Releases for application updates."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any

GITHUB_OWNER = "slnnzmtl"
GITHUB_REPO = "rekordbox-playlist-converter"
LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag_name: str
    html_url: str
    release_notes: str = ""


@dataclass(frozen=True)
class UpdateCheckResult:
    kind: str
    release: ReleaseInfo | None = None
    message: str = ""

    @property
    def is_update_available(self) -> bool:
        return self.kind == "update_available"

    @property
    def is_up_to_date(self) -> bool:
        return self.kind == "up_to_date"

    @property
    def is_error(self) -> bool:
        return self.kind == "error"


def _first_paragraph(body: str) -> str:
    text = body.strip()
    if not text:
        return ""
    return text.split("\n\n", 1)[0].strip()


def fetch_latest_release(timeout: float = 10.0) -> ReleaseInfo:
    request = urllib.request.Request(
        LATEST_RELEASE_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "rekordbox-playlist-converter"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload: dict[str, Any] = json.loads(response.read().decode())
    if payload.get("draft") or payload.get("prerelease"):
        raise ValueError("Latest release is draft or prerelease")
    tag_name = str(payload["tag_name"])
    return ReleaseInfo(
        version=_normalize_version(tag_name),
        tag_name=tag_name,
        html_url=str(payload["html_url"]),
        release_notes=_first_paragraph(str(payload.get("body") or "")),
    )


def _normalize_version(version: str) -> str:
    version = version.strip()
    if version.startswith("v") or version.startswith("V"):
        version = version[1:]
    return version


def _version_tuple(version: str) -> tuple[int, ...]:
    """Parse dot-separated numeric semver segments; non-numeric parts become 0."""
    normalized = _normalize_version(version)
    parts: list[int] = []
    for segment in normalized.split("."):
        digits = ""
        for ch in segment:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(current: str, latest: str) -> bool:
    """Return True when latest is a newer semver than current."""
    cur = _version_tuple(current)
    lat = _version_tuple(latest)
    length = max(len(cur), len(lat))
    cur = cur + (0,) * (length - len(cur))
    lat = lat + (0,) * (length - len(lat))
    return lat > cur


def check_for_update(current_version: str, timeout: float = 10.0) -> UpdateCheckResult:
    try:
        release = fetch_latest_release(timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — surface as user-facing error on manual check
        return UpdateCheckResult(kind="error", message=str(exc))
    if is_newer(current_version, release.version):
        return UpdateCheckResult(kind="update_available", release=release)
    return UpdateCheckResult(kind="up_to_date")
