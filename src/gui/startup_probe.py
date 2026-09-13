"""Pure startup probe: Documents access and optional XML autoload hits."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gui_prefs import find_rekordbox_xml_via_child, probe_path_via_child


@dataclass(frozen=True)
class StartupProbeResult:
    documents_accessible: bool
    xml_search_hits: list[Path] | None


def run_startup_probe(
    *,
    has_saved_source_xml: bool,
    home: Path | None = None,
    probe_documents: Callable[[Path], bool] | None = None,
    find_xml: Callable[[Path], list[Path]] | None = None,
) -> StartupProbeResult:
    """Probe Documents listing and optionally search for Rekordbox XML exports.

    When *has_saved_source_xml* is true, XML search is skipped (``xml_search_hits``
    is None). Otherwise hits may be an empty list.
    """
    root = home if home is not None else Path.home()
    probe_fn = probe_documents or probe_path_via_child
    find_fn = find_xml or find_rekordbox_xml_via_child
    xml_hits: list[Path] | None
    if has_saved_source_xml:
        xml_hits = None
    else:
        xml_hits = find_fn(root)
    accessible = probe_fn(root / "Documents")
    return StartupProbeResult(
        documents_accessible=accessible,
        xml_search_hits=xml_hits,
    )
