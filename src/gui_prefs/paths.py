"""Default output paths and startup path resolution."""

from __future__ import annotations

from pathlib import Path

from rekordbox_xml import path_is_under_documents

OUTPUT_DIR_NAME = "rekordbox-converter"
IMPORT_XML_NAME = "rekordbox-import.xml"


def import_xml_path(library_dir: Path) -> Path:
    """Canonical import XML path for a self-contained library folder."""
    return library_dir.expanduser() / IMPORT_XML_NAME


def default_output_paths(
    *,
    documents_accessible: bool,
    home: Path | None = None,
) -> tuple[Path, Path]:
    """Return default library dir and import XML based on Documents access."""
    base = home if home is not None else Path.home()
    if documents_accessible:
        library_dir = base / "Documents" / OUTPUT_DIR_NAME
    else:
        library_dir = base / OUTPUT_DIR_NAME
    return library_dir, import_xml_path(library_dir)


def _library_dir_is_valid(path: Path) -> bool:
    expanded = path.expanduser()
    if expanded.is_dir():
        return True
    parent = expanded.parent
    return parent.exists() and parent.is_dir()


def resolve_startup_paths(
    saved: dict[str, str],
    *,
    default_wav_dir: Path,
    default_import_xml: Path,
    documents_accessible: bool = True,
    home: Path | None = None,
) -> tuple[Path, Path]:
    """Restore library_dir from prefs; always derive import XML from library_dir.

    Reads ``library_dir`` first, then legacy ``wav_dir``. Legacy ``import_xml``
    preference keys are ignored.
    """
    saved_lib = saved.get("library_dir") or saved.get("wav_dir")
    if not saved_lib:
        return default_wav_dir, default_import_xml

    candidate = Path(saved_lib).expanduser()
    if not documents_accessible and path_is_under_documents(candidate, home=home):
        return default_wav_dir, default_import_xml
    if not _library_dir_is_valid(candidate):
        return default_wav_dir, default_import_xml

    library_dir = candidate.resolve()
    return library_dir, import_xml_path(library_dir)
