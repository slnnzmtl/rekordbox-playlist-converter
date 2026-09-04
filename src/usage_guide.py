"""In-app usage guide text for the Rekordbox WAV converter GUI."""

USAGE_GUIDE = """\
How to convert a playlist (Rekordbox 6 and 7)

This app makes new WAV copies of a playlist, with cues and beatgrid, without
touching your originals. The import playlist is named {your playlist} [WAV].

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

1. Choose the XML export (Browse… next to Rekordbox XML).
2. Select one or more playlists (hold ⌃ to multi-select). Search filters the
   list.
3. Confirm WAV folder and Import XML. Defaults are ~/Documents/rekordbox-wav
   and ~/Documents/rekordbox-wav/rekordbox-wav-import.xml.
4. Click Convert.

What you get:
• WAVs in <WAV folder>/<playlist name>/
• Import file at the Import XML path
• Playlist inside that file named {your playlist} [WAV]

Your original files stay where they are. Re-running with the same import file
adds new tracks; it does not replace the [WAV] playlist. Check “Overwrite
existing WAV files” only if you want to rebuild WAVs that already exist.

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
2. Under rekordbox xml, set Imported Library to the Import XML this app wrote
   (default: ~/Documents/rekordbox-wav/rekordbox-wav-import.xml) — not your
   original collection export.
3. Close Preferences. You should see rekordbox xml in the browser tree.

If that pane already pointed at another XML, change Imported Library to this
file. If tracks do not show up, use the refresh control on the rekordbox xml
library.

Copy into your collection:
1. Open rekordbox xml → Playlists.
2. Find {your playlist} [WAV].
3. Drag it onto Playlists in your main library, or right-click → Import
   Playlist.
4. Tracks only: rekordbox xml → All Tracks, select the WAV rows, drag onto
   Collection (or right-click → Import to Collection).

If Rekordbox asks whether to load information from the library being imported,
choose Yes so cues, grid, BPM, and key come across.

Play one track. Confirm it is a WAV on a disk Rekordbox can read.

────────────────────────────────────────
4. After import
────────────────────────────────────────

• Analyze again only if waveforms are missing; cues and grid should already
  be there.
• Do not move the WAV folder. Rekordbox stores those paths. Convert again if
  you relocate files.
• Original lossless files are untouched.

New tracks later: export XML from Rekordbox again, convert with the same WAV
folder and Import XML, refresh Imported Library, then import the new rows.
"""
