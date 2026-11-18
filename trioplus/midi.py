"""Write the pedal's chord analysis as a Standard MIDI File.

Track 1: tempo, time signature and a marker per chord.
Track 2: chords (channel 1, electric piano), held for each segment.
Track 3: bass (channel 2, finger bass), the root on every beat.
Track 4: stand-in drums for the part's genre (channel 10), see drums.py.
"""

from __future__ import annotations

from pathlib import Path

from .analysis import Analysis, analyse, part_seconds
from .tlsd import Part, Song, TlsdError

PPQ = 480


def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


class _Track:
    def __init__(self):
        self.events: list[tuple[int, int, bytes]] = []  # (tick, order, data)

    def add(self, tick: int, data: bytes, order: int = 1):
        self.events.append((tick, order, data))

    def meta(self, tick: int, kind: int, payload: bytes):
        self.add(tick, bytes([0xFF, kind]) + _vlq(len(payload)) + payload, order=0)

    def note(self, start: int, length: int, ch: int, key: int, vel: int):
        self.add(start, bytes([0x90 | ch, key, vel]), order=2)
        self.add(start + length, bytes([0x80 | ch, key, 0]), order=1)

    def chunk(self) -> bytes:
        body = bytearray()
        now = 0
        for tick, _, data in sorted(self.events, key=lambda e: (e[0], e[1])):
            body += _vlq(tick - now) + data
            now = tick
        body += b"\x00\xff\x2f\x00"
        return b"MTrk" + len(body).to_bytes(4, "big") + bytes(body)


def _chord_keys(root: int, intervals) -> list[int]:
    base = 48 + root  # C3 .. B3
    if base > 54:
        base -= 12
    return [base + 12 + i if i < 0 else base + i for i in intervals]


def _bass_key(root: int) -> int:
    return 28 + (root - 4) % 12  # E1 .. D#2


def build_midi(segments: list[tuple[str, Analysis, int]], tempo: float, title: str,
               genre: int | None = None) -> bytes:
    """segments: (label, analysis, repeats) at one tempo; see build_song_midi."""
    return build_song_midi([(label, an, reps, tempo, genre) for label, an, reps in segments], title)


def _key_signature(an: Analysis) -> bytes | None:
    if an.key is None:
        return None
    first = an.chords[0]
    minor = first.root == an.key and first.kind in (4, 6)
    tonic = (an.key + 3) % 12 if minor else an.key  # relative major
    sharps = {0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: 6, 1: -5, 8: -4, 3: -3, 10: -2, 5: -1}[tonic]
    return bytes([sharps & 0xFF, 1 if minor else 0])


def build_song_midi(parts: list[tuple[str, Analysis, int, float, int | None]], title: str,
                    fixed_tempo: float | None = None) -> bytes:
    """parts: (label, analysis, repeats, tempo, genre or None) played one after another.

    Each part gets its own tempo and time signature, so a part lasts exactly
    as long as its loop and the file lines up with audio placed back to back.
    With fixed_tempo the file has a single tempo and every note sits at its
    absolute time instead, for DAWs that ignore or mishandle tempo maps.
    """
    from .drums import events as drum_events, genre_name

    with_drums = any(g is not None for *_, g in parts)
    meta, chords, bass, drums = _Track(), _Track(), _Track(), _Track()
    meta.meta(0, 0x03, title.encode("ascii", "replace"))
    chords.meta(0, 0x03, b"Chords")
    chords.add(0, bytes([0xC0, 4]), order=0)  # electric piano
    chords.add(0, bytes([0xB0, 7, 64]), order=0)  # channel volume: sit under the guitar
    bass.meta(0, 0x03, b"Bass")
    bass.add(0, bytes([0xC1, 33]), order=0)  # finger bass
    bass.add(0, bytes([0xB1, 7, 84]), order=0)
    genres = sorted({genre_name(g) for *_, g in parts if g is not None})
    drums.meta(0, 0x03, f"Drums ({', '.join(genres)}, stand-in)".encode("ascii", "replace"))
    drums.add(0, bytes([0xB9, 7, 72]), order=0)

    tick = 0
    seconds = 0.0
    last_ts = last_us = last_key = None
    if fixed_tempo:
        meta.meta(0, 0x51, round(60_000_000 / fixed_tempo).to_bytes(3, "big"))
    for label, an, repeats, tempo, genre in parts:
        if fixed_tempo:
            # beats of this part -> ticks at the fixed tempo, from absolute time
            scale = fixed_tempo / tempo
        else:
            us = round(60_000_000 / tempo)
            if us != last_us:
                meta.meta(tick, 0x51, us.to_bytes(3, "big"))
                last_us = us
            scale = 1.0
        here = round(seconds * fixed_tempo / 60 * PPQ) if fixed_tempo else tick
        ts = (an.beats_per_bar, 4)
        if ts != last_ts and not fixed_tempo:
            meta.meta(here, 0x58, bytes([ts[0], 2, 24, 8]))
            last_ts = ts
        key = _key_signature(an)
        if key is not None and key != last_key:
            meta.meta(here, 0x59, key)
            last_key = key
        meta.meta(here, 0x07, label.encode("ascii", "replace"))
        for _ in range(repeats):
            if fixed_tempo:
                base = round(seconds * fixed_tempo / 60 * PPQ)
            else:
                base = tick
            at = lambda beat: base + round(beat * PPQ * scale)  # noqa: E731
            spans = an.chords + [None]
            for c, nxt in zip(an.chords, spans[1:]):
                start, end = at(c.beat), at(nxt.beat if nxt else an.beats)
                meta.meta(start, 0x06, c.name.encode("ascii", "replace"))
                for k in _chord_keys(c.root, c.intervals):
                    chords.note(start, end - start - 10, 0, k, 80)
                for b in range(c.beat, nxt.beat if nxt else an.beats):
                    s0, s1 = at(b), at(b + 1)
                    bass.note(s0, max(10, s1 - s0 - 30), 1, _bass_key(c.root), 96)
            if genre is not None:
                for beat, note, vel, length in drum_events(genre, an.bars, an.beats_per_bar):
                    t = at(beat)
                    drums.note(t, max(10, round(length * PPQ * scale) - 10), 9, note, vel)
            tick += an.beats * PPQ
            seconds += an.beats * 60.0 / tempo
    tracks = [meta.chunk(), chords.chunk(), bass.chunk()] + ([drums.chunk()] if with_drums else [])
    header = b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") + \
        len(tracks).to_bytes(2, "big") + PPQ.to_bytes(2, "big")
    return header + b"".join(tracks)


def part_midi(part: Part, title: str, section: int | None = None, repeats: int = 1,
              drums: bool = True, edits: list | None = None) -> tuple[bytes, dict]:
    from .analysis import apply_edits

    an = apply_edits(analyse(part, section), edits)
    tempo = an.tempo_for(part_seconds(part))
    genre = part.band()["genre"] if drums else None
    data = build_midi([(f"Part {part.number}", an, repeats)], tempo, title, genre)
    return data, {"part": part.number, "bars": an.bars, "time": f"{an.beats_per_bar}/4", "key": an.key_name,
                  "tempo": round(tempo, 2), "chords": [c.name for c in an.chords], "section": an.section}


def write_song_midi(song: Song, outdir: Path, stem: str, section: int | None = None,
                    repeats: int = 1, drums: bool = True) -> list[dict]:
    outdir.mkdir(parents=True, exist_ok=True)
    results = []
    for p in song.parts:
        if not p.trained:
            continue
        try:
            data, info = part_midi(p, f"{song.name} Part {p.number}", section, repeats, drums)
        except TlsdError as e:
            results.append({"part": p.number, "error": str(e)})
            continue
        out = outdir / f"{stem} Part {p.number}.mid"
        out.write_bytes(data)
        info["path"] = str(out)
        results.append(info)
    return results
