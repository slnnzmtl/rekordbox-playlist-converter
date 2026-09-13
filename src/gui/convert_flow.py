"""Convert / preview / finish mixin."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from convert.models import PreparedConversion
from convert.quality import (
    coerce_bit_depth,
    coerce_output_format,
    coerce_sample_rate,
)
from gui import constants
from gui import dialogs as gui_dialogs
from gui import runtime
from gui.helpers import (
    total_successful_conversions,
)


@dataclass(frozen=True)
class _PrepareHandoff:
    """Snapshot args for the prepare thread; not parked on ConverterApp."""

    selected: tuple[tuple[str, str], ...]
    keys_by_playlist: Mapping[tuple[str, str], tuple[str, ...]]
    wav_dir: Path
    output: Path
    output_format: str
    max_bit_depth: int
    max_sample_rate: int
    xml_path: Path


class ConvertFlowMixin:
    def _start_convert(self) -> None:
        if self._busy:
            return
        if not self._wav_dir_valid or self._wav_dir_checking:
            return
        xml_s = self.xml_var.get().strip()
        if not xml_s:
            runtime.messagebox.showerror("Missing XML", "Choose a Rekordbox XML export.")
            return
        try:
            selected = self._selected_playlists(unique_names=False)
        except runtime.CliError as exc:
            runtime.messagebox.showerror("Selection", str(exc))
            return
        if not selected:
            runtime.messagebox.showerror("Selection", "Select at least one playlist.")
            return

        keys_by_playlist: dict[tuple[str, str], list[str]] = {}
        for iid in self.tracklist_tree.selection():
            meta = self._tracklist_iids.get(iid)
            if meta is None:
                continue
            folder, name, key = meta.folder, meta.name, meta.key
            if not key:
                continue
            keys_by_playlist.setdefault((folder, name), []).append(key)
        selected = [
            (folder, name)
            for folder, name in selected
            if keys_by_playlist.get((folder, name))
        ]
        if not selected:
            runtime.messagebox.showerror("Selection", "Select at least one track.")
            return
        names = [name for _folder, name in selected]
        dupe_error = runtime.duplicate_playlist_name_error(names)
        if dupe_error is not None:
            runtime.messagebox.showerror("Selection", dupe_error)
            return

        wav_dir, output = self._resolved_output_paths()
        self._persist_output_preferences()
        output_format = coerce_output_format(self.format_var.get())
        max_bit_depth = coerce_bit_depth(self.bit_depth_var.get())
        max_sample_rate = coerce_sample_rate(self.sample_rate_var.get())
        xml_path = Path(xml_s).expanduser()

        self._set_busy(True)
        self._cancel_event.clear()
        self.status_var.set("Preparing…")

        handoff = _PrepareHandoff(
            selected=tuple(selected),
            keys_by_playlist=MappingProxyType(
                {key: tuple(keys) for key, keys in keys_by_playlist.items()}
            ),
            wav_dir=wav_dir,
            output=output,
            output_format=output_format,
            max_bit_depth=max_bit_depth,
            max_sample_rate=max_sample_rate,
            xml_path=xml_path,
        )
        runtime.threading.Thread(
            target=self._prepare_worker, args=(handoff,), daemon=True
        ).start()

    def _prepare_worker(self, handoff: _PrepareHandoff) -> None:
        try:
            # Reuse UI-loaded tree when it matches this convert's XML; else parse once.
            loaded_xml = Path(self.xml_var.get().strip()).expanduser()
            if self._source_root is not None and loaded_xml == handoff.xml_path:
                source_root = self._source_root
            elif handoff.xml_path.is_file():
                try:
                    source_root = runtime.load_dj_playlists(handoff.xml_path)
                except runtime.CliError as exc:
                    self._ui(lambda e=[str(exc)]: self._finish_error(e))
                    return
            else:
                source_root = None

            def progress_tick(
                current: int,
                total: int,
                action: str,
                track_name: str,
            ) -> None:
                self._ui(
                    lambda c=current, t=total, a=action, n=track_name: self._set_progress(
                        c, t, action=a, name=n
                    )
                )

            def on_playlist_preparing(name: str, index: int, total: int) -> None:
                label = f"{name} ({index + 1}/{total})"
                self._ui(lambda l=label: self.status_var.set(f"Preparing {l}…"))

            prepared, errors = runtime.prepare_batch(
                handoff.xml_path,
                list(handoff.selected),
                handoff.wav_dir,
                handoff.output,
                output_format=handoff.output_format,
                max_bit_depth=handoff.max_bit_depth,
                max_sample_rate=handoff.max_sample_rate,
                force=False,
                track_keys_by_playlist=dict(handoff.keys_by_playlist),
                on_progress=progress_tick,
                cancel_event=self._cancel_event,
                source_root=source_root,
                on_playlist_preparing=on_playlist_preparing,
            )
            if errors:
                self._ui(lambda e=errors: self._finish_error(e))
                return
            assert prepared is not None
            self._ui(lambda p=prepared: self._on_prepare_ready(p))
        except runtime.CancelledError:
            self._ui(self._finish_cancelled)
        except runtime.CliError as exc:
            self._ui(lambda e=str(exc): self._finish_error(e))
        except Exception as exc:  # noqa: BLE001 — show unexpected errors in UI
            self._ui(lambda e=str(exc): self._finish_error(e))

    def _on_prepare_ready(self, prepared: PreparedConversion) -> None:
        if self._cancel_event.is_set():
            self._finish_cancelled()
            return
        self._prepared_conversion = prepared
        self._show_conversion_preview(prepared)

    def _show_conversion_preview(self, prepared: PreparedConversion) -> None:
        """Modal unique-output preview; Convert continues, Back/Escape discard."""
        self._close_preview_dialog()
        preview = prepared.preview
        summary = (
            f"{preview.unique_outputs} unique output file(s) · "
            f"{preview.selected} selected · "
            f"{preview.resolved} resolved · "
            f"{preview.duplicates} duplicate(s) · "
            f"{preview.missing} missing"
        )
        space_issue = runtime.insufficient_output_space_message(
            prepared.library_dir,
            runtime.preview_write_bytes(preview),
        )
        self._preview_dialog = gui_dialogs.show_conversion_preview_dialog(
            self.root,
            summary=summary,
            items=preview.items,
            action_labels=constants.PREVIEW_ACTION_LABELS,
            bit_depth_labels=constants.BIT_DEPTH_LABELS,
            sample_rate_labels=constants.SAMPLE_RATE_LABELS,
            space_issue=space_issue,
            on_back=self._discard_prepared_conversion,
            on_convert=self._confirm_prepared_conversion,
            place_over=self._place_dialog_over_app,
        )
        self.status_var.set("Review conversion…")
        self._animate_progress_to(0, snap=True)

    def _close_preview_dialog(self) -> None:
        dlg = self._preview_dialog
        self._preview_dialog = None
        if dlg is None:
            return
        try:
            dlg.grab_release()
        except tk.TclError:
            pass
        try:
            dlg.destroy()
        except tk.TclError:
            pass

    def _discard_prepared_conversion(self) -> None:
        self._close_preview_dialog()
        self._prepared_conversion = None
        self._confirm_prepared = None
        self._cancel_event.clear()
        self._set_busy(False)
        self._animate_progress_to(0, snap=True)
        self._set_idle_status(self._tracklist_selection_summary())

    def _confirm_prepared_conversion(self) -> None:
        prepared = self._prepared_conversion
        if prepared is None:
            return
        space_issue = runtime.insufficient_output_space_message(
            prepared.library_dir,
            runtime.preview_write_bytes(prepared.preview),
        )
        if space_issue:
            runtime.messagebox.showerror("Not enough space", space_issue)
            return
        self._close_preview_dialog()
        self._prepared_conversion = None
        self._confirm_prepared = prepared
        self.status_var.set("Converting…")
        runtime.threading.Thread(target=self._write_worker, daemon=True).start()

    def _write_worker(self) -> None:
        prepared = self._confirm_prepared
        if prepared is None:
            self._ui(lambda: self._finish_error("Nothing to convert."))
            return
        plans = prepared.plans
        items = prepared.items
        output = prepared.output
        skipped = list(prepared.skipped)
        try:
            summaries: list[str] = []
            total = len(items)

            def on_progress(
                current: int,
                _plan_total: int,
                action: str,
                track_name: str,
            ) -> None:
                self._ui(
                    lambda o=current, t=total, a=action, n=track_name: self._set_progress(
                        o, t, action=a, name=n
                    )
                )

            def _finish_cancel_with_errors(
                encode_errors: list[str] | None = None,
            ) -> None:
                self._ui(lambda e=encode_errors: self._finish_cancelled(e or None))

            if self._cancel_event.is_set():
                _finish_cancel_with_errors()
                return
            try:
                batch_stats = runtime.execute_prepared(
                    prepared,
                    force=False,
                    progress=False,
                    on_progress=on_progress,
                    cancel_event=self._cancel_event,
                )
            except OSError as exc:
                self._ui(
                    lambda e=[f"cannot write converter manifest: {exc}"]: self._finish_error(
                        e
                    )
                )
                return

            for i, plan in enumerate(plans):
                parts = []
                if batch_stats.converted and plan is plans[0]:
                    parts.append(f"{batch_stats.converted} converted")
                if batch_stats.copied and plan is plans[0]:
                    parts.append(f"{batch_stats.copied} copied")
                if batch_stats.skipped and plan is plans[0]:
                    parts.append(f"{batch_stats.skipped} skipped")
                appended = (
                    batch_stats.appended_by_plan[i]
                    if i < len(batch_stats.appended_by_plan)
                    else 0
                )
                if appended:
                    parts.append(f"+{appended} playlist entries")
                if plan.warnings:
                    parts.append(f"{len(plan.warnings)} missing skipped")
                detail = ", ".join(parts) if parts else "done"
                summaries.append(f"{plan.wav_playlist_name}: {detail}")

            if self._cancel_event.is_set():
                _finish_cancel_with_errors(batch_stats.errors or None)
                return
            if total == 0:
                self._ui(lambda: self._set_progress(0, 0))
            else:
                self._ui(lambda t=total: self._set_progress(t, t))
            if batch_stats.errors:
                self._ui(lambda e=batch_stats.errors: self._finish_error(e))
                return
            open_dir = plans[0].media_dir
            out = str(output)
            if total_successful_conversions([batch_stats]) == 0:
                self._ui(
                    lambda s=summaries, w=skipped: self._finish_no_conversions(s, w)
                )
            else:
                self._ui(
                    lambda s=summaries, o=out, w=skipped, d=open_dir, x=output: self._finish_ok(
                        s, o, w, d, x
                    )
                )
        except runtime.CliError as exc:
            self._ui(lambda e=str(exc): self._finish_error(e))
        except Exception as exc:  # noqa: BLE001 — show unexpected errors in UI
            self._ui(lambda e=str(exc): self._finish_error(e))
        finally:
            self._confirm_prepared = None

    def _show_conversion_errors(self, message: str | list[str]) -> None:
        lines = message if isinstance(message, list) else message.splitlines()
        self._show_list_dialog(
            "Conversion failed",
            "These errors occurred during conversion:",
            lines,
            wait=False,
        )

    def _finish_cancelled(self, errors: str | list[str] | None = None) -> None:
        self._close_preview_dialog()
        self._prepared_conversion = None
        self._confirm_prepared = None
        self._set_busy(False)
        self.status_var.set("Cancelled.")
        self._cancel_cancelled_clear()
        self._cancelled_clear_id = self.root.after(
            constants.CANCELLED_STATUS_CLEAR_MS, self._clear_cancelled_status
        )
        if errors:
            self._show_conversion_errors(errors)

    def _finish_error(self, message: str | list[str]) -> None:
        self._close_preview_dialog()
        self._prepared_conversion = None
        self._confirm_prepared = None
        self._set_busy(False)
        self._animate_progress_to(0, snap=True)
        self.status_var.set("Failed.")
        self._show_conversion_errors(message)

    def _finish_no_conversions(
        self,
        summaries: list[str],
        warnings: list[str] | None = None,
    ) -> None:
        self._prepared_conversion = None
        self._confirm_prepared = None
        self._set_busy(False)
        self._animate_progress_to(0, snap=True)
        self.status_var.set("Finished with no audio files converted or copied.")
        if warnings:
            self._show_list_dialog(
                "No conversions",
                "These files were missing and were skipped:",
                warnings,
                summary="\n".join(summaries),
            )
            return
        runtime.messagebox.showwarning("No conversions", "\n".join(summaries))

    def _finish_ok(
        self,
        summaries: list[str],
        output: str,
        warnings: list[str] | None = None,
        open_dir: Path | None = None,
        import_xml: Path | None = None,
    ) -> None:
        self._prepared_conversion = None
        self._confirm_prepared = None
        self._set_busy(False)
        self._animate_progress_to(100, snap=True)
        body = "\n".join(summaries)
        self.status_var.set(
            f"Done. Point Rekordbox Imported Library at:\n{output}"
        )
        if warnings:
            self._show_list_dialog(
                "Skipped missing tracks",
                "These files were missing and were skipped:",
                warnings,
            )
        fmt = self.format_var.get().strip().lower()
        suffix = "[AIFF]" if fmt == "aiff" else "[WAV]"
        message = (
            f"{body}\n\n"
            "Import into Rekordbox:\n"
            "1. Preferences → View → Layout → enable rekordbox xml\n"
            "2. Preferences → Advanced → Database → Imported Library →\n"
            f"   {output}\n"
            "3. Browser → rekordbox xml → Playlists → Import Playlist\n"
            f"   (or drag the {suffix} playlist into Playlists)"
        )
        self._show_done_dialog(message, open_dir, import_xml)

    def _show_list_dialog(
        self,
        title: str,
        intro: str,
        lines: list[str],
        *,
        summary: str | None = None,
        wait: bool = True,
    ) -> None:
        gui_dialogs.show_list_dialog(
            self.root,
            title,
            intro,
            lines,
            summary=summary,
            wait=wait,
            place_over=self._place_dialog_over_app,
        )

    def _show_done_dialog(
        self,
        message: str,
        open_dir: Path | None,
        import_xml: Path | None = None,
    ) -> None:
        gui_dialogs.show_done_dialog(
            self.root,
            message,
            open_dir=open_dir,
            import_xml=import_xml,
            reveal=runtime.open_in_finder,
            on_open_guide=self._show_usage_guide,
            place_over=self._place_dialog_over_app,
        )


