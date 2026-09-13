"""Shared CLI/GUI error type for the Rekordbox converter."""


class CliError(Exception):
    """Fatal error with a user-facing message."""


class CancelledError(Exception):
    """Conversion or prepare aborted because cancel was requested.

    Not a CliError: cancel is not a fatal user-facing failure, and
    ``except CliError`` must not treat it as one.
    """
