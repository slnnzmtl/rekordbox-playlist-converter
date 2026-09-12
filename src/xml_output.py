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
from convert_plan import Plan
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


def rewrite_counts(output_root: ET.Element, wav_node: ET.Element) -> None:
    collection = output_root.find("COLLECTION")
    if collection is not None:
        collection.set("Entries", str(len(collection.findall("TRACK"))))
    wav_node.set("Entries", str(len(wav_node.findall("TRACK"))))
    playlists = output_root.find("PLAYLISTS")
    if playlists is None:
        return
    root_node = None
    for child in playlists:
        if child.tag == "NODE" and child.get("Name") == "ROOT" and child.get("Type") == "0":
            root_node = child
            break
    if root_node is not None:
        count = sum(1 for c in root_node if c.tag == "NODE")
        root_node.set("Count", str(count))


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


def playlist_keys(node: ET.Element) -> list[str]:
    return [t.get("Key", "") for t in node.findall("TRACK")]


def apply_xml(plan: Plan) -> int:
    collection = plan.output_root.find("COLLECTION")
    if collection is None:
        collection = ET.SubElement(plan.output_root, "COLLECTION", {"Entries": "0"})
    _, by_location = collection_indexes(plan.output_root)
    next_id = next_track_id(plan.output_root)
    dest_to_id: dict[str, str] = {}

    for item in plan.unique:
        if not item.dest_path.exists():
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
    if existed:
        present = set(playlist_keys(wav_node))
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
    else:
        for item in plan.tracks:
            if item.dest_location not in dest_to_id:
                continue
            tid = dest_to_id[item.dest_location]
            ET.SubElement(wav_node, "TRACK", {"Key": tid})
            appended += 1
    rewrite_counts(plan.output_root, wav_node)
    return appended


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


