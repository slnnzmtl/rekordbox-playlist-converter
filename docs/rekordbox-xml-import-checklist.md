# Rekordbox XML import checklist (manual)

Blank evidence for app v2.0.0. Fill in after testing on a real machine — do
not invent version numbers, pass/fail, or untested hardware claims. Empty
cells mean **untested**, not supported.

Use the same source collection as the automated fidelity fixture where
practical: ratings, BPM, key, comments, colour, memory cue, hot cue, loop,
constant and variable-tempo grids, a nested source folder, a track shared
across playlists, and a repeated Key in one source playlist (Import XML
still stores that track once per playlist).

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
| Repeated source Key (one playlist Key after dedup) | | |
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
| Repeated source Key (one playlist Key after dedup) | | |
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
