# How to convert a playlist (Rekordbox 6 and 7)

Goal: new **WAV** or **AIFF** copies of a playlist, with cues and beatgrid, **without touching your FLACs / ALACs / AIFFs**.

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
3. **Confirm folders** — defaults are `./output` for audio files and `./output/rekordbox-import.xml` for the import file.
4. **Format** — `wav` (default) or `aiff`.
5. **Max bit depth** — `16` (default) or `24`. 16-bit tracks are not upconverted to 24-bit.
6. **Max sample rate** — `44100` (default) or `48000`. 44.1 kHz tracks are not upconverted to 48 kHz.

**What you get**

- Audio files in `output/<playlist name>/`
  - WAV: stereo `WAVE_FORMAT_PCM` (`fmt ` + `data` only) at the effective depth/rate
  - AIFF: stereo PCM at the effective depth/rate, plus ID3v2.3 from the XML and optional cover art
- Import file `output/rekordbox-import.xml`
- Playlist named `{your playlist} [WAV]` or `{your playlist} [AIFF]`

These quality settings are maxima, not targets. Defaults stay WAV / 16-bit / 44.1 kHz.

Your original files stay where they are. Re-running with the same import file **adds** new tracks and refreshes metadata for existing dest paths.

If two tracks would share a filename, nothing is written and you get an error.

### Same thing with options (optional)

Playlist name must match Rekordbox **exactly** (spaces included). The wizard can do several playlists in one go; with flags you pass one name at a time (same `--output` file is extended).

```bash
./rb-converter.py \
  --xml ~/Documents/rekordbox/rekordbox.xml \
  --playlist "Dark forest duplicate" \
  --format aiff \
  --bit-depth 24 \
  --sample-rate 48000
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--xml` | asked | Collection export |
| `--playlist` | asked | Exact playlist name |
| `--format` | `wav` | `wav` or `aiff` |
| `--bit-depth` | `16` | Max `16` or `24` (no upconvert) |
| `--sample-rate` | `44100` | Max `44100` or `48000` (no upconvert) |
| `--wav-dir` | `./output` | Audio folder (`<this>/<playlist>/`) |
| `--output` | `./output/rekordbox-import.xml` | Import file for Rekordbox |
| `--force` | off | Rebuild files that already match the profile |
| `--dry-run` | off | Check only; write nothing |

---

## 3. Bring it into Rekordbox

Do **not** use **File → Import**. Point Rekordbox at the **generated** XML, then copy the playlist into your library.

### Show the rekordbox xml pane (once)

1. **Preferences → View → Layout**.
2. Under **Media Browser**, check **rekordbox xml**.

### Point Rekordbox at this tool’s XML

1. **Preferences → Advanced → Database**.
2. Under **rekordbox xml**, set **Imported Library** to `output/rekordbox-import.xml` — the file this tool wrote, **not** your original collection export.
3. Close Preferences. You should see **rekordbox xml** in the browser tree.

If that pane already pointed at another XML, change **Imported Library** to this file. If tracks do not show up, use the refresh control on the rekordbox xml library.

### Copy into your collection

1. Open **rekordbox xml** → **Playlists**.
2. Find `{your playlist} [WAV]` or `{your playlist} [AIFF]`.
3. Drag it onto **Playlists** in your main library, or right-click → **Import Playlist**.
4. Tracks only: **rekordbox xml → All Tracks**, select the audio rows, drag onto **Collection** (or right-click → **Import to Collection**).

If Rekordbox asks whether to load information from the library being imported, choose **Yes** so cues, grid, BPM, and key come across.

Play one track. Confirm it is on a disk Rekordbox can read (internal drive or a mounted volume).

---

## 4. After import

- Analyze again only if waveforms are missing; cues and grid should already be there.
- **Do not move the output folder.** Rekordbox stores those paths. Convert again if you relocate files.
- Original lossless files are untouched.

**New tracks later:** export XML from Rekordbox again, run `./rb-converter.py` with the same output folder and import file, refresh **Imported Library**, then import the new rows.
