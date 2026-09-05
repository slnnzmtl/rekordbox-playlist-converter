# Bundled ffmpeg / ffprobe

The macOS `.app` ships static **release** builds of `ffmpeg` and `ffprobe` from
[ffmpeg.martin-riedl.de](https://ffmpeg.martin-riedl.de/) (GPL configuration).

Those binaries are licensed under the **GNU General Public License v3**.
See [COPYING.GPLv3](COPYING.GPLv3) in this directory.

`./scripts/build-macos-app.sh` downloads arm64 and amd64 zips, `lipo`s them into
`vendor/ffmpeg/`, and always copies this `COPYING.GPLv3` next to the binaries
(the upstream zips often omit a license file). PyInstaller then includes the
project [LICENSE](../../LICENSE) and this ffmpeg license text in the app bundle.
