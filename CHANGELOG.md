# Changelog

## Unreleased

- Manifest **v2** is the first released converter-library contract. Unreleased v1
  and unknown future versions are refused; leftover managed audio still causes
  folder validation to reject the library, so recreate the whole development
  output library or choose a new empty folder (deleting only
  `.rekordbox-converter-manifest.json` is not enough). Reruns classify explicit
  actions from source, metadata, output, and recipe signatures: reuse, refresh
  XML, update AIFF metadata, rewrite container, transcode, recreate missing,
  in-place skip, or conflict. Preview and execute share a frozen preflight
  Decision plus filesystem snapshots; execute does not reclassify before write.
  Mutating writes save incomplete first, then persist complete signatures after
  a successful replace. WAV/AIFF metadata freshness is committed only after
  Import XML succeeds. Mid-batch checkpoints and the final save keep unfinished
  mutations incomplete on disk so a crash cannot look like an external edit.
  Conflicts are not overwritten and keep existing Import XML / playlist keys.

- GUI **Unknown** playlist lists collection tracks that are not in any playlist.
- GUI tracklist shows artist and title without a file-extension suffix;
  track search still matches format and filename.
- GUI tracklist hover shows each track’s file path.
- GUI **Edit** mode for a generated Import XML: multi-select tracks, remove
  playlists or tracks from the draft, Save confirms pending actions then updates
  XML/manifest and moves owned audio to Trash, Cancel discards. Source Rekordbox
  XML stays read-only.
- App display name is **Simple Rekordbox Converter**. Selectable WAV/AIFF output with
  quality ceiling (defaults WAV / **24** / **48000**); never upconvert. Format-flat
  layout under `WAV|AIFF/<artist> - <track>`; sticky manifest keeps per-source
  destinations stable; deleting the manifest refuses the folder. Default output
  folder is `rekordbox-converter` (Documents when allowed). Import XML collection
  tracks refresh on rerun (same Location) while preserving TrackID / playlist keys.
- Batch convert encodes each unique source once; Import XML is
  `<wav-dir>/rekordbox-import.xml` (CLI `--output` override). GUI preview before
  write; CLI `--dry-run`. Success offers Reveal output folder. Track failures
  and filename collisions skip with warnings; cancel after encode still writes
  Import XML for successes.
- Unique-track and prepare (ffprobe) run up to **4** workers in parallel. Cancel
  stops in-flight encodes. GUI tracklist (Track / Format / Bit depth / Sample rate /
  Rating) drives selection; folder tree + dual search; off-thread output-folder
  validation; scrollable error/missing dialogs; centered window and dialogs.
- Preferences: `library_dir` (legacy `wav_dir` read-only); save failures show on the
  status line without aborting convert. Prepare rejects invalid format / bit depth /
  sample rate instead of silently coercing.
- Internal: `convert/` package (`plan` / `preview` / `prepare` / `write` /
  `format_policy` / `quality` / `paths` / `encode`) with shared
  `prepare_batch` + `execute_prepared`; `rb_playlist_to_wav` is CLI-only; tests
  import owning modules. GUI in `gui/` mixins behind `gui.runtime`; prepare thread
  uses `_PrepareHandoff` (no ephemeral `_convert_*` on the app). Workers /
  CDJ helpers / cancel pools cleaned up; `gui_prefs/` and typed playlist refs.

## 1.2.0

- Output WAVs are always **CDJ-safe**: 16-bit / 44.1 kHz / stereo `WAVE_FORMAT_PCM` with only `fmt ` and `data` chunks (no `WAVE_FORMAT_EXTENSIBLE`, no LIST/INFO metadata).
- Stop copying source WAVs as-is unless they already match that profile; re-encode extensible, non-44.1 kHz, non-stereo, or non-16-bit WAVs.
- Skip existing dest files only when they are already CDJ-safe (not merely any PCM WAV).
- Refuse in-place conversion when a playlist source path is the same as the dest and the file is not CDJ-safe (originals stay untouched).
- GUI remembers the last Rekordbox XML and limits auto-search to home plus Documents (with the existing Documents-access fallback).

## 1.1.1

- App icon from the new rpc-logo-white mark, inset to Apple's 824/1024 Dock grid and clipped to a macOS squircle (`.app` via `iconutil` or handmade ICNS, plus a 256px Tk window icon).
- GUI remembers the last WAV folder and Import XML path between launches.

## 1.1.0

- Help → Check for Updates (GitHub Releases) with an in-app notification modal.
- App version shown under the application title.
- Fix NFC/NFD source path collision detection on macOS.
- Share a single output root when converting multiple playlists in one run.
- Clearer GUI finish path when a run performs zero conversions.

## 1.0.0

First public release.

- CLI (`rb-converter.py`) converts Rekordbox playlist lossless tracks to WAV and writes an import XML with cues, beatgrid, rating, BPM, and tags preserved.
- macOS universal2 app (**Simple Rekordbox Converter**) with bundled static ffmpeg/ffprobe (ad-hoc signed).
- Supports Rekordbox 6 and 7 XML exports (FLAC, ALAC, AIFF; WAV copied as-is).
- Licensed under GPL-3.0-or-later.
