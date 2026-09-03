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

## First time on a Mac

1. Install **Homebrew** if you do not have it: [https://brew.sh](https://brew.sh)
2. In Terminal:

```bash
brew install ffmpeg
```

You also need **Python 3.10 or newer**. On many Macs, `python3` is already there. If Terminal says `command not found: python3`:

```bash
brew install python@3.12
```



## Run it

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