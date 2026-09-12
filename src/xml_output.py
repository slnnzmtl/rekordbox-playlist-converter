"""Write Rekordbox import XML from a convert Plan."""

from __future__ import annotations

import copy
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import convert_plan
import ffmpeg_tools
from cli_error import CliError
from convert_plan import Plan, PlannedTrack
from rekordbox_xml import (
    collection_indexes,
    find_playlists_by_name,
)

def next_track_id(root: ET.Element) -> int:
    by_id, _ = collection_indexes(root)
    values = []
    for tid in by_id:
        try:
            values.append(int(tid))
        except ValueError:
            continue
    return (max(values) if values else 0) + 1


def ensure_root_node(playlists: ET.Element) -> ET.Element:
    for child in playlists:
        if child.tag == "NODE" and child.get("Name") == "ROOT" and child.get("Type") == "0":
            return child
    return ET.SubElement(playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": "0"})


def find_or_create_wav_playlist(output_root: ET.Element, name: str) -> tuple[ET.Element, bool]:
    existing = find_playlists_by_name(output_root, name)
    if len(existing) > 1:
        raise CliError(f"duplicate playlist name in output: {name}")
    if existing:
        return existing[0], True
    playlists = output_root.find("PLAYLISTS")
    if playlists is None:
        playlists = ET.SubElement(output_root, "PLAYLISTS")
    root_node = ensure_root_node(playlists)
    node = ET.SubElement(
        root_node,
        "NODE",
        {"Name": name, "Type": "1", "KeyType": "0", "Entries": "0"},
    )
    return node, False


def share_output_root(plans: list[Plan]) -> None:
    if len(plans) < 2:
        return
    shared = plans[0].output_root
    for plan in plans[1:]:
        plan.output_root = shared


def rewrite_counts(output_root: ET.Element) -> None:
    """Recompute COLLECTION Entries and every folder Count / playlist Entries."""
    collection = output_root.find("COLLECTION")
    if collection is not None:
        collection.set("Entries", str(len(collection.findall("TRACK"))))
    playlists = output_root.find("PLAYLISTS")
    if playlists is None:
        return

    def rewrite_node(node: ET.Element) -> None:
        if node.tag != "NODE":
            return
        if node.get("Type") == "1":
            node.set("Entries", str(len(node.findall("TRACK"))))
            return
        if node.get("Type") == "0":
            children = [c for c in node if c.tag == "NODE"]
            node.set("Count", str(len(children)))
            for child in children:
                rewrite_node(child)

    for child in playlists:
        rewrite_node(child)


def probe_dest_tech(path: Path) -> tuple[str, str, str]:
    size = str(path.stat().st_size)
    probe = ffmpeg_tools.run_ffprobe(path)
    stream = ffmpeg_tools.first_stream(probe) or {}
    rate = str(stream.get("sample_rate") or "")
    try:
        codec = ffmpeg_tools.pcm_codec_for_stream(stream)
        depth = ffmpeg_tools.bit_depth_of_codec(codec)
    except CliError:
        name = str(stream.get("codec_name") or "")
        depth_map = {
            "pcm_s16le": 16,
            "pcm_s16be": 16,
            "pcm_s24le": 24,
            "pcm_s24be": 24,
            "pcm_s32le": 32,
            "pcm_f32le": 32,
        }
        depth = depth_map.get(name, 16)
    try:
        channels = int(stream.get("channels") or 2)
    except (TypeError, ValueError):
        channels = 2
    try:
        sr = int(float(rate)) if rate else 0
    except ValueError:
        sr = 0
    bitrate = str(int(sr * depth * channels / 1000)) if sr else "0"
    return size, bitrate, rate or "0"


def clone_track(source_el: ET.Element, track_id: str, dest_path: Path, dest_location: str) -> ET.Element:
    clone = copy.deepcopy(source_el)
    size, bitrate, sample_rate = probe_dest_tech(dest_path)
    clone.set("TrackID", track_id)
    clone.set("Location", dest_location)
    kind = "AIFF File" if dest_path.suffix.lower() == ".aiff" else "WAV File"
    clone.set("Kind", kind)
    clone.set("Size", size)
    clone.set("BitRate", bitrate)
    clone.set("SampleRate", sample_rate)
    return clone


def refresh_track(
    existing: ET.Element,
    source_el: ET.Element,
    dest_path: Path,
    dest_location: str,
) -> None:
    """Update an existing collection TRACK from source_el; keep TrackID."""
    tid = existing.get("TrackID", "")
    # Replace children and attributes from a fresh clone, then restore TrackID.
    refreshed = clone_track(source_el, tid, dest_path, dest_location)
    existing.clear()
    existing.attrib.update(refreshed.attrib)
    existing.set("TrackID", tid)
    existing.set("Location", dest_location)
    for child in list(refreshed):
        existing.append(child)


def assignment_key(item: PlannedTrack) -> tuple[str, str]:
    """(source_key, format) for success-set membership."""
    fmt = "aiff" if item.dest_path.suffix.lower() == ".aiff" else "wav"
    return convert_plan.source_key(item.source_path), fmt


def playlist_keys(node: ET.Element) -> list[str]:
    return [t.get("Key", "") for t in node.findall("TRACK")]


def apply_xml(plan: Plan, success: set[tuple[str, str]]) -> int:
    collection = plan.output_root.find("COLLECTION")
    if collection is None:
        collection = ET.SubElement(plan.output_root, "COLLECTION", {"Entries": "0"})
    _, by_location = collection_indexes(plan.output_root)
    next_id = next_track_id(plan.output_root)
    dest_to_id: dict[str, str] = {}

    for item in plan.unique:
        if assignment_key(item) not in success:
            continue
        existing = by_location.get(item.dest_location)
        if existing is not None:
            refresh_track(
                existing, item.source_el, item.dest_path, item.dest_location
            )
            dest_to_id[item.dest_location] = existing.get("TrackID", "")
            continue
        tid = str(next_id)
        next_id += 1
        clone = clone_track(item.source_el, tid, item.dest_path, item.dest_location)
        collection.append(clone)
        dest_to_id[item.dest_location] = tid
        by_location[item.dest_location] = clone

    wav_node, existed = find_or_create_wav_playlist(plan.output_root, plan.wav_playlist_name)
    appended = 0
    present = set(playlist_keys(wav_node)) if existed else set()
    seen_this_run: set[str] = set()
    for item in plan.tracks:
        if item.dest_location not in dest_to_id:
            continue
        tid = dest_to_id[item.dest_location]
        if tid in present or tid in seen_this_run:
            continue
        ET.SubElement(wav_node, "TRACK", {"Key": tid})
        present.add(tid)
        seen_this_run.add(tid)
        appended += 1
    return appended


def validate_import_xml(root: ET.Element) -> list[str]:
    """Return integrity errors for a Rekordbox import XML tree (empty if ok)."""
    errors: list[str] = []
    if root.tag != "DJ_PLAYLISTS":
        errors.append(f"root tag must be DJ_PLAYLISTS, got {root.tag}")
        return errors
    if root.get("Version") != "1.0.0":
        errors.append(f'DJ_PLAYLISTS Version must be "1.0.0", got {root.get("Version")!r}')
    if root.find("PRODUCT") is None:
        errors.append("missing PRODUCT")
    collections = root.findall("COLLECTION")
    if len(collections) != 1:
        errors.append(f"expected exactly one COLLECTION, found {len(collections)}")
    playlists_els = root.findall("PLAYLISTS")
    if len(playlists_els) != 1:
        errors.append(f"expected exactly one PLAYLISTS, found {len(playlists_els)}")
    if errors:
        return errors
    collection = collections[0]
    playlists = playlists_els[0]
    roots = [
        n
        for n in playlists
        if n.tag == "NODE" and n.get("Name") == "ROOT" and n.get("Type") == "0"
    ]
    if len(roots) != 1:
        errors.append(f"expected exactly one PLAYLISTS/ROOT, found {len(roots)}")

    by_id: dict[str, ET.Element] = {}
    locations: set[str] = set()
    for track in collection.findall("TRACK"):
        tid = track.get("TrackID", "")
        if not tid or not tid.isdigit():
            errors.append(f"TrackID must be numeric, got {tid!r}")
        elif tid in by_id:
            errors.append(f"duplicate TrackID {tid}")
        else:
            by_id[tid] = track
        loc = track.get("Location", "")
        if not loc.startswith("file://localhost"):
            errors.append(f"Location must be file://localhost absolute, got {loc!r}")
        elif loc in locations:
            errors.append(f"duplicate Location {loc}")
        else:
            locations.add(loc)
        kind = track.get("Kind", "")
        if kind not in {"WAV File", "AIFF File"}:
            errors.append(f"Kind must be WAV File or AIFF File, got {kind!r}")

    expected_collection = str(len(collection.findall("TRACK")))
    if collection.get("Entries") != expected_collection:
        errors.append(
            f"COLLECTION Entries={collection.get('Entries')!r} "
            f"!= {expected_collection}"
        )

    def check_node(node: ET.Element) -> None:
        if node.tag != "NODE":
            return
        if node.get("Type") == "1":
            if node.get("KeyType") != "0":
                errors.append(
                    f"playlist {node.get('Name')!r} KeyType must be 0, "
                    f"got {node.get('KeyType')!r}"
                )
            tracks = node.findall("TRACK")
            expected = str(len(tracks))
            if node.get("Entries") != expected:
                errors.append(
                    f"playlist {node.get('Name')!r} Entries={node.get('Entries')!r} "
                    f"!= {expected}"
                )
            for entry in tracks:
                key = entry.get("Key", "")
                if key not in by_id:
                    errors.append(
                        f"playlist {node.get('Name')!r} Key={key!r} missing in COLLECTION"
                    )
            return
        if node.get("Type") == "0":
            children = [c for c in node if c.tag == "NODE"]
            expected = str(len(children))
            if node.get("Count") != expected:
                errors.append(
                    f"folder {node.get('Name')!r} Count={node.get('Count')!r} "
                    f"!= {expected}"
                )
            for child in children:
                check_node(child)

    for child in playlists:
        check_node(child)
    return errors


def write_import_xml(root: ET.Element, path: Path) -> None:
    """Validate then atomically write import XML; refuse on integrity errors."""
    rewrite_counts(root)
    problems = validate_import_xml(root)
    if problems:
        raise CliError("import XML failed integrity checks:\n" + "\n".join(problems))
    atomic_write_xml(root, path)


def atomic_write_xml(root: ET.Element, path: Path) -> None:
    path = convert_plan.abs_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="  ")
    fd, tmp = tempfile.mkstemp(prefix=".rb_wav_", suffix=".xml", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            tree = ET.ElementTree(root)
            tree.write(handle, encoding="UTF-8", xml_declaration=True)
            handle.write(b"\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


