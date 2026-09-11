# Changelog

## Unreleased

Intended for **1.3.0** (app version remains 1.2.0 until release).

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
- macOS universal2 app (**Rekordbox Playlist Converter**) with bundled static ffmpeg/ffprobe (ad-hoc signed).
- Supports Rekordbox 6 and 7 XML exports (FLAC, ALAC, AIFF; WAV copied as-is).
- Licensed under GPL-3.0-or-later.
