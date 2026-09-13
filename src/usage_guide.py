"""In-app usage guide text for the Simple Rekordbox Converter GUI."""

USAGE_GUIDE = """\
How to convert a playlist (Rekordbox 6 and 7)

This app makes new audio copies of a playlist (WAV or AIFF), with cues and
beatgrid, without touching your originals. The import playlist is named
{your playlist} [WAV] or {your playlist} [AIFF].

Menu names match Rekordbox 7. Rekordbox 6 is the same idea: export the
collection, then use the rekordbox xml pane — never File → Import.

────────────────────────────────────────
1. Export your collection from Rekordbox
────────────────────────────────────────

This app does not open Rekordbox’s internal database. It only reads an XML
export.

1. Open Rekordbox and wait until analysis has finished on the tracks you care
   about (cues and grids come from this export).
2. Rekordbox 7 — beatgrid in the XML: Preferences → Advanced → rekordbox xml
   → enable Export BeatGrid information.
3. File → Export Collection in xml format.
4. Save locally (for example Documents/rekordbox/rekordbox.xml). Avoid iCloud
   or Dropbox for a large export if you can.

────────────────────────────────────────
2. Convert in this app
────────────────────────────────────────

1. Choose the XML export (Browse… next to Rekordbox XML). The app remembers
   your last Rekordbox XML between launches. If none is saved, it searches
   only your home folder (top-level files) and ~/Documents for filenames that
   contain “rekordbox” and end in .xml (skipping Desktop, Downloads, and
   iCloud). One match is loaded automatically; if several are found, pick from
   a short list. File → Search for Rekordbox XML… runs that search again even
   when a file is already loaded.
2. Browse playlists in the folder tree (expand folders; hold ⌃ to multi-select).
   Selecting a folder lists every playlist under it. The tracklist table shows
   only convertible formats (FLAC, ALAC, WAV, AIFF) plus missing collection
   rows: Track, Format, Bit depth, and Sample rate. Click a column header to
   sort within each playlist group. Format and rate come from the
   export; bit depth is read from FLAC, ALAC (M4A), WAV, or AIFF file headers
   when the file is on disk, otherwise — (Scanning bit depth… appears beside the
   unique-tracks status line while that runs). Track rows start selected; hold ⌃ to
   refine which tracks to convert (across playlists). Playlist search filters
   the left tree; track search filters the current tracklist (artist / title /
   filename).
3. Confirm the output folder. Defaults are ~/Documents/rekordbox-converter when
   Documents access is allowed, or ~/rekordbox-converter if you decline that
   request; Browse… can prompt again when you open Documents. Import XML is
   always <output folder>/rekordbox-import.xml in a disabled field (click to
   copy the full path). The app remembers your last-used output folder between
   launches. Deleting the hidden .rekordbox-converter-manifest.json leaves
   audio unmanaged and the folder is refused — choose a new empty folder.
4. Choose Format: WAV or AIFF.
5. Choose Maximum output quality (bit depth 16-bit/24-bit and rate
   44.1 kHz/48 kHz). These are maxima, not targets: 16-bit tracks stay
   16-bit; 44.1 kHz tracks stay 44.1 kHz. Defaults are 24-bit / 48 kHz.
6. Click Convert (beside the progress bar). A Conversion preview lists unique
   outputs, actions, quality, and size. Back (or Escape) writes nothing and
   returns to the main window. Confirm Convert to start encoding. While
   converting, Cancel replaces Convert in that spot. Cancel stops in-flight
   encodes (up to 4 at once); files already written are kept and Import XML
   includes tracks already in the success set (re-run Convert to finish the
   rest). If some tracks fail, convertible tracks still finish and all errors
   are reported together. After success, Reveal audio folder opens the selected
   format directory (WAV/ or AIFF/).

What you get:
• Audio files in <output folder>/WAV/ or …/AIFF/ as <artist> - <track>
  (no Album or quality directories; shared across playlists)
  — WAV: stereo WAVE_FORMAT_PCM, fmt + data only, at the effective depth/rate
  — AIFF: stereo PCM at the effective depth/rate, plus ID3v2.3 (COMM + SSND + ID3)
• Sticky hidden .rekordbox-converter-manifest.json in the output folder
• Import file <output folder>/rekordbox-import.xml
• Playlist inside that file named {your playlist} [WAV] or [AIFF]

Your original files stay where they are. Re-running with the same output folder
adds new tracks and refreshes metadata for existing dest paths; it does not
replace the playlist. Each unique source converts once per batch even if it
appears in several playlists. Assignments are sticky per source and format.
If two different sources would share <artist> - <track>, the second gets (2),
then (3), and so on. Deleting a generated audio file recreates it at the same
assignment on the next run. Dest files that already match the chosen profile
are skipped unless you force a rebuild (CLI: --force). CLI --dry-run prints
the same plan as this preview and writes nothing.

────────────────────────────────────────
3. Bring it into Rekordbox
────────────────────────────────────────

Do not use File → Import. Point Rekordbox at the generated XML, then copy the
playlist into your library.

Show the rekordbox xml pane (once):
1. Preferences → View → Layout.
2. Under Media Browser, check rekordbox xml.

Point Rekordbox at this app’s XML:
1. Preferences → Advanced → Database.
2. Under rekordbox xml, set Imported Library to
   <output folder>/rekordbox-import.xml — not your original collection export.
3. Close Preferences. You should see rekordbox xml in the browser tree.

If that pane already pointed at another XML, change Imported Library to this
file. If tracks do not show up, use the refresh control on the rekordbox xml
library.

Copy into your collection:
1. Open rekordbox xml → Playlists.
2. Find {your playlist} [WAV] or [AIFF].
3. Drag it onto Playlists in your main library, or right-click → Import
   Playlist.
4. Tracks only: rekordbox xml → All Tracks, select the audio rows, drag onto
   Collection (or right-click → Import to Collection).

If Rekordbox asks whether to load information from the library being imported,
choose Yes so cues, grid, BPM, and key come across.

Play one track. Confirm it is on a disk Rekordbox can read.

────────────────────────────────────────
4. After import
────────────────────────────────────────

• Analyze again only if waveforms are missing; cues and grid should already
  be there.
• Do not move the output folder. Rekordbox stores those paths. Convert again if
  you relocate files.
• Original lossless files are untouched.

New tracks later: export XML from Rekordbox again, convert with the same output
folder, refresh Imported Library, then import the new rows.
"""
