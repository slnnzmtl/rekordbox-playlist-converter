"""Read-only change-aware rerun classification for manifest v2."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from convert.freshness import assignment_state, metadata_signature, recipe_from_item
from convert.models import Plan, PlannedTrack
from convert.paths import source_key
from convert.quality import coerce_output_format
from cli_error import CliError

_PCM_RECIPE_KEYS = ("format", "bit_depth", "sample_rate", "channels")

ACTION_LABELS = {
    "reuse": "Reuse existing",
    "refresh_xml": "Refresh XML",
    "update_metadata": "Update metadata",
    "rewrite_container": "Rebuild container",
    "transcode": "Transcode",
    "recreate_missing": "Recreate missing",
    "external_modification_conflict": "Conflict",
    "in_place_noop": "In-place skip",
}

REASON_LABELS = {
    "in_place": "Source and destination are the same file",
    "dest_missing": "Destination file is missing",
    "external_modification": "Destination was changed outside this app",
    "incomplete": "Previous conversion did not finish",
    "unverified": "Existing destination is not yet verified",
    "force": "Forced rebuild",
    "source_changed": "Source file changed",
    "recipe_changed": "Output quality recipe changed",
    "revision_changed": "Converter revision requires a container rebuild",
    "metadata_changed": "Rekordbox metadata changed",
    "unchanged": "Output is already current",
}

ACTION_WRITE_KIND = {
    "reuse": "none",
    "in_place_noop": "none",
    "external_modification_conflict": "none",
    "refresh_xml": "metadata",
    "update_metadata": "metadata",
    "rewrite_container": "audio",
    "transcode": "audio",
    "recreate_missing": "audio",
}

WRITE_KIND_LABELS = {
    "audio": "writes audio",
    "metadata": "updates metadata only",
    "none": "writes nothing",
}


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    write_kind: str
    source_stat: dict[str, Any] | None = None
    dest_stat: dict[str, Any] | None = None


def preview_reason(action: str, reason: str | None = None) -> str:
    """User-facing reason plus whether the action writes audio, metadata, or nothing."""
    base = REASON_LABELS.get(reason or "", "") or ACTION_LABELS.get(action, "")
    kind = ACTION_WRITE_KIND.get(action)
    extra = WRITE_KIND_LABELS.get(kind, "") if kind else ""
    if base and extra:
        return f"{base} ({extra})"
    return base or extra


def _decision(
    action: str,
    reason: str,
    *,
    source_stat: dict[str, Any] | None = None,
    dest_stat: dict[str, Any] | None = None,
) -> Decision:
    return Decision(
        action=action,
        reason=reason,
        write_kind=ACTION_WRITE_KIND.get(action, "none"),
        source_stat=source_stat,
        dest_stat=dest_stat,
    )


def container_rewrite_supported(path: Path, output_format: str) -> bool | None:
    """True/False when path exists; None when it cannot be inspected."""
    if not path.is_file():
        return None
    fmt = coerce_output_format(output_format)
    try:
        if fmt == "wav":
            from cdj_wav import parse_wav_info, pcm_rewrite_supported

            return pcm_rewrite_supported(parse_wav_info(path))
        from cdj_aiff import info_is_cdj_safe_aiff, parse_aiff_audio

        info = parse_aiff_audio(path)
        return info_is_cdj_safe_aiff(
            info, bit_depth=info.bits_per_sample, sample_rate=_aiff_rate_hz(info)
        )
    except CliError:
        return False


def _aiff_rate_hz(info: object) -> int:
    rate_bytes = getattr(info, "sample_rate_bytes", b"")
    from cdj_aiff import AIFF_RATE_BYTES

    for hz, packed in AIFF_RATE_BYTES.items():
        if packed == rate_bytes:
            return hz
    return 48000


def _rebuild_action(item: PlannedTrack) -> str:
    if not item.passthrough:
        return "transcode"
    dest_ok = (
        container_rewrite_supported(item.dest_path, item.output_format)
        if item.dest_path.is_file()
        else None
    )
    if dest_ok is True:
        return "rewrite_container"
    if container_rewrite_supported(item.source_path, item.output_format) is False:
        return "transcode"
    return "rewrite_container"


def _revision_action(item: PlannedTrack) -> str:
    """PCM-preserving rewrite when dest (or a passthrough source) is rewriteable."""
    if container_rewrite_supported(item.dest_path, item.output_format) is True:
        return "rewrite_container"
    if (
        item.passthrough
        and container_rewrite_supported(item.source_path, item.output_format) is True
    ):
        return "rewrite_container"
    return "transcode"


def snapshots_match(
    stored: dict[str, Any] | None, current: dict[str, Any] | None
) -> bool:
    """True when size and mtime_ns both match. Same-size content with an
    identical timestamp is treated as unchanged; full hashes stay optional."""
    if stored is None or current is None:
        return False
    return stored.get("size") == current.get("size") and stored.get(
        "mtime_ns"
    ) == current.get("mtime_ns")


def file_snapshot(path: Path) -> dict[str, Any] | None:
    """Return size and mtime_ns for a file, or None if it is missing."""
    try:
        if not path.is_file():
            return None
        st = path.stat()
    except OSError:
        return None
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def classify_assignment(
    *,
    item: PlannedTrack,
    record: dict[str, Any] | None,
    force: bool,
    dest_exists: bool,
    dest_stat: dict[str, Any] | None,
    source_stat: dict[str, Any] | None,
) -> Decision:
    if item.noop:
        return _decision(
            "in_place_noop",
            "in_place",
            source_stat=source_stat,
            dest_stat=dest_stat,
        )
    if not dest_exists:
        return _decision(
            "recreate_missing",
            "dest_missing",
            source_stat=source_stat,
            dest_stat=dest_stat,
        )
    record = record or {}
    state = assignment_state(record)
    if state == "complete" and not snapshots_match(record.get("output"), dest_stat):
        return _decision(
            "external_modification_conflict",
            "external_modification",
            source_stat=source_stat,
            dest_stat=dest_stat,
        )
    if state == "incomplete":
        return _decision(
            _rebuild_action(item),
            "incomplete",
            source_stat=source_stat,
            dest_stat=dest_stat,
        )
    if state == "unverified":
        return _decision(
            _rebuild_action(item),
            "unverified",
            source_stat=source_stat,
            dest_stat=dest_stat,
        )
    if force:
        return _decision(
            _rebuild_action(item),
            "force",
            source_stat=source_stat,
            dest_stat=dest_stat,
        )
    if not snapshots_match(record.get("source"), source_stat):
        return _decision(
            _rebuild_action(item),
            "source_changed",
            source_stat=source_stat,
            dest_stat=dest_stat,
        )
    stored_recipe = record.get("recipe") or {}
    current_recipe = recipe_from_item(item)
    if any(
        stored_recipe.get(key) != current_recipe.get(key) for key in _PCM_RECIPE_KEYS
    ):
        return _decision(
            "transcode",
            "recipe_changed",
            source_stat=source_stat,
            dest_stat=dest_stat,
        )
    if stored_recipe.get("revision") != current_recipe.get("revision"):
        return _decision(
            _revision_action(item),
            "revision_changed",
            source_stat=source_stat,
            dest_stat=dest_stat,
        )
    stored_meta = (record.get("metadata") or {}).get("signature")
    if stored_meta != metadata_signature(item.source_el):
        if coerce_output_format(item.output_format) == "aiff":
            return _decision(
                "update_metadata",
                "metadata_changed",
                source_stat=source_stat,
                dest_stat=dest_stat,
            )
        return _decision(
            "refresh_xml",
            "metadata_changed",
            source_stat=source_stat,
            dest_stat=dest_stat,
        )
    return _decision(
        "reuse",
        "unchanged",
        source_stat=source_stat,
        dest_stat=dest_stat,
    )


def classify_item(plan: Plan, item: PlannedTrack, force: bool) -> Decision:
    """Stat source and dest now, then classify. Safe to call again before write."""
    fmt = coerce_output_format(item.output_format)
    record = None
    if plan.manifest is not None:
        record = plan.manifest.tracks.get(source_key(item.source_path), {}).get(fmt)
    dest_stat = file_snapshot(item.dest_path)
    return classify_assignment(
        item=item,
        record=record,
        force=force,
        dest_exists=dest_stat is not None,
        dest_stat=dest_stat,
        source_stat=file_snapshot(item.source_path),
    )
