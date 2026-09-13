"""Playlists / tracklist mixin."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

from gui import constants
from gui import runtime
from gui import tracklist as gui_tracklist
from gui.types import PlaylistEntry, PlaylistNodeKind, PlaylistRef, TrackLeafRef
from preview_bit_depth import (
    PREVIEW_BIT_DEPTH_BATCH,
    PREVIEW_BIT_DEPTH_YIELD_S,
    cached_preview_bit_depth,
    peek_cached_preview_bit_depth,
)


class PlaylistsMixin:
    def _load_playlists(self) -> None:
        self.playlist_tree.delete(*self.playlist_tree.get_children())
        self._playlist_entries = []
        self._playlist_iids = {}
        self._source_root = None
        self._collection_indexes_cache = None
        # Clear under the same lock used by peek/fill so a probe worker cannot
        # race a load that resets the cache.
        with self._preview_bit_depth_lock:
            self._preview_bit_depth_cache.clear()
        xml_s = self.xml_var.get().strip()
        if not xml_s:
            self._refresh_tracklist_preview()
            return
        path = Path(xml_s).expanduser()
        if not path.is_file():
            self._refresh_tracklist_preview()
            self.status_var.set(f"XML not found: {path}")
            return
        try:
            root = runtime.load_dj_playlists(path)
        except runtime.CliError as exc:
            self._refresh_tracklist_preview()
            self.status_var.set(str(exc))
            return
        self._source_root = root
        self._collection_indexes_cache = runtime.collection_indexes(root)
        nodes = runtime.iter_playlist_nodes(root)
        by_id, by_location = self._collection_indexes_cache
        for kind, folder, name, node in nodes:
            count = (
                runtime.playlist_preview_track_count(
                    node,
                    by_id,
                    by_location,
                    supported_ext=runtime.SUPPORTED_LOSSLESS_EXT,
                )
                if kind == "playlist"
                else 0
            )
            self._playlist_entries.append(
                PlaylistEntry.from_walk(kind, folder, name, count, node)
            )
        self._playlist_search.show()
        self._track_search.show()
        self._apply_playlist_filter()

    @staticmethod
    def _playlist_iid(kind: str, folder: str, name: str) -> str:
        return gui_tracklist.playlist_iid(
            kind, folder, name, playlist_label=runtime.playlist_label
        )

    @staticmethod
    def _playlist_row_text(kind: str, name: str, count: int) -> str:
        return gui_tracklist.playlist_row_text(kind, name, count)

    def _apply_playlist_filter(self) -> None:
        query = self._playlist_search.query().casefold()
        self.playlist_tree.delete(*self.playlist_tree.get_children())
        self._playlist_iids = {}

        if query:
            keep: set[tuple[str, str, str]] = set()
            for entry in self._playlist_entries:
                if entry.kind != PlaylistNodeKind.PLAYLIST:
                    continue
                label = runtime.playlist_label(entry.folder, entry.name)
                display = f"{label} ({entry.count} tracks)"
                haystack = f"{entry.name} {label} {display}".casefold()
                if query not in haystack:
                    continue
                keep.add((entry.kind.value, entry.folder, entry.name))
                parts = [p for p in entry.folder.split(" / ") if p] if entry.folder else []
                for i in range(len(parts)):
                    anc_folder = " / ".join(parts[:i]) if i else ""
                    keep.add(("folder", anc_folder, parts[i]))
            visible = [
                entry
                for entry in self._playlist_entries
                if (entry.kind.value, entry.folder, entry.name) in keep
            ]
        else:
            visible = list(self._playlist_entries)

        folder_iid_by_path: dict[str, str] = {"": ""}
        for entry in visible:
            kind = entry.kind.value
            folder = entry.folder
            name = entry.name
            count = entry.count
            iid = self._playlist_iid(kind, folder, name)
            parent_path = folder
            parent_iid = folder_iid_by_path.get(parent_path, "")
            text = self._playlist_row_text(kind, name, count)
            self.playlist_tree.insert(parent_iid, tk.END, iid=iid, text=text, open=False)
            self._playlist_iids[iid] = (kind, folder, name)
            if kind == "folder":
                child_path = runtime.playlist_label(folder, name)
                folder_iid_by_path[child_path] = iid

        if query:
            for iid, (kind, _folder, _name) in self._playlist_iids.items():
                if kind == "folder":
                    self.playlist_tree.item(iid, open=True)

        self._refresh_tracklist_preview()

    def _on_playlist_button1(self, event: object) -> str | None:
        """Folder row clicks only expand/collapse; playlists keep normal select."""
        if self._busy:
            return None
        tree = self.playlist_tree
        y = getattr(event, "y", None)
        if y is None:
            return None
        row = tree.identify_row(y)
        if not row:
            return None
        meta = self._playlist_iids.get(row)
        if meta is None or meta[0] != "folder":
            return None
        tree.item(row, open=not bool(tree.item(row, "open")))
        return "break"

    def _on_playlist_select(self, _event: object = None) -> None:
        if self._busy or self._playlist_selecting:
            return
        tree = self.playlist_tree
        keep = [
            iid
            for iid in tree.selection()
            if (meta := self._playlist_iids.get(iid)) is not None
            and meta[0] == "playlist"
        ]
        if set(keep) != set(tree.selection()):
            self._playlist_selecting = True
            try:
                tree.selection_set(keep)
            finally:
                self._playlist_selecting = False
        self._refresh_tracklist_preview()

    def _on_tracklist_button1(self, event: object) -> str | None:
        """Expand/collapse arrow on a playlist group must not change selection."""
        if self._busy:
            return None
        tree = self.tracklist_tree
        y = getattr(event, "y", None)
        x = getattr(event, "x", None)
        if y is None or x is None:
            return None
        row = tree.identify_row(y)
        if not row or row not in self._tracklist_group_iids:
            return None
        element = tree.identify("element", x, y)
        if element != "Treeitem.indicator":
            return None
        is_open = not bool(tree.item(row, "open"))
        tree.item(row, open=is_open)
        self._tracklist_group_open[self._tracklist_group_iids[row]] = is_open
        return "break"

    def _remember_tracklist_group_open(self, _event: object = None) -> None:
        iid = self.tracklist_tree.focus()
        key = self._tracklist_group_iids.get(iid)
        if key is not None:
            self._tracklist_group_open[key] = bool(
                self.tracklist_tree.item(iid, "open")
            )

    def _on_tracklist_select(self, _event: object = None) -> None:
        if self._busy or self._tracklist_selecting:
            return
        preview = self.tracklist_tree
        selected = list(preview.selection())
        leaf_iids: list[str] = []
        remapped = False
        for iid in selected:
            if iid in self._tracklist_iids:
                leaf_iids.append(iid)
                continue
            remapped = True
            for child in preview.get_children(iid):
                if child in self._tracklist_iids:
                    leaf_iids.append(child)
        seen: set[str] = set()
        unique_leaves: list[str] = []
        for iid in leaf_iids:
            if iid not in seen:
                seen.add(iid)
                unique_leaves.append(iid)
        if remapped or set(selected) != set(unique_leaves):
            self._tracklist_selecting = True
            try:
                preview.selection_set(unique_leaves)
            finally:
                self._tracklist_selecting = False
        self._sync_tracklist_header_highlights()
        self._set_idle_status(self._tracklist_selection_summary())

    def _sync_tracklist_header_highlights(self) -> None:
        """Darker blue on playlist group rows when any of their tracks are selected."""
        preview = self.tracklist_tree
        selected = set(preview.selection())
        for group_iid in preview.get_children(""):
            children = preview.get_children(group_iid)
            active = bool(children) and any(child in selected for child in children)
            tags = (
                (constants.TRACKLIST_HEADER_TAG, constants.TRACKLIST_HEADER_SELECTED_TAG)
                if active
                else (constants.TRACKLIST_HEADER_TAG,)
            )
            preview.item(group_iid, tags=tags)

    def _tracklist_selection_summary(self) -> str | None:
        unique_keys: set[str] = set()
        playlists: set[tuple[str, str]] = set()
        for iid in self.tracklist_tree.selection():
            meta = self._tracklist_iids.get(iid)
            if meta is None:
                continue
            playlists.add((meta.folder, meta.name))
            if meta.key:
                unique_keys.add(meta.key)
        if not unique_keys or not playlists:
            return None
        painted = len(playlists)
        playlist_word = "playlist" if painted == 1 else "playlists"
        return f"{len(unique_keys)} unique tracks from {painted} {playlist_word}"

    def _playlist_node(self, folder: str, name: str):
        for entry in self._playlist_entries:
            if (
                entry.kind == PlaylistNodeKind.PLAYLIST
                and entry.folder == folder
                and entry.name == name
            ):
                return entry.node
        return None

    @staticmethod
    def _track_preview_row(track) -> tuple[str, str, str, str]:
        """Return (label, format, bit_depth, sample_rate) from a collection TRACK.

        Bit depth is always — here; file headers are filled asynchronously.
        """
        return gui_tracklist.track_preview_row(
            track, decode_location=runtime.decode_location
        )

    def _sync_scan_indicator(self) -> None:
        if self._busy or not self._preview_scan_active:
            self.scan_status_var.set("")
            return
        self.scan_status_var.set(constants.SCANNING_BIT_DEPTH)

    def _set_preview_scan_active(self, active: bool) -> None:
        self._preview_scan_active = active
        self._sync_scan_indicator()

    def _refresh_tracklist_preview(self) -> None:
        self._tracklist_tech_gen += 1
        gen = self._tracklist_tech_gen
        self.tracklist_tree.delete(*self.tracklist_tree.get_children())
        self._tracklist_iids = {}
        self._tracklist_paths = {}
        self._tracklist_group_iids = {}
        if self._source_root is None:
            self._tracklist_group_open.clear()
            self._set_preview_scan_active(False)
            self._set_idle_status()
            return
        selected = self._selected_playlists(unique_names=False)
        if not selected:
            self._tracklist_group_open.clear()
            self._set_preview_scan_active(False)
            self._set_idle_status()
            return
        selected_keys = set(selected)
        for key in list(self._tracklist_group_open):
            if key not in selected_keys:
                del self._tracklist_group_open[key]
        query = self._track_search.query().casefold()
        if self._collection_indexes_cache is None:
            self._collection_indexes_cache = runtime.collection_indexes(self._source_root)
        by_id, by_location = self._collection_indexes_cache
        leaf_iids: list[str] = []
        painted = 0
        paths: list[Path] = []
        seen_paths: set[Path] = set()
        for folder, name in selected:
            node = self._playlist_node(folder, name)
            if node is None:
                continue
            key_type = node.get("KeyType", "0")
            matched: list[tuple[str, str, tuple[str, str, str], Path | None]] = []
            for entry in node.findall("TRACK"):
                key = entry.get("Key") or ""
                track = None
                if key:
                    if key_type == "1":
                        track = by_location.get(key)
                    else:
                        track = by_id.get(key)
                label, fmt, depth, rate = self._track_preview_row(track)
                if query and query not in label.casefold():
                    continue
                loc = (track.get("Location") or "") if track is not None else ""
                path = runtime.decode_location(loc) if loc else None
                if not runtime.track_included_in_playlist_preview(
                    track, supported_ext=runtime.SUPPORTED_LOSSLESS_EXT
                ):
                    continue
                if path is not None:
                    hit, bits = peek_cached_preview_bit_depth(
                        path,
                        self._preview_bit_depth_cache,
                        lock=self._preview_bit_depth_lock,
                    )
                    if hit:
                        depth = str(bits) if bits is not None else "—"
                    elif path not in seen_paths:
                        seen_paths.add(path)
                        paths.append(path)
                matched.append((key, label, (fmt, depth, rate), path))
            if not matched:
                continue
            group_key = (folder, name)
            is_open = self._tracklist_group_open.setdefault(group_key, True)
            group_text = f"{name} ({len(matched)} tracks)"
            group_iid = self.tracklist_tree.insert(
                "",
                tk.END,
                text=group_text,
                open=is_open,
                values=("", "", ""),
                tags=(constants.TRACKLIST_HEADER_TAG,),
            )
            self._tracklist_group_iids[group_iid] = group_key
            painted += 1
            for key, label, values, path in matched:
                leaf_iid = self.tracklist_tree.insert(
                    group_iid, tk.END, text=label, values=values
                )
                self._tracklist_iids[leaf_iid] = TrackLeafRef(
                    folder=folder, name=name, key=key
                )
                if path is not None:
                    self._tracklist_paths[leaf_iid] = path
                leaf_iids.append(leaf_iid)
        if not painted:
            self._set_preview_scan_active(False)
            self._set_idle_status()
            return
        self._tracklist_selecting = True
        try:
            if leaf_iids:
                self.tracklist_tree.selection_set(leaf_iids)
        finally:
            self._tracklist_selecting = False
        self._sync_tracklist_header_highlights()
        self._set_idle_status(self._tracklist_selection_summary())
        if self._tracklist_sort_column is not None:
            self._apply_tracklist_sort()
        if paths:
            self._set_preview_scan_active(True)
            worker = runtime.threading.Thread(
                target=lambda: self._fill_preview_bit_depths(gen, paths),
                daemon=True,
            )
            self._preview_probe_thread = worker
            worker.start()
        else:
            self._set_preview_scan_active(False)

    def _on_tracklist_sort(self, column: str) -> None:
        if self._tracklist_sort_column == column:
            self._tracklist_sort_reverse = not self._tracklist_sort_reverse
        else:
            self._tracklist_sort_column = column
            self._tracklist_sort_reverse = False
        self._apply_tracklist_sort()

    def _tracklist_sort_key(self, iid: str, column: str):
        preview = self.tracklist_tree
        return gui_tracklist.tracklist_sort_key(
            preview.item(iid, "text"),
            list(preview.item(iid, "values")),
            column,
        )

    def _apply_tracklist_sort(self) -> None:
        column = self._tracklist_sort_column
        if column is None:
            return
        preview = self.tracklist_tree
        reverse = self._tracklist_sort_reverse
        for group_iid in preview.get_children(""):
            leaves = list(preview.get_children(group_iid))
            ordered = gui_tracklist.order_tracklist_leaves(
                leaves,
                column=column,
                reverse=reverse,
                sort_key=self._tracklist_sort_key,
            )
            for index, iid in enumerate(ordered):
                preview.move(iid, group_iid, index)

    def _fill_preview_bit_depths(self, gen: int, paths: list[Path]) -> None:
        batch: dict[Path, str] = {}

        def flush() -> None:
            if not batch or gen != self._tracklist_tech_gen:
                batch.clear()
                return
            snapshot = dict(batch)
            batch.clear()
            self._ui(lambda b=snapshot, g=gen: self._apply_preview_bit_depths(g, b))

        for path in paths:
            if gen != self._tracklist_tech_gen:
                return
            try:
                bits = cached_preview_bit_depth(
                    path,
                    self._preview_bit_depth_cache,
                    lock=self._preview_bit_depth_lock,
                )
            except Exception:
                continue
            if bits is not None:
                batch[path] = str(bits)
            if len(batch) >= PREVIEW_BIT_DEPTH_BATCH:
                flush()
                runtime.time.sleep(PREVIEW_BIT_DEPTH_YIELD_S)
        flush()
        if gen == self._tracklist_tech_gen:
            self._ui(lambda g=gen: self._finish_preview_bit_depth_scan(g))

    def _finish_preview_bit_depth_scan(self, gen: int) -> None:
        if gen != self._tracklist_tech_gen:
            return
        self._set_preview_scan_active(False)

    def _apply_preview_bit_depths(self, gen: int, depths: dict[Path, str]) -> None:
        if gen != self._tracklist_tech_gen or not depths:
            return
        for iid, path in self._tracklist_paths.items():
            depth = depths.get(path)
            if depth is None:
                continue
            values = list(self.tracklist_tree.item(iid, "values"))
            if len(values) < 3:
                continue
            values[1] = depth
            self.tracklist_tree.item(iid, values=values)
        if self._tracklist_sort_column == "bit_depth":
            self._apply_tracklist_sort()

    def _selected_playlists(self, *, unique_names: bool = True) -> list[tuple[str, str]]:
        chosen: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def add_playlist(folder: str, name: str) -> None:
            entry = (folder, name)
            if entry not in seen:
                seen.add(entry)
                chosen.append(entry)

        def collect_from_iid(iid: str) -> None:
            meta = self._playlist_iids.get(iid)
            if meta is None:
                return
            kind, folder, name = meta
            if kind == "playlist":
                add_playlist(folder, name)

        for iid in self.playlist_tree.selection():
            collect_from_iid(iid)

        if unique_names:
            names = [name for _folder, name in chosen]
            dupe_error = runtime.duplicate_playlist_name_error(names)
            if dupe_error is not None:
                raise runtime.CliError(dupe_error)
        return chosen
