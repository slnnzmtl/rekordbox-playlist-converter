"""Source, metadata, output, and recipe freshness for manifest v2."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from convert.quality import coerce_bit_depth, coerce_output_format, coerce_sample_rate

RECIPE_REVISION = 1
RECIPE_CHANNELS = 2
OUTPUT_ONLY_ATTRS = frozenset(
    {"TrackID", "Location", "Kind", "Size", "BitRate", "SampleRate"}
)


def source_signature(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "hash": None}


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

