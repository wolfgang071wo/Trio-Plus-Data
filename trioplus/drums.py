"""Stand-in drum grooves for the TRIO+ genres.

The pedal generates its drums internally; they are not in the song files.
These patterns are original, simple grooves in the spirit of each genre, so
that MIDI exports and previews have a drum track that follows the part's
time signature, length and tempo. General MIDI drum notes, 16th-note grid.
"""

from __future__ import annotations

KICK, RIM, SNARE, CLAP, HAT, PEDAL_HAT, OPEN_HAT, CRASH, RIDE = 36, 37, 38, 39, 42, 44, 46, 49, 51
TOM_HI, TOM_MID, TOM_LO = 50, 47, 45

# genre order as on the pedal's GENRE knob; 0..2 confirmed by test files
GENRES = ["Blues", "R&B", "Rock", "Alt-Rock", "Metal", "Pop", "E-Pop", "Hip-Hop",
          "Country", "Folk", "Latin", "Jazz"]


def _hits(steps, note, vel):
    return [(s, note, vel) for s in steps]


# One bar of 4/4 in 16ths: (hits, swing). swing = position of the off-beat 8th
# inside the beat (0.5 straight, 0.667 triplet shuffle).
PATTERNS_44 = {
    "Blues": (_hits([0, 8], KICK, 100) + _hits([4, 12], SNARE, 105)
              + _hits(range(0, 16, 2), RIDE, 70) + _hits([4, 12], PEDAL_HAT, 60), 0.667),
    "R&B": (_hits([0, 6, 10], KICK, 100) + _hits([4, 12], SNARE, 100)
            + _hits(range(0, 16, 1), HAT, 55) + _hits([15], SNARE, 45), 0.58),
    "Rock": (_hits([0, 8, 10], KICK, 105) + _hits([4, 12], SNARE, 110)
             + _hits(range(0, 16, 2), HAT, 75), 0.5),
    "Alt-Rock": (_hits([0, 3, 8, 11], KICK, 105) + _hits([4, 12], SNARE, 112)
                 + _hits(range(0, 14, 2), HAT, 78) + _hits([14], OPEN_HAT, 80), 0.5),
    "Metal": (_hits(range(0, 16, 1), KICK, 95) + _hits([4, 12], SNARE, 120)
              + _hits(range(0, 16, 2), RIDE, 85), 0.5),
    "Pop": (_hits([0, 7, 8], KICK, 100) + _hits([4, 12], SNARE, 100) + _hits([4, 12], CLAP, 70)
            + _hits(range(0, 16, 2), HAT, 70), 0.5),
    "E-Pop": (_hits([0, 4, 8, 12], KICK, 110) + _hits([4, 12], CLAP, 100)
              + _hits([2, 6, 10, 14], OPEN_HAT, 70) + _hits(range(0, 16, 1), HAT, 40), 0.5),
    "Hip-Hop": (_hits([0, 7, 10], KICK, 110) + _hits([4, 12], SNARE, 105)
                + _hits(range(0, 16, 2), HAT, 65), 0.6),
    "Country": (_hits([0, 8], KICK, 95) + _hits([4, 12], SNARE, 95)
                + _hits([2, 6, 10, 14], RIM, 55) + _hits(range(0, 16, 2), HAT, 60), 0.55),
    "Folk": (_hits([0, 8], KICK, 85) + _hits([4, 12], RIM, 75) + _hits(range(0, 16, 4), HAT, 55), 0.5),
    "Latin": (_hits([0, 3, 4, 7, 8, 11, 12, 15], KICK, 85) + _hits([0, 6, 12], RIM, 85)
              + _hits(range(0, 16, 2), HAT, 60), 0.5),
    "Jazz": (_hits([0, 4, 6, 8, 12, 14], RIDE, 80) + _hits([4, 12], PEDAL_HAT, 65)
             + _hits([0, 4, 8, 12], KICK, 35), 0.667),
}

# One bar of 3/4 in 16ths (12 steps)
WALTZ = _hits([0], KICK, 100) + _hits([4, 8], SNARE, 80) + _hits(range(0, 12, 2), HAT, 60)
JAZZ_WALTZ = _hits([0], KICK, 70) + _hits([0, 4, 6, 8], RIDE, 80) + _hits([4, 8], PEDAL_HAT, 60)


def genre_name(code: int) -> str:
    return GENRES[code] if 0 <= code < len(GENRES) else "Rock"


def events(genre: int, bars: int, beats_per_bar: int, fill: bool = True, crash: bool = True):
    """(beat, note, velocity, length_in_beats) for a whole part."""
    name = genre_name(genre)
    if beats_per_bar == 3:
        bar = JAZZ_WALTZ if name in ("Jazz", "Blues") else WALTZ
        swing = 0.667 if name in ("Jazz", "Blues") else 0.5
        steps = 12
    else:
        bar, swing = PATTERNS_44.get(name, PATTERNS_44["Rock"])
        steps = 16

    def when(step: int) -> float:
        beat, sub = divmod(step, 4)
        frac = {0: 0.0, 1: 0.25, 2: swing, 3: 0.75 if swing == 0.5 else (1 + swing) / 2}[sub]
        return beat + frac

    out = []
    for b in range(bars):
        start = b * beats_per_bar
        last = fill and bars > 1 and b == bars - 1
        fill_from = steps - 4  # last beat of the bar
        for step, note, vel in bar:
            if last and step >= fill_from:
                continue
            out.append((start + when(step), note, vel, 0.25))
        if last:
            toms = [SNARE, SNARE, TOM_HI, TOM_MID] if steps == 16 else [SNARE, TOM_HI, TOM_MID, TOM_LO]
            for i, note in enumerate(toms):
                out.append((start + when(fill_from + i), note, 90 + 6 * i, 0.25))
            out.append((start + when(fill_from), KICK, 90, 0.25))
    if crash and out:
        out.append((0.0, CRASH, 100, 1.0))
    return sorted(out)
