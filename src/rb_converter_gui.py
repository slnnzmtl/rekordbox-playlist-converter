#!/usr/bin/env python3
"""Tiny tkinter front-end for the Rekordbox WAV converter."""

from __future__ import annotations

import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import rb_playlist_to_wav as rb
from usage_guide import USAGE_GUIDE
from version import __version__

DEFAULT_WAV_DIR = Path.home() / "Documents" / "rekordbox-wav"
DEFAULT_OUTPUT = DEFAULT_WAV_DIR / "rekordbox-wav-import.xml"
SEARCH_PLACEHOLDER = "Search playlists…"


def open_in_finder(path: Path) -> None:
    """Reveal a folder in Finder (macOS) or the platform file browser."""
    target = path if path.is_dir() else path.parent
    if not target.exists():
        return
    subprocess.run(["open", str(target)], check=False)


class ConverterApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Rekordbox WAV Converter")
        root.minsize(560, 480)
        root.geometry("1120x720")

        self.xml_var = tk.StringVar()
        self.wav_dir_var = tk.StringVar(value=str(DEFAULT_WAV_DIR))
        self.output_var = tk.StringVar(value=str(DEFAULT_OUTPUT))
        self.force_var = tk.BooleanVar(value=False)
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Choose a Rekordbox XML export.")
        self._busy = False
        self._search_showing_placeholder = False
        self._usage_window: tk.Toplevel | None = None
        self._progress_target = 0.0
        self._progress_anim_id: str | None = None
        # (folder, name, display label) for every playlist in the XML
        self._playlist_entries: list[tuple[str, str, str]] = []
        # Currently visible rows after search filter
        self._visible_entries: list[tuple[str, str, str]] = []

        self._build()
        self.search_var.trace_add("write", lambda *_: self._apply_playlist_filter())
        if not self.search_var.get():
            self._show_search_placeholder()

    def _build(self) -> None:
        self._build_menubar()

        pad = {"padx": 10, "pady": 4}
        frm = ttk.Frame(self.root, padding=12)
        frm.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(3, weight=1)

        header = ttk.Frame(frm)
        header.grid(row=0, column=0, columnspan=3, sticky="w", **pad)
        self.title_label = ttk.Label(header, text="Rekordbox WAV Converter")
        self.title_label.grid(row=0, column=0, sticky="w")
        self.version_label = ttk.Label(header, text=__version__)
        self.version_label.grid(row=1, column=0, sticky="w")

        ttk.Label(frm, text="Rekordbox XML").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.xml_var).grid(
            row=1, column=1, sticky="ew", **pad
        )
        ttk.Button(frm, text="Browse…", command=self._browse_xml).grid(
            row=1, column=2, **pad
        )

        ttk.Label(frm, text="Playlists").grid(row=2, column=0, sticky="w", **pad)
        self.search_entry = ttk.Entry(frm, textvariable=self.search_var)
        self.search_entry.grid(row=2, column=1, sticky="ew", **pad)
        self.search_entry.bind("<FocusIn>", self._on_search_focus_in)
        self.search_entry.bind("<FocusOut>", self._on_search_focus_out)
        ttk.Label(frm, text="Hold ⌃ to multi-select").grid(
            row=2, column=2, sticky="e", **pad
        )

        list_frame = ttk.Frame(frm)
        list_frame.grid(row=3, column=0, columnspan=3, sticky="nsew", **pad)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.playlist_list = tk.Listbox(
            list_frame, selectmode=tk.EXTENDED, exportselection=False, height=12
        )
        scroll = ttk.Scrollbar(
            list_frame, orient=tk.VERTICAL, command=self.playlist_list.yview
        )
        self.playlist_list.configure(yscrollcommand=scroll.set)
        self.playlist_list.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        ttk.Label(frm, text="WAV folder").grid(row=4, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.wav_dir_var).grid(
            row=4, column=1, sticky="ew", **pad
        )
        ttk.Button(frm, text="Browse…", command=self._browse_wav_dir).grid(
            row=4, column=2, **pad
        )

        ttk.Label(frm, text="Import XML").grid(row=5, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.output_var).grid(
            row=5, column=1, sticky="ew", **pad
        )
        ttk.Button(frm, text="Browse…", command=self._browse_output).grid(
            row=5, column=2, **pad
        )

        opts = ttk.Frame(frm)
        opts.grid(row=6, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Checkbutton(
            opts, text="Overwrite existing WAV files", variable=self.force_var
        ).pack(side=tk.LEFT)
        self.convert_btn = ttk.Button(opts, text="Convert", command=self._start_convert)
        self.convert_btn.pack(side=tk.RIGHT)
        ttk.Button(opts, text="How to use", command=self._show_usage_guide).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

        self.progress = ttk.Progressbar(frm, mode="determinate", maximum=100)
        self.progress.grid(row=7, column=0, columnspan=3, sticky="ew", **pad)
        self.progress["value"] = 0

        ttk.Label(frm, textvariable=self.status_var, wraplength=1000).grid(
            row=8, column=0, columnspan=3, sticky="ew", **pad
        )

        candidates = rb.discover_xml_candidates()
        if candidates:
            self.xml_var.set(str(candidates[0]))
            self._load_playlists()

    def _build_menubar(self) -> None:
        menubar = tk.Menu(self.root)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(
            label="How to Use…",
            command=self._show_usage_guide,
            accelerator="Command-?",
        )
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.config(menu=menubar)
        try:
            self.root.bind_all("<Command-?>", lambda _e: self._show_usage_guide())
            self.root.bind_all("<Command-Shift-/>", lambda _e: self._show_usage_guide())
        except tk.TclError:
            pass

    def _show_usage_guide(self) -> None:
        if self._usage_window is not None and self._usage_window.winfo_exists():
            self._usage_window.lift()
            self._usage_window.focus_force()
            return

        dlg = tk.Toplevel(self.root)
        self._usage_window = dlg
        dlg.title("How to use")
        dlg.transient(self.root)
        dlg.geometry("640x520")
        dlg.minsize(480, 360)

        frm = ttk.Frame(dlg, padding=12)
        frm.grid(row=0, column=0, sticky="nsew")
        dlg.columnconfigure(0, weight=1)
        dlg.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(0, weight=1)

        text = scrolledtext.ScrolledText(
            frm, wrap=tk.WORD, width=72, height=28, font=("Menlo", 11)
        )
        text.grid(row=0, column=0, sticky="nsew")
        text.insert("1.0", USAGE_GUIDE.strip() + "\n")
        text.configure(state=tk.DISABLED)

        def close() -> None:
            self._usage_window = None
            dlg.destroy()

        btns = ttk.Frame(frm)
        btns.grid(row=1, column=0, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Close", command=close).pack(side=tk.RIGHT)
        dlg.bind("<Escape>", lambda _e: close())
        dlg.protocol("WM_DELETE_WINDOW", close)
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        y = (
            self.root.winfo_rooty()
            + (self.root.winfo_height() - dlg.winfo_height()) // 2
        )
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dlg.focus_force()

    def _browse_xml(self) -> None:
        initial = Path.home() / "Documents"
        path = filedialog.askopenfilename(
            title="Rekordbox XML export",
            initialdir=str(initial) if initial.is_dir() else str(Path.home()),
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")],
        )
        if path:
            self.xml_var.set(path)
            self._load_playlists()

    def _browse_wav_dir(self) -> None:
        path = filedialog.askdirectory(
            title="WAV output folder",
            initialdir=self.wav_dir_var.get() or str(DEFAULT_WAV_DIR),
        )
        if path:
            self.wav_dir_var.set(path)

    def _browse_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Import XML",
            initialfile=Path(self.output_var.get()).name or "rekordbox-wav-import.xml",
            initialdir=str(Path(self.output_var.get()).parent)
            if self.output_var.get()
            else str(DEFAULT_WAV_DIR),
            defaultextension=".xml",
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")],
        )
        if path:
            self.output_var.set(path)

    def _load_playlists(self) -> None:
        self.playlist_list.delete(0, tk.END)
        self._playlist_entries = []
        self._visible_entries = []
        xml_s = self.xml_var.get().strip()
        if not xml_s:
            return
        path = Path(xml_s).expanduser()
        if not path.is_file():
            self.status_var.set(f"XML not found: {path}")
            return
        try:
            root = rb.load_dj_playlists(path)
        except rb.CliError as exc:
            self.status_var.set(str(exc))
            return
        entries = rb.iter_playlists(root)
        for folder, name, node in entries:
            count = rb.playlist_track_count(node)
            label = rb.playlist_label(folder, name)
            display = f"{label} ({count} tracks)"
            self._playlist_entries.append((folder, name, display))
        self._show_search_placeholder()
        self._apply_playlist_filter()
        if entries:
            self.status_var.set(f"Loaded {len(entries)} playlist(s). Select and Convert.")
        else:
            self.status_var.set("No playlists found in XML.")

    def _show_search_placeholder(self) -> None:
        self._search_showing_placeholder = True
        self.search_var.set(SEARCH_PLACEHOLDER)

    def _clear_search_placeholder(self) -> None:
        if not self._search_showing_placeholder:
            return
        self._search_showing_placeholder = False
        self.search_var.set("")

    def _on_search_focus_in(self, _event: object | None = None) -> None:
        self._clear_search_placeholder()

    def _on_search_focus_out(self, _event: object | None = None) -> None:
        if not self.search_var.get().strip():
            self._show_search_placeholder()

    def _search_query(self) -> str:
        if self._search_showing_placeholder:
            return ""
        return self.search_var.get().strip()

    def _apply_playlist_filter(self) -> None:
        query = self._search_query().casefold()
        self.playlist_list.delete(0, tk.END)
        if query:
            self._visible_entries = [
                entry
                for entry in self._playlist_entries
                if query in entry[2].casefold()
            ]
        else:
            self._visible_entries = list(self._playlist_entries)
        for _folder, _name, display in self._visible_entries:
            self.playlist_list.insert(tk.END, display)

    def _selected_playlists(self) -> list[tuple[str, str]]:
        indices = self.playlist_list.curselection()
        chosen = [(self._visible_entries[i][0], self._visible_entries[i][1]) for i in indices]
        names = [name for _folder, name in chosen]
        # Same output playlist name `{name} [WAV]` — refuse converting two at once.
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            listed = ", ".join(sorted(dupes))
            raise rb.CliError(
                f"cannot select multiple playlists with the same name: {listed}"
            )
        seen: set[tuple[str, str]] = set()
        unique: list[tuple[str, str]] = []
        for entry in chosen:
            if entry not in seen:
                seen.add(entry)
                unique.append(entry)
        return unique

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.convert_btn.configure(state=state)
        if busy:
            self._cancel_progress_anim()
            self._progress_target = 0.0
            self.progress["value"] = 0
        # On success finish_ok leaves the bar at 100; on error finish_error resets.

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
            self.status_var.set(f"{action.capitalize()} {name} ({current}/{total})…")
        elif total > 0:
            self.status_var.set(f"Working… {current}/{total} ({int(pct)}%)")

    def _ui(self, fn) -> None:
        self.root.after(0, fn)

    def _start_convert(self) -> None:
        if self._busy:
            return
        xml_s = self.xml_var.get().strip()
        if not xml_s:
            messagebox.showerror("Missing XML", "Choose a Rekordbox XML export.")
            return
        try:
            selected = self._selected_playlists()
        except rb.CliError as exc:
            messagebox.showerror("Selection", str(exc))
            return
        if not selected:
            messagebox.showerror("Selection", "Select at least one playlist.")
            return
        wav_dir = Path(self.wav_dir_var.get().strip() or str(DEFAULT_WAV_DIR)).expanduser()
        output = Path(self.output_var.get().strip() or str(DEFAULT_OUTPUT)).expanduser()
        if not wav_dir.is_absolute():
            wav_dir = Path.home() / wav_dir
        if not output.is_absolute():
            output = Path.home() / output
        force = bool(self.force_var.get())
        xml_path = Path(xml_s).expanduser()

        self._set_busy(True)
        self.status_var.set("Preparing…")

        def worker() -> None:
            try:
                summaries: list[str] = []
                skipped: list[str] = []
                playlist_dirs: list[Path] = []
                plans: list[rb.Plan] = []
                for i, (folder, name) in enumerate(selected):
                    label = f"{name} ({i + 1}/{len(selected)})"
                    self._ui(lambda l=label: self.status_var.set(f"Preparing {l}…"))
                    plan, errors = rb.prepare(
                        xml_path,
                        name,
                        wav_dir,
                        output,
                        playlist_folder=folder,
                    )
                    if errors:
                        msg = "\n".join(errors)
                        self._ui(lambda m=msg: self._finish_error(m))
                        return
                    assert plan is not None
                    plans.append(plan)
                    skipped.extend(plan.warnings)
                    playlist_dirs.append(plan.playlist_dir)

                total = sum(len(plan.unique) for plan in plans)
                done_base = 0

                def on_progress(
                    current: int,
                    _plan_total: int,
                    action: str,
                    track_name: str,
                    base: int = 0,
                ) -> None:
                    overall = base + current
                    self._ui(
                        lambda o=overall, t=total, a=action, n=track_name: self._set_progress(
                            o, t, action=a, name=n
                        )
                    )

                for plan in plans:
                    base = done_base

                    def tick(
                        current: int,
                        plan_total: int,
                        action: str,
                        track_name: str,
                        b: int = base,
                    ) -> None:
                        on_progress(current, plan_total, action, track_name, base=b)

                    stats = rb.convert_unique(
                        plan, force=force, progress=False, on_progress=tick
                    )
                    done_base += len(plan.unique)
                    stats.appended = rb.apply_xml(plan)
                    rb.atomic_write_xml(plan.output_root, plan.output)
                    parts = []
                    if stats.converted:
                        parts.append(f"{stats.converted} converted")
                    if stats.copied:
                        parts.append(f"{stats.copied} copied")
                    if stats.skipped:
                        parts.append(f"{stats.skipped} skipped")
                    if stats.appended:
                        parts.append(f"+{stats.appended} playlist entries")
                    if plan.warnings:
                        parts.append(f"{len(plan.warnings)} missing skipped")
                    detail = ", ".join(parts) if parts else "done"
                    summaries.append(f"{plan.wav_playlist_name}: {detail}")

                if total == 0:
                    self._ui(lambda: self._set_progress(0, 0))
                else:
                    self._ui(lambda t=total: self._set_progress(t, t))
                open_dir = playlist_dirs[0] if len(playlist_dirs) == 1 else wav_dir
                out = str(output)
                self._ui(
                    lambda s=summaries, o=out, w=skipped, d=open_dir: self._finish_ok(
                        s, o, w, d
                    )
                )
            except rb.CliError as exc:
                self._ui(lambda e=str(exc): self._finish_error(e))
            except Exception as exc:  # noqa: BLE001 — show unexpected errors in UI
                self._ui(lambda e=str(exc): self._finish_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_error(self, message: str) -> None:
        self._set_busy(False)
        self._animate_progress_to(0, snap=True)
        self.status_var.set("Failed.")
        messagebox.showerror("Conversion failed", message)

    def _finish_ok(
        self,
        summaries: list[str],
        output: str,
        warnings: list[str] | None = None,
        open_dir: Path | None = None,
    ) -> None:
        self._set_busy(False)
        self._animate_progress_to(100, snap=True)
        body = "\n".join(summaries)
        self.status_var.set(
            f"Done. Point Rekordbox Imported Library at:\n{output}"
        )
        if warnings:
            messagebox.showwarning(
                "Skipped missing tracks",
                "These files were missing and were skipped:\n\n"
                + "\n".join(warnings),
            )
        message = (
            f"{body}\n\n"
            "Import into Rekordbox:\n"
            "1. Preferences → View → Layout → enable rekordbox xml\n"
            "2. Preferences → Advanced → Database → Imported Library →\n"
            f"   {output}\n"
            "3. Browser → rekordbox xml → Playlists → Import Playlist\n"
            "   (or drag the [WAV] playlist into Playlists)"
        )
        self._show_done_dialog(message, open_dir)

    def _show_done_dialog(self, message: str, open_dir: Path | None) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("Done")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)

        frm = ttk.Frame(dlg, padding=16)
        frm.grid(row=0, column=0, sticky="nsew")
        ttk.Label(frm, text=message, justify=tk.LEFT, wraplength=480).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        btns = ttk.Frame(frm)
        btns.grid(row=1, column=0, columnspan=2, sticky="e", pady=(16, 0))

        def close() -> None:
            dlg.destroy()

        def open_folder() -> None:
            if open_dir is not None:
                open_in_finder(open_dir)
            close()

        def open_guide() -> None:
            close()
            self._show_usage_guide()

        if open_dir is not None:
            ttk.Button(btns, text="Open folder", command=open_folder).pack(
                side=tk.LEFT, padx=(0, 8)
            )
        ttk.Button(btns, text="Open usage guide", command=open_guide).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(btns, text="OK", command=close).pack(side=tk.LEFT)
        dlg.bind("<Return>", lambda _e: close())
        dlg.bind("<Escape>", lambda _e: close())
        dlg.protocol("WM_DELETE_WINDOW", close)
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dlg.wait_window()


def main() -> int:
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
