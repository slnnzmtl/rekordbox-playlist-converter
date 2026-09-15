"""Provisional Import XML + converter-manifest edit draft (domain, no Tk)."""

from __future__ import annotations

import copy
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import converter_manifest as cm
from cli_error import CliError
from convert.paths import abs_path, collision_key
from gui_prefs.paths import import_xml_path
from rekordbox_xml import (
    UNKNOWN_PLAYLIST_NAME,
    decode_location,
    iter_playlist_nodes,
    load_dj_playlists,
    playlist_label,
)
from xml_output import rewrite_counts, write_import_xml

KIND_TO_FORMAT = {"WAV File": "wav", "AIFF File": "aiff"}
STAGING_PREFIX = "import-edit-trash-"
ACTION_REMOVE_FROM_PLAYLIST = "Remove from playlist"
ACTION_MOVE_TO_TRASH = "Move to Trash"


@dataclass(frozen=True)
class EditPreviewRow:
    track: str
    action: str
    playlist: str = ""


def _find_collection_track(root: ET.Element, track_id: str) -> ET.Element | None:
    collection = root.find("COLLECTION")
    if collection is None:
        return None
    for track in collection.findall("TRACK"):
        if track.get("TrackID") == track_id:
            return track
    return None


def collection_track_label(root: ET.Element, track_id: str) -> str:
    """Artist - Name for a collection TrackID, or a missing-track fallback."""
    track = _find_collection_track(root, track_id)
    if track is None:
        return "! (missing track)"
    artist = (track.get("Artist") or "").strip()
    title = (track.get("Name") or "").strip()
    if artist and title:
        return f"{artist} - {title}"
    return title or artist or "! (missing track)"


@dataclass
class FileFingerprint:
    path: Path
    mtime_ns: int
    size: int

    def matches_disk(self) -> bool:
        try:
            st = self.path.stat()
        except OSError:
            return False
        return st.st_mtime_ns == self.mtime_ns and st.st_size == self.size


def fingerprint_file(path: Path) -> FileFingerprint:
    st = path.stat()
    return FileFingerprint(path=path, mtime_ns=st.st_mtime_ns, size=st.st_size)


@dataclass
class DestOwner:
    source_key: str
    output_format: str
    relative_dest: str


@dataclass
class ImportEditDraft:
    library_dir: Path
    root: ET.Element
    original_root: ET.Element
    manifest: cm.ConverterManifest
    xml_path: Path
    manifest_path: Path
    xml_fingerprint: FileFingerprint
    manifest_fingerprint: FileFingerprint
    dirty: bool = False
    trash_relative_dests: set[str] = field(default_factory=set)

    def mark_dirty(self) -> None:
        self.dirty = True


def build_dest_owner_index(
    manifest: cm.ConverterManifest,
) -> dict[str, DestOwner]:
    """collision_key(dest) -> unique owner. Raises if duplicates slip through."""
    index: dict[str, DestOwner] = {}
    for source_key, formats in manifest.tracks.items():
        for fmt, record in formats.items():
            dest = record.get("dest") or ""
            if not dest:
                continue
            ck = collision_key(dest)
            if ck in index:
                raise CliError(
                    f"manifest duplicate dest ownership after collision_key: {dest!r}"
                )
            index[ck] = DestOwner(
                source_key=source_key,
                output_format=fmt,
                relative_dest=dest,
            )
    return index


def _relative_dest_under_library(library_dir: Path, path: Path) -> str:
    """Return POSIX relative dest under library, or raise CliError."""
    root = abs_path(library_dir).resolve()
    try:
        resolved = path.expanduser().resolve()
    except OSError as exc:
        raise CliError(f"cannot resolve path: {path}: {exc}") from exc
    if path.is_symlink() or resolved.is_symlink():
        raise CliError(f"collection Location must not be a symlink: {path}")
    try:
        rel = resolved.relative_to(root)
    except ValueError as exc:
        raise CliError(
            f"collection Location resolves outside library: {path}"
        ) from exc
    parts = rel.parts
    if len(parts) != 2 or any(p in ("", ".", "..") for p in parts):
        raise CliError(
            f"collection Location must be WAV|AIFF/<file> under library: {path}"
        )
    return PurePosixPath(*parts).as_posix()


def resolve_collection_owner(
    track: ET.Element,
    *,
    library_dir: Path,
    owners: dict[str, DestOwner],
) -> DestOwner:
    """Match a COLLECTION TRACK to exactly one manifest dest owner."""
    kind = (track.get("Kind") or "").strip()
    fmt = KIND_TO_FORMAT.get(kind)
    if fmt is None:
        raise CliError(
            f"Kind must be WAV File or AIFF File, got {kind!r}"
        )
    loc = track.get("Location") or ""
    path = decode_location(loc)
    if path is None:
        raise CliError(f"invalid collection Location: {loc!r}")
    # Symlink check on the Location path before resolve collapses it.
    if path.is_symlink():
        raise CliError(f"collection Location must not be a symlink: {path}")
    relative = _relative_dest_under_library(library_dir, path)
    expected_dir = "AIFF" if fmt == "aiff" else "WAV"
    expected_ext = f".{fmt}"
    directory, filename = relative.split("/", 1)
    if directory != expected_dir or not filename.lower().endswith(expected_ext):
        raise CliError(
            f"collection Location does not match Kind {kind!r}: {relative}"
        )
    owner = owners.get(collision_key(relative))
    if owner is None:
        raise CliError(f"unmanaged collection destination: {relative}")
    if owner.output_format != fmt:
        raise CliError(
            f"manifest format mismatch for {relative}: "
            f"Kind={fmt!r} owner={owner.output_format!r}"
        )
    if collision_key(owner.relative_dest) != collision_key(relative):
        raise CliError(f"manifest dest mismatch for {relative}")
    return owner


def _validate_edit_consistency(
    root: ET.Element,
    *,
    library_dir: Path,
    manifest: cm.ConverterManifest,
) -> None:
    """Raise CliError on fatal load-time problems. Dangling Keys are allowed."""
    owners = build_dest_owner_index(manifest)
    collection = root.find("COLLECTION")
    if collection is None:
        raise CliError("Import XML missing COLLECTION")

    by_id: dict[str, ET.Element] = {}
    locations: set[str] = set()
    for track in collection.findall("TRACK"):
        tid = track.get("TrackID") or ""
        if not tid or not tid.isdigit():
            raise CliError(f"TrackID must be numeric, got {tid!r}")
        if tid in by_id:
            raise CliError(f"duplicate TrackID {tid}")
        by_id[tid] = track
        loc = track.get("Location") or ""
        if loc in locations:
            raise CliError(f"duplicate Location {loc}")
        locations.add(loc)
        resolve_collection_owner(
            track, library_dir=library_dir, owners=owners
        )

    seen_playlist_paths: set[tuple[str, str]] = set()
    for kind, folder, name, node in iter_playlist_nodes(root):
        if kind != "playlist":
            continue
        path_key = (folder, name)
        if path_key in seen_playlist_paths:
            raise CliError(
                f"duplicate playlist path: {playlist_label(folder, name)}"
            )
        seen_playlist_paths.add(path_key)
        if node.get("KeyType", "0") != "0":
            raise CliError(
                f"playlist {name!r} KeyType must be 0, got {node.get('KeyType')!r}"
            )
        keys_in_playlist: set[str] = set()
        for entry in node.findall("TRACK"):
            key = entry.get("Key")
            if key is None or key == "":
                raise CliError(
                    f"playlist {name!r} has blank or missing Key"
                )
            if key in keys_in_playlist:
                raise CliError(
                    f"playlist {name!r} has duplicate Key {key!r}"
                )
            keys_in_playlist.add(key)


def load_import_edit_draft(library_dir: Path) -> ImportEditDraft:
    """Load Import XML + required manifest into a provisional edit draft."""
    library = abs_path(library_dir)
    xml_path = import_xml_path(library)
    man_path = cm.manifest_path(library)
    if not xml_path.is_file():
        raise CliError(f"Import XML not found: {xml_path}")
    if not man_path.is_file():
        raise CliError(f"Converter manifest not found: {man_path}")
    try:
        root = load_dj_playlists(xml_path)
    except CliError:
        raise
    try:
        manifest = cm.load_manifest(library)
    except cm.ManifestError as exc:
        raise CliError(str(exc)) from exc

    _validate_edit_consistency(root, library_dir=library, manifest=manifest)

    return ImportEditDraft(
        library_dir=library,
        root=copy.deepcopy(root),
        original_root=copy.deepcopy(root),
        manifest=cm.ConverterManifest(
            tracks=copy.deepcopy(manifest.tracks),
        ),
        xml_path=xml_path,
        manifest_path=man_path,
        xml_fingerprint=fingerprint_file(xml_path),
        manifest_fingerprint=fingerprint_file(man_path),
    )


@dataclass
class EditImpact:
    playlist_entries_removed: int = 0
    collection_removed: int = 0
    files_to_trash: list[str] = field(default_factory=list)
    missing_files_cleaned: int = 0
    playlist_refs_removed: int = 0


def _find_playlist_node(
    draft: ImportEditDraft, *, folder: str, name: str
) -> ET.Element:
    for kind, folder_path, node_name, node in iter_playlist_nodes(draft.root):
        if kind != "playlist":
            continue
        if folder_path == folder and node_name == name:
            return node
    raise CliError(f"playlist not found: {playlist_label(folder, name)}")


def _find_playlist_parent(
    draft: ImportEditDraft, *, folder: str, name: str
) -> tuple[ET.Element, ET.Element]:
    """Return (parent_folder_node, playlist_node)."""
    playlists = draft.root.find("PLAYLISTS")
    if playlists is None:
        raise CliError("Import XML missing PLAYLISTS")
    root_node = None
    for child in playlists:
        if child.tag == "NODE" and child.get("Name") == "ROOT" and child.get("Type") == "0":
            root_node = child
            break
    if root_node is None:
        raise CliError("Import XML missing PLAYLISTS/ROOT")

    def walk(
        node: ET.Element, folder_parts: list[str]
    ) -> tuple[ET.Element, ET.Element] | None:
        for child in list(node):
            if child.tag != "NODE":
                continue
            child_name = child.get("Name") or ""
            if child.get("Type") == "1":
                folder_path = " / ".join(folder_parts) if folder_parts else ""
                if folder_path == folder and child_name == name:
                    return node, child
            elif child.get("Type") == "0":
                if child_name == "ROOT":
                    next_parts = folder_parts
                else:
                    next_parts = [*folder_parts, child_name]
                found = walk(child, next_parts)
                if found is not None:
                    return found
        return None

    found = walk(root_node, [])
    if found is None:
        raise CliError(f"playlist not found: {playlist_label(folder, name)}")
    return found


def track_id_reference_counts(root: ET.Element) -> dict[str, int]:
    """Count TrackID Key references across the full playlist hierarchy."""
    counts: dict[str, int] = {}
    for kind, _folder, _name, node in iter_playlist_nodes(root):
        if kind != "playlist":
            continue
        for entry in node.findall("TRACK"):
            key = entry.get("Key") or ""
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
    return counts


def _schedule_orphan_collection_removal(
    draft: ImportEditDraft,
    track: ET.Element,
    *,
    owners: dict[str, DestOwner],
    impact: EditImpact,
) -> None:
    owner = resolve_collection_owner(
        track, library_dir=draft.library_dir, owners=owners
    )
    dest_path = cm.resolve_dest_under_wav_dir(
        draft.library_dir, owner.relative_dest
    )
    if dest_path.is_file():
        draft.trash_relative_dests.add(owner.relative_dest)
        impact.files_to_trash.append(owner.relative_dest)
    else:
        impact.missing_files_cleaned += 1
    draft.manifest.remove_assignment(owner.source_key, owner.output_format)
    collection = draft.root.find("COLLECTION")
    if collection is not None and track in list(collection):
        collection.remove(track)
    impact.collection_removed += 1


def _playlist_key_map(root: ET.Element) -> dict[tuple[str, str], list[str]]:
    mapping: dict[tuple[str, str], list[str]] = {}
    for kind, folder, name, node in iter_playlist_nodes(root):
        if kind != "playlist":
            continue
        mapping[(folder, name)] = [e.get("Key") or "" for e in node.findall("TRACK")]
    return mapping


def _original_relative_dest(draft: ImportEditDraft, track_id: str) -> str | None:
    track = _find_collection_track(draft.original_root, track_id)
    if track is None:
        return None
    path = decode_location(track.get("Location") or "")
    if path is None:
        return None
    try:
        return _relative_dest_under_library(draft.library_dir, path)
    except CliError:
        return None


def _collection_track_ids(root: ET.Element) -> list[str]:
    collection = root.find("COLLECTION")
    if collection is None:
        return []
    return [
        tid
        for track in collection.findall("TRACK")
        if (tid := track.get("TrackID") or "")
    ]


def preview_save(draft: ImportEditDraft) -> list[EditPreviewRow]:
    """Net pending edits vs the loaded Import XML (for the Save confirmation)."""
    orig_playlists = _playlist_key_map(draft.original_root)
    curr_playlists = _playlist_key_map(draft.root)
    rows: list[EditPreviewRow] = []
    for (folder, name), orig_keys in orig_playlists.items():
        label = playlist_label(folder, name)
        remaining = set(curr_playlists.get((folder, name), []))
        for key in orig_keys:
            if not key or key in remaining:
                continue
            dest = _original_relative_dest(draft, key)
            action = (
                ACTION_MOVE_TO_TRASH
                if dest and dest in draft.trash_relative_dests
                else ACTION_REMOVE_FROM_PLAYLIST
            )
            rows.append(
                EditPreviewRow(
                    track=collection_track_label(draft.original_root, key),
                    action=action,
                    playlist=label,
                )
            )
    # Unknown: collection-only tracks (never in a playlist NODE) removed for Trash.
    orig_playlist_keys = {
        key for keys in orig_playlists.values() for key in keys if key
    }
    curr_ids = set(_collection_track_ids(draft.root))
    for key in _collection_track_ids(draft.original_root):
        if key in curr_ids or key in orig_playlist_keys:
            continue
        dest = _original_relative_dest(draft, key)
        action = (
            ACTION_MOVE_TO_TRASH
            if dest and dest in draft.trash_relative_dests
            else ACTION_REMOVE_FROM_PLAYLIST
        )
        rows.append(
            EditPreviewRow(
                track=collection_track_label(draft.original_root, key),
                action=action,
                playlist=UNKNOWN_PLAYLIST_NAME,
            )
        )
    return rows


def remove_track_from_playlist(
    draft: ImportEditDraft,
    *,
    folder: str,
    name: str,
    track_id: str,
) -> None:
    """Remove one Key from one playlist; preserve collection and other refs."""
    node = _find_playlist_node(draft, folder=folder, name=name)
    removed = False
    for entry in list(node.findall("TRACK")):
        if entry.get("Key") == track_id:
            node.remove(entry)
            removed = True
            break
    if not removed:
        raise CliError(
            f"track {track_id!r} not in playlist "
            f"{playlist_label(folder, name)}"
        )
    rewrite_counts(draft.root)
    draft.mark_dirty()


def remove_playlist(
    draft: ImportEditDraft, *, folder: str, name: str
) -> EditImpact:
    """Remove one playlist NODE; orphan collection rows that lose all refs."""
    parent, node = _find_playlist_parent(draft, folder=folder, name=name)
    keys = [e.get("Key") or "" for e in node.findall("TRACK")]
    impact = EditImpact(playlist_entries_removed=len(keys))
    # Snapshot collection index and owners before mutating.
    owners = build_dest_owner_index(draft.manifest)
    collection = draft.root.find("COLLECTION")
    by_id = {
        (t.get("TrackID") or ""): t
        for t in (collection.findall("TRACK") if collection is not None else [])
    }
    parent.remove(node)
    refs = track_id_reference_counts(draft.root)
    for key in keys:
        if not key:
            continue
        if refs.get(key, 0) > 0:
            continue
        track = by_id.get(key)
        if track is None:
            # Dangling Key: no collection/manifest cleanup.
            continue
        _schedule_orphan_collection_removal(
            draft, track, owners=owners, impact=impact
        )
    rewrite_counts(draft.root)
    draft.mark_dirty()
    return impact


def remove_track_from_collection(
    draft: ImportEditDraft, *, track_id: str
) -> EditImpact:
    """Remove TrackID from every playlist and the collection; schedule Trash."""
    collection = draft.root.find("COLLECTION")
    if collection is None:
        raise CliError("Import XML missing COLLECTION")
    track = _find_collection_track(draft.root, track_id)
    if track is None:
        raise CliError(f"collection track not found: {track_id}")

    impact = EditImpact()
    owners = build_dest_owner_index(draft.manifest)

    for kind, _folder, _name, node in iter_playlist_nodes(draft.root):
        if kind != "playlist":
            continue
        for entry in list(node.findall("TRACK")):
            if entry.get("Key") == track_id:
                node.remove(entry)
                impact.playlist_refs_removed += 1

    _schedule_orphan_collection_removal(
        draft, track, owners=owners, impact=impact
    )
    rewrite_counts(draft.root)
    draft.mark_dirty()
    return impact


def move_path_to_trash(path: Path) -> None:
    """Move *path* to the macOS Trash via NSFileManager (JXA).

    Finder cannot resolve hidden ``.`` paths; NSFileManager can. The POSIX
    path is passed as an osascript argument, not interpolated into a script.
    """
    if sys.platform != "darwin":
        raise CliError("Moving files to Trash requires macOS")
    posix = str(path.resolve())
    script = (
        "ObjC.import('Foundation');\n"
        "function run(argv) {\n"
        "  var fm = $.NSFileManager.defaultManager;\n"
        "  var url = $.NSURL.fileURLWithPath(argv[0]);\n"
        "  var err = $();\n"
        "  var ok = fm.trashItemAtURLResultingItemURLError(url, null, err);\n"
        "  if (!ok) {\n"
        "    var msg = 'Trash failed';\n"
        "    try { msg = ObjC.unwrap(err.localizedDescription); } catch (e) {}\n"
        "    throw new Error(msg);\n"
        "  }\n"
        "}\n"
    )
    try:
        proc = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script, posix],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CliError(f"Trash failed: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise CliError(f"Trash failed: {detail or proc.returncode}")


def _restore_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".import-edit-restore-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _unstage_files(
    library_dir: Path, staging_dir: Path, relative_dests: list[str]
) -> None:
    for rel in relative_dests:
        staged = staging_dir.joinpath(*PurePosixPath(rel).parts)
        if not staged.exists():
            continue
        dest = library_dir.joinpath(*PurePosixPath(rel).parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, dest)


def save_import_edit_draft(
    draft: ImportEditDraft,
    *,
    move_to_trash: Callable[[Path], None] | None = None,
) -> None:
    """Persist draft with staging-before-Trash caught-failure atomicity."""
    if not draft.dirty:
        return
    trash_fn = move_to_trash or move_path_to_trash
    library = abs_path(draft.library_dir)
    scheduled = sorted(draft.trash_relative_dests)

    if scheduled and move_to_trash is None and sys.platform != "darwin":
        raise CliError("Moving files to Trash requires macOS")

    # Validate draft XML before touching disk.
    try:
        rewrite_counts(draft.root)
        from xml_output import validate_import_xml

        problems = validate_import_xml(draft.root)
        if problems:
            raise CliError(
                "import XML failed integrity checks:\n" + "\n".join(problems)
            )
    except CliError as exc:
        raise CliError(f"Save failed (validation): {exc}") from exc

    existing_to_stage: list[str] = []
    for rel in scheduled:
        try:
            dest = cm.resolve_dest_under_wav_dir(library, rel)
        except cm.ManifestError as exc:
            raise CliError(f"Save failed (preflight): {exc}") from exc
        if dest.is_file():
            if dest.is_symlink():
                raise CliError(
                    f"Save failed (preflight): symlink not allowed: {rel}"
                )
            existing_to_stage.append(rel)

    if not draft.xml_fingerprint.matches_disk():
        raise CliError(
            "Save failed (fingerprint): Import XML changed on disk since editing began"
        )
    if not draft.manifest_fingerprint.matches_disk():
        raise CliError(
            "Save failed (fingerprint): converter manifest changed on disk "
            "since editing began"
        )

    xml_backup = draft.xml_path.read_bytes()
    manifest_backup = draft.manifest_path.read_bytes()

    staging_dir = Path(
        tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=str(library))
    )
    staged: list[str] = []
    metadata_written = False
    try:
        for rel in existing_to_stage:
            src = library.joinpath(*PurePosixPath(rel).parts)
            dest = staging_dir.joinpath(*PurePosixPath(rel).parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(src, dest)
            except OSError as exc:
                raise CliError(f"Save failed (staging): {exc}") from exc
            staged.append(rel)

        try:
            write_import_xml(draft.root, draft.xml_path)
        except CliError as exc:
            raise CliError(f"Save failed (XML): {exc}") from exc
        except OSError as exc:
            raise CliError(f"Save failed (XML): {exc}") from exc
        metadata_written = True

        try:
            cm.save_manifest(draft.manifest, library)
        except Exception as exc:
            raise CliError(f"Save failed (manifest): {exc}") from exc

        if staged:
            try:
                trash_fn(staging_dir)
            except Exception as exc:
                if staging_dir.exists():
                    # Caught Trash failure with staging present → full rollback.
                    _restore_bytes(draft.xml_path, xml_backup)
                    _restore_bytes(draft.manifest_path, manifest_backup)
                    _unstage_files(library, staging_dir, staged)
                    try:
                        shutil.rmtree(staging_dir, ignore_errors=True)
                    except OSError:
                        pass
                    raise CliError(f"Save failed (Trash): {exc}") from exc
                # Staging gone but Trash reported failure → ambiguous.
                raise CliError(
                    "Save failed (Trash): file state could not be proven after "
                    f"Trash error: {exc}"
                ) from exc
            if staging_dir.exists():
                # Trash returned without error but left staging → treat as failure.
                _restore_bytes(draft.xml_path, xml_backup)
                _restore_bytes(draft.manifest_path, manifest_backup)
                _unstage_files(library, staging_dir, staged)
                shutil.rmtree(staging_dir, ignore_errors=True)
                raise CliError(
                    "Save failed (Trash): staging directory still present"
                )
        else:
            shutil.rmtree(staging_dir, ignore_errors=True)

        draft.dirty = False
        draft.trash_relative_dests.clear()
        draft.xml_fingerprint = fingerprint_file(draft.xml_path)
        draft.manifest_fingerprint = fingerprint_file(draft.manifest_path)
    except CliError:
        if metadata_written and staging_dir.exists() and staged:
            # Failure after metadata write but before Trash handling above
            # (e.g. XML ok, manifest fail): restore.
            try:
                _restore_bytes(draft.xml_path, xml_backup)
                _restore_bytes(draft.manifest_path, manifest_backup)
                _unstage_files(library, staging_dir, staged)
                shutil.rmtree(staging_dir, ignore_errors=True)
            except OSError:
                pass
        elif not metadata_written and staging_dir.exists():
            try:
                _unstage_files(library, staging_dir, staged)
                shutil.rmtree(staging_dir, ignore_errors=True)
            except OSError:
                pass
        raise
    except Exception as exc:
        # Unexpected: best-effort rollback when staging still present.
        if staging_dir.exists():
            try:
                _restore_bytes(draft.xml_path, xml_backup)
                _restore_bytes(draft.manifest_path, manifest_backup)
                _unstage_files(library, staging_dir, staged)
                shutil.rmtree(staging_dir, ignore_errors=True)
            except OSError:
                pass
        raise CliError(f"Save failed: {exc}") from exc
