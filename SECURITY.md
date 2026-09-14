# Security

If you find a security issue in this project, please report it privately via
[GitHub Security Advisories](https://github.com/slnnzmtl/rekordbox-playlist-converter/security/advisories/new)
rather than opening a public issue.

This tool only reads a Rekordbox XML export and writes audio files (WAV/AIFF)
plus an import XML. At runtime the GUI may contact GitHub Releases
(`api.github.com`) to check for updates (automatic on launch, or Help → Check
for Updates…). There is no analytics client in this application. The macOS
build script also downloads ffmpeg at **build** time.
