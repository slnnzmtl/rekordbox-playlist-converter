# Rekordbox playlist → WAV

Convert a Rekordbox playlist’s lossless tracks to PCM WAV and write a small import XML with copied cues, beatgrid, and tags.

Requires **Python 3.10+**, plus **ffmpeg** and **ffprobe** on `PATH`. Rekordbox 6 and 7 XML exports are both accepted.

## Usage

```bash
./rb_playlist_to_wav.py \
  --xml rekordbox.xml \
  --playlist "Dark forest duplicate"
```

| Flag | Default | |
| --- | --- | --- |
| `--xml` | required | Rekordbox XML export |
| `--playlist` | required | Exact playlist name |
| `--wav-dir` | `./WAV` | WAV root; files go in `--wav-dir/<playlist>/` |
| `--output` | `./rekordbox-wav-import.xml` | Import XML (created or **extended**, never overwritten) |
| `--force` | off | Reconvert existing valid WAVs |
| `--dry-run` | off | Validate only; write nothing |

FLAC, ALAC, and AIFF are converted (sample rate and bit depth preserved). WAVs are copied. Existing dest files are skipped unless `--force`. Filename collisions abort before any write. A progress bar is printed on a TTY during convert/copy/skip.

The new playlist is named `{original} [WAV]`. Re-running appends new tracks; it does not replace the playlist.

See **[USAGE.md](USAGE.md)** for exporting the library from Rekordbox and importing the generated XML back (via the **rekordbox xml** pane, not File → Import). Original files are never modified.
