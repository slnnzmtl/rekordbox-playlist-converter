# Security

If you find a security issue in this project, please report it privately via
[GitHub Security Advisories](https://github.com/slnnzmtl/rekordbox-playlist-converter/security/advisories/new)
rather than opening a public issue.

This tool only reads a Rekordbox XML export and writes audio files (WAV/AIFF)
plus an import XML. At runtime it may contact the network for two purposes
(conversion itself does not require the network):

1. **Update check (GUI).** GitHub Releases (`api.github.com`) on launch or via
   Help → Check for Updates…. This is **not** gated by the analytics opt-in.
   Packaged-app smoke on the **2.0.0** RC recorded that check as **up to date**
   versus the published GitHub latest (**v1.2.0**); see the
   [import checklist](docs/rekordbox-xml-import-checklist.md).
2. **Usage analytics (optional).** First-launch Welcome has a checkbox that is
   **checked by default**; Continue with it checked opts in. Uncheck it, Help →
   Share anonymous usage analytics, or CLI `--analytics on|off`. When enabled,
   a fire-and-forget `POST` to `https://analytics.slnnzmtl.xyz/v1/events` after
   first opt-in (`install`), after a successful conversion
   (`conversion_completed`), and after a failed conversion job
   (`conversion_failed` with a closed reason). No client secret is embedded;
   failures never change conversion results. Failed events are queued in
   `analytics_queue.json` beside preferences and retried until a successful
   send (held without transmitting while analytics is off). HTTP 4xx responses
   drop that event so a permanently rejected payload cannot block later events.

Neither endpoint receives track titles, artists, paths, playlist names, XML
bodies, accounts, devices, or session identifiers. See the Privacy section in
[README.md](README.md). The macOS build script also downloads ffmpeg at
**build** time.
