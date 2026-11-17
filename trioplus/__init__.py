"""Library and command line for DigiTech TRIO+ song files (.tlsd)."""

__version__ = "0.6.0"

from .tlsd import Part, Song, TlsdError, split_tracks, trim_silence  # noqa: E402,F401
