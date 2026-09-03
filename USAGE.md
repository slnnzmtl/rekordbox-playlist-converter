# How to convert a playlist (Rekordbox 6 and 7)

Goal: new WAV copies of a playlist, with cues and beatgrid, **without touching your FLACs / ALACs / AIFFs**.

Menu names below match **Rekordbox 7**. Rekordbox 6 is the same idea: export the collection, then use the **rekordbox xml** pane — never **File → Import**.

---

## First time on this Mac

You need Python 3.10+ and `ffmpeg` (that package also installs `ffprobe`).

1. Install Homebrew if needed: [https://brew.sh](https://brew.sh)
2. In Terminal:

```bash
brew install ffmpeg
```

If `python3` is missing:

```bash
brew install python@3.12
```

---

## 1. Export your collection from Rekordbox

This tool does not open Rekordbox’s internal database. It only reads an XML export.

1. Open Rekordbox and wait until analysis has finished on the tracks you care about (cues and grids come from this export).
2. **Rekordbox 7 — beatgrid in the XML:** **Preferences → Advanced → rekordbox xml** → enable **Export BeatGrid information**.
3. **File → Export Collection in xml format**.
4. Save locally, for example `Documents/rekordbox/rekordbox.xml`. Avoid iCloud / Dropbox for a large export if you can — it is slower and easier to corrupt.

---

## 2. Convert

In Terminal, go to this project folder, then:

```bash
./rb-converter.py
```

You will be asked to:

1. **Choose the XML** — common export paths are listed; type a number or a full path.
2. **Choose playlists** — numbered list with folder and track count. Type `1`, `1,4,7`, or `all`.
3. **Confirm folders** — defaults are `./output` for WAVs and `./output/rekordbox-wav-import.xml` for the import file.

Then it converts (or copies existing WAVs) and prints how to import.

**What you get**

- WAVs in `output/<playlist name>/`
- Import file `output/rekordbox-wav-import.xml`
- Playlist inside that file named `{your playlist} [WAV]`

Your original files stay where they are.

If two tracks would share a filename, nothing is written and you get an error. Re-running with the same import file **adds** new tracks; it does not replace the `[WAV]` playlist.

### Same thing with options (optional)

Playlist name must match Rekordbox **exactly** (spaces included). The wizard can do several playlists in one go; with flags you pass one name at a time (same `--output` file is extended).

```bash
./rb-converter.py \
  --xml ~/Documents/rekordbox/rekordbox.xml \
  --playlist "Dark forest duplicate"
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--xml` | asked | Collection export |
| `--playlist` | asked | Exact playlist name |
| `--wav-dir` | `./output` | WAV folder (`<this>/<playlist>/`) |
| `--output` | `./output/rekordbox-wav-import.xml` | Import file for Rekordbox |
| `--force` | off | Rebuild WAVs that already exist |
| `--dry-run` | off | Check only; write nothing |

---

## 3. Bring it into Rekordbox

Do **not** use **File → Import**. Point Rekordbox at the **generated** XML, then copy the playlist into your library.

### Show the rekordbox xml pane (once)

1. **Preferences → View → Layout**.
2. Under **Media Browser**, check **rekordbox xml**.

### Point Rekordbox at this tool’s XML

1. **Preferences → Advanced → Database**.
2. Under **rekordbox xml**, set **Imported Library** to `output/rekordbox-wav-import.xml` — the file this tool wrote, **not** your original collection export.
3. Close Preferences. You should see **rekordbox xml** in the browser tree.

If that pane already pointed at another XML, change **Imported Library** to this file. If tracks do not show up, use the refresh control on the rekordbox xml library.

### Copy into your collection

1. Open **rekordbox xml** → **Playlists**.
2. Find `{your playlist} [WAV]`.
3. Drag it onto **Playlists** in your main library, or right-click → **Import Playlist**.
4. Tracks only: **rekordbox xml → All Tracks**, select the WAV rows, drag onto **Collection** (or right-click → **Import to Collection**).

If Rekordbox asks whether to load information from the library being imported, choose **Yes** so cues, grid, BPM, and key come across.

Play one track. Confirm it is a WAV on a disk Rekordbox can read (internal drive or a mounted volume).

---

## 4. After import

- Analyze again only if waveforms are missing; cues and grid should already be there.
- **Do not move the WAV folder.** Rekordbox stores those paths. Convert again if you relocate files.
- Original lossless files are untouched.

**New tracks later:** export XML from Rekordbox again, run `./rb-converter.py` with the same output folder and import file, refresh **Imported Library**, then import the new rows.
