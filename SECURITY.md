# Security

If you find a security issue in this project, please report it privately via
[GitHub Security Advisories](https://github.com/slnnzmtl/rekordbox-playlist-converter/security/advisories/new)
rather than opening a public issue.

This tool only reads a Rekordbox XML export and writes audio files (WAV/AIFF)
plus an import XML. At runtime it may contact the network for two optional
purposes:

1. **Update check (GUI).** GitHub Releases (`api.github.com`) on launch or via
   Help → Check for Updates….
2. **Usage analytics (opt-in, default off).** When enabled, a fire-and-forget
   `POST` to `https://analytics.slnnzmtl.xyz/v1/events` after first opt-in
   (`install`) and after a successful conversion (`conversion_completed`). No
   client secret is embedded; failures never change conversion results. Failed
   events are queued in `analytics_queue.json` beside preferences and retried
   until a successful send (held without transmitting while analytics is off).
   HTTP 4xx responses drop that event so a permanently rejected payload cannot
   block later events.

Neither endpoint receives track titles, paths, playlist names, XML bodies,
accounts, or session identifiers. See the Privacy section in
[README.md](README.md). The macOS build script also downloads ffmpeg at
**build** time.
