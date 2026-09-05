# Security

If you find a security issue in this project, please report it privately via
[GitHub Security Advisories](https://github.com/slnnzmtl/rekordbox-playlist-converter/security/advisories/new)
rather than opening a public issue.

This tool only reads a Rekordbox XML export and writes WAV files plus an import
XML. It does not talk to the network at runtime (the macOS build script
downloads ffmpeg at build time only).
