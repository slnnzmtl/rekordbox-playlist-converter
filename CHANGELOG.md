# Changelog

## 2.0.0

- Unique-track conversion runs up to **4 encodes in parallel** (capped at 5). Progress counts completed tracks; Cancel stops in-flight encodes (completed files kept; interrupted playlist is not written to Import XML).
- Track failures no longer abort the rest of the run: convertible tracks finish, Import XML is written for successes, remaining playlists continue, then all errors are reported together. Cancel after a failure still surfaces those encode errors.
- Filename collisions (same dest name from different sources) no longer abort: the first playlist entry is converted; later duplicates are skipped with a warning.
- Conversion failure and cancel-with-errors dialogs use the same scrollable list view as skipped missing tracks (not a flat alert).
- Prepare (ffprobe) runs in parallel with the same worker cap; File → Search for Rekordbox XML runs off the UI thread with a timeout; tracklist search is debounced and collection indexes are cached for the loaded XML.
- ffmpeg encodes use `-nostats -loglevel error` and drain pipes on cancel/timeout so long parallel encodes cannot wedge on a full stderr pipe.
- Skip the post-ffmpeg WAV rewrite when output is already CDJ-safe (e.g. 16-bit PCM); skip the redundant AIFF re-normalize after a successful ffmpeg stage.
- While tracklist bit depth headers are read, `Scanning bit depth…` appears beside the idle unique-tracks status line.
- Tracklist lists only convertible lossless formats (by file extension); missing collection rows stay visible. Column headers are left-aligned and clickable to sort by Track, Format, Bit depth, or Sample rate within each playlist group. Playlist / tracklist panes default to a 30% / 70% split.
- App display name is **Simple Rekordbox Converter** (window title, macOS .app bundle, docs).
- Tracklist bit depth is remembered for the session so reselecting a playlist does not re-read file headers; Refresh or a new XML path clears the cache.
- Default output folder is `rekordbox-converter` (`~/Documents/rekordbox-converter` when Documents access is allowed).
- Success and other app dialogs open centered over the main window.
- Playlist search and track search sit over each pane (follow the splitter). Track search filters the currently listed preview by artist/title/filename; Convert still uses the selected visible tracks.
- Convert uses the tracklist preview selection: listed tracks start selected; hold ⌃ to refine a subset across playlists. Import XML and audio output include only those tracks.
- Convert sits beside the progress bar; Cancel replaces it while a run is in progress. Cancel stops in-flight encodes (up to 4; completed files kept; interrupted playlist is not written to Import XML). “Cancelled.” clears after 3 seconds and resets the progress bar.
- GUI shows a tracklist table beside the playlist tree (Track, Format, Bit depth, Sample rate). Format and rate come from the Rekordbox XML; bit depth is read from FLAC, ALAC-in-M4A, WAV, or AIFF headers when the file is present, otherwise —. The unique-track selection summary appears on the bottom status line when idle.
- Main window opens centered on the display under the pointer instead of straddling dual-monitor layouts.
- Missing skipped tracks open in a scrollable list dialog instead of a flat warning alert.
- App version is shown in the window title; the in-window title label is removed.
- Convert aligns with the Browse column; the redundant How to use button is removed (Help menu unchanged).
- GUI playlist picker is a Rekordbox-style folder tree (expand/collapse); selecting a folder converts all playlists under it. Search still filters the tree.
- Selectable output quality: `--format wav|aiff`, `--bit-depth 16|24`, `--sample-rate 44100|48000` (GUI Sampling format dropdowns + prefs). Defaults are WAV / **24** / **48000**.
- Quality is a **ceiling**: never upconvert 16-bit to 24-bit or 44.1 kHz to 48 kHz; preserve source when it fits; reduce only when over the cap.
- WAV stays stripped `WAVE_FORMAT_PCM` with `fmt `+`data` only (including 24-bit); AIFF stays `FORM`/`AIFF` with ID3v2.3 from the XML and optional cover.
- Skip dest only when it matches the **effective** quality and canonical container (AIFF also ID3/art), so raising 16/44.1 → 24/48 rebuilds previously reduced files when the source is higher quality.
- Opt-in **AIFF** output (`--format aiff`, wizard prompt, GUI radios) with ID3v2.3 text/cover; import XML Kind/suffix follow format.
- Import XML collection tracks are **refreshed** on rerun (same Location) while preserving TrackID / playlist keys.
- GUI/docs say “audio files” where the format is selectable; `--wav-dir` and the app bundle name are unchanged.
- Internal refactor: split conversion into `cdj_wav`, `cdj_aiff`, `rekordbox_xml`, and `cli_error` modules behind the existing `rb_playlist_to_wav` facade (no user-facing change).

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
