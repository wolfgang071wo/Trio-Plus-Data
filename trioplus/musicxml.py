"""MusicXML lead sheet (chord symbols, time signatures, tempo, part marks).

Meant for JJazzLab (File > Import songs), which takes the time signatures and
chord symbols from MusicXML and builds its own band around them. MuseScore,
Band-in-a-Box and other notation programs read the same file.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from .analysis import Analysis, analyse, apply_edits, part_seconds
from .tlsd import Song, TlsdError

# pitch class -> (step, alter), flats where they read more naturally
_SPELL = [("C", 0), ("C", 1), ("D", 0), ("E", -1), ("E", 0), ("F", 0),
          ("F", 1), ("G", 0), ("A", -1), ("A", 0), ("B", -1), ("B", 0)]
# chord kind code -> (MusicXML kind, text shown)
_KIND = {0: ("major", ""), 2: ("major-seventh", "maj7"), 4: ("minor", "m"), 6: ("minor-seventh", "m7"),
         8: ("diminished", "dim"), 10: ("dominant", "7"), 14: ("suspended-fourth", "sus4"),
         15: ("power", "5")}
# key signature (fifths) for a major key pitch class
_FIFTHS = {0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: 6, 1: -5, 8: -4, 3: -3, 10: -2, 5: -1}


def _fifths(an: Analysis) -> int:
    """Key signature from the pedal's key estimate, read as major or relative
    minor depending on the first chord; no signature when unclear."""
    if an.key is None or not an.chords:
        return 0
    first = an.chords[0]
    if first.root == an.key and first.kind in (0, 2, 10, 14, 15):
        return _FIFTHS[an.key]
    if first.root == an.key and first.kind in (4, 6):
        return _FIFTHS[(an.key + 3) % 12]
    return 0


def _harmony(root: int, kind: int) -> str:
    step, alter = _SPELL[root % 12]
    xkind, text = _KIND.get(kind, ("major", ""))
    alt = f"<root-alter>{alter}</root-alter>" if alter else ""
    return (f"<harmony><root><root-step>{step}</root-step>{alt}</root>"
            f'<kind text="{text}">{xkind}</kind></harmony>')


def _rest(beats: int) -> str:
    return f"<note><rest/><duration>{beats}</duration><voice>1</voice></note>"


def lead_sheet(parts: list[tuple[str, Analysis, float, int]], title: str) -> bytes:
    """parts: [(label, analysis, tempo, repeats)] in playing order."""
    measures = []
    bar_no = 0
    last_time = None
    for label, an, tempo, repeats in parts:
        bpb = an.beats_per_bar
        for rep in range(repeats):
            for bar in range(an.bars):
                bar_no += 1
                head = []
                if bar == 0 and rep == 0:
                    attrs = "<divisions>1</divisions>" if bar_no == 1 else ""
                    if bar_no == 1:
                        attrs += f"<key><fifths>{_fifths(an)}</fifths></key>"
                    if bpb != last_time:
                        attrs += f"<time><beats>{bpb}</beats><beat-type>4</beat-type></time>"
                        last_time = bpb
                    if attrs:
                        head.append(f"<attributes>{attrs}</attributes>")
                    head.append('<direction placement="above"><direction-type>'
                                f"<rehearsal>{escape(label)}</rehearsal></direction-type>"
                                "<direction-type><metronome><beat-unit>quarter</beat-unit>"
                                f"<per-minute>{tempo:.0f}</per-minute></metronome></direction-type>"
                                f'<sound tempo="{tempo:.1f}"/></direction>')
                start = bar * bpb
                cuts = sorted({c.beat - start for c in an.chords if start <= c.beat < start + bpb} | {0, bpb})
                at = {c.beat - start: c for c in an.chords if start <= c.beat < start + bpb}
                body = []
                for a, b in zip(cuts, cuts[1:]):
                    c = at.get(a)
                    if c is None and a == 0 and bar == 0:
                        # the part starts mid-chord: restate the chord that is sounding
                        prev = [x for x in an.chords if x.beat <= start]
                        c = prev[-1] if prev else (an.chords[0] if an.chords else None)
                    if c is not None:
                        body.append(_harmony(c.root, c.kind))
                    body.append(_rest(b - a))
                measures.append(f'<measure number="{bar_no}">' + "".join(head + body) + "</measure>")
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
           '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
           '"http://www.musicxml.org/dtds/partwise.dtd">\n'
           '<score-partwise version="4.0">'
           f"<work><work-title>{escape(title)}</work-title></work>"
           "<identification><encoding><software>TrioTool</software></encoding></identification>"
           '<part-list><score-part id="P1"><part-name>Guitar</part-name></score-part></part-list>'
           '<part id="P1">' + "".join(measures) + "</part></score-partwise>\n")
    return xml.encode("utf-8")


def song_lead_sheet(song: Song, title: str, repeats: int = 1, section: int | None = None,
                    edits: dict | None = None) -> bytes:
    """Lead sheet for all trained parts of a song (chord edits applied)."""
    parts = []
    for p in song.parts:
        if not p.trained:
            continue
        try:
            an = analyse(p, section)
        except TlsdError:
            continue
        an = apply_edits(an, (edits or {}).get(p.number) or (edits or {}).get(str(p.number)))
        if not an.chords:
            continue
        parts.append((f"Part {p.number}", an, an.tempo_for(part_seconds(p)), repeats))
    if not parts:
        raise TlsdError("Kein Part mit Akkord-Analyse in diesem Song")
    return lead_sheet(parts, title)
