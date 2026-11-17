"""Remember the state of a card and report what the pedal saved since.

A snapshot holds a short hash of every part slot (both settings blocks) and
the selected-song byte. Snapshots are kept per card path in the user's
config folder, so the TRIO Tool can say "since the last time: song 6 parts
2 and 3 are new" or "nothing was saved".
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from .card import NUM_SLOTS, NUM_SONGS, Card

SELECTED_SONG = 0xC810  # 0-based index of the song selected on the pedal


def config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "TrioTool"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "trioplus"


def snapshot(card: Card) -> dict:
    slots = []
    for i in range(NUM_SLOTS):
        s = card.slot(i)
        h = hashlib.sha1(s.block_a + s.block_b).hexdigest()[:16]
        slots.append({"h": h, "present": s.present})
    sel = card._m[SELECTED_SONG]
    return {"time": time.time(), "selected": sel + 1 if sel < NUM_SONGS else None, "slots": slots}


def compare(old: dict | None, new: dict) -> dict:
    if not old:
        return {"first": True}
    added, changed, removed = [], [], []
    for i, (a, b) in enumerate(zip(old["slots"], new["slots"])):
        label = {"song": i // 5 + 1, "part": i % 5 + 1}
        if a["h"] == b["h"]:
            continue
        if b["present"] and not a["present"]:
            added.append(label)
        elif a["present"] and not b["present"]:
            removed.append(label)
        else:
            changed.append(label)
    return {
        "first": False,
        "since": old.get("time"),
        "added": added, "changed": changed, "removed": removed,
        "selected_before": old.get("selected"), "selected_now": new.get("selected"),
        "nothing": not (added or changed or removed) and old.get("selected") == new.get("selected"),
    }


def check(card: Card, store: Path | None = None) -> dict:
    """Compare with the stored snapshot for this card path, then store the new one."""
    store = store or config_dir()
    store.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(str(card.path.resolve()).lower().encode()).hexdigest()[:16]
    f = store / f"card-{key}.json"
    try:
        old = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = None
    new = snapshot(card)
    report = compare(old, new)
    try:
        f.write_text(json.dumps(new), encoding="utf-8")
    except OSError:
        pass
    return report
