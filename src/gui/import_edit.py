"""Import XML edit-mode mixin for ConverterApp."""

from __future__ import annotations

import tkinter as tk

from gui import dialogs as gui_dialogs
from gui import runtime
from import_edit import (
    ACTION_MOVE_TO_TRASH,
    ImportEditDraft,
    preview_save,
    remove_playlist,
    remove_track_from_collection,
    remove_track_from_playlist,
)


class ImportEditMixin:
    def _import_edit_active(self) -> bool:
        return self._import_edit_draft is not None

    def _update_import_edit_button(self) -> None:
        if not hasattr(self, "import_edit_btn"):
            return
        if self._import_edit_active() or self._busy:
            self.import_edit_btn.pack_forget()
            return
        wav_dir, _output = self._resolved_output_paths()
        xml_path = runtime.import_xml_path(wav_dir)
        man_path = runtime.converter_manifest.manifest_path(wav_dir)
        show = (
            self._wav_dir_valid
            and not self._wav_dir_checking
            and xml_path.is_file()
            and man_path.is_file()
        )
        if show:
            self.import_edit_btn.configure(state=tk.NORMAL)
            self.import_edit_btn.pack(side=tk.LEFT)
        else:
            self.import_edit_btn.pack_forget()

    def _sync_import_edit_chrome(self) -> None:
        editing = self._import_edit_active()
        if editing:
            self.import_edit_btn.pack_forget()
            self.import_save_btn.pack_forget()
            self.import_cancel_btn.pack_forget()
            dirty = bool(self._import_edit_draft and self._import_edit_draft.dirty)
            if dirty:
                self.import_save_btn.configure(state=tk.NORMAL)
                self.import_save_btn.pack(side=tk.LEFT)
            self.import_cancel_btn.configure(state=tk.NORMAL)
            self.import_cancel_btn.pack(side=tk.LEFT, padx=(4, 0))
            lock = tk.DISABLED
            combo_state = "disabled"
            self.xml_entry.configure(state=lock)
            self.wav_dir_entry.configure(state=lock)
            self.xml_browse_btn.configure(state=lock)
            self.wav_dir_browse_btn.configure(state=lock)
            self.refresh_btn.configure(state=lock)
            self.format_wav_radio.configure(state=lock)
            self.format_aiff_radio.configure(state=lock)
            self.bit_depth_combo.configure(state=combo_state)
            self.sample_rate_combo.configure(state=combo_state)
            self.convert_btn.grid_remove()
            self.status_var.set("Editing Import XML — Save or Cancel when done.")
        else:
            self.import_save_btn.pack_forget()
            self.import_cancel_btn.pack_forget()
            self._update_import_edit_button()
            if not self._busy:
                edit_state = tk.NORMAL
                self.xml_entry.configure(state=edit_state)
                self.wav_dir_entry.configure(state=edit_state)
                self.xml_browse_btn.configure(state=edit_state)
                self.wav_dir_browse_btn.configure(state=edit_state)
                self.refresh_btn.configure(state=edit_state)
                self.format_wav_radio.configure(state=edit_state)
                self.format_aiff_radio.configure(state=edit_state)
                self.bit_depth_combo.configure(state="readonly")
                self.sample_rate_combo.configure(state="readonly")
                self.convert_btn.grid()
                self._update_convert_enabled()

    def _enter_import_edit_mode(self) -> None:
        if self._busy or self._import_edit_active():
            return
        wav_dir, _output = self._resolved_output_paths()
        try:
            draft = runtime.load_import_edit_draft(wav_dir)
        except runtime.CliError as exc:
            self._show_edit_error("Cannot edit Import XML", exc)
            return
        self._import_edit_selection_snapshot = self._selected_playlists(
            unique_names=False
        )
        self._import_edit_draft = draft
        self._view_root = draft.root
        self._paint_playlist_tree(draft.root, select=[])
        self._sync_import_edit_chrome()
        title = self.root.title()
        if "Editing Import XML" not in title:
            self.root.title(title + " — Editing Import XML")

    def _leave_import_edit_mode(self, *, discard: bool) -> None:
        if not self._import_edit_active():
            return
        if discard and self._import_edit_draft and self._import_edit_draft.dirty:
            if not runtime.ask_centered_yesno(
                self.root,
                "Discard changes?",
                "Discard unsaved Import XML edits?",
            ):
                return
        self._import_edit_draft = None
        self._view_root = self._source_root
        title = self.root.title()
        self.root.title(title.replace(" — Editing Import XML", ""))
        if self._source_root is not None:
            wanted = list(self._import_edit_selection_snapshot or ())
            self._paint_playlist_tree(self._source_root, select=wanted)
        else:
            self.playlist_tree.delete(*self.playlist_tree.get_children())
            self._playlist_entries = []
            self._playlist_iids = {}
            self._refresh_tracklist_preview()
        self._sync_import_edit_chrome()
        self._set_idle_status()

    def _confirm_edit_preview(
        self,
        *,
        title: str,
        summary: str,
        rows: list,
        confirm_label: str,
    ) -> bool:
        return gui_dialogs.show_edit_confirm_dialog(
            self.root,
            title=title,
            summary=summary,
            rows=rows,
            confirm_label=confirm_label,
            place_over=self._place_dialog_over_app,
        )

    def _save_import_edit(self) -> None:
        draft = self._import_edit_draft
        if draft is None or not draft.dirty:
            return
        rows = preview_save(draft)
        trash_count = sum(1 for row in rows if row.action == ACTION_MOVE_TO_TRASH)
        if trash_count:
            noun = "file" if trash_count == 1 else "files"
            summary = (
                f"{trash_count} {noun} scheduled for Trash. "
                "Import XML will be updated."
            )
        else:
            summary = "Import XML will be updated."
        if not self._confirm_edit_preview(
            title="Save Import XML edits?",
            summary=summary,
            rows=rows,
            confirm_label="Save",
        ):
            return
        try:
            runtime.save_import_edit_draft(draft)
        except runtime.CliError as exc:
            self._show_edit_error("Save failed", exc)
            return
        runtime.show_centered_message(
            self.root,
            "Saved",
            "Import XML was updated.",
        )
        self._leave_import_edit_mode(discard=False)

    def _cancel_import_edit(self) -> None:
        self._leave_import_edit_mode(discard=True)

    def _show_edit_error(self, title: str, exc: BaseException) -> None:
        runtime.show_centered_message(self.root, title, str(exc))

    def _refresh_after_edit(self, select: list[tuple[str, str]]) -> None:
        draft = self._import_edit_draft
        if draft is None:
            return
        self._paint_playlist_tree(draft.root, select=select)
        self._sync_import_edit_chrome()

    def _confirm_discard_if_editing(self) -> bool:
        """Return True if it is OK to proceed (not editing, or discard confirmed)."""
        if not self._import_edit_active():
            return True
        self._leave_import_edit_mode(discard=True)
        return not self._import_edit_active()

    def _on_playlist_context_menu(self, event: tk.Event) -> None:
        if not self._import_edit_active():
            return
        row = self.playlist_tree.identify_row(event.y)
        if not row:
            return
        meta = self._playlist_iids.get(row)
        if meta is None or meta[0] != "playlist":
            return
        self.playlist_tree.selection_set(row)
        folder, name = meta[1], meta[2]
        if self._playlist_is_virtual(folder, name):
            return
        menu = tk.Menu(self.root, tearoff=0)

        def do_remove() -> None:
            self._edit_remove_playlist(folder, name)

        menu.add_command(label="Remove playlist", command=do_remove)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _on_tracklist_context_menu(self, event: tk.Event) -> None:
        if not self._import_edit_active():
            return
        row = self.tracklist_tree.identify_row(event.y)
        if not row or row not in self._tracklist_iids:
            return
        if row not in set(self.tracklist_tree.selection()):
            self.tracklist_tree.selection_set(row)
        menu = tk.Menu(self.root, tearoff=0)
        path = self._tracklist_paths.get(row)
        if path is not None:
            menu.add_command(
                label="Reveal in Finder",
                command=lambda p=path: runtime.open_in_finder(p),
            )
        refs = self._selected_track_leaf_refs()
        can_remove_from_playlist = any(
            not self._playlist_is_virtual(ref.folder, ref.name) for ref in refs
        )
        if path is not None:
            menu.add_separator()
        if can_remove_from_playlist:
            menu.add_command(
                label="Remove from playlist",
                command=self._edit_remove_selected_tracks_from_playlist,
            )
        menu.add_command(
            label="Move to Trash",
            command=self._edit_remove_selected_tracks_from_collection,
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _selected_track_leaf_refs(self):
        refs = []
        for iid in self.tracklist_tree.selection():
            ref = self._tracklist_iids.get(iid)
            if ref is not None:
                refs.append(ref)
        return refs

    def _edit_remove_playlist(self, folder: str, name: str) -> None:
        draft: ImportEditDraft | None = self._import_edit_draft
        if draft is None:
            return
        if self._playlist_is_virtual(folder, name):
            return
        try:
            remove_playlist(draft, folder=folder, name=name)
        except runtime.CliError as exc:
            self._show_edit_error("Cannot remove playlist", exc)
            return
        self._refresh_after_edit([])

    def _edit_remove_track_from_playlist(
        self, folder: str, name: str, track_id: str
    ) -> None:
        draft = self._import_edit_draft
        if draft is None:
            return
        if self._playlist_is_virtual(folder, name):
            return
        try:
            remove_track_from_playlist(
                draft, folder=folder, name=name, track_id=track_id
            )
        except runtime.CliError as exc:
            self._show_edit_error("Cannot remove track", exc)
            return
        self._refresh_after_edit([(folder, name)])

    def _edit_remove_track_from_collection(self, track_id: str) -> None:
        draft = self._import_edit_draft
        if draft is None:
            return
        keep = self._selected_playlists(unique_names=False)
        try:
            remove_track_from_collection(draft, track_id=track_id)
        except runtime.CliError as exc:
            self._show_edit_error("Cannot remove track", exc)
            return
        self._refresh_after_edit(keep)

    def _edit_remove_selected_tracks_from_playlist(self) -> None:
        draft = self._import_edit_draft
        if draft is None:
            return
        refs = [
            ref
            for ref in self._selected_track_leaf_refs()
            if not self._playlist_is_virtual(ref.folder, ref.name)
        ]
        if not refs:
            return
        last_err: runtime.CliError | None = None
        removed = False
        keep: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for ref in refs:
            try:
                remove_track_from_playlist(
                    draft, folder=ref.folder, name=ref.name, track_id=ref.key
                )
                removed = True
            except runtime.CliError as exc:
                last_err = exc
            pair = (ref.folder, ref.name)
            if pair not in seen:
                seen.add(pair)
                keep.append(pair)
        if not removed and last_err is not None:
            self._show_edit_error("Cannot remove track", last_err)
            return
        self._refresh_after_edit(keep)

    def _edit_remove_selected_tracks_from_collection(self) -> None:
        draft = self._import_edit_draft
        if draft is None:
            return
        refs = self._selected_track_leaf_refs()
        keys: list[str] = []
        seen: set[str] = set()
        for ref in refs:
            if ref.key and ref.key not in seen:
                seen.add(ref.key)
                keys.append(ref.key)
        if not keys:
            return
        keep = self._selected_playlists(unique_names=False)
        last_err: runtime.CliError | None = None
        removed = False
        for track_id in keys:
            try:
                remove_track_from_collection(draft, track_id=track_id)
                removed = True
            except runtime.CliError as exc:
                last_err = exc
        if not removed and last_err is not None:
            self._show_edit_error("Cannot remove track", last_err)
            return
        self._refresh_after_edit(keep)

    def _bind_import_edit_window_guards(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._on_import_edit_close)
        self.root.bind("<Escape>", self._on_import_edit_escape, add="+")

    def _on_import_edit_close(self) -> None:
        if not self._confirm_discard_if_editing():
            return
        self.root.destroy()

    def _on_import_edit_escape(self, _event: object = None) -> str | None:
        if not self._import_edit_active():
            return None
        self._cancel_import_edit()
        return "break"
