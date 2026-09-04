# Rekordbox playlist → WAV

Turn a Rekordbox playlist of lossless tracks into WAV files, **without changing your originals**. Cues, beatgrid, rating, BPM, and tags are copied into a new playlist named `{your playlist} [WAV]`.

Works with Rekordbox **6** and **7**.

**Not File → Import.** Rekordbox loads this XML from the **rekordbox xml** pane. The full click-path is in **[USAGE.md](USAGE.md)**.

## What gets converted


| You have                               | What happens                             |
| -------------------------------------- | ---------------------------------------- |
| FLAC (`.flac`)                         | New WAV (same sample rate and bit depth) |
| Apple Lossless / ALAC (`.m4a`, `.caf`) | New WAV                                  |
| AIFF (`.aiff`, `.aif`)                 | New WAV                                  |
| WAV (`.wav`, `.wave`)                  | Copied as-is                             |


MP3, AAC, and other lossy files are skipped with an error. Existing WAVs in the output folder are left alone unless you pass `--force`.

## macOS app (no Terminal)

Double-click **Rekordbox WAV Converter.app**. It is a **universal** binary (Intel and Apple Silicon). Defaults write to `~/Documents/rekordbox-wav` (a different folder than the CLI’s `./output`). First launch: right-click → **Open** if Gatekeeper blocks it. macOS may ask for Documents access when writing there.

Import into Rekordbox the same way as the CLI — point **Imported Library** at the app’s import XML. In the app, **Help → How to Use…** (or the **How to use** button) covers the full Rekordbox click-path. Same steps are also in **[USAGE.md](USAGE.md)**.

### Build the .app

Needs the [python.org macOS 64-bit universal2](https://www.python.org/downloads/macos/) installer (3.12 or newer, Tk included). Homebrew Python cannot produce this `.app`.

```bash
./scripts/build-macos-app.sh
```

That script downloads static **release** `ffmpeg`/`ffprobe` for arm64 and amd64 from [ffmpeg.martin-riedl.de](https://ffmpeg.martin-riedl.de/), lipos them into `vendor/ffmpeg/`, recreates `.venv` from the python.org interpreter if needed, runs PyInstaller, and ad-hoc signs the bundle. Override the interpreter with `PYTHON=/path/to/python3` if you have more than one framework install.

Do **not** copy Homebrew’s ffmpeg (cellar dylibs). Static ffmpeg is GPL — the script keeps a `COPYING` / `LICENSE` from the zip when present.

The app lands in `dist/Rekordbox WAV Converter.app`. Confirm both slices: `lipo -archs "dist/Rekordbox WAV Converter.app/Contents/MacOS/Rekordbox WAV Converter"`.

## First time on a Mac (CLI)

1. Install **Homebrew** if you do not have it: [https://brew.sh](https://brew.sh)
2. In Terminal:

```bash
brew install ffmpeg
```

You also need **Python 3.10 or newer**. On many Macs, `python3` is already there. If Terminal says `command not found: python3`:

```bash
brew install python@3.12
```



## Run it (CLI)

1. In Rekordbox: **File → Export Collection in xml format**. Save somewhere local (not iCloud if you can avoid it).
2. Open Terminal, go to this folder, then:

```bash
./rb-converter.py
```

1. Pick the XML export, pick one or more playlists (`1`, `1,4,7`, or `all`), and confirm the output folder (default `./output`).
2. Follow the import steps printed at the end — or open **[USAGE.md](USAGE.md)** and do section 3.

The new playlist in the import file is named `{original} [WAV]`. Running again **adds** tracks; it does not wipe the playlist.

## Options (optional)

Most people can ignore this and use the prompts.

```bash
./rb-converter.py \
  --xml rekordbox.xml \
  --playlist "Dark forest duplicate"
```


| Option       | Default                             | Meaning                                           |
| ------------ | ----------------------------------- | ------------------------------------------------- |
| `--xml`      | asked                               | Your Rekordbox collection export                  |
| `--playlist` | asked                               | Playlist name, exactly as in Rekordbox            |
| `--wav-dir`  | `./output`                          | Folder for WAV files (`output/<playlist>/`)       |
| `--output`   | `./output/rekordbox-wav-import.xml` | File Rekordbox should import (appended on re-run) |
| `--force`    | off                                 | Rebuild WAVs even if they already exist           |
| `--dry-run`  | off                                 | Check only; write nothing                         |


If two tracks would get the same filename, the run stops before writing anything. Keep the WAV folder where it is after import — moving files later breaks the paths Rekordbox stored.