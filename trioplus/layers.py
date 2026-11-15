"""Isolate the most recent overdub layer of a part.

The pedal keeps two tracks per part and alternates between them: every
overdub writes "older mix + new playing" into the other track and plays that
one. The track not being played is therefore the state one layer earlier
(the undo state). Subtracting it, scaled by a fitted level, leaves the layer
that was played last.

With a layer count of 1 but audio in the overdub track, an overdub was made
and then undone; the result is that undone layer.
"""

from __future__ import annotations

import array
import math
import operator
import sys
from dataclasses import dataclass

from .tlsd import Part, TlsdError

MAX_LAG = 16
FIT_WINDOW = 400_000  # samples used for the lag search


def _samples(pcm: bytes) -> array.array:
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if sys.byteorder == "big":
        a.byteswap()
    return a


def _dot(x, y) -> float:
    return math.fsum(map(operator.mul, x, y))


@dataclass
class LayerResult:
    pcm: bytes
    newer: str
    older: str
    gain: float
    lag: int
    undone: bool
    level_ratio: float  # rms(result) / rms(newer)


def last_layer(part: Part) -> LayerResult:
    tracks = part.tracks()
    if not (any(tracks["main"]) and any(tracks["overdub"])):
        raise TlsdError(f"Part {part.number} hat nur eine Ebene, da gibt es nichts herauszurechnen")
    if part.layers == 1:
        newer, older, undone = "overdub", "main", True
    else:
        newer = part.current_track or "main"
        older = "overdub" if newer == "main" else "main"
        undone = False

    y = _samples(tracks[newer])
    x = _samples(tracks[older])
    n = min(len(x), len(y))

    # lag search on a window from the middle of the part
    w = min(FIT_WINDOW, n - 2 * MAX_LAG)
    s0 = max(MAX_LAG, (n - w) // 2)
    ys = y[s0:s0 + w]
    best_lag, best = 0, -math.inf
    for lag in range(-MAX_LAG, MAX_LAG + 1):
        score = _dot(x[s0 + lag:s0 + lag + w], ys)
        if score > best:
            best_lag, best = lag, score

    # least-squares level over the whole overlap
    if best_lag >= 0:
        xa, ya = x[best_lag:n], y[:n - best_lag]
    else:
        xa, ya = x[:n + best_lag], y[-best_lag:n]
    sxx = _dot(xa, xa)
    gain = _dot(xa, ya) / sxx if sxx else 0.0

    out = array.array("h", y)  # keep full length of the newer track
    offset = 0 if best_lag >= 0 else -best_lag
    for i, (xv, yv) in enumerate(zip(xa, ya)):
        v = yv - gain * xv
        out[i + offset] = -32768 if v < -32768 else 32767 if v > 32767 else int(v)

    rms_y = math.sqrt(_dot(y, y) / len(y)) if len(y) else 0.0
    rms_o = math.sqrt(_dot(out, out) / len(out)) if len(out) else 0.0
    if sys.byteorder == "big":
        out.byteswap()
    return LayerResult(out.tobytes(), newer, older, round(gain, 3), best_lag, undone,
                       round(rms_o / rms_y, 3) if rms_y else 0.0)
