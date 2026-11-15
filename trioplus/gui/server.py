"""Local web interface for TRIO+ songs.

Starts a small HTTP server (standard library only) and opens the browser.
The same server can run on a Raspberry Pi and be reached from a tablet with
--host 0.0.0.0.

    python -m trioplus.gui [--port 8765] [--host 127.0.0.1] [--no-browser]
"""

from __future__ import annotations

import argparse
import array
import io
import json
import os
import re
import secrets
import string
import subprocess
import sys
import threading
import time
import webbrowser
from collections import OrderedDict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import __version__
from ..card import NUM_SONGS, Card
from ..tlsd import NUM_PARTS, SAMPLE_RATE, SAMPLE_WIDTH, TRACKS, Song, TlsdError

STATIC = Path(__file__).with_name("static")


def _wav_bytes(pcm: bytes) -> bytes:
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def _peaks(pcm: bytes, n: int) -> list[int]:
    """Max absolute amplitude per bucket, scaled to 0..1000."""
    if not pcm:
        return []
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    total = len(samples)
    n = max(1, min(n, total))
    out = []
    for i in range(n):
        chunk = samples[i * total // n:(i + 1) * total // n]
        if chunk:
            out.append(max(max(chunk), -min(chunk)) * 1000 // 32768)
        else:
            out.append(0)
    return out


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 _.@+-]", "_", name).strip() or "Song"


def _default_export_dir() -> Path:
    home = Path.home()
    docs = home / "Documents"
    return (docs if docs.is_dir() else home) / "TRIO Export"


def _find_cards() -> list[str]:
    """TRIOPLUS.DAT in the root of removable drives (Windows) or mounts."""
    found = []
    if os.name == "nt":
        roots = [f"{d}:\\" for d in string.ascii_uppercase[3:]]
    else:
        roots = []
        for base in ("/media", "/run/media", "/mnt", "/Volumes"):
            p = Path(base)
            if p.is_dir():
                for child in p.iterdir():
                    roots.append(str(child))
                    if child.is_dir():
                        roots.extend(str(c) for c in child.iterdir() if c.is_dir())
    for r in roots:
        try:
            cand = Path(r) / "TRIOPLUS.DAT"
            if cand.is_file():
                found.append(str(cand))
        except OSError:
            continue
    return found


def _dialog(kind: str) -> str:
    """Native file/folder dialog via tkinter; empty string if unavailable or cancelled."""
    try:
        import tkinter
        from tkinter import filedialog
    except Exception:
        return ""
    result = {"path": ""}

    def run():
        try:
            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            if kind == "folder":
                result["path"] = filedialog.askdirectory(title="Ordner wählen") or ""
            else:
                result["path"] = filedialog.askopenfilename(
                    title="TRIO+ Karte oder Song öffnen",
                    filetypes=[("TRIO+ Dateien", "*.DAT *.dat *.tlsd"), ("Alle Dateien", "*.*")],
                ) or ""
            root.destroy()
        except Exception:
            result["path"] = ""

    t = threading.Thread(target=run)
    t.start()
    t.join()
    return result["path"]


class Library:
    """Open cards and song files, with a small cache of decoded songs."""

    def __init__(self, export_dir: Path):
        self.lock = threading.RLock()
        self.sources: list[dict] = []
        self.export_dir = export_dir
        self._cache: OrderedDict[str, Song] = OrderedDict()
        self._info: dict[str, dict] = {}
        self._next = 1

    # sources ------------------------------------------------------------

    def open(self, path: str) -> list[str]:
        p = Path(path).expanduser()
        if not p.exists():
            raise TlsdError(f"{p} gibt es nicht")
        with self.lock:
            for s in self.sources:
                if Path(s["path"]) == p:
                    return [s["id"]]
            if p.is_dir():
                files = sorted(p.glob("*.tlsd")) + sorted(p.glob("*.TLSD"))
                if (p / "TRIOPLUS.DAT").is_file():
                    return self.open(str(p / "TRIOPLUS.DAT")) + [i for f in files for i in self.open(str(f))]
                if not files:
                    raise TlsdError(f"In {p} liegen keine .tlsd-Dateien")
                return [i for f in files for i in self.open(str(f))]
            sid = f"s{self._next}"
            self._next += 1
            with open(p, "rb") as fh:
                magic = fh.read(4)
            if magic == b"TLSD":
                card = Card(p)
                self.sources.append({"id": sid, "kind": "card", "path": str(p), "card": card})
            else:
                Song.load(p)  # validate
                self.sources.append({"id": sid, "kind": "file", "path": str(p)})
            return [sid]

    def close(self, sid: str) -> None:
        with self.lock:
            for s in list(self.sources):
                if s["id"] == sid:
                    if s["kind"] == "card":
                        s["card"].close()
                    self.sources.remove(s)
            for key in [k for k in self._cache if k.startswith(sid + ":")]:
                del self._cache[key]
            for key in [k for k in self._info if k.startswith(sid + ":")]:
                del self._info[key]

    def _source(self, sid: str) -> dict:
        for s in self.sources:
            if s["id"] == sid:
                return s
        raise TlsdError("Quelle ist nicht mehr geöffnet")

    # songs --------------------------------------------------------------

    def song_ids(self) -> list[str]:
        ids = []
        for s in self.sources:
            if s["kind"] == "card":
                ids += [f"{s['id']}:{n}" for n in range(1, NUM_SONGS + 1)]
            else:
                ids.append(f"{s['id']}:0")
        return ids

    def song(self, song_id: str) -> Song:
        with self.lock:
            if song_id in self._cache:
                self._cache.move_to_end(song_id)
                return self._cache[song_id]
            sid, num = song_id.split(":")
            src = self._source(sid)
            song = src["card"].song(int(num)) if src["kind"] == "card" else Song.load(src["path"])
            self._cache[song_id] = song
            while len(self._cache) > 4:
                self._cache.popitem(last=False)
            return song

    def info(self, song_id: str) -> dict:
        with self.lock:
            if song_id in self._info:
                return self._info[song_id]
            sid, num = song_id.split(":")
            src = self._source(sid)
            if src["kind"] == "card":
                slots = src["card"].song_slots(int(num))
                name = src["card"].song_names()[int(num) - 1] or f"Song {int(num):02d}"
                if not any(s.present for s in slots):
                    info = {"id": song_id, "source": sid, "number": int(num), "name": name,
                            "recorded": False, "parts": [{"part": i + 1, "trained": False} for i in range(NUM_PARTS)]}
                    self._info[song_id] = info
                    return info
            song = self.song(song_id)
            d = song.describe()
            info = {
                "id": song_id, "source": sid,
                "number": int(num) if src["kind"] == "card" else None,
                "name": d["name"] if src["kind"] == "file" else name,
                "file": Path(src["path"]).name if src["kind"] == "file" else None,
                "recorded": any(t["has_audio"] for p in d["parts"] for t in p["tracks"].values()),
                "parts": [{"part": p["part"], "trained": p["trained"],
                           "tracks": p["tracks"], "band": p["band_provisional"]} for p in d["parts"]],
            }
            self._info[song_id] = info
            return info

    def state(self) -> dict:
        with self.lock:
            return {
                "version": __version__,
                "export_dir": str(self.export_dir),
                "sources": [{"id": s["id"], "kind": s["kind"], "path": s["path"],
                             "name": Path(s["path"]).name} for s in self.sources],
                "songs": [self.info(i) for i in self.song_ids()],
            }

    # output -------------------------------------------------------------

    def _out(self, name: str) -> Path:
        self.export_dir.mkdir(parents=True, exist_ok=True)
        out = self.export_dir / name
        stem, suffix = out.stem, out.suffix
        n = 2
        while out.exists():
            out = self.export_dir / f"{stem} ({n}){suffix}"
            n += 1
        return out

    def export(self, song_id: str, what: str, part: int | None = None, track: str | None = None) -> list[str]:
        song = self.song(song_id)
        info = self.info(song_id)
        prefix = f"{info['number']:02d} " if info.get("number") else ""
        base = _safe(prefix + info["name"])
        written = []
        if what == "tlsd":
            out = self._out(f"{base}.tlsd")
            out.write_bytes(song.to_bytes())
            written.append(str(out))
        elif what == "wav":
            parts = [song.part(part)] if part else song.parts
            for p in parts:
                for t, pcm in p.tracks().items():
                    if (track and t != track) or not any(pcm):
                        continue
                    out = self._out(f"{base} - Part {p.number} {t}.wav")
                    out.write_bytes(_wav_bytes(pcm))
                    written.append(str(out))
        else:
            raise TlsdError("unbekanntes Exportformat")
        return written

    def build(self, name: str, parts: list) -> str:
        song = Song.empty()
        for slot, spec in enumerate(parts[:NUM_PARTS], start=1):
            if not spec:
                continue
            src = self.song(spec["song"]).part(int(spec["part"]))
            if not src.trained:
                raise TlsdError(f"Part {spec['part']} des gewählten Songs ist leer")
            song.set_part(slot, src)
        if not any(p.trained for p in song.parts):
            raise TlsdError("Wähle mindestens einen Part")
        song.name = name
        out = self._out(f"{_safe(name)}.tlsd")
        data = song.to_bytes()
        Song.from_bytes(data)
        out.write_bytes(data)
        return str(out)


class Handler(BaseHTTPRequestHandler):
    server_version = f"TrioTool/{__version__}"
    lib: Library
    token: str

    def log_message(self, fmt, *args):  # quiet console
        pass

    # helpers
    def _send(self, status, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        extra = dict(extra or {})
        self.send_header("Cache-Control", extra.pop("Cache-Control", "no-store"))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=HTTPStatus.OK):
        self._send(status, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def _error(self, msg, status=HTTPStatus.BAD_REQUEST):
        self._json({"error": msg}, status)

    def _authorized(self, query) -> bool:
        return secrets.compare_digest(self.headers.get("X-Token", "") or query.get("t", [""])[0], self.token)

    # routes
    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", self.token)
            return self._send(HTTPStatus.OK, html.encode(), "text/html; charset=utf-8")
        if url.path.startswith("/fonts/"):
            name = url.path.rsplit("/", 1)[-1]
            f = STATIC / "fonts" / name
            if re.fullmatch(r"[a-z0-9-]+\.woff2", name) and f.is_file():
                return self._send(HTTPStatus.OK, f.read_bytes(), "font/woff2",
                                  {"Cache-Control": "max-age=86400"})
            return self._error("nicht gefunden", HTTPStatus.NOT_FOUND)
        if not url.path.startswith("/api/"):
            return self._error("nicht gefunden", HTTPStatus.NOT_FOUND)
        if not self._authorized(q):
            return self._error("nicht erlaubt", HTTPStatus.FORBIDDEN)
        try:
            if url.path == "/api/state":
                return self._json(self.lib.state())
            if url.path == "/api/cards":
                return self._json({"cards": _find_cards()})
            if url.path in ("/api/peaks", "/api/wav"):
                song = self.lib.song(q["song"][0])
                track = q.get("track", ["main"])[0]
                if track not in TRACKS:
                    return self._error("unbekannte Spur")
                pcm = song.part(int(q["part"][0])).tracks()[track]
                if url.path == "/api/peaks":
                    return self._json({"peaks": _peaks(pcm, int(q.get("n", ["600"])[0])),
                                       "seconds": len(pcm) / SAMPLE_WIDTH / SAMPLE_RATE})
                return self._send(HTTPStatus.OK, _wav_bytes(pcm), "audio/wav")
        except (KeyError, ValueError, TlsdError) as e:
            return self._error(str(e) or "ungültige Anfrage")
        return self._error("nicht gefunden", HTTPStatus.NOT_FOUND)

    def do_POST(self):
        url = urlparse(self.path)
        if not self._authorized(parse_qs(url.query)):
            return self._error("nicht erlaubt", HTTPStatus.FORBIDDEN)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._error("ungültige Anfrage")
        try:
            if url.path == "/api/open":
                path = body.get("path") or (_dialog(body.get("dialog", "file")) if body.get("dialog") else "")
                if not path:
                    return self._json({"opened": [], "cancelled": True})
                return self._json({"opened": self.lib.open(path), "state": self.lib.state()})
            if url.path == "/api/close":
                self.lib.close(body["source"])
                return self._json({"state": self.lib.state()})
            if url.path == "/api/export":
                files = self.lib.export(body["song"], body["what"], body.get("part"), body.get("track"))
                return self._json({"files": files})
            if url.path == "/api/build":
                out = self.lib.build(body.get("name", "").strip() or "Neuer Song", body.get("parts", []))
                self.lib.open(out)
                return self._json({"file": out, "state": self.lib.state()})
            if url.path == "/api/exportdir":
                path = body.get("path") or (_dialog("folder") if body.get("dialog") else "")
                if path:
                    self.lib.export_dir = Path(path)
                return self._json({"export_dir": str(self.lib.export_dir)})
            if url.path == "/api/reveal":
                d = self.lib.export_dir
                d.mkdir(parents=True, exist_ok=True)
                if os.name == "nt":
                    os.startfile(str(d))  # noqa: S606
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(d)])
                else:
                    subprocess.Popen(["xdg-open", str(d)])
                return self._json({"ok": True})
            if url.path == "/api/quit":
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return None
        except (KeyError, ValueError, TlsdError, OSError) as e:
            return self._error(str(e) or "Aktion fehlgeschlagen")
        return self._error("nicht gefunden", HTTPStatus.NOT_FOUND)


def make_server(host: str = "127.0.0.1", port: int = 8765, export_dir: Path | None = None):
    lib = Library(export_dir or _default_export_dir())
    token = secrets.token_urlsafe(16)
    handler = type("BoundHandler", (Handler,), {"lib": lib, "token": token})
    for p in range(port, port + 20):
        try:
            srv = ThreadingHTTPServer((host, p), handler)
            srv.daemon_threads = True
            return srv, lib, token
        except OSError:
            continue
    raise OSError(f"kein freier Port ab {port}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="trio-gui", description="TRIO+ Tool im Browser")
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 macht das Tool im Netzwerk erreichbar")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--export-dir", type=Path)
    ap.add_argument("paths", nargs="*", help="Karte, Songs oder Ordner, die gleich geöffnet werden")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    srv, lib, _token = make_server(args.host, args.port, args.export_dir)
    for p in args.paths:
        try:
            lib.open(p)
        except (TlsdError, OSError) as e:
            print(f"Konnte {p} nicht öffnen: {e}")
    port = srv.server_address[1]
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{port}/"
    print(f"TRIO Tool {__version__} läuft auf {url}")
    print("Zum Beenden dieses Fenster schließen oder in der App auf „Beenden“ klicken.")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        for s in list(lib.sources):
            lib.close(s["id"])
    time.sleep(0.1)
    return 0
