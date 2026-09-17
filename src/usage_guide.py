"""In-app usage guide text for the Simple Rekordbox Converter GUI."""

WELCOME_GUIDE = """\
Welcome to Simple Rekordbox Converter

This app makes new WAV or AIFF copies of a playlist, with cues, loops,
beatgrid, rating, BPM, key, comments, colour, and tags, without touching
your originals. The import playlist is named {your playlist} [WAV] or
{your playlist} [AIFF].

1. Export your collection from Rekordbox
   File → Export Collection in xml format. Save locally (for example
   Documents/rekordbox/rekordbox.xml). In Rekordbox 7, enable Export
   BeatGrid information under Preferences → Advanced → rekordbox xml.

2. Convert in this app
   Choose the XML export, select playlists and tracks, confirm the output
   folder, choose Format and Maximum output quality, then Convert. Review
   the preview and confirm. Import XML is always
   <output folder>/rekordbox-import.xml.

3. Bring it into Rekordbox
   Do not use File → Import. Show the rekordbox xml pane
   (Preferences → View → Layout → Media Browser), set Imported Library to
   this app’s rekordbox-import.xml, then drag the [WAV] or [AIFF] playlist
   into your collection (or Import Playlist). Choose Yes if asked to load
   information from the library being imported.

Full steps: Help → How to Use…
"""

USAGE_GUIDE = """\
How to convert a playlist (Rekordbox 6 and 7)

This app makes new WAV or AIFF copies of a playlist, with cues, loops,
beatgrid, rating, BPM, key, comments, colour, and tags, without touching
your originals. The import playlist is named {your playlist} [WAV] or
{your playlist} [AIFF].

Menu names match Rekordbox 7. Rekordbox 6 is the same idea: export the
collection, then use the rekordbox xml pane — never File → Import.

────────────────────────────────────────
1. Export your collection from Rekordbox
────────────────────────────────────────

1. Wait until analysis has finished on the tracks you care about.
2. Rekordbox 7: Preferences → Advanced → rekordbox xml → enable Export
   BeatGrid information.
3. File → Export Collection in xml format.
4. Save locally (for example Documents/rekordbox/rekordbox.xml). Avoid
   iCloud or Dropbox for a large export if you can.

────────────────────────────────────────
2. Convert in this app
────────────────────────────────────────

1. Choose the XML export (Browse… next to Rekordbox XML).
2. Select playlists in the folder tree and tracks in the tracklist.
3. Confirm the output folder. Import XML is always
   <output folder>/rekordbox-import.xml.
4. Choose Format: WAV or AIFF.
5. Choose Maximum output quality (16/24-bit and 44.1/48 kHz). These are
   ceilings, not targets. Defaults are 24-bit / 48 kHz.
6. Click Convert. Review the preview (action, reason, quality, size), then
   confirm. Cancel stops in-flight work; already-written files are kept.
   When finished, Reveal output folder opens the chosen folder.

What you get:
• Audio in <output folder>/WAV/ or …/AIFF/ as <artist> - <track>
• Import file <output folder>/rekordbox-import.xml
• Playlist inside that file named {your playlist} [WAV] or [AIFF]

Originals stay where they are. Re-run with the same output folder to refresh
or add tracks.

────────────────────────────────────────
3. Bring it into Rekordbox
────────────────────────────────────────

Do not use File → Import.

Show the rekordbox xml pane (once):
1. Preferences → View → Layout.
2. Under Media Browser, check rekordbox xml.

Point Rekordbox at this app’s XML:
1. Preferences → Advanced → Database.
2. Under rekordbox xml, set Imported Library to
   <output folder>/rekordbox-import.xml — not your original collection export.
3. Close Preferences, then refresh the rekordbox xml library if needed.

Copy into your collection:
1. Open rekordbox xml → Playlists.
2. Find {your playlist} [WAV] or [AIFF].
3. Drag it onto Playlists, or right-click → Import Playlist.

If Rekordbox asks whether to load information from the library being imported,
choose Yes so cues, loops, grid, BPM, key, comments, colour, and rating come
across. Play one track to confirm it is on a readable disk.

────────────────────────────────────────
4. After import
────────────────────────────────────────

• Analyze again only if waveforms are missing; cues and grid should already
  be there.
• Do not move the output folder — Rekordbox stores those paths.
• New tracks later: export XML again, convert with the same output folder,
  refresh Imported Library, then import the new rows.

Edit (optional): when the output folder already has rekordbox-import.xml and
the converter manifest, Edit appears beside Import XML. Change the draft, then
Save or Cancel. Your Rekordbox source XML stays read-only.

If Convert is disabled, read the preview reason — conflicts (file changed
outside the app), insufficient disk space, or nothing to convert (sources
missing).

More detail: USAGE.md in the project (or on GitHub).
"""
