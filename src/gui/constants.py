"""UI constants for Simple Rekordbox Converter (no TCC / no ConverterApp)."""

from __future__ import annotations

from gui_prefs import default_output_paths

DEFAULT_WAV_DIR, DEFAULT_OUTPUT = default_output_paths(documents_accessible=True)
FALLBACK_WAV_DIR, FALLBACK_OUTPUT = default_output_paths(documents_accessible=False)
SEARCH_PLACEHOLDER = "Search playlists…"
TRACK_SEARCH_PLACEHOLDER = "Search tracks…"
SCANNING_BIT_DEPTH = "Scanning bit depth…"

# Tracklist playlist-group highlight when any of its tracks are selected
# (darker than the Treeview selection blue used on leaf rows).
TRACKLIST_HEADER_TAG = "playlist_header"
TRACKLIST_HEADER_SELECTED_TAG = "playlist_header_selected"
TRACKLIST_HEADER_SELECTED_BG = "#0a2f55"
TRACKLIST_HEADER_SELECTED_FG = "#ffffff"
APP_LOGO_NAME = "rpc-logo-white.png"
XML_SEARCH_TIMEOUT_SECONDS = 30.0
APP_WINDOW_ICON_NAME = "rpc-logo-white-256.png"
BIT_DEPTH_24_TOOLTIP = (
    "This is a maximum, not a target. "
    "16-bit tracks are not upconverted to 24-bit."
)
SAMPLE_RATE_48_TOOLTIP = (
    "This is a maximum, not a target. "
    "44.1 kHz tracks are not upconverted to 48 kHz."
)
BIT_DEPTH_LABELS = {"16": "16-bit", "24": "24-bit"}
SAMPLE_RATE_LABELS = {"44100": "44.1 kHz", "48000": "48 kHz"}
BIT_DEPTH_FROM_LABEL = {label: value for value, label in BIT_DEPTH_LABELS.items()}
SAMPLE_RATE_FROM_LABEL = {label: value for value, label in SAMPLE_RATE_LABELS.items()}
CANCELLED_STATUS_CLEAR_MS = 3000
SEARCH_DEBOUNCE_MS = 200
WAV_DIR_VALIDATE_DEBOUNCE_MS = 300
