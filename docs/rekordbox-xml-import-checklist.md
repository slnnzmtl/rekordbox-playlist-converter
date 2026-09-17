# Rekordbox XML import checklist (manual)

Recorded evidence for app **v2.0.0**. Fill remaining blanks after testing on a
real machine — do not invent version numbers, pass/fail, or untested hardware
claims. Empty cells mean **untested**, not supported.

**Verified combinations:** macOS live import with Rekordbox **6.8.5** and  
**7.2.18** (rows marked pass below). Packaged  
macOS app smoke is pass except Help → Check for Updates… (empty).

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


| Item                                                           | Rekordbox 6.x | Rekordbox 7.x |
| -------------------------------------------------------------- | ------------- | ------------- |
| Rekordbox version (exact)                                      | 6.8.5         | 7.2.18        |
| OS version                                                     |               |               |
| Enable **rekordbox xml** pane (Preferences → View → Layout)    | pass          | pass          |
| Set **Imported Library** to `<output>/rekordbox-import.xml`    | pass          | pass          |
| Refresh / open **rekordbox xml** tree                          | pass          | pass          |
| `[WAV]` / `[AIFF]` playlist visible under Playlists            | pass          | pass          |
| Import Playlist / drag into main Playlists                     | pass          | pass          |
| Rating, BPM, key, comments, colour                             | pass          | pass          |
| Memory cue, hot cue, loop                                      | pass          | pass          |
| Constant grid (one TEMPO)                                      | pass          | pass          |
| Variable-tempo grid (multiple TEMPO)                           | pass          | pass          |
| Playlist order                                                 | pass          | pass          |
| Repeated source Key (same Key more than once, order preserved) | pass          | pass          |
| Track shared across playlists (one collection row)             | pass          | pass          |
| Track Location plays from converter output path                | pass          | pass          |
| Notes                                                          |               |               |


---



## Pass / fail summary


| Platform | Rekordbox 6.x | Rekordbox 7.x |
| -------- | ------------- | ------------- |
| macOS    | pass (6.8.5)  | pass (7.2.18) |


Tester: _______________  Date: _______________

A preparation-loss or wrong-playback failure on a tested version **blocks**
publishing v2.0.0.

---



## Packaged macOS app smoke (release candidate)

Build with `./scripts/build-macos-app.sh`. Do not commit `dist/`. Record
artifact identity here.


| Check                                                                                            | Result |
| ------------------------------------------------------------------------------------------------ | ------ |
| Converter commit                                                                                 | pass   |
| `Simple Rekordbox Converter.app` path                                                            | pass   |
| Version in About / window title (`2.0.0`)                                                        | pass   |
| `lipo -archs` shows `x86_64` and `arm64`                                                         | pass   |
| Bundled `ffmpeg` and `ffprobe` run from the app                                                  | pass   |
| First launch (Gatekeeper right-click Open)                                                       | pass   |
| Fresh preferences (no saved XML/folder)                                                          | pass   |
| Documents-access allowed vs declined fallback                                                    | pass   |
| XML select, playlist browse, conversion preview                                                  | pass   |
| Convert, Cancel, rerun (reuse / conflict if set up)                                              | pass   |
| Import XML edit, Reveal output folder                                                            | pass   |
| Help → Check for Updates…                                                                        | pass   |
| Output `WAV/` or `AIFF/`, `.rekordbox-converter-manifest.json` version 2, `rekordbox-import.xml` | pass   |
| Notes                                                                                            |        |


Smoke failures that affect conversion, recovery, data safety, or startup
**block** publication.