# Usage guide

Convert a Rekordbox playlist to WAV files, then bring the new tracks and playlist back into Rekordbox. The original lossless files are never modified.

## Prerequisites

- **Python 3.10+**
- **ffmpeg** and **ffprobe** on your `PATH`

On macOS, install both via Homebrew (one package):

```bash
brew install ffmpeg
```

Works with Rekordbox **6** and **7**. Menu names below match Rekordbox 7; Rekordbox 6 is the same idea (File → Export Collection, plus the **rekordbox xml** pane).

---

## 1. Export your library from Rekordbox

The tool does not read Rekordbox’s internal database. It needs an XML export.

1. Open Rekordbox and wait until collection analysis has finished for the tracks you care about (cues and beatgrids are copied from this export).
2. **File → Export Collection in xml format**.
3. Save the file somewhere local, for example `~/Documents/rekordbox/rekordbox.xml`.

Avoid cloud-synced folders if you can; a large export is slower there and easier to corrupt.

To include beatgrid data in the XML (Rekordbox 7): **Preferences → Advanced → rekordbox xml** and enable **Export BeatGrid information**.

---

## 2. Convert a playlist

### Interactive (easiest)

```bash
./rb-converter.py
```

The script offers XML files from common export locations, lists playlists (with folder path and track count), lets you pick one or more (`1`, `1,4,7`, or `all`), confirms `./output` and `./output/rekordbox-wav-import.xml`, converts, then prints the Rekordbox import steps.

### Flags

```bash
./rb-converter.py \
  --xml ~/Documents/rekordbox/rekordbox.xml \
  --playlist "Dark forest duplicate"
```

That writes:

- WAV files → `./output/Dark forest duplicate/`
- Import XML → `./output/rekordbox-wav-import.xml`
- New playlist name in the XML → `Dark forest duplicate [WAV]`

`--playlist` must match the name in Rekordbox **exactly** (including spaces). With the wizard you can convert several playlists in one run; they all extend the same `--output` file.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--xml` | prompted | Rekordbox XML export |
| `--playlist` | prompted | Exact playlist name |
| `--wav-dir` | `./output` | Root folder for WAVs (`--wav-dir/<playlist>/`) |
| `--output` | `./output/rekordbox-wav-import.xml` | Import XML |
| `--force` | off | Reconvert even if a valid WAV already exists |
| `--dry-run` | off | Check paths and collisions; write nothing |

FLAC, ALAC, and AIFF become PCM WAV (same sample rate and bit depth). Existing WAVs are copied. If two tracks would produce the same filename, the run stops before writing anything.

Re-running with the same `--output` **extends** the file: new tracks and playlist entries are appended; existing ones stay. Convert several playlists into one import XML by repeating the command with different `--playlist` values.

---

## 3. Import the XML back into Rekordbox

Rekordbox does **not** use **File → Import**. XML is a side library you point at, then copy into your collection.

### Show the XML pane (once)

1. **Preferences → View → Layout**.
2. Under **Media Browser**, check **rekordbox xml**.

### Point Rekordbox at the generated file

1. **Preferences → Advanced → Database**.
2. In **rekordbox xml**, set **Imported Library** to `./output/rekordbox-wav-import.xml` (the file this tool wrote, not your original export).
3. Close Preferences. In the browser tree you should see **rekordbox xml**.

If the pane was already pointing at another XML, change **Imported Library** to this file. Click the refresh control on the rekordbox xml library if tracks do not appear yet.

### Copy into your collection

1. Open **rekordbox xml** in the tree, then **Playlists**.
2. Find `{your playlist} [WAV]`.
3. Either:
   - Drag that playlist onto **Playlists** in your main library, or
   - Right-click the playlist → **Import Playlist**.
4. To pull tracks only: open **rekordbox xml → All Tracks**, select the WAV rows, and drag them onto **Collection** (or right-click → **Import to Collection**).

If Rekordbox asks whether to load information from the library being imported, choose **Yes** so cues, beatgrid, BPM, and key come across.

Confirm that each track’s path points at the WAV under `--wav-dir`, then play one track to make sure the file is on a disk Rekordbox can read (internal disk or a mounted volume).

---

## 4. After import

- Analyze only if Rekordbox still wants waveforms; cues and grid should already be there.
- Keep the WAVs where they were written. Moving them later breaks `Location` in the XML until you convert again.
- Original FLACs (or other lossless files) stay in place; this tool never writes over them.

If you add tracks to the source playlist later: export XML from Rekordbox again, re-run this script with the same `--output` and `--wav-dir`, then refresh **Imported Library** and import the new rows.
