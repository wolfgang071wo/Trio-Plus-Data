"""Put a WAV file into a trained part, the way the pedal stores a recording."""

from __future__ import annotations

import array
import sys
import wave
from pathlib import Path

from .tlsd import SAMPLE_RATE, Part, TlsdError


def read_wav_mono16(path: str | Path) -> bytes:
    """Read a PCM WAV (8/16/24/32 bit, mono or stereo, any rate) as 16-bit mono at 44.1 kHz."""
    try:
        with wave.open(str(path), "rb") as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
    except (wave.Error, EOFError) as e:
        raise TlsdError(f"{path}: kein lesbares PCM-WAV ({e})") from None

    if width == 1:
        vals = [(b - 128) << 8 for b in raw]
    elif width == 2:
        a = array.array("h")
        a.frombytes(raw[: len(raw) - len(raw) % 2])
        if sys.byteorder == "big":
            a.byteswap()
        vals = a
    elif width in (3, 4):
        step = width
        vals = [int.from_bytes(raw[i + step - 2:i + step], "little", signed=True) for i in range(0, len(raw) - step + 1, step)]
    else:
        raise TlsdError(f"{path}: {width * 8}-Bit-WAV wird nicht unterstützt")

    if ch > 1:
        vals = [sum(vals[i:i + ch]) // ch for i in range(0, len(vals) - ch + 1, ch)]
    if rate != SAMPLE_RATE:
        vals = _resample(vals, rate, SAMPLE_RATE)
    out = array.array("h", (max(-32768, min(32767, int(v))) for v in vals))
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


def _resample(vals, src: int, dst: int) -> list[int]:
    n = len(vals)
    if n == 0:
        return []
    m = int(n * dst / src)
    ratio = src / dst
    out = []
    for i in range(m):
        x = i * ratio
        j = int(x)
        f = x - j
        a = vals[j]
        b = vals[j + 1] if j + 1 < n else a
        out.append(a + (b - a) * f)
    return out


def fit(pcm: bytes, length: int, loop: bool = False) -> bytes:
    """Trim or extend 16-bit PCM to exactly `length` bytes."""
    if len(pcm) >= length:
        return pcm[:length]
    if loop and pcm:
        reps = length // len(pcm) + 1
        return (pcm * reps)[:length]
    return pcm + b"\x00" * (length - len(pcm))


def mix(a: bytes, b: bytes, gain_b: float) -> bytes:
    x = array.array("h")
    x.frombytes(a)
    y = array.array("h")
    y.frombytes(b)
    if sys.byteorder == "big":
        x.byteswap()
        y.byteswap()
    out = array.array("h", (max(-32768, min(32767, int(p + q * gain_b))) for p, q in zip(x, y)))
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


def import_wav(part: Part, pcm: bytes, mode: str = "replace", gain: float = 1.0, loop: bool = False) -> tuple[Part, dict]:
    """Return a new part with `pcm` as its recording.

    replace: the WAV becomes the only layer (main track, overdub cleared,
             layer count 1), exactly like a fresh recording on the pedal.
    mix:     the WAV is mixed into the track the pedal currently plays;
             the other track and the layer count stay as they are.
    """
    tracks = part.tracks()
    info = {"wav_seconds": round(len(pcm) / 2 / SAMPLE_RATE, 2)}
    if mode == "replace":
        main = fit(pcm, len(tracks["main"]), loop)
        if gain != 1.0:
            main = mix(b"\x00" * len(main), main, gain)
        new = part.with_tracks(main, b"\x00" * len(tracks["overdub"]), layers=1)
        info.update(track="main", part_seconds=round(len(main) / 2 / SAMPLE_RATE, 2))
    elif mode == "mix":
        target = part.current_track
        if target is None:
            raise TlsdError(f"Part {part.number} hat noch keine Aufnahme zum Mischen, nimm 'replace'")
        cur = tracks[target]
        mixed = mix(cur, fit(pcm, len(cur), loop), gain)
        new_tracks = dict(tracks)
        new_tracks[target] = mixed
        new = part.with_tracks(new_tracks["main"], new_tracks["overdub"], layers=part.layers)
        info.update(track=target, part_seconds=round(len(cur) / 2 / SAMPLE_RATE, 2))
    else:
        raise TlsdError("mode must be replace or mix")
    info["truncated"] = info["wav_seconds"] > info["part_seconds"] + 0.01
    return new, info
