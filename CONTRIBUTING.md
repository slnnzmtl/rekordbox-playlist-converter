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

Do not commit `dist/`, `build/`, `vendor/`, or `.venv/` (they are gitignored).

## Pull requests

- Keep changes focused; match existing style in `src/`.
- Add or update tests under `src/tests/` when behavior changes.
- Run the unittest suite before opening a PR.
- This project is GPL-3.0-or-later; by contributing you agree your changes are licensed under the same terms.
