# Rekordbox XML import checklist (manual)

Blank evidence for app v2.0.0. Fill in after testing on a real machine — do
not invent version numbers, pass/fail, or untested hardware claims. Empty
cells mean **untested**, not supported.

Use the same source collection as the automated fidelity fixture where
practical: ratings, BPM, key, comments, colour, memory cue, hot cue, loop,
constant and variable-tempo grids, a nested source folder, a track shared
across playlists, and a repeated Key in one source playlist (Import XML
stores the track once in COLLECTION and repeats the Key in playlist order).
Generated `[WAV]`/`[AIFF]` playlists stay flat (source folder hierarchy is
not reproduced).

Target: **Rekordbox 6.x** and **Rekordbox 7.x** on each OS below.

Converter commit / build: _______________
Output format + quality ceiling (WAV|AIFF, bit depth, rate): _______________

Mark each cell: **pass** / **fail** / **limitation** plus a short note.

---

## macOS

| Item | Rekordbox 6.x | Rekordbox 7.x |
| --- | --- | --- |
| Rekordbox version (exact) | | |
| OS version | | |
| Enable **rekordbox xml** pane (Preferences → View → Layout) | | |
| Set **Imported Library** to `<output>/rekordbox-import.xml` | | |
| Refresh / open **rekordbox xml** tree | | |
| `[WAV]` / `[AIFF]` playlist visible under Playlists | | |
| Import Playlist / drag into main Playlists | | |
| Rating, BPM, key, comments, colour | | |
| Memory cue, hot cue, loop | | |
| Constant grid (one TEMPO) | | |
| Variable-tempo grid (multiple TEMPO) | | |
| Playlist order | | |
| Repeated source Key (same Key more than once, order preserved) | | |
| Track shared across playlists (one collection row) | | |
| Track Location plays from converter output path | | |
| Notes | | |

---

## Windows

| Item | Rekordbox 6.x | Rekordbox 7.x |
| --- | --- | --- |
| Rekordbox version (exact) | | |
| OS version | | |
| Enable **rekordbox xml** pane (Preferences → View → Layout) | | |
| Set **Imported Library** to `<output>/rekordbox-import.xml` | | |
| Refresh / open **rekordbox xml** tree | | |
| `[WAV]` / `[AIFF]` playlist visible under Playlists | | |
| Import Playlist / drag into main Playlists | | |
| Rating, BPM, key, comments, colour | | |
| Memory cue, hot cue, loop | | |
| Constant grid (one TEMPO) | | |
| Variable-tempo grid (multiple TEMPO) | | |
| Playlist order | | |
| Repeated source Key (same Key more than once, order preserved) | | |
| Track shared across playlists (one collection row) | | |
| Track Location plays from converter output path | | |
| Notes | | |

---

## Pass / fail summary

| Platform | Rekordbox 6.x | Rekordbox 7.x |
| --- | --- | --- |
| macOS | | |
| Windows | | |

Tester: _______________  Date: _______________

A preparation-loss or wrong-playback failure on a tested version **blocks**
publishing v2.0.0.

---

## Packaged macOS app smoke (release candidate)

Build with `./scripts/build-macos-app.sh`. Do not commit `dist/`. Record
artifact identity here.

| Check | Result |
| --- | --- |
| Converter commit | |
| `Simple Rekordbox Converter.app` path | |
| Version in About / window title (`2.0.0`) | |
| `lipo -archs` shows `x86_64` and `arm64` | |
| Bundled `ffmpeg` and `ffprobe` run from the app | |
| First launch (Gatekeeper right-click Open) | |
| Fresh preferences (no saved XML/folder) | |
| Documents-access allowed vs declined fallback | |
| XML select, playlist browse, conversion preview | |
| Convert, Cancel, rerun (reuse / conflict if set up) | |
| Import XML edit, Reveal output folder | |
| Help → Check for Updates… | |
| Output `WAV/` or `AIFF/`, `.rekordbox-converter-manifest.json` version 2, `rekordbox-import.xml` | |
| Notes | |

Smoke failures that affect conversion, recovery, data safety, or startup
**block** publication.
