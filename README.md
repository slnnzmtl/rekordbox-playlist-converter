# Rekordbox playlist → WAV

<p align="center">
  <img src="assets/rpc-logo-white.png" alt="Simple Rekordbox Converter logo" width="160">
</p>

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![CI](https://github.com/slnnzmtl/rekordbox-playlist-converter/actions/workflows/test.yml/badge.svg)](https://github.com/slnnzmtl/rekordbox-playlist-converter/actions/workflows/test.yml)

Turn a Rekordbox playlist of lossless tracks into **WAV** or **AIFF** files, **without changing your originals**. Cues, beatgrid, rating, BPM, and tags are copied into a new playlist named `{your playlist} [WAV]` or `{your playlist} [AIFF]`.

Works with Rekordbox **6** and **7**.

**Not File → Import.** Rekordbox loads this XML from the **rekordbox xml** pane. The full click-path is in **[USAGE.md](USAGE.md)**.

Pre-built **macOS app** (universal2): see [GitHub Releases](https://github.com/slnnzmtl/rekordbox-playlist-converter/releases). The **CLI** runs on macOS, Linux, and Windows (Python 3.10+ and `ffmpeg`).

## What gets converted


| You have                               | Output                                                                |
| -------------------------------------- | --------------------------------------------------------------------- |
| FLAC / ALAC / AIFF / WAV               | Stereo PCM at selected max bit depth (16/24) and sample rate (44.1/48 kHz); never upconvert |
| Already matching dest                  | Skip when container + effective quality match (AIFF also ID3/art)     |


MP3, AAC, and other lossy files are skipped with an error.

Quality flags are a **ceiling**, not a target: 16-bit tracks stay 16-bit; 44.1 kHz tracks stay 44.1 kHz. Other sample rates (for example 88.2 kHz or 22.05 kHz) are snapped to 44100 when that rate is allowed by the ceiling. Defaults are WAV / 24-bit / 48 kHz.

**WAV profile:** uncompressed stereo `WAVE_FORMAT_PCM` (not extensible), `fmt `+`data` only, at the effective depth/rate.

**AIFF profile:** uncompressed `FORM`/`AIFF` (not AIFC), stereo PCM at the effective depth/rate, plus ID3v2.3 text from the Rekordbox XML and an optional JPEG cover from the source file.
## macOS app (no Terminal)

Download **Simple Rekordbox Converter.app** from [Releases](https://github.com/slnnzmtl/rekordbox-playlist-converter/releases), or build it yourself (below). It is a **universal** binary (Intel and Apple Silicon). Defaults write to `~/Documents/rekordbox-converter` (a different folder than the CLI’s `./output`); the app remembers your last Rekordbox XML and output folder between launches. Import XML is always `<output folder>/rekordbox-import.xml`. On launch it searches only your home folder (top-level files) and `~/Documents` for `*rekordbox*.xml` (skipping Desktop, Downloads, and iCloud) and auto-loads a single match, or asks you to choose if several are found. First launch: right-click → **Open** if Gatekeeper blocks it (ad-hoc signed). macOS may ask for Documents access on first open; if you decline, the app still opens and defaults to `~/rekordbox-converter` — Browse… can prompt again when you navigate into Documents.

Import into Rekordbox the same way as the CLI — point **Imported Library** at `<output folder>/rekordbox-import.xml`. In the app, **Convert** opens a conversion preview first (**Back** writes nothing); after a successful run, **Reveal audio folder** opens the selected format directory (`WAV/` or `AIFF/`). **Help → How to Use…** covers the full Rekordbox click-path. Same steps are also in **[USAGE.md](USAGE.md)**.

### Build the .app

Needs the [python.org macOS 64-bit universal2](https://www.python.org/downloads/macos/) installer (3.12 or newer, Tk included). Homebrew Python cannot produce this `.app`.

```bash
./scripts/build-macos-app.sh
```

That script downloads static **release** `ffmpeg`/`ffprobe` for arm64 and amd64 from [ffmpeg.martin-riedl.de](https://ffmpeg.martin-riedl.de/), lipos them into `vendor/ffmpeg/`, copies the ffmpeg GPL notice from `third_party/ffmpeg/`, recreates `.venv` from the python.org interpreter if needed, runs PyInstaller, and ad-hoc signs the bundle. Override the interpreter with `PYTHON=/path/to/python3` if you have more than one framework install.

Do **not** copy Homebrew’s ffmpeg (cellar dylibs). Static ffmpeg is GPL — see [third_party/ffmpeg/](third_party/ffmpeg/).

The app lands in `dist/Simple Rekordbox Converter.app`. Confirm both slices: `lipo -archs "dist/Simple Rekordbox Converter.app/Contents/MacOS/Simple Rekordbox Converter"`.

## First time (CLI)

1. Install **ffmpeg** (provides `ffprobe` too). On a Mac with Homebrew:

```bash
brew install ffmpeg
```

On Linux, use your package manager (e.g. `sudo apt install ffmpeg`). On Windows, install ffmpeg and ensure it is on `PATH`.

2. You need **Python 3.10 or newer**. On many Macs, `python3` is already there. If Terminal says `command not found: python3`:

```bash
brew install python@3.12
```

## Run it (CLI)

1. In Rekordbox: **File → Export Collection in xml format**. Save somewhere local (not iCloud if you can avoid it).
2. Clone this repo and open a terminal in the project folder:

```bash
git clone https://github.com/slnnzmtl/rekordbox-playlist-converter.git
cd rekordbox-playlist-converter
./rb-converter.py
```

3. Pick the XML export, pick one or more playlists (`1`, `1,4,7`, or `all`), and confirm the output folder (default `./output`). Audio lands under `output/WAV/` or `output/AIFF/` as `<artist> - <track>`; the import file is `output/rekordbox-import.xml`.
4. Follow the import steps printed at the end — or open **[USAGE.md](USAGE.md)** and do section 3.

The new playlist in the import file is named `{original} [WAV]` or `{original} [AIFF]`. Running again **adds** tracks and refreshes metadata for existing dest paths; it does not wipe the playlist. A hidden sticky `.rekordbox-converter-manifest.json` in the output folder remembers each source’s path per format so reruns stay stable. Deleting that manifest leaves the audio unmanaged and the folder is refused — use a new empty output folder.

## Options (optional)

Most people can ignore this and use the prompts.

```bash
./rb-converter.py \
  --xml rekordbox.xml \
  --playlist "Dark forest duplicate"
```


| Option       | Default                             | Meaning                                                         |
| ------------ | ----------------------------------- | --------------------------------------------------------------- |
| `--xml`      | asked                               | Your Rekordbox collection export                                |
| `--playlist` | asked                               | Playlist name, exactly as in Rekordbox                          |
| `--format`   | `wav`                               | `wav` or `aiff`                                                 |
| `--bit-depth` | `24`                               | Max bit depth `16` or `24` (never upconvert 16-bit to 24-bit)   |
| `--sample-rate` | `48000`                         | Max rate `44100` or `48000` (never upconvert 44.1 to 48 kHz; other rates snap to 44100 when allowed) |
| `--wav-dir`  | `./output`                          | Shared library folder (`WAV/` or `AIFF/` + `<artist> - <track>`) |
| `--output`   | `<wav-dir>/rekordbox-import.xml` | Optional override; default is derived from `--wav-dir`          |
| `--force`    | off                                 | Rebuild even if dest already matches the profile                |
| `--dry-run`  | off                                 | Print the conversion plan; write nothing                        |


Layout is `WAV|AIFF/<artist> - <track>` (no Album or quality directories). Assignments are sticky per source and format; `(2)` / `(3)` suffixes apply only when different sources would share a name. If you delete a generated audio file, the next run recreates it at the same assignment. `--dry-run` prints the same plan the GUI Convert preview shows. Keep the output folder where it is after import — moving files later breaks the paths Rekordbox stored.

## Tests

```bash
python3 -m unittest discover -s src/tests -v
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

This project is licensed under the [GNU General Public License v3.0 or later](LICENSE).

The macOS `.app` bundles GPL `ffmpeg`/`ffprobe`; see [third_party/ffmpeg/](third_party/ffmpeg/).

## Trademark

Rekordbox is a trademark of AlphaTheta Corporation / Pioneer DJ. This is an unofficial tool and is not affiliated with, endorsed by, or sponsored by AlphaTheta or Pioneer DJ.
