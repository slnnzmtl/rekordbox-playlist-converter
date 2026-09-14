"""Source, metadata, output, and recipe freshness for manifest v2."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from convert.paths import source_key
from convert.quality import coerce_bit_depth, coerce_output_format, coerce_sample_rate

RECIPE_REVISION = 1
RECIPE_CHANNELS = 2
OUTPUT_ONLY_ATTRS = frozenset(
    {"TrackID", "Location", "Kind", "Size", "BitRate", "SampleRate"}
)


def source_signature(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _canonical_element(el: ET.Element) -> dict[str, Any]:
    attrs = {
        key: value
        for key, value in el.attrib.items()
        if key not in OUTPUT_ONLY_ATTRS
    }
    children = [_canonical_element(child) for child in list(el)]
    return {
        "tag": el.tag,
        "attrs": attrs,
        "text": (el.text or "").strip(),
        "children": children,
    }


def metadata_signature(track_el: ET.Element) -> str:
    payload = json.dumps(
        _canonical_element(track_el),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def recipe_from_item(item: Any) -> dict[str, Any]:
    return {
        "format": coerce_output_format(item.output_format),
        "bit_depth": coerce_bit_depth(item.bit_depth),
        "sample_rate": coerce_sample_rate(item.sample_rate),
        "channels": RECIPE_CHANNELS,
        "revision": RECIPE_REVISION,
    }


def output_signature(path: Path) -> dict[str, Any]:
    return source_signature(path)


def mark_incomplete(record: dict[str, Any]) -> None:
    record["state"] = "incomplete"


def mark_complete(
    record: dict[str, Any],
    *,
    source: dict[str, Any],
    metadata: str,
    output: dict[str, Any],
    recipe: dict[str, Any],
) -> None:
    record["state"] = "complete"
    record["source"] = source
    record["metadata"] = {"signature": metadata}
    record["output"] = output
    record["recipe"] = recipe


def _has_stat(obj: object) -> bool:
    return (
        isinstance(obj, dict)
        and _is_nonneg_int(obj.get("size"))
        and _is_nonneg_int(obj.get("mtime_ns"))
    )


def _is_nonneg_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _has_recipe(obj: object) -> bool:
    if not isinstance(obj, dict):
        return False
    return all(
        key in obj
        for key in ("format", "bit_depth", "sample_rate", "channels", "revision")
    )


def assignment_state(record: dict[str, Any]) -> str:
    if record.get("state") == "incomplete":
        return "incomplete"
    metadata = record.get("metadata")
    has_metadata = isinstance(metadata, dict) and isinstance(
        metadata.get("signature"), str
    ) and metadata.get("signature")
    if (
        record.get("state") == "complete"
        and _has_stat(record.get("source"))
        and _has_stat(record.get("output"))
        and has_metadata
        and _has_recipe(record.get("recipe"))
    ):
        return "complete"
    return "unverified"


def bind_complete_assignment(
    manifest: Any,
    item: Any,
    relative_dest: str,
) -> None:
    """Store a complete freshness record for *item* at *relative_dest*."""
    record: dict[str, Any] = {"dest": relative_dest}
    mark_complete(
        record,
        source=source_signature(item.source_path),
        metadata=metadata_signature(item.source_el),
        output=output_signature(item.dest_path),
        recipe=recipe_from_item(item),
    )
    fmt = coerce_output_format(item.output_format)
    manifest.tracks.setdefault(source_key(item.source_path), {})[fmt] = record

