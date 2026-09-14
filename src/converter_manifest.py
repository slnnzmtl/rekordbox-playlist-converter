"""Per-format sticky dest assignments under wav_dir (private to convert/prepare batch)."""

from __future__ import annotations

import json
import os
import re
import tempfile
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from cli_error import CliError
from convert.paths import abs_path, collision_key, same_file
from convert.quality import (
    BIT_DEPTHS,
    FORMAT_DIR_NAMES,
    OUTPUT_FORMATS,
    SAMPLE_RATES,
)

MANIFEST_NAME = ".rekordbox-converter-manifest.json"
MANIFEST_VERSION = 2
MANIFEST_LAYOUT = "format-flat"
SUPPORTED_FORMATS = OUTPUT_FORMATS
_FORMAT_DIRS = FORMAT_DIR_NAMES
RECORD_KEYS = frozenset({"dest", "state", "source", "metadata", "output", "recipe"})
SOURCE_OUTPUT_KEYS = frozenset({"size", "mtime_ns", "hash"})
METADATA_KEYS = frozenset({"signature"})
RECIPE_KEYS = frozenset(
    {"format", "bit_depth", "sample_rate", "channels", "revision"}
)
ALLOWED_STATES = frozenset({"complete", "incomplete", "unverified"})
ALLOWED_CHANNELS = frozenset({2})
_SHA256_HASH = re.compile(r"^sha256:[0-9a-fA-F]{64}$")


class ManifestError(CliError):
    """Invalid or unusable converter manifest."""


@dataclass
class ConverterManifest:
    """In-memory source→(format→dest) assignments for one wav_dir."""

    tracks: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)

    def get_dest(self, source_key: str, output_format: str) -> str | None:
        dest = self.tracks.get(source_key, {}).get(output_format, {}).get("dest")
        return dest or None

    def set_dest(self, source_key: str, output_format: str, dest: str) -> None:
        self.tracks.setdefault(source_key, {})[output_format] = {"dest": dest}

    def dest_collision_keys(
        self, *, exclude: tuple[str, str] | None = None
    ) -> set[str]:
        """collision_key of every assigned dest, optionally excluding one owner."""
        keys: set[str] = set()
        for sk, formats in self.tracks.items():
            for fmt, record in formats.items():
                if exclude is not None and (sk, fmt) == exclude:
                    continue
                dest = record.get("dest")
                if dest:
                    keys.add(collision_key(dest))
        return keys

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": MANIFEST_VERSION,
            "layout": MANIFEST_LAYOUT,
            "tracks": self.tracks,
        }


def manifest_path(wav_dir: Path) -> Path:
    return abs_path(wav_dir) / MANIFEST_NAME


def empty_manifest() -> ConverterManifest:
    return ConverterManifest()


def _require_under_wav_dir(wav_dir: Path, dest: Path, detail: str) -> Path:
    root = abs_path(wav_dir).resolve()
    try:
        dest.relative_to(root)
    except ValueError as exc:
        raise ManifestError(detail) from exc
    return dest


def resolve_dest_under_wav_dir(wav_dir: Path, relative_dest: str) -> Path:
    """Resolve relative_dest under wav_dir; raise if it escapes wav_dir."""
    root = abs_path(wav_dir).resolve()
    dest = root.joinpath(*PurePosixPath(relative_dest).parts).resolve()
    return _require_under_wav_dir(
        wav_dir,
        dest,
        f"manifest dest resolves outside wav_dir: {relative_dest!r}",
    )


def ensure_dest_path_under_wav_dir(wav_dir: Path, dest_path: Path) -> Path:
    """Resolve dest_path and refuse if it is outside wav_dir."""
    dest = dest_path.expanduser().resolve()
    return _require_under_wav_dir(
        wav_dir,
        dest,
        f"dest path resolves outside wav_dir: {dest_path}",
    )


def _validate_dest_relative(dest: object, *, fmt: str, wav_dir: Path) -> str | None:
    """Return an error message, or None if dest is valid under wav_dir."""
    if not isinstance(dest, str) or not dest:
        return "manifest dest must be a non-empty string"
    if "\\" in dest:
        return f"manifest dest must be a relative posix path (no backslashes): {dest!r}"
    if dest.startswith("/") or PurePosixPath(dest).is_absolute():
        return f"manifest dest must be relative (not absolute): {dest!r}"
    # Split the raw string so '.' / '..' are not normalized away by pathlib.
    parts = dest.split("/")
    if len(parts) != 2 or any(p in ("", ".", "..") for p in parts):
        return (
            "manifest dest must be exactly two components "
            f"(WAV/<file>.wav or AIFF/<file>.aiff): {dest!r}"
        )
    expected_dir = _FORMAT_DIRS[fmt]
    expected_ext = f".{fmt}"
    directory, filename = parts
    if directory != expected_dir:
        return (
            f"manifest dest directory must be {expected_dir!r} for format "
            f"{fmt!r}: {dest!r}"
        )
    if PurePosixPath(filename).name != filename:
        return f"manifest dest filename must be a single path segment: {dest!r}"
    if PurePosixPath(filename).suffix.lower() != expected_ext:
        return (
            f"manifest dest extension must match format {fmt!r}: {dest!r}"
        )
    try:
        resolve_dest_under_wav_dir(wav_dir, dest)
    except ManifestError as exc:
        return str(exc)
    return None


def _unknown_field_errors(
    obj: dict[str, Any], allowed: frozenset[str], where: str
) -> list[str]:
    extra = sorted(set(obj) - allowed)
    return [f"manifest unknown {where} field: {key!r}" for key in extra]


def _validate_optional_hash(value: object, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256_HASH.fullmatch(value) is None:
        return (
            f"manifest {where} hash must be sha256: followed by "
            "exactly 64 hexadecimal characters"
        )
    return None


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_stat_object(obj: dict[str, Any], where: str) -> list[str]:
    errors = _unknown_field_errors(obj, SOURCE_OUTPUT_KEYS, where)
    size = obj.get("size")
    if "size" in obj and (not _is_int(size) or size < 0):
        errors.append(f"manifest {where} size must be a non-negative integer")
    mtime_ns = obj.get("mtime_ns")
    if "mtime_ns" in obj and (not _is_int(mtime_ns) or mtime_ns < 0):
        errors.append(f"manifest {where} mtime_ns must be a non-negative integer")
    hash_err = _validate_optional_hash(obj.get("hash"), where)
    if hash_err:
        errors.append(hash_err)
    return errors


def _validate_recipe(recipe: dict[str, Any]) -> list[str]:
    errors = _unknown_field_errors(recipe, RECIPE_KEYS, "recipe")
    fmt = recipe.get("format")
    if "format" in recipe and fmt not in OUTPUT_FORMATS:
        errors.append(f"manifest recipe format must be wav or aiff, got {fmt!r}")
    bit_depth = recipe.get("bit_depth")
    if "bit_depth" in recipe and bit_depth not in BIT_DEPTHS:
        errors.append(
            f"manifest recipe bit_depth must be 16 or 24, got {bit_depth!r}"
        )
    sample_rate = recipe.get("sample_rate")
    if "sample_rate" in recipe and sample_rate not in SAMPLE_RATES:
        errors.append(
            "manifest recipe sample_rate must be 44100 or 48000, "
            f"got {sample_rate!r}"
        )
    channels = recipe.get("channels")
    if "channels" in recipe and channels not in ALLOWED_CHANNELS:
        errors.append(f"manifest recipe channels must be 2, got {channels!r}")
    revision = recipe.get("revision")
    if "revision" in recipe and (not _is_int(revision) or revision < 1):
        errors.append(
            "manifest recipe revision must be a positive integer, "
            f"got {revision!r}"
        )
    return errors


def _validate_optional_freshness(record: dict[str, Any]) -> list[str]:
    errors = _unknown_field_errors(record, RECORD_KEYS, "assignment")
    state = record.get("state")
    if state is not None and state not in ALLOWED_STATES:
        errors.append(
            "manifest state must be complete, incomplete, or unverified, "
            f"got {state!r}"
        )
    source = record.get("source")
    if "source" in record:
        if not isinstance(source, dict):
            errors.append("manifest source must be an object")
        else:
            errors.extend(_validate_stat_object(source, "source"))
    metadata = record.get("metadata")
    if "metadata" in record:
        if not isinstance(metadata, dict):
            errors.append("manifest metadata must be an object")
        else:
            errors.extend(_unknown_field_errors(metadata, METADATA_KEYS, "metadata"))
    output = record.get("output")
    if "output" in record:
        if not isinstance(output, dict):
            errors.append("manifest output must be an object")
        else:
            errors.extend(_validate_stat_object(output, "output"))
    recipe = record.get("recipe")
    if "recipe" in record:
        if not isinstance(recipe, dict):
            errors.append("manifest recipe must be an object")
        else:
            errors.extend(_validate_recipe(recipe))
    return errors


def validate_manifest_data(data: object, wav_dir: Path) -> list[str]:
    """Return validation errors for parsed manifest JSON (empty if ok)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["manifest root must be a JSON object"]
    if data.get("version") != MANIFEST_VERSION:
        found = data.get("version")
        if found == 1:
            errors.append(
                "manifest version 1 is unsupported; delete "
                f"{MANIFEST_NAME} or choose a new output folder"
            )
        else:
            errors.append(
                f"manifest version must be {MANIFEST_VERSION}, "
                f"got {found!r}"
            )
    if data.get("layout") != MANIFEST_LAYOUT:
        errors.append(
            f"manifest layout must be {MANIFEST_LAYOUT!r}, "
            f"got {data.get('layout')!r}"
        )
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        errors.append("manifest must contain a 'tracks' object")
        return errors

    ownership: dict[str, tuple[str, str]] = {}
    for source_key, formats in tracks.items():
        if not isinstance(source_key, str) or not source_key:
            errors.append("manifest source keys must be non-empty strings")
            continue
        if not isinstance(formats, dict) or not formats:
            errors.append(
                f"manifest entry for {source_key!r} must be a non-empty object"
            )
            continue
        for fmt, record in formats.items():
            if fmt not in SUPPORTED_FORMATS:
                errors.append(
                    f"manifest format key must be wav or aiff, got {fmt!r}"
                )
                continue
            if not isinstance(record, dict) or not record:
                errors.append(
                    f"manifest format record for {source_key!r}/{fmt} "
                    "must be a non-empty object"
                )
                continue
            dest = record.get("dest")
            dest_err = _validate_dest_relative(dest, fmt=fmt, wav_dir=wav_dir)
            if dest_err:
                errors.append(dest_err)
                continue
            errors.extend(_validate_optional_freshness(record))
            assert isinstance(dest, str)
            ck = collision_key(dest)
            prior = ownership.get(ck)
            if prior is not None and prior != (source_key, fmt):
                errors.append(
                    "manifest duplicate dest ownership after collision_key: "
                    f"{dest!r} claimed by {prior[0]!r}/{prior[1]} and "
                    f"{source_key!r}/{fmt}"
                )
            else:
                ownership[ck] = (source_key, fmt)
    return errors


def load_manifest(wav_dir: Path) -> ConverterManifest:
    """Load and validate manifest from wav_dir, or return empty if missing."""
    path = manifest_path(wav_dir)
    if not path.is_file():
        return empty_manifest()
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError(
            f"invalid converter manifest JSON: {path}: {exc}"
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError(f"cannot read converter manifest: {path}: {exc}") from exc
    errors = validate_manifest_data(data, abs_path(wav_dir))
    if errors:
        raise ManifestError(errors[0])
    tracks = data["tracks"]
    typed: dict[str, dict[str, dict[str, Any]]] = {}
    for sk, formats in tracks.items():
        typed[sk] = {}
        for fmt, record in formats.items():
            typed[sk][fmt] = deepcopy(record)
    return ConverterManifest(tracks=typed)


def _path_is_writable_dir(path: Path) -> bool:
    try:
        if not path.is_dir():
            return False
        if os.access(path, os.W_OK):
            return True
        # access() can lie on some mounts; probe with a temp file.
        fd, tmp_name = tempfile.mkstemp(dir=path, prefix=".rb-write-probe-")
        os.close(fd)
        os.unlink(tmp_name)
        return True
    except OSError:
        return False


def _parent_writable_for_create(path: Path) -> bool:
    """True if path can be created later (parent exists and is writable)."""
    parent = path
    while True:
        parent = parent.parent
        if parent == parent.parent:
            return False
        if parent.exists():
            return _path_is_writable_dir(parent)


def _has_legacy_library_content(wav_dir: Path) -> bool:
    """True if wav_dir has legacy audio or import XML without a manifest."""
    import_xml = wav_dir / "rekordbox-import.xml"
    if import_xml.is_file():
        return True
    try:
        for path in wav_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.casefold() in {".wav", ".aiff", ".aif"}:
                return True
    except OSError:
        return False
    return False


def validate_library_folder(wav_dir: Path) -> str | None:
    """Return an error message if *wav_dir* is not usable as a library root.

    Empty/missing folders are OK (created at convert time) when the parent is
    writable. Existing manifests must validate. Legacy audio or import XML
    without a manifest is refused.
    """
    expanded = wav_dir.expanduser()
    if expanded.exists() and not expanded.is_dir():
        return "Output folder path exists and is not a directory."
    if expanded.is_dir():
        if not _path_is_writable_dir(expanded):
            return "Output folder is not writable."
    else:
        if not _parent_writable_for_create(expanded):
            return "Output folder is not accessible or not writable."

    if expanded.is_dir() and (expanded / MANIFEST_NAME).is_file():
        try:
            load_manifest(expanded)
        except ManifestError as exc:
            return f"Converter manifest is invalid: {exc}"
        return None

    if expanded.is_dir() and _has_legacy_library_content(expanded):
        return (
            "This folder looks like an older converter library without a "
            "manifest. Choose a new empty output folder."
        )
    return None


def save_manifest(manifest: ConverterManifest, wav_dir: Path) -> None:
    """Atomically write manifest under wav_dir (tempfile + os.replace)."""
    root = abs_path(wav_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / MANIFEST_NAME
    payload = json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=root, prefix=".manifest-", suffix=".tmp.json"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def relative_dest_occupied(
    manifest: ConverterManifest,
    relative_dest: str,
    *,
    wav_dir: Path,
    exclude: tuple[str, str] | None = None,
    source_path: Path | None = None,
) -> bool:
    """True if dest is taken by another assignment or an unrelated file on disk."""
    ck = collision_key(relative_dest)
    if ck in manifest.dest_collision_keys(exclude=exclude):
        return True
    try:
        dest = resolve_dest_under_wav_dir(wav_dir, relative_dest)
    except ManifestError:
        return True

    def _is_source(path: Path) -> bool:
        return source_path is not None and same_file(path, source_path)

    if dest.is_file():
        return not _is_source(dest)
    parent = dest.parent
    if parent.is_dir():
        key = collision_key(dest.name)
        for entry in parent.iterdir():
            if entry.is_file() and collision_key(entry.name) == key:
                return not _is_source(entry)
    return False


def next_free_relative_dest(
    preferred: str,
    *,
    manifest: ConverterManifest,
    wav_dir: Path,
    exclude: tuple[str, str] | None = None,
    source_path: Path | None = None,
) -> str:
    """Return preferred or Name (2), Name (3), ... when occupied."""
    if not relative_dest_occupied(
        manifest,
        preferred,
        wav_dir=wav_dir,
        exclude=exclude,
        source_path=source_path,
    ):
        return preferred
    posix = PurePosixPath(preferred)
    parent = posix.parent
    stem = posix.stem
    suffix = posix.suffix
    n = 2
    while True:
        name = f"{stem} ({n}){suffix}"
        candidate = (
            name if str(parent) == "." else f"{parent.as_posix()}/{name}"
        )
        if not relative_dest_occupied(
            manifest,
            candidate,
            wav_dir=wav_dir,
            exclude=exclude,
            source_path=source_path,
        ):
            return candidate
        n += 1


def reserve_relative_dest(
    manifest: ConverterManifest,
    *,
    source_key: str,
    output_format: str,
    preferred: str,
    wav_dir: Path,
    source_path: Path | None = None,
) -> str:
    """Return sticky dest for (source_key, format), reserving on first assign."""
    existing = manifest.get_dest(source_key, output_format)
    if existing is not None:
        return existing
    exclude = (source_key, output_format)
    dest = next_free_relative_dest(
        preferred,
        manifest=manifest,
        wav_dir=wav_dir,
        exclude=exclude,
        source_path=source_path,
    )
    manifest.set_dest(source_key, output_format, dest)
    return dest
