"""Convert / preview / finish mixin."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from convert.models import (
    ConvertStats,
    PreparedConversion,
    conversion_report_title,
    format_conversion_counts,
    format_import_guidance,
    format_playlist_report,
    stats_for_playlist,
)
from convert.preview import format_preview_summary
from convert.quality import (
    coerce_bit_depth,
    coerce_output_format,
    coerce_sample_rate,
)
from convert.rerun import ACTION_LABELS
from gui import constants
from gui import dialogs as gui_dialogs
from gui import runtime


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
        if self._import_edit_active():
            runtime.show_centered_message(
                self.root,
                "Editing Import XML",
                "Finish or cancel Import XML editing before converting.",
            )
            return
        if not self._wav_dir_valid or self._wav_dir_checking:
            return
        xml_s = self.xml_var.get().strip()
        if not xml_s:
            runtime.show_centered_message(
                self.root, "Missing XML", "Choose a Rekordbox XML export."
            )
            return
        try:
            selected = self._selected_playlists(unique_names=False)
        except runtime.CliError as exc:
            runtime.show_centered_message(self.root, "Selection", str(exc))
            return
        if not selected:
            runtime.show_centered_message(
                self.root, "Selection", "Select at least one playlist."
            )
            return
        selected = [
            pair
            for pair in selected
            if not self._playlist_is_virtual(pair[0], pair[1])
        ]
        if not selected:
            runtime.show_centered_message(
                self.root,
                "Selection",
                "Unknown lists collection tracks that are not in a playlist. "
                "Select another playlist to convert.",
            )
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
            runtime.show_centered_message(
                self.root, "Selection", "Select at least one track."
            )
            return
        names = [name for _folder, name in selected]
        dupe_error = runtime.duplicate_playlist_name_error(names)
        if dupe_error is not None:
            runtime.show_centered_message(self.root, "Selection", dupe_error)
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

            cached_manifest = None
            cached_fingerprint = None
            if (
                self._cached_library_gen == self._wav_dir_validate_gen
                and self._cached_library_path == handoff.wav_dir
            ):
                cached_manifest = self._cached_library_manifest
                cached_fingerprint = self._cached_library_fingerprint
            opened = runtime.converter_manifest.manifest_for_prepare(
                handoff.wav_dir,
                cached_manifest=cached_manifest,
                cached_fingerprint=cached_fingerprint,
            )
            if opened.error is not None:
                self._ui(lambda e=opened.error: self._finish_error(e))
                return
            manifest = opened.manifest

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
                manifest=manifest,
                fingerprint=opened.fingerprint,
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
        summary = format_preview_summary(preview)
        info_message, block_message = runtime.preview_dialog_footer(
            preview,
            prepared.library_dir,
        )
        self._preview_dialog = gui_dialogs.show_conversion_preview_dialog(
            self.root,
            summary=summary,
            items=preview.items,
            action_labels=ACTION_LABELS,
            bit_depth_labels=constants.BIT_DEPTH_LABELS,
            sample_rate_labels=constants.SAMPLE_RATE_LABELS,
            info_message=info_message,
            block_message=block_message,
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
        block_message = runtime.preview_block_message(
            prepared.preview,
            prepared.library_dir,
        )
        if block_message:
            runtime.show_centered_message(self.root, "Cannot convert", block_message)
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

            playlist_pairs: list[tuple[str, object]] = []
            for i, plan in enumerate(plans):
                appended = (
                    batch_stats.appended_by_plan[i]
                    if i < len(batch_stats.appended_by_plan)
                    else 0
                )
                playlist_result = (
                    batch_stats.playlist_results[i]
                    if i < len(batch_stats.playlist_results)
                    else None
                )
                playlist_pairs.append((plan.wav_playlist_name, playlist_result))
                plan_stats = stats_for_playlist(
                    batch_stats,
                    plan.wav_playlist_name,
                    appended=appended,
                )
                parts = format_conversion_counts(plan_stats, missing=0)
                summaries.extend(
                    format_playlist_report(
                        plan.wav_playlist_name,
                        playlist_result,
                        count_parts=parts,
                        missing=list(plan.warnings),
                    )
                )
                if plan_stats.state_changed:
                    summaries.append("State changed (refresh preview):")
                    summaries.extend(plan_stats.state_changed)
                if plan_stats.errors:
                    summaries.append("Failed:")
                    summaries.extend(plan_stats.errors)
                if plan_stats.conflicts:
                    summaries.append("Conflicts:")
                    summaries.extend(plan_stats.conflicts)
            # Batch-level skipped (deduped across plans) only when not already
            # listed under a playlist via plan.warnings.
            if skipped:
                already = {w for plan in plans for w in plan.warnings}
                extra = [s for s in skipped if s not in already]
                if extra:
                    summaries.append("Missing skipped:")
                    summaries.extend(extra)

            if total == 0:
                self._ui(lambda: self._set_progress(0, 0))
            else:
                self._ui(lambda t=total: self._set_progress(t, t))
            out = str(output)
            title = conversion_report_title(
                batch_stats,
                cancelled=self._cancel_event.is_set(),
                missing=sum(len(plan.warnings) for plan in plans),
            )
            source_root = getattr(plans[0], "source_root", None) if plans else None
            source_paths = [t.source_path for t in items]
            self._ui(
                lambda s=summaries, o=out, folder=output.parent, t=title, p=playlist_pairs, st=batch_stats, root=source_root, paths=source_paths: self._finish_report(
                    s,
                    o,
                    folder,
                    title=t,
                    playlists=p,
                    analytics_stats=st,
                    analytics_source_root=root,
                    source_paths=paths,
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
        """Compatibility wrapper: one report dialog titled No conversions."""
        body = list(summaries)
        if warnings:
            body.append("Missing skipped:")
            body.extend(warnings)
        self._finish_report(
            body, output="", output_folder=None, title="No conversions"
        )

    def _finish_ok(
        self,
        summaries: list[str],
        output: str,
        warnings: list[str] | None = None,
        output_folder: Path | None = None,
    ) -> None:
        """Compatibility wrapper: one Done report including optional missing."""
        body = list(summaries)
        if warnings:
            body.append("Missing skipped:")
            body.extend(warnings)
        self._finish_report(body, output, output_folder, title="Done")

    def _import_xml_instructions(
        self,
        body: str,
        output: str,
        playlists: list[tuple[str, object]] | None = None,
    ) -> str:
        fmt = self.format_var.get().strip().lower()
        guidance = format_import_guidance(
            Path(output),
            output_format=fmt,
            playlists=playlists,
        )
        return f"{body}\n\n{guidance}"

    def _finish_report(
        self,
        summaries: list[str],
        output: str,
        output_folder: Path | None,
        *,
        title: str,
        playlists: list[tuple[str, object]] | None = None,
        analytics_stats: ConvertStats | None = None,
        analytics_source_root=None,
        source_paths=None,
    ) -> None:
        self._prepared_conversion = None
        self._confirm_prepared = None
        self._set_busy(False)
        if title == "Done" and analytics_stats is not None:
            runtime.report_conversion(
                surface="gui",
                source_root=analytics_source_root,
                output_format=coerce_output_format(self.format_var.get()),
                bit_depth=coerce_bit_depth(self.bit_depth_var.get()),
                sample_rate=coerce_sample_rate(self.sample_rate_var.get()),
                stats=analytics_stats,
                source_paths=source_paths if source_paths is not None else (),
            )
        snap_progress = 0 if title in {"No conversions", "Failed"} else 100
        self._animate_progress_to(snap_progress, snap=True)
        body = "\n".join(summaries)
        if title == "No conversions":
            self.status_var.set("Finished with no audio files converted or copied.")
            self._show_done_dialog(body, output_folder, title=title)
            return
        if title == "Failed":
            self.status_var.set("Failed.")
            self._show_done_dialog(body, output_folder, title=title)
            return
        if title == "Cancelled":
            self.status_var.set("Cancelled.")
            self._cancel_cancelled_clear()
            self._cancelled_clear_id = self.root.after(
                constants.CANCELLED_STATUS_CLEAR_MS, self._clear_cancelled_status
            )
            message = (
                self._import_xml_instructions(body, output, playlists)
                if output
                else body
            )
            self._show_done_dialog(message, output_folder, title=title)
            return
        if title == "Partial":
            if output:
                self.status_var.set(
                    f"Partial. Point Rekordbox Imported Library at:\n{output}"
                )
            else:
                self.status_var.set("Partial.")
        elif output:
            self.status_var.set(
                f"Done. Point Rekordbox Imported Library at:\n{output}"
            )
        else:
            self.status_var.set(f"{title}.")
        message = (
            self._import_xml_instructions(body, output, playlists)
            if output
            else body
        )
        self._show_done_dialog(message, output_folder, title=title)

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
        output_folder: Path | None = None,
        *,
        title: str = "Done",
    ) -> None:
        gui_dialogs.show_done_dialog(
            self.root,
            message,
            output_folder=output_folder,
            reveal=runtime.open_in_finder,
            on_open_guide=self._show_usage_guide,
            place_over=self._place_dialog_over_app,
            title=title,
        )


