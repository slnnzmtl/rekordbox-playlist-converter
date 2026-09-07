# Changelog

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
