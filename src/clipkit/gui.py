"""ClipKit desktop app: a small local web server plus an app window.

The page (webui/index.html) talks to this server over a JSON API bound to
127.0.0.1. One job (find moments / render) runs at a time on a background
thread; the page polls /api/state. The window is Edge or Chrome in app mode
(no address bar) and falls back to the default browser.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from clipkit import __version__, updates
from clipkit.pipeline import Options, clip_name, prepare, render_all, save_segments
from clipkit.util import (Cancelled, ClipKitError, add_log_sink, app_home, default_output_dir, log, move_to_trash,
                          parse_section, read_json, request_cancel, reset_cancel, write_json)

WEBUI = Path(__file__).with_name("webui")
SETTINGS_FILE = "gui-settings.json"
SERVER_FILE = "gui-server.json"
DEFAULTS = {"clips": 5, "min_len": 20.0, "max_len": 60.0, "layout": "blur", "crop_x": 0.5, "language": "",
            "model": "small", "captions": True, "title": True, "uppercase": False, "review": True,
            "section": "", "force": False, "out": str(default_output_dir()), "check_updates": True}
STEPS = {1: (0, 15), 2: (15, 55), 3: (70, 5), 4: (75, 25)}  # progress bar share per step: (start, width)
THUMB_W, THUMB_H = 384, 216
SERVER: ThreadingHTTPServer | None = None
IDLE_EXIT = 150  # quit after this many seconds without the page (never while working)
SEGMENT_KEYS = ("rank", "start", "end", "title", "score", "reasons", "text", "enabled")
MEDIA_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
               ".mkv": "video/x-matroska", ".jpg": "image/jpeg", ".png": "image/png", ".ico": "image/x-icon",
               ".srt": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8"}


# ---------------------------------------------------------------- settings

def load_settings() -> dict:
    settings = dict(DEFAULTS)
    try:
        saved = read_json(app_home() / SETTINGS_FILE)
        for k, v in saved.items():
            default = DEFAULTS.get(k)
            if isinstance(default, bool):
                ok = isinstance(v, bool)
            elif isinstance(default, (int, float)):
                ok = isinstance(v, (int, float)) and not isinstance(v, bool)
            else:
                ok = isinstance(v, str)
            if k in DEFAULTS and ok:
                settings[k] = v
    except (OSError, ValueError, AttributeError):
        pass
    if not settings["out"]:
        settings["out"] = DEFAULTS["out"]
    return settings


def save_settings(new: dict) -> dict:
    settings = load_settings()
    for key, default in DEFAULTS.items():
        if key not in new:
            continue
        value = new[key]
        if isinstance(default, bool):
            value = bool(value)
        elif isinstance(default, (int, float)):
            try:
                value = int(float(value)) if isinstance(default, int) else float(value)
            except (TypeError, ValueError):
                raise ClipKitError(f"'{key}' must be a number.") from None
        else:
            value = str(value).strip()
        settings[key] = value
    try:
        write_json(app_home() / SETTINGS_FILE, settings)
    except OSError:
        pass
    return settings


def options_from(s: dict) -> Options:
    if not 1 <= s["clips"] <= 30:
        raise ClipKitError("Clips must be between 1 and 30.")
    if s["min_len"] < 3 or s["min_len"] >= s["max_len"]:
        raise ClipKitError("Min length must be at least 3 seconds and below max length.")
    if s["max_len"] > 180:
        raise ClipKitError("YouTube Shorts can be at most 3 minutes (180 s).")
    try:
        section = parse_section(s["section"] or None)
    except ValueError as exc:
        raise ClipKitError(f"Only this part: {exc}") from None
    return Options(clips=int(s["clips"]), min_len=s["min_len"], max_len=s["max_len"], model=s["model"],
                   language=s["language"] or None, layout=s["layout"], crop_x=min(1.0, max(0.0, s["crop_x"])),
                   captions=s["captions"], title=s["title"], uppercase=s["uppercase"], section=section,
                   out=Path(s["out"]), force=s["force"])


# ---------------------------------------------------------------- job state

class AppState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.busy = False
        self.phase = "idle"  # idle | finding | review | rendering | done | stopped | error
        self.step = 0
        self.progress = 0.0
        self.status = "Ready."
        self.error = ""
        self.project = ""
        self.log: list[str] = []
        self.last_seen = time.monotonic()
        self.seen = False
        self.bye_at = 0.0

    def touch(self) -> None:
        self.last_seen = time.monotonic()
        self.seen = True

    def on_log(self, line: str) -> None:
        with self.lock:
            self.log.append(line)
            del self.log[:-400]
            if line.strip():
                self.status = line.strip()
            m = re.match(r"\s*\[(\d)/4\]", line)
            if m:
                self.step = int(m.group(1))
                self._set(0)
                return
            frac = None
            if self.step == 1 and (m := re.search(r"downloaded (\d+)%", line)):
                frac = int(m.group(1)) / 100
            elif self.step == 2 and (m := re.search(r"transcribed (\d+)%", line)):
                frac = int(m.group(1)) / 100
            elif self.step == 4 and (m := re.search(r"Clip (\d+)/(\d+)", line)):
                frac = (int(m.group(1)) - 1) / int(m.group(2))
            if frac is not None:
                self._set(frac)

    def _set(self, frac: float) -> None:
        start, width = STEPS.get(self.step, (0, 0))
        self.progress = round(start + width * max(0.0, min(1.0, frac)), 1)

    def snapshot(self) -> dict:
        with self.lock:
            return {"busy": self.busy, "phase": self.phase, "step": self.step, "progress": self.progress,
                    "status": self.status, "error": self.error, "project": self.project,
                    "log": self.log[-200:], "version": __version__}


STATE = AppState()


def start_job(phase: str, job, *args) -> None:
    with STATE.lock:
        if STATE.busy:
            raise ClipKitError("ClipKit is already working. Wait for it to finish or press Stop.")
        reset_cancel()
        if phase == "finding":
            STATE.project = ""  # set again once the new video's work folder is known
        STATE.busy, STATE.phase, STATE.error = True, phase, ""
        STATE.step, STATE.progress, STATE.log = 0, 0.0, []

    def guard() -> None:
        try:
            job(*args)
        except Cancelled:
            _finish("stopped", "Stopped.")
        except ClipKitError as exc:
            _finish("error", "Something went wrong.", str(exc))
        except Exception:  # noqa: BLE001 - show anything in the app instead of dying silently
            _finish("error", "Something went wrong.", traceback.format_exc())
        finally:
            with STATE.lock:
                STATE.busy = False

    threading.Thread(target=guard, daemon=True).start()


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _finish(phase: str, status: str, error: str = "") -> None:
    if error:
        log("ERROR: " + error)
    with STATE.lock:
        STATE.phase, STATE.status, STATE.error = phase, status, error


def find_job(source: str, opts: Options, review: bool) -> None:
    def got_workdir(workdir: Path) -> None:
        with STATE.lock:
            STATE.project = workdir.name

    workdir, transcript, segments = prepare(source, opts, on_workdir=got_workdir)
    with STATE.lock:
        STATE.project = workdir.name
    if review:
        with STATE.lock:
            STATE.progress = STEPS[4][0]
        _finish("review", f"Found {_count(len(segments), 'moment')}. Untick the ones you don't want, then press Render.")
    else:
        render_job(workdir, segments, opts, transcript)


def render_job(workdir: Path, segments: list[dict], opts: Options, transcript: dict | None = None) -> None:
    from clipkit.deps import find_ffmpeg

    with STATE.lock:
        STATE.phase, STATE.project = "rendering", workdir.name
    log("[4/4] Rendering clips")
    save_segments(workdir, segments)
    outputs = render_all(workdir, find_ffmpeg(), opts, transcript, segments)
    with STATE.lock:
        STATE.progress = 100.0
    _finish("done", f"Done! {_count(len(outputs), 'clip')} ready.")


# ---------------------------------------------------------------- projects

def project_dir(pid: str) -> Path:
    if not pid or pid.startswith(".") or any(c in pid for c in "/\\:"):
        raise ClipKitError("Invalid project.")
    path = Path(load_settings()["out"]) / pid
    if not (path / "info.json").is_file():
        raise ClipKitError("Project not found. Was the output folder changed?")
    return path


def thumb_at(seg: dict) -> float:
    start, end = float(seg["start"]), float(seg["end"])
    return round(start + min(1.0, (end - start) / 3), 1)


def _segments(d: Path) -> list[dict]:
    try:
        return read_json(d / "segments.json")["segments"]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def list_projects() -> list[dict]:
    base = Path(load_settings()["out"])
    items = []
    if not base.is_dir():
        return items
    for d in base.iterdir():
        try:
            info = read_json(d / "info.json")
        except (OSError, ValueError):
            continue
        segs = _segments(d)
        clips = [p for p in (d / "clips").glob("*.mp4") if not p.name.endswith(".part.mp4")]
        stamps = [p.stat().st_mtime for p in (d / "info.json", d / "segments.json", d / "clips") if p.exists()]
        duration = float(info.get("duration") or 0)
        at = thumb_at(segs[0]) if segs else round(min(30.0, duration / 3), 1)
        items.append({"id": d.name, "title": info.get("title") or d.name, "uploader": info.get("uploader") or "",
                      "license": info.get("license") or "", "duration": duration, "updated": max(stamps),
                      "moments": len(segs), "clips": len(clips), "thumb": f"/thumb/{d.name}/{at}",
                      "size": sum(f.stat().st_size for f in d.rglob("*") if f.is_file())})
    items.sort(key=lambda x: x["updated"], reverse=True)
    return items


def project_detail(pid: str) -> dict:
    d = project_dir(pid)
    info = read_json(d / "info.json")
    out = []
    for n, seg in enumerate(_segments(d), 1):
        item = {k: seg[k] for k in SEGMENT_KEYS if k in seg}
        item["thumb"] = f"/thumb/{pid}/{thumb_at(seg)}"
        clip = d / "clips" / f"{clip_name(seg, n)}.mp4"
        if clip.is_file():
            v = int(clip.stat().st_mtime)
            item["clip"] = f"/files/{pid}/clips/{clip.name}?v={v}"
            if clip.with_suffix(".jpg").is_file():
                item["cover"] = f"/files/{pid}/clips/{clip.with_suffix('.jpg').name}?v={v}"
            item["clip_name"] = clip.name
        out.append(item)
    return {"id": pid, "path": str(d), "info": {k: info.get(k) for k in ("title", "uploader", "url", "license",
                                                                          "duration", "width", "height")},
            "transcribed": (d / "transcript.json").is_file(), "source": f"/source/{pid}", "segments": out}


def clean_segments(raw: list) -> list[dict]:
    segments = []
    for n, s in enumerate(raw, 1):
        try:
            start, end = float(s["start"]), float(s["end"])
        except (KeyError, TypeError, ValueError):
            raise ClipKitError("Each moment needs a start and an end time.") from None
        if end - start < 1:
            raise ClipKitError(f"Moment {n}: the end must be at least 1 second after the start.")
        seg = {k: s[k] for k in SEGMENT_KEYS if k in s}
        seg.update(start=round(start, 2), end=round(end, 2), title=str(s.get("title") or "").strip(),
                   enabled=bool(s.get("enabled", True)), rank=int(s.get("rank") or n))
        segments.append(seg)
    return segments


_thumb_slots = threading.Semaphore(2)


def make_thumb(pid: str, at: float) -> Path:
    from clipkit.deps import find_ffmpeg
    from clipkit.download import source_video

    d = project_dir(pid)
    path = d / "thumbs" / f"{at:.1f}.jpg"
    if path.is_file():
        return path
    path.parent.mkdir(exist_ok=True)
    with _thumb_slots:
        if not path.is_file():
            tmp = path.with_suffix(".tmp.jpg")
            subprocess.run(
                [str(find_ffmpeg().ffmpeg), "-y", "-loglevel", "error", "-ss", f"{at:.2f}",
                 "-i", str(source_video(d)), "-frames:v", "1", "-q:v", "4", "-vf",
                 f"scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=decrease,"
                 f"pad={THUMB_W}:{THUMB_H}:(ow-iw)/2:(oh-ih)/2:black", str(tmp)],
                capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if not tmp.is_file():
                raise ClipKitError("Could not create the preview.")
            tmp.replace(path)
    return path


# ---------------------------------------------------------------- native dialogs and Explorer

_dialog_lock = threading.Lock()


def pick(kind: str, initial: str = "") -> str:
    """Native file/folder picker (Tk, used only for this dialog)."""
    import tkinter as tk
    from tkinter import filedialog

    with _dialog_lock:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            if kind == "folder":
                path = filedialog.askdirectory(parent=root, title="Save clips to folder", initialdir=initial or None)
            else:
                path = filedialog.askopenfilename(
                    parent=root, title="Choose a video",
                    filetypes=[("Video", "*.mp4 *.mkv *.webm *.mov *.m4v *.avi"), ("All files", "*.*")])
        finally:
            root.destroy()
    return str(Path(path)) if path else ""


def reveal(path: Path) -> None:
    if os.name != "nt":
        webbrowser.open(path.as_uri())
    elif path.is_file():
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        os.startfile(path)  # noqa: S606 - open the folder in Explorer


# ---------------------------------------------------------------- HTTP

def _demo_script() -> str:
    path = os.environ.get("CLIPKIT_DEMO_JS", "")
    return Path(path).read_text(encoding="utf-8") if path and Path(path).is_file() else ""


class Handler(BaseHTTPRequestHandler):
    server_version = "ClipKit"
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:  # keep the console quiet
        pass

    # -- helpers
    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _serve_file(self, path: Path) -> None:
        size = path.stat().st_size
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 16, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def _index(self) -> None:
        html = (WEBUI / "index.html").read_text(encoding="utf-8")
        if _demo_script():
            html = html.replace("</body>", '<script src="/__demo.js"></script></body>')
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    # -- routes
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._send(403, b"Forbidden", "text/plain")
        url = urlparse(self.path)
        path, query = unquote(url.path), parse_qs(url.query)
        try:
            if path in ("/", "/index.html"):
                return self._index()
            if path in ("/icon-32.png", "/icon-192.png", "/icon-512.png", "/favicon.ico"):
                return self._serve_file(WEBUI / ("clipkit.ico" if path == "/favicon.ico" else path[1:]))
            if path == "/__demo.js" and _demo_script():
                return self._send(200, _demo_script().encode("utf-8"), "text/javascript; charset=utf-8")
            if path == "/api/ping":
                return self._json({"app": "clipkit", "version": __version__})
            if path == "/api/state":
                STATE.touch()
                return self._json(STATE.snapshot())
            if path == "/api/settings":
                return self._json(load_settings())
            if path == "/api/update":
                return self._json(dict(updates.status(), enabled=load_settings()["check_updates"]))
            if path == "/api/projects":
                return self._json(list_projects())
            if path == "/api/project":
                return self._json(project_detail(query.get("id", [""])[0]))
            if path.startswith("/thumb/"):
                _, _, pid, at = path.split("/", 3)
                return self._serve_file(make_thumb(pid, round(float(at), 1)))
            if path.startswith("/source/"):  # the original video, for previews in the moment cards
                from clipkit.download import source_video

                video = source_video(project_dir(path.split("/", 2)[2]))
                if video.suffix.lower() not in MEDIA_TYPES or not video.is_file():
                    return self._send(404, b"Not found", "text/plain")
                return self._serve_file(video)
            if path.startswith("/files/"):
                _, _, pid, rel = path.split("/", 3)
                base = project_dir(pid).resolve()
                target = (base / rel).resolve()
                if base not in target.parents or target.suffix.lower() not in MEDIA_TYPES or not target.is_file():
                    return self._send(404, b"Not found", "text/plain")
                return self._serve_file(target)
            self._send(404, b"Not found", "text/plain")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except (ClipKitError, ValueError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception:  # noqa: BLE001
            self._json({"error": traceback.format_exc()}, 500)

    def do_POST(self) -> None:
        if not self._host_ok():
            return self._send(403, b"Forbidden", "text/plain")
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if path == "/api/bye":  # sent by the page when its window closes (sendBeacon, no custom header)
            STATE.bye_at = time.monotonic()
            return self._json({"ok": True})
        if self.headers.get("X-ClipKit") != "1":  # other web pages cannot set this header without CORS
            return self._send(403, b"Forbidden", "text/plain")
        try:
            body = json.loads(raw or b"{}")
            self._json(self._post(path, body))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except (ClipKitError, ValueError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception:  # noqa: BLE001
            self._json({"error": traceback.format_exc()}, 500)

    def _post(self, path: str, body: dict) -> dict:
        if path == "/api/settings":
            return save_settings(body)
        if path == "/api/find":
            source = str(body.get("source") or "").strip().strip('"')
            if not source:
                raise ClipKitError("Paste a video link or choose a file first.")
            settings = save_settings(body.get("settings") or {})
            opts = options_from(settings)
            start_job("finding", find_job, source, opts, settings["review"])
            return {"ok": True}
        if path == "/api/segments":
            d = project_dir(body.get("id", ""))
            if STATE.busy and STATE.project == d.name:
                raise ClipKitError("Wait until ClipKit has finished with this project.")
            save_segments(d, clean_segments(body.get("segments") or []))
            return {"ok": True}
        if path == "/api/render":
            d = project_dir(body.get("id", ""))
            segments = clean_segments(body.get("segments") or _segments(d))
            if not any(s["enabled"] for s in segments):
                raise ClipKitError("Tick at least one moment to render.")
            opts = options_from(save_settings(body.get("settings") or {}))
            start_job("rendering", render_job, d, segments, opts)
            return {"ok": True}
        if path == "/api/project/delete":
            d = project_dir(body.get("id", ""))
            if STATE.busy and STATE.project == d.name:
                raise ClipKitError("ClipKit is working on this project. Stop it first.")
            move_to_trash(d)  # the downloaded video, transcript and clips; a local source file is never touched
            return {"ok": True}
        if path == "/api/moments/remove":
            d = project_dir(body.get("id", ""))
            if STATE.busy and STATE.project == d.name:
                raise ClipKitError("Wait until ClipKit has finished with this project.")
            clips = (d / "clips").resolve()
            for name in body.get("files") or []:
                clip = (clips / str(name)).resolve()
                if clip.parent != clips or clip.suffix.lower() != ".mp4":
                    raise ClipKitError("Invalid clip.")
                for f in (clip, clip.with_suffix(".srt"), clip.with_suffix(".jpg")):
                    move_to_trash(f)
            save_segments(d, clean_segments(body.get("segments") or []))
            return {"ok": True}
        if path == "/api/stop":
            request_cancel()
            with STATE.lock:
                if STATE.busy:
                    STATE.status = "Stopping..."
            return {"ok": True}
        if path == "/api/update/check":
            updates.check(force=True)
            return dict(updates.status(), enabled=load_settings()["check_updates"])
        if path == "/api/update/seen":
            updates.mark_seen()
            return {"ok": True}
        if path == "/api/update/install":
            if STATE.busy:
                raise ClipKitError("Wait until ClipKit has finished its current job, then update.")
            if updates.PROGRESS["phase"] in ("downloading", "verifying", "restarting"):
                return {"ok": True}
            threading.Thread(target=_install_update, daemon=True).start()
            return {"ok": True}
        if path == "/api/pick":
            return {"path": pick(body.get("kind", "file"), load_settings()["out"])}
        if path == "/api/open":
            if body.get("id"):
                d = project_dir(body["id"])
                target = d / "clips" / body["file"] if body.get("file") else d / "clips"
                if not target.exists():
                    target = d
                if d.resolve() not in target.resolve().parents and target.resolve() != d.resolve():
                    raise ClipKitError("Invalid file.")
            else:
                target = Path(load_settings()["out"])
                target.mkdir(parents=True, exist_ok=True)
            reveal(target)
            return {"ok": True}
        raise ClipKitError("Unknown action.")


# ---------------------------------------------------------------- window and lifecycle

def _install_update() -> None:
    """Runs on a thread: on success the installer is running and this app quits so files can be replaced."""
    def quit_soon() -> None:
        if SERVER:
            threading.Timer(2.5, SERVER.shutdown).start()  # let the page see "restarting" first

    try:
        updates.download_and_install(quit_soon, port=SERVER.server_address[1] if SERVER else None)
    except ClipKitError:
        pass  # the error is in updates.PROGRESS for the page to show



def _browser() -> str | None:
    candidates = []
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                           Path(base) / "Google/Chrome/Application/chrome.exe"]
    for c in candidates:
        if c.is_file():
            return str(c)
    return shutil.which("msedge") or shutil.which("chrome") or shutil.which("google-chrome")


def open_window(url: str) -> None:
    exe = _browser()
    if exe:
        extra = os.environ.get("CLIPKIT_BROWSER_ARGS", "").split()
        subprocess.Popen([exe, f"--app={url}", "--window-size=1280,900", *extra])
    else:
        webbrowser.open(url)


def _running_instance() -> str | None:
    try:
        port = int(read_json(app_home() / SERVER_FILE)["port"])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as resp:
            if json.loads(resp.read()).get("app") == "clipkit":
                return f"http://127.0.0.1:{port}/"
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _watchdog(server: ThreadingHTTPServer) -> None:
    while True:
        time.sleep(1)
        if STATE.busy or not STATE.seen:
            continue
        now = time.monotonic()
        closed = STATE.bye_at and STATE.bye_at > STATE.last_seen and now - STATE.bye_at > 5
        if closed or now - STATE.last_seen > IDLE_EXIT:
            server.shutdown()
            return


def _ensure_streams() -> None:
    """The windowed build has no console: send output to a log file instead of nowhere."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    stream = open(app_home() / "clipkit-app.log", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream


def _window_if_nobody_returns(url: str, wait: float = 20) -> None:
    """After an update the old window should reconnect by itself; open one if it doesn't."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if STATE.seen:
            return
        time.sleep(0.5)
    open_window(url)


def main() -> int:
    global SERVER
    _ensure_streams()
    existing = _running_instance()
    if existing:
        open_window(existing)
        return 0
    # After an in-app update, serve on the old address: the open window reconnects and reloads.
    relaunch = updates.take_relaunch()
    port = int(os.environ.get("CLIPKIT_GUI_PORT") or (relaunch or {}).get("port") or 0)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:  # the old port is taken by something else
        server, relaunch = ThreadingHTTPServer(("127.0.0.1", 0), Handler), None
    server.daemon_threads = True
    SERVER = server
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        write_json(app_home() / SERVER_FILE, {"port": server.server_address[1], "pid": os.getpid()})
    except OSError:
        pass
    add_log_sink(STATE.on_log)
    threading.Thread(target=_watchdog, args=(server,), daemon=True).start()
    if load_settings()["check_updates"]:
        updates.start_background_check()
    if relaunch and not os.environ.get("CLIPKIT_GUI_NO_WINDOW"):
        threading.Thread(target=_window_if_nobody_returns, args=(url,), daemon=True).start()
    elif not os.environ.get("CLIPKIT_GUI_NO_WINDOW"):
        open_window(url)
    print(f"ClipKit is running at {url}  (close the app window to quit)", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        request_cancel()
        server.server_close()
        try:
            (app_home() / SERVER_FILE).unlink(missing_ok=True)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
