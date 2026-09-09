# Changelog

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
- macOS universal2 app (**Rekordbox WAV Converter**) with bundled static ffmpeg/ffprobe (ad-hoc signed).
- Supports Rekordbox 6 and 7 XML exports (FLAC, ALAC, AIFF; WAV copied as-is).
- Licensed under GPL-3.0-or-later.
