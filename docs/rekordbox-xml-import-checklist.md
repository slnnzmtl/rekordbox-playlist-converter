# Rekordbox XML import checklist (manual)

Recorded evidence for app **v2.0.0**. Fill remaining blanks after testing on a
real machine — do not invent version numbers, pass/fail, or untested hardware
claims. Empty cells mean **untested**, not supported.

**Verified combinations:** macOS live import with Rekordbox **6.8.5** and  
**7.2.18** (rows marked pass below). Packaged macOS app smoke for the
**2.0.0** RC at commit `cdd5f710627108d014023d6d2ef69d1878012991` is **pass**,
including Help → Check for Updates… (app **2.0.0** is up to date versus the
published GitHub latest **v1.2.0**).

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
| Converter commit                                                                                 | `cdd5f710627108d014023d6d2ef69d1878012991` |
| `Simple Rekordbox Converter.app` path                                                            | `dist/Simple Rekordbox Converter.app` (not committed) |
| Version in About / window title (`2.0.0`)                                                        | pass (window title `Simple Rekordbox Converter 2.0.0`; Info.plist `2.0.0`) |
| `lipo -archs` shows `x86_64` and `arm64`                                                         | pass (exe, bundled ffmpeg, bundled ffprobe) |
| Bundled `ffmpeg` and `ffprobe` run from the app                                                  | pass (`Contents/Frameworks/`, ffmpeg/ffprobe **9.0.1**) |
| First launch (Gatekeeper right-click Open)                                                       | pass (ad-hoc signed; launched from `Contents/MacOS/` on this RC) |
| Fresh preferences (no saved XML/folder)                                                          | pass (isolated `HOME`; Welcome created prefs with no saved XML/folder) |
| Documents-access allowed vs declined fallback                                                    | pass (`--probe-documents` exit 0 on existing Documents, exit 1 when missing) |
| XML select, playlist browse, conversion preview                                                  | pass |
| Convert, Cancel, rerun (reuse / conflict if set up)                                              | pass (bundled ffmpeg: convert; `--dry-run` reuse then dest-conflict) |
| Import XML edit, Reveal output folder                                                            | pass |
| Help → Check for Updates…                                                                        | pass (`check_for_update("2.0.0")` → up to date vs published v1.2.0) |
| Output `WAV/` or `AIFF/`, `.rekordbox-converter-manifest.json` version 2, `rekordbox-import.xml` | pass (WAV and AIFF libraries, manifest version 2) |
| Notes                                                                                            | RC smoke **2026-09-17** on macOS **14.4.1** (23E224). Exe SHA-256 `2230df24c53bb8a1608549081508d2d68452b2d8d0b1d17a598cf9104c78dadd`. Quality for this RC convert: WAV/AIFF **16-bit / 44.1 kHz**. Isolated GUI launched (title 2.0.0, Welcome → prefs). Convert/preview/reuse/conflict and WAV/AIFF+manifest v2 rechecked with bundled ffmpeg. Help menu, Import XML edit, Reveal, and Finder Gatekeeper right-click were not re-clicked (no Assistive Access). GitHub **.app** not published yet. |


Smoke failures that affect conversion, recovery, data safety, or startup
**block** publication.