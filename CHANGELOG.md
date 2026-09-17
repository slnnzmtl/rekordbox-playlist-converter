# Changelog

## Unreleased

## 2.0.0

First public release of the v2 workflow and **manifest v2**. GitHub release
artifacts are not published yet; the latest **published** download is still
v1.2.0. Rollback: keep the previous `.app` and output library; this release
refuses unreleased v1 manifests (back up or recreate the **whole** output
folder, or choose a new empty folder).

Live import is **verified on macOS** with Rekordbox **6.8.5** and **7.2.18**. Packaged macOS app smoke for the **2.0.0** RC
(`cdd5f710627108d014023d6d2ef69d1878012991`) is recorded as **pass**, including Help → Check for Updates… (2.0.0 is up to
date versus published GitHub latest v1.2.0). Evidence:
[docs/rekordbox-xml-import-checklist.md](docs/rekordbox-xml-import-checklist.md)
(empty cells = untested).

- Closing the window or using macOS Quit during a conversion asks to cancel,
  then quits after cancel finishes without a Done or error dialog; declining
  leaves the job running.
- The playlist/tracklist split stays at one-third playlist width, caps the
  playlist column, and restores that ratio when the window shrinks (it no
  longer collapses after a wide-window cap).
- The Done / Partial track table sizes the Track column to the longest
  playlist status header so the header is not clipped.
- Confirmation dialogs show **Yes** then **No** (Return still confirms; Escape
  and the window close control still decline).
- Convert no longer fails with **Output destination conflict since preview**
  when the output library already has a track under a different Unicode
  spelling of the same filename (NFC vs NFD, e.g. accented titles on external
  SSDs) than the planned destination—common after quitting mid-conversion;
  re-preparing alone could not clear it. Complements the 1.1.0 fix for NFC/NFD
  **source** path collisions; this is the **output** execute-inventory check.
- Output-folder validation no longer treats unrelated nested WAV/AIFF under a
  parent folder (for example Documents) as a leftover converter library; only
  root `rekordbox-import.xml` or files directly in `WAV/` / `AIFF/` count.
- First-launch welcome modal with a short how-to guide and opt-in checkbox for
  anonymous usage analytics (checked by default; creates preferences on
  dismiss). Documents/XML discovery waits until Welcome is closed (and until
  the full usage guide is closed if opened from Welcome) so the collection
  chooser cannot open on top of it.
- GUI and CLI share the same Done / Partial / Failed / Cancelled batch title;
  finish-table Failed Detail uses `source → dest: error` like the CLI Failed
  list; playlist accordion headers include missing-skipped counts.
- Done / Partial finish reports show a resizable track-status table grouped by
  playlist (status line as tree header, collapsed by default; wide Track column),
  with import steps below; reports without per-track results still use a
  scrollable line list.
- Conversion preview lists missing source files in the table (Missing /
  Source file is missing), disables Convert when there is nothing to convert,
  and shows about how much disk space audio writes need when space is OK.
- Opt-in anonymous usage analytics: Welcome checkbox (checked by default) or
  Help checkbutton or `--analytics on|off`; one `install` event on first
  opt-in, `conversion_completed` after successful writes (includes aggregate
  `input_file_types` source-extension counts), and `conversion_failed` after
  a failed job with a closed reason (`xml_parse` / `encode` / `config` /
  `unknown`); failed POSTs are queued in `analytics_queue.json` beside
  preferences and retried until success (held while opted out; HTTP 4xx drops
  that event so the queue can advance); privacy docs in README, USAGE, SECURITY.
- Preview Action for a first conversion (dest reserved, never written) is
  **Convert** / Not converted yet; **Recreate missing** remains when a prior
  complete or incomplete assignment’s file is gone. The Done report counts
  those first writes as converted/copied, not recreated.
- Conversion preview omits Destination and Write columns (Action + Reason
  remain; Format directory still shown in CLI dry-run).
- Save Import XML confirmation lists Unknown (no-playlist) tracks moved to Trash.
- Manifest V2 planning and checkpoints scale better for large output libraries:
  live dest-owner indexing, one disk inventory scan per batch, time-based
  ordered checkpoint persistence, and single-load library open with content
  fingerprints. Execute re-checks the fingerprint and dest inventory before
  the prebatch save; persist failures surface as `CliError`; periodic
  checkpoints are requested atomically; Import XML edit treats repeated Keys
  as a multiset. Public JSON schema and durability rules are unchanged.
- Preview shows input, format, action, classifier reason, quality, and size
  (GUI omits Destination and Write; CLI `--dry-run` also prints the Format
  directory). Convert is blocked on unresolved destination conflicts,
  insufficient disk space, or when there is nothing to convert. GUI and CLI
  `--dry-run` share the same labels.
- On a complete success rerun, generated `[WAV]`/`[AIFF]` playlist Keys are
  rewritten to the current source order (reorder, remove, insert, repeats).
  Missing, failed, and conflicted tracks are omitted from the generated
  playlist; the NODE is still created or refreshed from successful dests.
  When no track succeeded, the playlist NODE is left unchanged (or not
  created). Partial runs still title the report Partial. Mixed batches name
  which playlists were written and are safe to import.
- The conversion report counts converted, copied, PCM-rebuilt,
  metadata-refreshed, reused, recreated, missing, conflicting, and failed
  outputs; mixed batches keep successes visible.
- Automated Import XML round-trips cover ratings, BPM, key, comments, colour,
  supported TRACK attributes, unknown fields and children, memory/hot/loop
  marks, constant and variable-tempo grids, playlist order and repeated Keys,
  flat `[WAV]`/`[AIFF]` nodes, and metadata refresh of an existing collection
  entry (TrackID / Keys preserved) for WAV and AIFF from Rekordbox 6- and
  7-style PRODUCT versions.
- Failed encode lines include source and destination (`src → WAV/out.wav: …`).
- The GUI may contact GitHub Releases to check for updates (not gated by
  analytics). Optional analytics is documented above and in README / SECURITY.
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
