"""Export a whole song for a DAW: one guitar WAV and one MIDI file that line up.

All trained parts are placed one after another (each repeated if asked).
The WAV holds the track the pedal plays for each part (silence for parts
without a recording). The MIDI file has chords, bass and stand-in drums,
with the tempo and time signature of every part, so both files start at
bar 1 and stay in sync in any DAW.
"""

from __future__ import annotations

import wave
from pathlib import Path

from .analysis import analyse, apply_edits, part_seconds
from .midi import build_song_midi
from .tlsd import SAMPLE_RATE, SAMPLE_WIDTH, Song, TlsdError

README = """{name} - Song-Paket aus TRIO Tool {version}

Gitarre.wav                 Die Spur, die das Pedal spielt, alle Parts
                            hintereinander.
Song.mid                    Akkorde, Bass und Ersatz-Drums (nicht die
                            Original-Drums des Pedals), mit Tempo- und
                            Taktwechsel für jeden Part. Mit Taktraster.
Song (festes Tempo).mid     Dieselben Noten bei festen 120 BPM, jede Note an
                            ihrer absoluten Zeit. Läuft in jeder DAW synchron
                            zur Gitarre, auch ohne Tempokarte, aber ohne
                            passendes Taktraster.

Alle Dateien beginnen am Anfang und laufen synchron.

Ardour: Neue Session mit 44,1 kHz anlegen, dann Sitzung > Importieren,
Position "Sitzungsanfang". Gitarre.wav importieren. Dann Song.mid mit der
Option "Use MIDI Tempo Map" importieren, damit das Taktraster passt.
Stürzt Ardour dabei ab (in Ardour 9.x bei manchen Dateien bekannt), stattdessen
"Song (festes Tempo).mid" ohne diese Option importieren und das Session-Tempo
auf 120 BPM lassen. Den MIDI-Spuren ein Instrument zuweisen (z. B. ACE
Reasonable Synth, für die Drums ein General-MIDI-Instrument).

Andere DAWs (Reaper, Ableton, Bitwig, Studio One ...): Gitarre.wav und
Song.mid an den Anfang ziehen und die Tempoinformation der MIDI-Datei
übernehmen; sonst "Song (festes Tempo).mid" bei 120 BPM verwenden.

Parts:
{parts}
"""

README_EN = """{name} - song package from TRIO Tool {version}

Guitar.wav                  The track the pedal plays, all parts one after
                            another.
Song.mid                    Chords, bass and stand-in drums (not the pedal's
                            own drums), with tempo and time signature changes
                            for every part. With a bar grid.
Song (fixed tempo).mid      The same notes at a fixed 120 BPM, every note at
                            its absolute time. Stays in sync with the guitar
                            in any DAW, even without a tempo map, but without
                            a matching bar grid.

All files start at the beginning and run in sync.

Ardour: create a new session at 44.1 kHz, then Session > Import, position
"session start". Import Guitar.wav. Then import Song.mid with the option
"Use MIDI Tempo Map" so the bar grid matches. If Ardour crashes doing this
(known with some files in Ardour 9.x), import "Song (fixed tempo).mid"
without that option instead and leave the session tempo at 120 BPM. Give the
MIDI tracks an instrument (e.g. ACE Reasonable Synth, and a General MIDI
instrument for the drums).

Other DAWs (Reaper, Ableton, Bitwig, Studio One ...): drag Guitar.wav and
Song.mid to the start and accept the MIDI file's tempo information;
otherwise use "Song (fixed tempo).mid" at 120 BPM.

Parts:
{parts}
"""

NAMES = {
    "de": {"wav": "Gitarre.wav", "fixed": "Song (festes Tempo).mid", "readme": "LIESMICH.txt",
           "skipped": "übersprungen", "bars": "Takte", "text": README},
    "en": {"wav": "Guitar.wav", "fixed": "Song (fixed tempo).mid", "readme": "README.txt",
           "skipped": "skipped", "bars": "bars", "text": README_EN},
}


def song_package(song: Song, outdir: Path, name: str, repeats: int = 1, drums: bool = True,
                 section: int | None = None, edits: dict | None = None, version: str = "",
                 lang: str = "de") -> dict:
    """Write Gitarre.wav, Song.mid and LIESMICH.txt to outdir (English names with lang="en").

    edits: {part_number: [{"i", "root", "kind"}, ...]} chord changes per part.
    """
    L = NAMES.get(lang, NAMES["de"])
    entries = []
    pcm_all = bytearray()
    for p in song.parts:
        if not p.trained:
            continue
        try:
            an = analyse(p, section)
        except TlsdError as e:
            entries.append({"part": p.number, "skipped": str(e)})
            continue
        an = apply_edits(an, (edits or {}).get(p.number) or (edits or {}).get(str(p.number)))
        seconds = part_seconds(p)
        n = round(seconds * SAMPLE_RATE) * SAMPLE_WIDTH
        track = p.current_track or ("main" if p.track_has_audio("main") else None)
        pcm = p.tracks()[track] if track else b""
        pcm = (pcm + b"\x00" * n)[:n]
        tempo = an.tempo_for(seconds)
        genre = p.band()["genre"] if drums else None
        entries.append({"part": p.number, "label": f"Part {p.number}", "an": an, "tempo": tempo,
                        "genre": genre, "seconds": seconds, "repeats": repeats,
                        "audio": track or "-"})
        for _ in range(repeats):
            pcm_all += pcm
    playable = [e for e in entries if "an" in e]
    if not playable:
        raise TlsdError("Kein Part mit Akkord-Analyse in diesem Song")

    outdir.mkdir(parents=True, exist_ok=True)
    wav_path = outdir / L["wav"]
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(pcm_all))
    spec = [(e["label"], e["an"], e["repeats"], e["tempo"], e["genre"]) for e in playable]
    mid_path = outdir / "Song.mid"
    mid_path.write_bytes(build_song_midi(spec, name))
    (outdir / L["fixed"]).write_bytes(build_song_midi(spec, name, fixed_tempo=120.0))

    lines = []
    t = 0.0
    for e in entries:
        if "an" not in e:
            lines.append(f"  Part {e['part']}: {L['skipped']} ({e['skipped']})")
            continue
        an = e["an"]
        lines.append(f"  {t:7.2f} s  Part {e['part']}: {an.bars} {L['bars']} {an.beats_per_bar}/4, "
                     f"{e['tempo']:.2f} BPM, x{e['repeats']}: " + " ".join(c.name for c in an.chords))
        t += e["seconds"] * e["repeats"]
    (outdir / L["readme"]).write_text(
        L["text"].format(name=name, version=version, parts="\n".join(lines)), encoding="utf-8")
    return {"folder": str(outdir), "wav": str(wav_path), "midi": str(mid_path),
            "midi_fixed": str(outdir / L["fixed"]),
            "seconds": round(len(pcm_all) / SAMPLE_WIDTH / SAMPLE_RATE, 3),
            "parts": [{k: v for k, v in e.items() if k != "an"} for e in entries]}
