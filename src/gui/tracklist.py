"""Pure tracklist / playlist-row helpers (no Tk module patches)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

TRACKLIST_VALUE_COLUMNS = ("track", "format", "bit_depth", "sample_rate", "rating")

_NUMBERED_SORT_COLUMNS = frozenset({"#0", "bit_depth", "sample_rate", "rating"})
_VALUE_COLUMN_INDEX = {
    name: i for i, name in enumerate(TRACKLIST_VALUE_COLUMNS)
}


def playlist_iid(kind: str, folder: str, name: str, *, playlist_label: Callable[[str, str], str]) -> str:
    return f"{kind}:{playlist_label(folder, name)}"


def playlist_row_text(kind: str, name: str, count: int) -> str:
    if kind == "folder":
        return name
    return f"{name} ({count} tracks)"


def rekordbox_star_rating(raw: object) -> str:
    """Map Rekordbox TRACK Rating (0–255) to five filled/empty stars."""
    try:
        stars = min(5, max(0, int(str(raw).strip()) // 51))
    except (TypeError, ValueError):
        stars = 0
    return ("★" * stars) + ("☆" * (5 - stars))


def track_preview_row(track: Any) -> tuple[str, str, str, str, str]:
    """Return (label, format, bit_depth, sample_rate, rating) from a TRACK.

    Bit depth is always — here; file headers are filled asynchronously.
    The label is artist - title without a file-extension suffix.
    """
    empty = "—"
    if track is None:
        return ("(missing track)",) + (empty,) * 4
    artist = track.get("Artist") or ""
    title = track.get("Name") or ""
    label = f"{artist} - {title}" if artist else title
    kind = (track.get("Kind") or "").strip()
    if kind.endswith(" File"):
        fmt = kind[: -len(" File")].strip() or empty
    else:
        fmt = kind or empty
    rate = (track.get("SampleRate") or "").strip() or empty
    rating = rekordbox_star_rating(track.get("Rating"))
    return label, fmt, empty, rate, rating


def track_search_haystack(label: str, fmt: str, path: Path | None) -> str:
    """Casefolded text matched by track search (label, format column, filename)."""
    parts = [label, fmt]
    if path is not None:
        parts.append(path.name)
    return " ".join(parts).casefold()


def tracklist_sort_key(text: str, values: list[Any], column: str):
    """Sort key for a tracklist leaf: tree text (# index) plus column values."""
    if column == "#0":
        raw = str(text or "")
        try:
            return (0, int(raw))
        except ValueError:
            return (1, 0)
    idx = _VALUE_COLUMN_INDEX.get(column)
    if idx is None or idx >= len(values):
        return ""
    raw = str(values[idx] or "")
    if column in _NUMBERED_SORT_COLUMNS:
        if column == "rating":
            if raw in ("", "—"):
                return (1, 0)
            return (0, raw.count("★"))
        try:
            return (0, int(raw))
        except ValueError:
            return (1, 0)
    return raw.casefold()


def order_tracklist_leaves(
    leaves: list[str],
    *,
    column: str,
    reverse: bool,
    sort_key: Callable[[str, str], Any],
) -> list[str]:
    """Return leaf iids ordered within one playlist group."""
    if column in _NUMBERED_SORT_COLUMNS:
        numbered: list[tuple[int, str]] = []
        empty: list[str] = []
        for iid in leaves:
            key = sort_key(iid, column)
            if isinstance(key, tuple) and key[0] == 0:
                numbered.append((key[1], iid))
            else:
                empty.append(iid)
        numbered.sort(key=lambda pair: pair[0], reverse=reverse)
        return [iid for _, iid in numbered] + empty
    return sorted(
        leaves,
        key=lambda iid: sort_key(iid, column),
        reverse=reverse,
    )
