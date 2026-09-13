"""GUI preference storage, default paths, Documents probe, and XML discovery."""

from __future__ import annotations

from gui_prefs.documents_probe import (
    DOCUMENTS_PROBE_FLAG,
    DOCUMENTS_PROBE_TIMEOUT_SECONDS,
    documents_folder_probe,
    documents_probe_command,
    probe_folder_access,
    probe_path_via_child,
    run_documents_probe_cli,
)
from gui_prefs.paths import (
    IMPORT_XML_NAME,
    OUTPUT_DIR_NAME,
    default_output_paths,
    import_xml_path,
    resolve_startup_paths,
)
from gui_prefs.storage import (
    BUNDLE_ID,
    PREFERENCES_VERSION,
    default_config_path,
    load_preferences,
    save_preferences,
)
from gui_prefs.xml_discovery import (
    FIND_REKORDBOX_XML_FLAG,
    find_rekordbox_xml_command,
    find_rekordbox_xml_via_child,
    iter_rekordbox_xml_files,
    run_find_rekordbox_xml_cli,
)

__all__ = [
    "BUNDLE_ID",
    "DOCUMENTS_PROBE_FLAG",
    "DOCUMENTS_PROBE_TIMEOUT_SECONDS",
    "FIND_REKORDBOX_XML_FLAG",
    "IMPORT_XML_NAME",
    "OUTPUT_DIR_NAME",
    "PREFERENCES_VERSION",
    "default_config_path",
    "default_output_paths",
    "documents_folder_probe",
    "documents_probe_command",
    "find_rekordbox_xml_command",
    "find_rekordbox_xml_via_child",
    "import_xml_path",
    "iter_rekordbox_xml_files",
    "load_preferences",
    "probe_folder_access",
    "probe_path_via_child",
    "resolve_startup_paths",
    "run_documents_probe_cli",
    "run_find_rekordbox_xml_cli",
    "save_preferences",
]
