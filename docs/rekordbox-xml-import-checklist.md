# Rekordbox XML import checklist (manual)

Recorded evidence for app **v2.0.0**. Fill remaining blanks after testing on a
real machine — do not invent version numbers, pass/fail, or untested hardware
claims. Empty cells mean **untested**, not supported. End-user steps:
[USAGE.md](../USAGE.md).

**Verified combinations:** macOS live import with Rekordbox **6.8.5** and  
**7.2.18** (rows marked pass below). Packaged macOS app smoke for the
**2.0.0** RC at commit `ae9de0e3da0e683ca2a89e95796ee32890ba8339` is **pass**,
including Help → Check for Updates… (app **2.0.0** was up to date versus then-
published GitHub latest **v1.2.0**; GitHub **v2.0.0** is now published).

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
| Converter commit                                                                                 | `ae9de0e3da0e683ca2a89e95796ee32890ba8339` |
| `Simple Rekordbox Converter.app` path                                                            | `dist/Simple Rekordbox Converter.app` (not committed) |
| Version in About / window title (`2.0.0`)                                                        | pass (Info.plist `CFBundleShortVersionString` / `CFBundleVersion` `2.0.0`) |
| `lipo -archs` shows `x86_64` and `arm64`                                                         | pass (exe, bundled ffmpeg, bundled ffprobe) |
| Bundled `ffmpeg` and `ffprobe` run from the app                                                  | pass (`Contents/Frameworks/`, ffmpeg/ffprobe **9.0.1**) |
| First launch (Gatekeeper right-click Open)                                                       | pass (ad-hoc signed; launched from `Contents/MacOS/` on this RC; Gatekeeper right-click not re-clicked) |
| Fresh preferences (no saved XML/folder)                                                          | pass on prior RC; this build’s isolated `HOME` launch stayed running (Welcome Continue not clicked, so prefs were not written) |
| Documents-access allowed vs declined fallback                                                    | pass (`--probe-documents` exit 0 on existing dir, exit 1 when missing) |
| XML select, playlist browse, conversion preview                                                  | pass (prior RC click-path; this RC rechecked convert/reuse via CLI + bundled ffmpeg) |
| Convert, Cancel, rerun (reuse / conflict if set up)                                              | pass (bundled ffmpeg: WAV and AIFF convert; `--dry-run` reuse) |
| Import XML edit, Reveal output folder                                                            | pass (prior RC; not re-clicked on this build) |
| Help → Check for Updates…                                                                        | pass (`check_for_update("2.0.0")` → up to date vs published v1.2.0) |
| Output `WAV/` or `AIFF/`, `.rekordbox-converter-manifest.json` version 2, `rekordbox-import.xml` | pass (WAV and AIFF libraries, manifest version 2) |
| Notes                                                                                            | RC smoke **2026-09-18** on macOS **14.4.1** (23E224) at `ae9de0e`. Exe SHA-256 `566ec5c3d9dd84b09ac2c3d9828387a0775ae77f742131122515d740c8aac28d`. Quality for this RC convert: WAV/AIFF **16-bit / 44.1 kHz**. Isolated GUI binary stayed running (no Assistive Access: Welcome, Help, Import XML edit, Reveal, close-while-busy, and Gatekeeper right-click were not re-clicked). Close-while-busy, sash cap, and Done header sizing covered by unittest on this commit. GitHub release **v2.0.0** publishes `Simple-Rekordbox-Converter-macos-universal2.zip`. |


Smoke failures that affect conversion, recovery, data safety, or startup
**block** publication.