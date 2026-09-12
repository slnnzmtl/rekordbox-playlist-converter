"""Per-format sticky dest assignments under wav_dir (private to convert_plan/batch)."""

from __future__ import annotations

import json
import os
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from cli_error import CliError

MANIFEST_NAME = "rekordbox-converter-manifest.json"
MANIFEST_VERSION = 1
SUPPORTED_FORMATS = frozenset({"wav", "aiff"})


class ManifestError(CliError):
    """Invalid or unusable converter manifest."""


def _abs_path(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _collision_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


@dataclass
class ConverterManifest:
    """In-memory source→(format→dest) assignments for one wav_dir."""

    tracks: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)

    def get_dest(self, source_key: str, output_format: str) -> str | None:
        entry = self.tracks.get(source_key)
        if entry is None:
            return None
        fmt = entry.get(output_format)
        if not fmt:
            return None
        dest = fmt.get("dest")
        return dest if dest else None

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
                    keys.add(_collision_key(dest))
        return keys

    def to_dict(self) -> dict[str, Any]:
        return {"version": MANIFEST_VERSION, "tracks": self.tracks}


def manifest_path(wav_dir: Path) -> Path:
    return _abs_path(wav_dir) / MANIFEST_NAME


def empty_manifest() -> ConverterManifest:
    return ConverterManifest()


def resolve_dest_under_wav_dir(wav_dir: Path, relative_dest: str) -> Path:
    """Resolve relative_dest under wav_dir; raise if it escapes wav_dir."""
    root = _abs_path(wav_dir).resolve()
    dest = root.joinpath(*PurePosixPath(relative_dest).parts).resolve()
    try:
        dest.relative_to(root)
    except ValueError as exc:
        raise ManifestError(
            f"manifest dest resolves outside wav_dir: {relative_dest!r}"
        ) from exc
    return dest


def ensure_dest_path_under_wav_dir(wav_dir: Path, dest_path: Path) -> Path:
    """Resolve dest_path and refuse if it is outside wav_dir."""
    root = _abs_path(wav_dir).resolve()
    dest = dest_path.expanduser().resolve()
    try:
        dest.relative_to(root)
    except ValueError as exc:
        raise ManifestError(
            f"dest path resolves outside wav_dir: {dest_path}"
        ) from exc
    return dest


def _validate_dest_relative(dest: object, *, fmt: str, wav_dir: Path) -> str | None:
    """Return an error message, or None if dest is valid under wav_dir."""
    if not isinstance(dest, str) or not dest:
        return "manifest dest must be a non-empty string"
    if "\\" in dest:
        return f"manifest dest must be a relative posix path (no backslashes): {dest!r}"
    if dest.startswith("/") or PurePosixPath(dest).is_absolute():
        return f"manifest dest must be relative (not absolute): {dest!r}"
    parts = PurePosixPath(dest).parts
    if not parts or any(p in (".", "..") for p in parts):
        return f"manifest dest must not contain '.' or '..' components: {dest!r}"
    expected_ext = f".{fmt}"
    if PurePosixPath(dest).suffix.lower() != expected_ext:
        return (
            f"manifest dest extension must match format {fmt!r}: {dest!r}"
        )
    try:
        resolve_dest_under_wav_dir(wav_dir, dest)
    except ManifestError as exc:
        return str(exc)
    return None


def validate_manifest_data(data: object, wav_dir: Path) -> list[str]:
    """Return validation errors for parsed manifest JSON (empty if ok)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["manifest root must be a JSON object"]
    if data.get("version") != MANIFEST_VERSION:
        errors.append(
            f"manifest version must be {MANIFEST_VERSION}, "
            f"got {data.get('version')!r}"
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
            assert isinstance(dest, str)
            ck = _collision_key(dest)
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
    except OSError as exc:
        raise ManifestError(f"cannot read converter manifest: {path}: {exc}") from exc
    errors = validate_manifest_data(data, _abs_path(wav_dir))
    if errors:
        raise ManifestError(errors[0])
    tracks = data["tracks"]
    typed: dict[str, dict[str, dict[str, str]]] = {}
    for sk, formats in tracks.items():
        typed[sk] = {}
        for fmt, record in formats.items():
            typed[sk][fmt] = {"dest": record["dest"]}
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

    manifest_file = expanded / MANIFEST_NAME if expanded.is_dir() else None
    if manifest_file is not None and manifest_file.is_file():
        try:
            raw = manifest_file.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            return f"Converter manifest is invalid: {exc}"
        errors = validate_manifest_data(data, expanded)
        if errors:
            return f"Converter manifest is invalid: {errors[0]}"
        return None

    if expanded.is_dir() and _has_legacy_library_content(expanded):
        return (
            "This folder looks like an older converter library without a "
            "manifest. Choose a new empty output folder."
        )
    return None


def save_manifest(manifest: ConverterManifest, wav_dir: Path) -> None:
    """Atomically write manifest under wav_dir (tempfile + os.replace)."""
    root = _abs_path(wav_dir)
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
    ck = _collision_key(relative_dest)
    if ck in manifest.dest_collision_keys(exclude=exclude):
        return True
    try:
        dest = resolve_dest_under_wav_dir(wav_dir, relative_dest)
    except ManifestError:
        return True

    def _is_source(path: Path) -> bool:
        if source_path is None:
            return False
        try:
            return path.exists() and source_path.exists() and path.samefile(source_path)
        except OSError:
            return False

    if dest.is_file():
        return not _is_source(dest)
    parent = dest.parent
    if parent.is_dir():
        key = _collision_key(dest.name)
        for entry in parent.iterdir():
            if entry.is_file() and _collision_key(entry.name) == key:
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
