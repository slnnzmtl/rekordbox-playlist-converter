"""ConverterApp assembly: mixins + __init__."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

from convert.models import PreparedConversion
from convert.quality import (
    coerce_bit_depth,
    coerce_output_format,
    coerce_sample_rate,
)
from gui import constants
from gui import runtime
from gui.convert_flow import ConvertFlowMixin
from gui.import_edit import ImportEditMixin
from gui.layout import SearchPlaceholder, active_display_bounds, fit_window_geometry
from gui.playlists import PlaylistsMixin
from gui.shell import ShellMixin
from gui.types import PlaylistEntry, TrackLeafRef
from version import __version__


class ConverterApp(ImportEditMixin, ConvertFlowMixin, PlaylistsMixin, ShellMixin):
    def __init__(
        self,
        root: tk.Tk,
        *,
        documents_accessible: bool | None = None,
    ) -> None:
        self.root = root
        root.title(f"Simple Rekordbox Converter {__version__}")
        root.minsize(560, 480)
        bounds = active_display_bounds()
        if bounds is not None:
            root.geometry(fit_window_geometry(1120, 720, *bounds))
        else:
            root.geometry("1120x720")
        self.logo_image = self._apply_window_icon()

        self.xml_var = tk.StringVar()
        self.library_dir_var = tk.StringVar()
        self.output_var = tk.StringVar()
        # Start with home fallback so the window can appear before Documents TCC.
        saved_prefs = runtime.load_preferences()
        startup_wav, _startup_output = runtime.resolve_startup_paths(
            saved_prefs,
            default_wav_dir=constants.FALLBACK_WAV_DIR,
            default_import_xml=constants.FALLBACK_OUTPUT,
            documents_accessible=False,
        )
        self.library_dir_var.set(str(startup_wav))
        self._sync_import_xml_display()
        self.format_var = tk.StringVar(
            value=coerce_output_format(saved_prefs.get("output_format", "wav"))
        )
        self.bit_depth_var = tk.StringVar(
            value=str(coerce_bit_depth(saved_prefs.get("bit_depth", "24")))
        )
        self.sample_rate_var = tk.StringVar(
            value=str(coerce_sample_rate(saved_prefs.get("sample_rate", "48000")))
        )
        self.search_var = tk.StringVar()
        self.track_search_var = tk.StringVar()
        self._playlist_search = SearchPlaceholder(self.search_var, constants.SEARCH_PLACEHOLDER)
        self._track_search = SearchPlaceholder(
            self.track_search_var, constants.TRACK_SEARCH_PLACEHOLDER
        )
        self.status_var = tk.StringVar(value="Choose a Rekordbox XML export.")
        self.scan_status_var = tk.StringVar(value="")
        self._busy = False
        self._cancel_event = runtime.threading.Event()
        self._prepared_conversion: PreparedConversion | None = None
        self._confirm_prepared: PreparedConversion | None = None
        self._preview_dialog: tk.Toplevel | None = None
        self._usage_window: tk.Toplevel | None = None
        self._update_modal_shown = False
        self._update_check_running = False
        self._progress_target = 0.0
        self._progress_anim_id: str | None = None
        self._cancelled_clear_id: str | None = None
        self._copy_status_clear_id: str | None = None
        self._documents_accessible = False
        self._source_root = None
        self._view_root = None
        self._import_edit_draft = None
        self._import_edit_selection_snapshot: tuple[tuple[str, str], ...] = ()
        self._collection_indexes_cache: tuple[dict, dict] | None = None
        self._playlist_entries: list[PlaylistEntry] = []
        # iid -> (kind, folder, name) for rows currently in the tree
        self._playlist_iids: dict[str, tuple[str, str, str]] = {}
        self._tracklist_iids: dict[str, TrackLeafRef] = {}
        self._tracklist_paths: dict[str, Path] = {}
        # (folder, name) -> open state for tracklist playlist groups
        self._tracklist_group_open: dict[tuple[str, str], bool] = {}
        self._tracklist_group_iids: dict[str, tuple[str, str]] = {}
        self._tracklist_painted_playlists: list[tuple[str, str]] = []
        self._tracklist_selecting = False
        self._playlist_selecting = False
        self._tracklist_tech_gen = 0
        self._preview_bit_depth_cache: dict = {}
        self._preview_bit_depth_lock = runtime.threading.Lock()
        self._preview_probe_thread: runtime.threading.Thread | None = None
        self._preview_scan_active = False
        self._browser_sash_set = False
        self._tracklist_sort_column: str | None = None
        self._tracklist_sort_reverse = False
        self._playlist_search_after_id: str | None = None
        self._track_search_after_id: str | None = None
        self._wav_dir_validate_after_id: str | None = None
        self._wav_dir_validate_gen = 0
        self._wav_dir_valid = False
        self._wav_dir_checking = False
        self._cached_library_manifest = None
        self._cached_library_fingerprint = None
        self._cached_library_path: Path | None = None
        self._cached_library_gen: int | None = None
        self.wav_dir_error_var = tk.StringVar(value="")

        self._build()
        self.search_var.trace_add(
            "write",
            lambda *_: self._debounce(
                "_playlist_search_after_id", self._apply_playlist_filter
            ),
        )
        self.track_search_var.trace_add(
            "write",
            lambda *_: self._debounce(
                "_track_search_after_id", self._refresh_tracklist_preview
            ),
        )
        self.library_dir_var.trace_add(
            "write",
            lambda *_: self._on_wav_dir_changed(),
        )
        if not self.search_var.get():
            self._playlist_search.show()
        if not self.track_search_var.get():
            self._track_search.show()
        self._schedule_wav_dir_validation()
        # Prefs key, not xml_var: skip first-launch search even if Documents
        # restore has not filled the field yet.
        self._has_saved_source_xml = bool(saved_prefs.get("source_xml", "").strip())
        self._restore_saved_source_xml(saved_prefs)
        if documents_accessible is None:
            # Never list Documents in this process during init: macOS TCC
            # blocks every thread until the user answers, so the window would
            # never appear if they dismiss the dialog.
            self.root.after_idle(self._start_documents_probe)
        else:
            self._apply_documents_access(documents_accessible)
        self._start_update_check(manual=False)


