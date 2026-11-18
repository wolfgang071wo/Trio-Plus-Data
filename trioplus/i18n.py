"""English versions of the German messages the app shows.

The library raises German messages (the app started German-only). The GUI
asks for a language per request; tr() turns a German message into English.
Unknown messages pass through unchanged.
"""

from __future__ import annotations

import re

LANGS = ("de", "en")

_EN = [
    (r"Part (\d+) ist nicht gelernt", r"Part \1 is not trained"),
    (r"Part (\d+): keine lesbare Analyse in Abschnitt (\d+)", r"Part \1: no readable analysis in section \2"),
    (r"Part (\d+): keine Akkorde gefunden", r"Part \1: no chords found"),
    (r"Akkord (\d+) gibt es in diesem Part nicht", r"Chord \1 does not exist in this part"),
    (r"ungültiger Akkord", "invalid chord"),
    (r"Kein Part mit Akkord-Analyse in diesem Song", "No part in this song has a chord analysis"),
    (r"Part (\d+) hat nur eine Ebene, da gibt es nichts herauszurechnen",
     r"Part \1 has only one layer, there is nothing to separate"),
    (r"kein lesbares PCM-WAV", "not a readable PCM WAV file"),
    (r"(\d+)-Bit-WAV wird nicht unterstützt", r"\1-bit WAV is not supported"),
    (r"Part (\d+) hat noch keine Aufnahme zum Mischen, nimm 'replace'",
     r"Part \1 has no recording to mix into yet, use 'replace'"),
    (r"Quelle ist nicht mehr geöffnet", "This source is no longer open"),
    (r"unbekannte Spur", "unknown track"),
    (r"Keine Akkorde gefunden", "No chords found"),
    (r"unbekanntes Exportformat", "unknown export format"),
    (r"Part (\S+) des gewählten Songs ist leer", r"Part \1 of the chosen song is empty"),
    (r"Wähle mindestens einen Part", "Choose at least one part"),
    (r"ungültige Anfrage", "invalid request"),
    (r"nicht gefunden", "not found"),
    (r"nicht erlaubt", "not allowed"),
    (r"Aktion fehlgeschlagen", "Action failed"),
    (r"In (.+) liegen keine \.tlsd-Dateien", r"There are no .tlsd files in \1"),
    (r"(.+) gibt es nicht", r"\1 does not exist"),
]


def norm(lang: str | None) -> str:
    return lang if lang in LANGS else "de"


def tr(msg: str, lang: str | None) -> str:
    """English version of a German message (unchanged for German or unknown text)."""
    if norm(lang) != "en" or not msg:
        return msg
    for pat, rep in _EN:
        new = re.sub(pat, rep, msg)
        if new != msg:
            return new
    return msg


# file names and dialog texts
WORDS = {
    "de": {"layer": "letzte Ebene", "with": "mit", "new_song": "Neuer Song",
           "pick_folder": "Ordner wählen", "pick_wav": "WAV-Datei wählen",
           "pick_trio": "TRIO+ Karte oder Song öffnen", "trio_files": "TRIO+ Dateien",
           "all_files": "Alle Dateien"},
    "en": {"layer": "last layer", "with": "with", "new_song": "New song",
           "pick_folder": "Choose a folder", "pick_wav": "Choose a WAV file",
           "pick_trio": "Open a TRIO+ card or song", "trio_files": "TRIO+ files",
           "all_files": "All files"},
}


def word(key: str, lang: str | None) -> str:
    return WORDS[norm(lang)][key]
