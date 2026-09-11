"""Shared CLI/GUI error type for the Rekordbox converter."""


class CliError(Exception):
    """Fatal error with a user-facing message."""


class CancelledError(CliError):
    """Conversion or prepare aborted because cancel was requested."""
