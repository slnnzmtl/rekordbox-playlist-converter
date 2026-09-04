#!/usr/bin/env bash
# Build a universal2 Rekordbox WAV Converter.app (Intel + Apple Silicon).
#
# Requires:
#   - macOS
#   - python.org macOS 64-bit universal2 installer (3.12+), with Tk
#   - network (to fetch static ffmpeg/ffprobe release zips, unless already fat)
#
# Usage:
#   ./scripts/build-macos-app.sh
#   PYTHON=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
#     ./scripts/build-macos-app.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENDOR="$ROOT/vendor/ffmpeg"
VENV="$ROOT/.venv"
APP="$ROOT/dist/Rekordbox WAV Converter.app"
EXE="$APP/Contents/MacOS/Rekordbox WAV Converter"
# PyInstaller BUNDLE puts --add-binary helpers in Frameworks, not MacOS.
BUNDLED_FFMPEG="$APP/Contents/Frameworks/ffmpeg"
BUNDLED_FFPROBE="$APP/Contents/Frameworks/ffprobe"
FFMPEG_BASE="https://ffmpeg.martin-riedl.de/redirect/latest/macos"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing command: $1"
}

require_universal2() {
  local path="$1"
  [[ -f "$path" ]] || die "missing $path"
  local arches
  arches="$(lipo -archs "$path" 2>/dev/null || true)"
  local have
  # shellcheck disable=SC2206
  have=($arches)
  local missing=()
  local need
  for need in x86_64 arm64; do
    local ok=0
    local a
    for a in "${have[@]+"${have[@]}"}"; do
      [[ "$a" == "$need" ]] && ok=1 && break
    done
    [[ $ok -eq 1 ]] || missing+=("$need")
  done
  if ((${#missing[@]})); then
    die "$path is not universal2 (archs: ${arches:-none}; need x86_64 and arm64)"
  fi
}

find_universal_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    [[ -x "$PYTHON" ]] || die "PYTHON is not executable: $PYTHON"
    printf '%s\n' "$PYTHON"
    return
  fi

  local ver candidate
  for ver in 3.13 3.12 3.14 3.11; do
    candidate="/Library/Frameworks/Python.framework/Versions/${ver}/bin/python3"
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return
    fi
  done

  die "python.org universal2 Python not found under /Library/Frameworks/Python.framework.
Install the macOS 64-bit universal2 installer from https://www.python.org/downloads/macos/
(Homebrew Python cannot build this .app). Or set PYTHON=/path/to/python3."
}

fetch_and_lipo_ffmpeg() {
  require_cmd curl
  require_cmd unzip
  require_cmd lipo
  require_cmd codesign
  require_cmd xattr

  local tmp
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/rb-ffmpeg.XXXXXX")"
  # shellcheck disable=SC2064
  trap "rm -rf '$tmp'" RETURN

  mkdir -p "$VENDOR"

  local name arch url zip bin out lic
  for name in ffmpeg ffprobe; do
    for arch in arm64 amd64; do
      url="${FFMPEG_BASE}/${arch}/release/${name}.zip"
      zip="$tmp/${name}-${arch}.zip"
      printf 'Downloading %s (%s)…\n' "$name" "$arch"
      curl -fL --retry 3 --retry-delay 2 -o "$zip" "$url"
      unzip -qo "$zip" -d "$tmp/${name}-${arch}"
      bin="$(find "$tmp/${name}-${arch}" -type f -name "$name" | head -n 1)"
      [[ -n "$bin" && -f "$bin" ]] || die "zip for $name/$arch did not contain $name"
      cp "$bin" "$tmp/${name}.${arch}"
      # Prefer a license file if the zip ships one.
      for lic in COPYING LICENSE LICENSE.md COPYING.GPLv3; do
        if [[ -f "$tmp/${name}-${arch}/$lic" && ! -f "$VENDOR/$lic" ]]; then
          cp "$tmp/${name}-${arch}/$lic" "$VENDOR/$lic"
        fi
      done
    done
    out="$VENDOR/$name"
    lipo -create "$tmp/${name}.arm64" "$tmp/${name}.amd64" -output "$out"
    chmod +x "$out"
  done

  xattr -cr "$VENDOR"
  codesign --force --sign - "$VENDOR/ffmpeg" "$VENDOR/ffprobe"
}

ensure_vendor_ffmpeg() {
  if [[ -f "$VENDOR/ffmpeg" && -f "$VENDOR/ffprobe" ]]; then
    if lipo -archs "$VENDOR/ffmpeg" 2>/dev/null | grep -qw x86_64 \
      && lipo -archs "$VENDOR/ffmpeg" 2>/dev/null | grep -qw arm64 \
      && lipo -archs "$VENDOR/ffprobe" 2>/dev/null | grep -qw x86_64 \
      && lipo -archs "$VENDOR/ffprobe" 2>/dev/null | grep -qw arm64; then
      printf 'Using existing universal2 ffmpeg/ffprobe in vendor/ffmpeg/\n'
      return
    fi
    printf 'vendor/ffmpeg is not universal2; re-fetching and lipo’ing…\n'
  else
    printf 'vendor/ffmpeg missing; fetching static release builds…\n'
  fi
  fetch_and_lipo_ffmpeg
  require_universal2 "$VENDOR/ffmpeg"
  require_universal2 "$VENDOR/ffprobe"
}

ensure_venv() {
  local py="$1"
  if [[ -x "$VENV/bin/python" ]]; then
    local base
    base="$("$VENV/bin/python" -c 'import sys; print(sys.base_prefix)')"
    local expected
    expected="$("$py" -c 'import sys; print(sys.prefix)')"
    if [[ "$base" == "$expected" ]]; then
      printf 'Reusing .venv (%s)\n' "$("$VENV/bin/python" -V)"
      return
    fi
    printf 'Recreating .venv (was %s, need %s)\n' "$base" "$expected"
    rm -rf "$VENV"
  fi
  "$py" -m venv "$VENV"
}

[[ "$(uname -s)" == Darwin ]] || die "this script only builds the macOS .app"

require_cmd lipo
require_cmd codesign

PY="$(find_universal_python)"
require_universal2 "$PY"
"$PY" -c 'import tkinter' >/dev/null 2>&1 \
  || die "$PY cannot import tkinter (install the python.org package with Tcl/Tk)"

printf 'Python: %s (%s)\n' "$PY" "$("$PY" -V)"
ensure_vendor_ffmpeg
printf 'ffmpeg arches:  %s\n' "$(lipo -archs "$VENDOR/ffmpeg")"
printf 'ffprobe arches: %s\n' "$(lipo -archs "$VENDOR/ffprobe")"

ensure_venv "$PY"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$ROOT/requirements-build.txt"

printf 'Running PyInstaller…\n'
python -m PyInstaller --noconfirm "$ROOT/rb_converter.spec"

[[ -d "$APP" ]] || die "PyInstaller did not produce $APP"

codesign --force --deep -s - "$APP"

require_universal2 "$EXE"
require_universal2 "$BUNDLED_FFMPEG"
require_universal2 "$BUNDLED_FFPROBE"

printf '\nBuilt: %s\n' "$APP"
printf '  app:     %s\n' "$(lipo -archs "$EXE")"
printf '  ffmpeg:  %s\n' "$(lipo -archs "$BUNDLED_FFMPEG")"
printf '  ffprobe: %s\n' "$(lipo -archs "$BUNDLED_FFPROBE")"
printf '\nAd-hoc signed. First launch on another Mac: right-click → Open.\n'
