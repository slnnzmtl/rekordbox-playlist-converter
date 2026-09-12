# Contributing

Thanks for helping improve this tool.

## Setup

- **CLI / tests:** Python 3.10+, and `ffmpeg` on your `PATH` (needed for conversion tests that invoke ffmpeg when available).
- **macOS `.app`:** python.org universal2 Python 3.12+ with Tk. See [README.md](README.md).

```bash
git clone https://github.com/slnnzmtl/rekordbox-playlist-converter.git
cd rekordbox-playlist-converter
python3 -m unittest discover -s src/tests -v
```

## Build the macOS app

```bash
./scripts/build-macos-app.sh
```

The `.app` icon is `assets/app.icns`, built from `assets/rpc-logo-white.png`. The generator insets the mark to Apple's 824/1024 Dock grid, clips it to a macOS squircle, writes the 256px Tk window icon, and uses `iconutil` on macOS (or a handmade PNG+ARGB `.icns` elsewhere) so Finder list view still gets `ic04`/`ic05`:

```bash
python3 scripts/make-app-icns.py
```

Do not commit `dist/`, `build/`, `vendor/`, or `.venv/` (they are gitignored).

## Pull requests

- Keep changes focused; match existing style in `src/`.
- Add or update tests under `src/tests/` when behavior changes.
- Run the unittest suite before opening a PR.
- After Import XML or library-layout changes, leave
  [docs/rekordbox-xml-import-checklist.md](docs/rekordbox-xml-import-checklist.md)
  blank for humans to fill against latest Rekordbox 6.x / 7.x on macOS and
  Windows (do not invent version numbers in CI or agent runs).
- This project is GPL-3.0-or-later; by contributing you agree your changes are licensed under the same terms.
