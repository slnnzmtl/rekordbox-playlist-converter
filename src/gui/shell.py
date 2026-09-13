"""Shell mixin: window chrome, browse, prefs, progress, Documents probe."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from convert.quality import (
    coerce_bit_depth,
    coerce_output_format,
    coerce_sample_rate,
)
from gui import constants
from gui import dialogs as gui_dialogs
from gui import runtime
from gui.browser import (
    build_format_quality_controls,
    build_menubar,
    build_playlist_pane,
    build_progress_convert_row,
    build_status_row,
    build_tracklist_pane,
)
from gui.helpers import app_window_icon_path, progress_action_status_hint
from gui.layout import (
    ACTION_BUTTON_WIDTH,
    HoverTooltip,
    RowHoverTooltip,
    bind_wraplength,
    path_row,
    place_dialog_over_parent,
)
from gui.startup_probe import StartupProbeResult, run_startup_probe
from gui.types import PlaylistNodeKind
from gui_prefs import default_output_paths
from update_check import ReleaseInfo, UpdateCheckResult
from usage_guide import USAGE_GUIDE
from version import __version__


class ShellMixin:
    def _restore_saved_source_xml(self, saved: dict[str, str]) -> None:
        raw = saved.get("source_xml", "").strip()
        if not raw:
            return
        candidate = Path(raw).expanduser()
        if not self._documents_accessible and runtime.path_is_under_documents(candidate):
            return
        try:
            if not candidate.is_file():
                return
        except OSError:
            return
        self._adopt_source_xml(candidate)

    def _adopt_source_xml(self, path: Path) -> None:
        self.xml_var.set(str(path))
        self._load_playlists()

    @property
    def documents_accessible(self) -> bool:
        return self._documents_accessible

    def _xml_search_hits_if_needed(self) -> list[Path] | None:
        if self._has_saved_source_xml:
            return None
        return runtime.find_rekordbox_xml_via_child(Path.home())

    def _start_documents_probe(self) -> None:
        def worker() -> None:
            result = run_startup_probe(
                has_saved_source_xml=self._has_saved_source_xml,
                probe_documents=runtime.probe_path_via_child,
                find_xml=runtime.find_rekordbox_xml_via_child,
            )
            self._ui(lambda r=result: self._apply_startup_probe(r))

        runtime.threading.Thread(target=worker, daemon=True).start()

    def _apply_startup_probe(self, result: StartupProbeResult) -> None:
        if result.xml_search_hits is not None:
            self._apply_xml_search_hits(result.xml_search_hits)
        self._apply_documents_access(result.documents_accessible)

    def _apply_xml_search_hits(self, paths: list[Path]) -> None:
        if self.xml_var.get().strip():
            return
        if len(paths) == 1:
            self._adopt_source_xml(paths[0])
        elif len(paths) >= 2:
            self._show_xml_choice_modal(paths)

    def _show_xml_choice_modal(self, paths: list[Path]) -> None:
        def on_open(chosen: Path) -> None:
            self._adopt_source_xml(chosen)
            self._persist_output_preferences(include_source_xml=True)

        gui_dialogs.show_xml_choice_dialog(self.root, paths, on_open=on_open)

    def _place_dialog_over_app(self, dlg: tk.Toplevel) -> None:
        place_dialog_over_parent(dlg, self.root)

    def _apply_documents_access(self, override: bool | None) -> None:
        if override is None:
            accessible = False
        else:
            accessible = override
        self._documents_accessible = accessible
        if accessible:
            saved = runtime.load_preferences()
            docs_wav, docs_xml = default_output_paths(documents_accessible=True)
            startup_wav, _startup_output = runtime.resolve_startup_paths(
                saved,
                default_wav_dir=docs_wav,
                default_import_xml=docs_xml,
                documents_accessible=True,
            )
            self.library_dir_var.set(str(startup_wav))
            self._schedule_wav_dir_validation()
            if not self.xml_var.get().strip():
                self._restore_saved_source_xml(saved)

    def _apply_window_icon(self) -> tk.PhotoImage | None:
        path = app_window_icon_path()
        if not path.is_file():
            return None
        try:
            image = tk.PhotoImage(file=str(path))
            self.root.iconphoto(True, image)
            return image
        except tk.TclError:
            return None

    def _build(self) -> None:
        self._build_menubar()

        pad = {"padx": 10, "pady": 4}
        frm = ttk.Frame(self.root, padding=12)
        frm.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(1, weight=1)

        _, self.xml_entry, xml_btns = path_row(
            frm, row=0, label="Rekordbox XML", textvariable=self.xml_var, pad=pad
        )
        self.xml_browse_btn = ttk.Button(
            xml_btns,
            text="Browse…",
            width=ACTION_BUTTON_WIDTH,
            command=self._browse_xml,
        )
        self.xml_browse_btn.pack(side=tk.LEFT)
        self.refresh_btn = ttk.Button(
            xml_btns, text="Refresh", command=self._refresh_xml
        )
        self.refresh_btn.pack(side=tk.LEFT, padx=(4, 0))

        list_frame = ttk.Frame(frm)
        list_frame.grid(row=1, column=0, columnspan=3, sticky="nsew", **pad)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        panes = ttk.Panedwindow(list_frame, orient=tk.HORIZONTAL)
        panes.grid(row=0, column=0, sticky="nsew")
        self.browser_panes = panes
        panes.bind("<Configure>", self._on_browser_panes_configure, add="+")

        left, self.search_entry, self.playlist_tree = build_playlist_pane(
            panes,
            search_var=self.search_var,
            bind_search=self._playlist_search.bind,
            on_select=self._on_playlist_select,
            on_button1=self._on_playlist_button1,
        )
        right, self.track_search_entry, self.tracklist_tree = build_tracklist_pane(
            panes,
            search_var=self.track_search_var,
            bind_search=self._track_search.bind,
            on_select=self._on_tracklist_select,
            on_button1=self._on_tracklist_button1,
            on_group_open=self._remember_tracklist_group_open,
            on_sort=self._on_tracklist_sort,
            header_selected_tag=constants.TRACKLIST_HEADER_SELECTED_TAG,
            header_selected_bg=constants.TRACKLIST_HEADER_SELECTED_BG,
            header_selected_fg=constants.TRACKLIST_HEADER_SELECTED_FG,
        )

        panes.add(left, weight=1)
        panes.add(right, weight=1)
        self._tracklist_path_tooltip = RowHoverTooltip(
            self.tracklist_tree, self._tracklist_hover_path
        )
        self._refresh_tracklist_preview()

        _, self.wav_dir_entry, wav_btns = path_row(
            frm, row=2, label="Output folder", textvariable=self.library_dir_var, pad=pad
        )
        self.wav_dir_browse_btn = ttk.Button(
            wav_btns,
            text="Browse…",
            width=ACTION_BUTTON_WIDTH,
            command=self._browse_wav_dir,
        )
        self.wav_dir_browse_btn.pack(side=tk.LEFT)
        self.wav_dir_error_label = ttk.Label(
            frm,
            textvariable=self.wav_dir_error_var,
            foreground="#a40000",
        )
        bind_wraplength(self.wav_dir_error_label, frm, inset=24)
        # Row 3 is used only while validation has a message (grid_remove otherwise).

        import_xml_label, self.import_xml_entry, import_btns = path_row(
            frm,
            row=4,
            label="Import XML",
            textvariable=self.output_var,
            pad=pad,
            entry_state="disabled",
            entry_cursor="hand2",
        )
        import_xml_label.configure(cursor="hand2")
        import_xml_label.bind("<Button-1>", self._copy_import_xml_path, add="+")
        self.import_xml_entry.bind(
            "<Button-1>", self._copy_import_xml_path, add="+"
        )
        HoverTooltip(self.import_xml_entry, "Click to copy the Import XML path")
        self.import_edit_btn = ttk.Button(
            import_btns,
            text="Edit",
            width=ACTION_BUTTON_WIDTH,
            command=self._enter_import_edit_mode,
        )
        self.import_save_btn = ttk.Button(
            import_btns,
            text="Save",
            width=ACTION_BUTTON_WIDTH,
            command=self._save_import_edit,
        )
        self.import_cancel_btn = ttk.Button(
            import_btns,
            text="Cancel",
            width=ACTION_BUTTON_WIDTH,
            command=self._cancel_import_edit,
        )
        self.import_edit_btn.pack(side=tk.LEFT)
        self.import_save_btn.pack(side=tk.LEFT)
        self.import_cancel_btn.pack(side=tk.LEFT, padx=(4, 0))
        self.import_save_btn.pack_forget()
        self.import_cancel_btn.pack_forget()
        self.import_edit_btn.pack_forget()
        self.playlist_tree.bind(
            "<<Button-3>>", self._on_playlist_context_menu, add="+"
        )
        # macOS Ctrl-click and right-click.
        self.playlist_tree.bind(
            "<Button-2>", self._on_playlist_context_menu, add="+"
        )
        self.playlist_tree.bind(
            "<Control-Button-1>", self._on_playlist_context_menu, add="+"
        )
        self.tracklist_tree.bind(
            "<Button-2>", self._on_tracklist_context_menu, add="+"
        )
        self.tracklist_tree.bind(
            "<Button-3>", self._on_tracklist_context_menu, add="+"
        )
        self.tracklist_tree.bind(
            "<Control-Button-1>", self._on_tracklist_context_menu, add="+"
        )
        self._bind_import_edit_window_guards()

        (
            self.format_wav_radio,
            self.format_aiff_radio,
            self.bit_depth_combo,
            self.sample_rate_combo,
        ) = build_format_quality_controls(
            frm,
            pad=pad,
            format_var=self.format_var,
            bit_depth_var=self.bit_depth_var,
            sample_rate_var=self.sample_rate_var,
            bit_depth_labels=constants.BIT_DEPTH_LABELS,
            sample_rate_labels=constants.SAMPLE_RATE_LABELS,
            bit_depth_tooltip=constants.BIT_DEPTH_24_TOOLTIP,
            sample_rate_tooltip=constants.SAMPLE_RATE_48_TOOLTIP,
            on_persist=self._persist_output_preferences,
            on_bit_depth=self._on_bit_depth_selected,
            on_sample_rate=self._on_sample_rate_selected,
        )

        self.progress, self.convert_btn, self.cancel_btn = build_progress_convert_row(
            frm,
            pad=pad,
            on_convert=self._start_convert,
            on_cancel=self._request_cancel,
        )
        build_status_row(
            frm,
            pad=pad,
            status_var=self.status_var,
            scan_status_var=self.scan_status_var,
        )

    def _build_menubar(self) -> None:
        build_menubar(
            self.root,
            on_search_xml=self._search_rekordbox_xml,
            on_usage=self._show_usage_guide,
            on_updates=self._check_for_updates_manual,
        )
        try:
            self.root.bind_all("<Command-?>", lambda _e: self._show_usage_guide())
            self.root.bind_all("<Command-Shift-/>", lambda _e: self._show_usage_guide())
        except tk.TclError:
            pass

    def _search_rekordbox_xml(self) -> None:
        if self._busy:
            return

        def worker() -> None:
            hits = runtime.find_rekordbox_xml_via_child(
                Path.home(),
                timeout_seconds=constants.XML_SEARCH_TIMEOUT_SECONDS,
            )

            def apply() -> None:
                if len(hits) == 1:
                    self._adopt_source_xml(hits[0])
                    self._persist_output_preferences(include_source_xml=True)
                elif len(hits) >= 2:
                    self._show_xml_choice_modal(hits)

            self._ui(apply)

        runtime.threading.Thread(target=worker, daemon=True).start()

    def _show_usage_guide(self) -> None:
        def on_closed() -> None:
            self._usage_window = None

        self._usage_window = gui_dialogs.show_usage_guide_dialog(
            self.root,
            USAGE_GUIDE,
            existing=self._usage_window,
            on_closed=on_closed,
        )

    def _on_browser_panes_configure(self, event: object = None) -> None:
        if self._browser_sash_set:
            return
        widget = getattr(event, "widget", None) or self.browser_panes
        try:
            width = int(widget.winfo_width())
        except tk.TclError:
            return
        if width <= 1:
            return
        try:
            self.browser_panes.sashpos(0, round(width * 0.3))
        except tk.TclError:
            return
        self._browser_sash_set = True

    def _on_bit_depth_selected(self, _event: object = None) -> None:
        label = self.bit_depth_combo.get().strip()
        mapped = constants.BIT_DEPTH_FROM_LABEL.get(label)
        if mapped is None:
            return
        self.bit_depth_var.set(mapped)
        self._persist_output_preferences()

    def _on_sample_rate_selected(self, _event: object = None) -> None:
        label = self.sample_rate_combo.get().strip()
        mapped = constants.SAMPLE_RATE_FROM_LABEL.get(label)
        if mapped is None:
            return
        self.sample_rate_var.set(mapped)
        self._persist_output_preferences()

    def _on_wav_dir_changed(self) -> None:
        self._sync_import_xml_display()
        self._schedule_wav_dir_validation()

    def _resolved_output_paths(self) -> tuple[Path, Path]:
        wav_dir = Path(
            self.library_dir_var.get().strip() or str(constants.DEFAULT_WAV_DIR)
        ).expanduser()
        if not wav_dir.is_absolute():
            wav_dir = Path.home() / wav_dir
        return wav_dir, runtime.import_xml_path(wav_dir)

    def _sync_import_xml_display(self) -> None:
        _wav_dir, output = self._resolved_output_paths()
        self.output_var.set(str(output))

    def _copy_import_xml_path(self, _event: object = None) -> str | None:
        path = str(self._resolved_output_paths()[1])
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(path)
            self.root.update_idletasks()
        except tk.TclError:
            return "break"
        if not self._busy:
            self._cancel_copy_status_clear()
            self.status_var.set(f"Copied path: {path}")
            self._copy_status_clear_id = self.root.after(
                constants.CANCELLED_STATUS_CLEAR_MS, self._clear_copy_status
            )
        return "break"

    def _cancel_copy_status_clear(self) -> None:
        if self._copy_status_clear_id is not None:
            self.root.after_cancel(self._copy_status_clear_id)
            self._copy_status_clear_id = None

    def _clear_copy_status(self) -> None:
        self._copy_status_clear_id = None
        if self._busy:
            return
        if not self.status_var.get().startswith("Copied path:"):
            return
        self._set_idle_status(self._tracklist_selection_summary())

    def _persist_output_preferences(self, *, include_source_xml: bool = False) -> None:
        wav_dir, _output = self._resolved_output_paths()
        source_xml = None
        if include_source_xml:
            source_s = self.xml_var.get().strip()
            source_xml = Path(source_s).expanduser() if source_s else None
        fmt = coerce_output_format(self.format_var.get())
        depth = str(coerce_bit_depth(self.bit_depth_var.get()))
        rate = str(coerce_sample_rate(self.sample_rate_var.get()))
        try:
            runtime.save_preferences(
                wav_dir,
                source_xml=source_xml,
                output_format=fmt,
                bit_depth=depth,
                sample_rate=rate,
            )
        except OSError:
            if not self._busy:
                self.status_var.set("Couldn’t save preferences")

    def _browse_initial_dir(self, preferred: Path | None = None) -> str:
        """Pick a file-dialog start folder without stating Documents when denied."""
        if preferred is not None:
            preferred_s = str(preferred).strip()
            if preferred_s:
                preferred_path = Path(preferred_s).expanduser()
                if self._documents_accessible or not runtime.path_is_under_documents(
                    preferred_path
                ):
                    return str(preferred_path)
        if self._documents_accessible:
            return str(Path.home() / "Documents")
        return str(Path.home())

    def _browse_xml(self) -> None:
        if self._busy or self._import_edit_active():
            return
        current = self.xml_var.get().strip()
        preferred = Path(current).expanduser().parent if current else None
        path = runtime.filedialog.askopenfilename(
            title="Rekordbox XML export",
            initialdir=self._browse_initial_dir(preferred),
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")],
        )
        if path:
            self._adopt_source_xml(Path(path))
            self._persist_output_preferences(include_source_xml=True)

    def _refresh_xml(self) -> None:
        if self._busy or self._import_edit_active():
            return
        xml_s = self.xml_var.get().strip()
        if not xml_s:
            runtime.show_centered_message(
                self.root, "Missing XML", "Choose a Rekordbox XML export."
            )
            return
        self._load_playlists()

    def _browse_wav_dir(self) -> None:
        if self._busy or self._import_edit_active():
            return
        current = self.library_dir_var.get().strip()
        path = runtime.filedialog.askdirectory(
            title="Audio output folder",
            initialdir=self._browse_initial_dir(
                Path(current) if current else constants.FALLBACK_WAV_DIR
            ),
        )
        if path:
            self.library_dir_var.set(path)
            self._persist_output_preferences()
            self._schedule_wav_dir_validation()

    def _debounce(self, attr: str, callback) -> None:
        prev = getattr(self, attr)
        if prev is not None:
            self.root.after_cancel(prev)

        def fire() -> None:
            setattr(self, attr, None)
            callback()

        setattr(self, attr, self.root.after(constants.SEARCH_DEBOUNCE_MS, fire))

    def _set_busy(self, busy: bool) -> None:
        if busy and self._import_edit_active():
            return
        self._busy = busy
        edit_state = tk.DISABLED if busy else tk.NORMAL
        combo_state = "disabled" if busy else "readonly"
        tree_state = ("disabled",) if busy else ("!disabled",)
        if self._import_edit_active():
            # Edit mode owns chrome locking; only toggle cancel/progress.
            if busy:
                self._cancel_cancelled_clear()
                self._cancel_progress_anim()
                self._progress_target = 0.0
                self.progress["value"] = 0
                self.convert_btn.grid_remove()
                self.cancel_btn.configure(state=tk.NORMAL)
                self.cancel_btn.grid()
                self._sync_scan_indicator()
            else:
                self.cancel_btn.grid_remove()
                self.cancel_btn.configure(state=tk.DISABLED)
                self.convert_btn.grid()
                self._sync_import_edit_chrome()
                self._sync_scan_indicator()
            return
        self.xml_entry.configure(state=edit_state)
        self.wav_dir_entry.configure(state=edit_state)
        self.xml_browse_btn.configure(state=edit_state)
        self.wav_dir_browse_btn.configure(state=edit_state)
        self.search_entry.configure(state=edit_state)
        self.track_search_entry.configure(state=edit_state)
        self.format_wav_radio.configure(state=edit_state)
        self.format_aiff_radio.configure(state=edit_state)
        self.bit_depth_combo.configure(state=combo_state)
        self.sample_rate_combo.configure(state=combo_state)
        self.refresh_btn.configure(state=edit_state)
        self.playlist_tree.state(tree_state)
        self.tracklist_tree.state(tree_state)
        if busy:
            self._cancel_cancelled_clear()
            self._cancel_progress_anim()
            self._progress_target = 0.0
            self.progress["value"] = 0
            self.convert_btn.grid_remove()
            self.cancel_btn.configure(state=tk.NORMAL)
            self.cancel_btn.grid()
            self._sync_scan_indicator()
            self._update_import_edit_button()
        else:
            self.cancel_btn.grid_remove()
            self.cancel_btn.configure(state=tk.DISABLED)
            self.convert_btn.grid()
            self._update_convert_enabled()
            self._sync_scan_indicator()

    def _update_convert_enabled(self) -> None:
        if self._busy:
            return
        if self._import_edit_active():
            self.convert_btn.configure(state=tk.DISABLED)
            return
        enabled = self._wav_dir_valid and not self._wav_dir_checking
        self.convert_btn.configure(state=tk.NORMAL if enabled else tk.DISABLED)
        self._update_import_edit_button()

    def _set_wav_dir_error(self, message: str) -> None:
        self.wav_dir_error_var.set(message)
        if message:
            self.wav_dir_error_label.grid(
                row=3, column=1, columnspan=2, sticky="w", padx=10, pady=(0, 4)
            )
        else:
            self.wav_dir_error_label.grid_remove()

    def _schedule_wav_dir_validation(self) -> None:
        prev = self._wav_dir_validate_after_id
        if prev is not None:
            self.root.after_cancel(prev)
        self._wav_dir_checking = True
        self._wav_dir_valid = False
        self._set_wav_dir_error("")
        self._update_convert_enabled()

        def fire() -> None:
            self._wav_dir_validate_after_id = None
            self._start_wav_dir_validation()

        self._wav_dir_validate_after_id = self.root.after(
            constants.WAV_DIR_VALIDATE_DEBOUNCE_MS, fire
        )

    def _start_wav_dir_validation(self) -> None:
        wav_dir, _output = self._resolved_output_paths()
        self._wav_dir_validate_gen += 1
        gen = self._wav_dir_validate_gen
        self._wav_dir_checking = True
        self._wav_dir_valid = False
        # Do not show a temporary "Checking…" in the error row — it flickers
        # and then hides for valid folders. Convert stays disabled via
        # _wav_dir_checking until the result lands.
        self._update_convert_enabled()

        def worker() -> None:
            error = runtime.converter_manifest.validate_library_folder(wav_dir)

            def on_ui() -> None:
                if gen != self._wav_dir_validate_gen:
                    return
                self._wav_dir_checking = False
                if error:
                    self._wav_dir_valid = False
                    self._set_wav_dir_error(error)
                else:
                    self._wav_dir_valid = True
                    self._set_wav_dir_error("")
                self._update_convert_enabled()

            self._ui(on_ui)

        runtime.threading.Thread(target=worker, daemon=True).start()

    def _request_cancel(self) -> None:
        if not self._busy:
            return
        if self._prepared_conversion is not None and self._confirm_prepared is None:
            self._discard_prepared_conversion()
            return
        self._cancel_event.set()
        self.status_var.set("Cancelling…")
        self.cancel_btn.configure(state=tk.DISABLED)

    def _cancel_cancelled_clear(self) -> None:
        if self._cancelled_clear_id is not None:
            self.root.after_cancel(self._cancelled_clear_id)
            self._cancelled_clear_id = None

    def _set_idle_status(self, unique_summary: str | None = None) -> None:
        if self._busy:
            return
        if self._import_edit_active():
            if unique_summary:
                self.status_var.set(unique_summary)
            else:
                self.status_var.set(
                    "Editing Import XML — Save or Cancel when done."
                )
            return
        if unique_summary:
            self.status_var.set(unique_summary)
            return
        playlist_count = sum(
            1
            for entry in self._playlist_entries
            if entry.kind == PlaylistNodeKind.PLAYLIST
        )
        if playlist_count:
            self.status_var.set(
                f"Loaded {playlist_count} playlist(s). Select and Convert."
            )
            return
        if self._source_root is not None:
            self.status_var.set("No playlists found in XML.")
            return
        self.status_var.set("Choose a Rekordbox XML export.")

    def _clear_cancelled_status(self) -> None:
        self._cancelled_clear_id = None
        if self._busy or self.status_var.get() != "Cancelled.":
            return
        self._animate_progress_to(0, snap=True)
        self._refresh_tracklist_preview()

    def _cancel_progress_anim(self) -> None:
        if self._progress_anim_id is not None:
            self.root.after_cancel(self._progress_anim_id)
            self._progress_anim_id = None

    def _animate_progress_to(self, pct: float, *, snap: bool = False) -> None:
        self._progress_target = max(0.0, min(100.0, float(pct)))
        if snap:
            self._cancel_progress_anim()
            self.progress["value"] = self._progress_target
            return
        if self._progress_anim_id is None:
            self._tick_progress_anim()

    def _tick_progress_anim(self) -> None:
        self._progress_anim_id = None
        cur = float(self.progress["value"])
        target = self._progress_target
        diff = target - cur
        if abs(diff) < 0.2:
            self.progress["value"] = target
            return
        # Ease toward target (~60fps); never overshoot.
        step = diff * 0.22
        if abs(step) < 0.35:
            step = 0.35 if diff > 0 else -0.35
        nxt = cur + step
        if (diff > 0 and nxt > target) or (diff < 0 and nxt < target):
            nxt = target
        self.progress["value"] = nxt
        self._progress_anim_id = self.root.after(16, self._tick_progress_anim)

    def _set_progress(
        self, current: int, total: int, *, action: str = "", name: str = ""
    ) -> None:
        if total <= 0:
            pct = 100.0 if current else 0.0
        else:
            pct = min(100.0, 100.0 * current / total)
        self._animate_progress_to(pct)
        if action and name:
            self.status_var.set(
                progress_action_status_hint(action, current, total, name)
            )
        elif total > 0:
            self.status_var.set(f"Working… {current}/{total} ({int(pct)}%)")

    def _ui(self, fn) -> None:
        try:
            self.root.after(0, fn)
        except tk.TclError:
            pass

    def _start_update_check(self, *, manual: bool) -> None:
        if self._update_check_running:
            return
        self._update_check_running = True

        def worker() -> None:
            result = runtime.check_for_update(__version__)

            def on_ui(r=result, m=manual) -> None:
                self._update_check_running = False
                self._handle_update_check_result(r, manual=m)

            self._ui(on_ui)

        runtime.threading.Thread(target=worker, daemon=True).start()

    def _check_for_updates_manual(self) -> None:
        self._start_update_check(manual=True)

    def _handle_update_check_result(
        self, result: UpdateCheckResult, *, manual: bool
    ) -> None:
        if result.is_error:
            if manual:
                runtime.show_centered_message(
                    self.root,
                    "Update check failed",
                    f"Could not check for updates:\n\n{result.message}",
                )
            return
        if result.is_up_to_date:
            if manual:
                runtime.show_centered_message(
                    self.root,
                    "No updates",
                    f"Simple Rekordbox Converter {__version__} is up to date.",
                )
            return
        if result.is_update_available and result.release is not None:
            if manual or not self._update_modal_shown:
                if not manual:
                    self._update_modal_shown = True
                self._show_update_available(result.release)

    def _show_update_available(self, release: ReleaseInfo) -> None:
        gui_dialogs.show_update_available_dialog(
            self.root,
            current_version=__version__,
            latest_version=release.version,
            release_notes=release.release_notes or "",
            html_url=release.html_url,
            open_url=runtime.webbrowser.open,
            place_over=self._place_dialog_over_app,
        )

