"""Rekordbox DJ_PLAYLISTS XML read and navigate helpers."""

from __future__ import annotations

import copy
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, unquote

from cli_error import CliError

XML_CANDIDATE_RELATIVE = (
    Path("rekordbox.xml"),
    Path("Rekordbox-collection.xml"),
    Path.home() / "Documents" / "rekordbox" / "rekordbox.xml",
    Path.home() / "Documents" / "rekordbox" / "Playlists" / "Rekordbox-collection.xml",
)


def load_dj_playlists(path: Path) -> ET.Element:
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise CliError(f"Invalid XML: {path}: {exc}") from exc
    root = tree.getroot()
    if root.tag != "DJ_PLAYLISTS":
        raise CliError(f"{path} is not a Rekordbox DJ_PLAYLISTS collection")
    if root.find("COLLECTION") is None or root.find("PLAYLISTS") is None:
        raise CliError(f"{path} is not a valid Rekordbox DJ_PLAYLISTS collection")
    if root.find("PLAYLISTS/NODE") is None:
        raise CliError(f"{path} is not a valid Rekordbox DJ_PLAYLISTS collection")
    return root


def skeleton_from(source_root: ET.Element) -> ET.Element:
    version = source_root.get("Version", "1.0.0")
    root = ET.Element("DJ_PLAYLISTS", {"Version": version})
    product = source_root.find("PRODUCT")
    if product is not None:
        root.append(copy.deepcopy(product))
    else:
        ET.SubElement(root, "PRODUCT", {"Name": "rekordbox", "Version": "", "Company": ""})
    ET.SubElement(root, "COLLECTION", {"Entries": "0"})
    playlists = ET.SubElement(root, "PLAYLISTS")
    ET.SubElement(playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": "0"})
    return root


def _walk_playlist_nodes(
    node: ET.Element, folder_parts: list[str]
) -> Iterable[tuple[str, str, str, ET.Element]]:
    """Yield (kind, folder_path, name, node) for folders and playlists under a tree.

    kind is \"folder\" or \"playlist\". ROOT is omitted; empty folders are included.
    """
    if node.tag != "NODE":
        return
    name = node.get("Name") or ""
    if node.get("Type") == "1":
        folder = " / ".join(folder_parts) if folder_parts else ""
        yield "playlist", folder, name, node
        return
    if node.get("Type") == "0":
        if name != "ROOT":
            folder = " / ".join(folder_parts) if folder_parts else ""
            yield "folder", folder, name, node
            next_parts = [*folder_parts, name]
        else:
            next_parts = folder_parts
        for child in node:
            yield from _walk_playlist_nodes(child, next_parts)


def _walk_playlists(
    node: ET.Element, folder_parts: list[str]
) -> Iterable[tuple[str, str, ET.Element]]:
    """Yield (folder_path, name, node) for playlist leaves under a folder tree."""
    for kind, folder, name, el in _walk_playlist_nodes(node, folder_parts):
        if kind == "playlist":
            yield folder, name, el


def iter_playlist_nodes(root: ET.Element) -> list[tuple[str, str, str, ET.Element]]:
    """All folders and playlists as (kind, folder_path, name, node), depth-first."""
    playlists = root.find("PLAYLISTS")
    if playlists is None:
        return []
    found: list[tuple[str, str, str, ET.Element]] = []
    for child in playlists:
        found.extend(_walk_playlist_nodes(child, []))
    return found


def iter_playlists(root: ET.Element) -> list[tuple[str, str, ET.Element]]:
    """All playlists as (folder_path, name, node), depth-first."""
    return [
        (folder, name, node)
        for kind, folder, name, node in iter_playlist_nodes(root)
        if kind == "playlist"
    ]


def find_playlists_by_name(root: ET.Element, name: str) -> list[ET.Element]:
    return [node for _folder, pl_name, node in iter_playlists(root) if pl_name == name]


def playlist_label(folder: str, name: str) -> str:
    return f"{folder} / {name}" if folder else name


def resolve_playlist(
    root: ET.Element,
    playlist_name: str,
    folder: str | None = None,
) -> tuple[tuple[str, str, ET.Element] | None, list[str]]:
    """
    Find one playlist by leaf name, folder+name, or 'folder / name' path.
    Ambiguous leaf names are an error unless folder (or a full path) is given.
    """
    entries = iter_playlists(root)
    if folder is not None:
        matches = [
            entry for entry in entries if entry[0] == folder and entry[1] == playlist_name
        ]
        label = playlist_label(folder, playlist_name)
        if not matches:
            return None, [f"playlist not found: {label}"]
        if len(matches) > 1:
            return None, [f"duplicate playlist name: {label}"]
        return matches[0], []

    by_name = [entry for entry in entries if entry[1] == playlist_name]
    if len(by_name) == 1:
        return by_name[0], []
    if len(by_name) > 1:
        listed = "\n".join(f"  {playlist_label(f, n)}" for f, n, _ in by_name)
        return None, [f"duplicate playlist name: {playlist_name}\n{listed}"]

    by_path = [entry for entry in entries if playlist_label(entry[0], entry[1]) == playlist_name]
    if len(by_path) == 1:
        return by_path[0], []
    if len(by_path) > 1:
        return None, [f"duplicate playlist name: {playlist_name}"]
    return None, [f"playlist not found: {playlist_name}"]


def playlist_track_count(node: ET.Element) -> int:
    return len(node.findall("TRACK"))


def path_is_under_documents(path: Path, *, home: Path | None = None) -> bool:
    """True if *path* is under ~/Documents without stating the filesystem."""
    base = home if home is not None else Path.home()
    documents = (base / "Documents").expanduser()
    expanded = path.expanduser()
    try:
        expanded.relative_to(documents)
        return True
    except ValueError:
        return False


def discover_xml_candidates(
    cwd: Path | None = None,
    candidates: tuple[Path, ...] | None = None,
    *,
    documents_accessible: bool = True,
    home: Path | None = None,
) -> list[Path]:
    """Existing XML paths from the default probe list (deduped, absolute).

    When *documents_accessible* is False, candidates under Documents are skipped
    without stating them (avoids hanging on macOS TCC dismiss).
    """
    base = cwd if cwd is not None else Path.cwd()
    probe = candidates if candidates is not None else XML_CANDIDATE_RELATIVE
    found: list[Path] = []
    seen: set[Path] = set()
    for rel in probe:
        path = rel if rel.is_absolute() else (base / rel)
        if not documents_accessible and path_is_under_documents(path, home=home):
            continue
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        if not resolved.is_file() or resolved in seen:
            continue
        seen.add(resolved)
        found.append(resolved)
    return found


def parse_playlist_selection(
    text: str,
    entries: list[tuple[str, str, ET.Element]],
) -> tuple[list[tuple[str, str, ET.Element]], list[str]]:
    """
    Parse '1', '1,4,7', or 'all' into playlist entries.
    Rejects selecting two playlists that share the same Name.
    """
    errors: list[str] = []
    raw = text.strip().lower()
    if not raw:
        return [], ["empty selection"]
    if raw == "all":
        indices = list(range(len(entries)))
    else:
        indices = []
        for part in text.replace(" ", "").split(","):
            if not part:
                continue
            if not part.isdigit():
                errors.append(f"invalid selection: {part!r}")
                continue
            n = int(part)
            if n < 1 or n > len(entries):
                errors.append(f"selection out of range: {n}")
                continue
            indices.append(n - 1)
        if not indices and not errors:
            errors.append("empty selection")
    if errors:
        return [], errors
    chosen = [entries[i] for i in indices]
    # Deduplicate by index order while keeping first occurrence
    seen_idx: set[int] = set()
    unique_chosen: list[tuple[str, str, ET.Element]] = []
    for i, entry in zip(indices, chosen):
        if i in seen_idx:
            continue
        seen_idx.add(i)
        unique_chosen.append(entry)
    names = [name for _f, name, _n in unique_chosen]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        listed = ", ".join(sorted(dupes))
        return [], [
            f"cannot select multiple playlists with the same name: {listed}"
        ]
    return unique_chosen, []


def collection_indexes(root: ET.Element) -> tuple[dict[str, ET.Element], dict[str, ET.Element]]:
    by_id: dict[str, ET.Element] = {}
    by_location: dict[str, ET.Element] = {}
    collection = root.find("COLLECTION")
    if collection is None:
        return by_id, by_location
    for track in collection.findall("TRACK"):
        tid = track.get("TrackID")
        loc = track.get("Location")
        if tid is not None:
            by_id[tid] = track
        if loc:
            by_location[loc] = track
    return by_id, by_location


def decode_location(url: str) -> Path | None:
    if not url:
        return None
    rest: str | None = None
    if url.startswith("file://localhost"):
        rest = url[len("file://localhost") :]
    elif url.startswith("file://"):
        rest = url[len("file://") :]
    else:
        return None
    if not rest:
        return None
    path = unquote(rest)
    if not path.startswith("/"):
        # file://localhost/C:/... already has a slash before the drive.
        return None
    return Path(path)


def encode_location(path: Path) -> str:
    posix = unicodedata.normalize("NFC", path.as_posix())
    quoted = quote(posix, safe="/:")
    if quoted.startswith("/"):
        return "file://localhost" + quoted
    return "file://localhost/" + quoted


def resolve_playlist_tracks(
    source_root: ET.Element, playlist: ET.Element
) -> tuple[list[ET.Element], list[str]]:
    errors: list[str] = []
    by_id, by_location = collection_indexes(source_root)
    key_type = playlist.get("KeyType", "0")
    resolved: list[ET.Element] = []
    for entry in playlist.findall("TRACK"):
        key = entry.get("Key")
        if key is None or key == "":
            errors.append("playlist entry missing Key")
            continue
        if key_type == "1":
            track = by_location.get(key)
        else:
            track = by_id.get(key)
        if track is None:
            errors.append(f"missing collection track for Key={key}")
            continue
        resolved.append(track)
    return resolved, errors
