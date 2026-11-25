"""Small helpers shared by every step."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import unicodedata
from pathlib import Path
from typing import Any, Callable, Sequence


class ClipKitError(RuntimeError):
    """An error we can explain to the user without a traceback."""


class Cancelled(ClipKitError):
    """The user pressed Stop."""

    def __init__(self, message: str = "Stopped by user.") -> None:
        super().__init__(message)


_cancel = threading.Event()
_log_sinks: list[Callable[[str], None]] = []


def request_cancel() -> None:
    _cancel.set()


def reset_cancel() -> None:
    _cancel.clear()


def check_cancel() -> None:
    """Raise Cancelled if Stop was pressed; long steps call this regularly."""
    if _cancel.is_set():
        raise Cancelled()


def add_log_sink(sink: Callable[[str], None]) -> None:
    """Also send every log line to `sink` (the GUI uses this)."""
    _log_sinks.append(sink)


def app_home() -> Path:
    """Folder for downloaded tools and models (not for user output)."""
    override = os.environ.get("CLIPKIT_HOME")
    if override:
        path = Path(override)
    elif os.name == "nt":
        path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ClipKit"
    else:
        path = Path.home() / ".clipkit"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_dir() -> Path:
    videos = Path.home() / "Videos"
    return (videos if videos.is_dir() else Path.cwd()) / "ClipKit"


def log(message: str = "") -> None:
    try:
        print(message, flush=True)
    except (AttributeError, OSError, ValueError):
        pass  # no console (e.g. started without one)
    for sink in _log_sinks:
        sink(message)


def fmt_ts(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_ts(value: str) -> float:
    """Parse '75', '75.5', '1:15' or '0:01:15' into seconds."""
    value = value.strip()
    if not value:
        raise ValueError("empty timestamp")
    parts = value.split(":")
    if len(parts) > 3:
        raise ValueError(f"bad timestamp: {value}")
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total


def parse_section(value: str | None) -> tuple[float, float] | None:
    """Parse '10:00-25:30' into (600.0, 1530.0)."""
    if not value:
        return None
    if "-" not in value:
        raise ValueError("section must look like START-END, e.g. 10:00-25:00")
    start_s, end_s = value.split("-", 1)
    start, end = parse_ts(start_s), parse_ts(end_s)
    if end <= start:
        raise ValueError("the end time must be after the start time")
    return start, end


def slugify(text: str, max_len: int = 48) -> str:
    """ASCII file-name-safe slug; Vietnamese diacritics are folded (đ -> d)."""
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return (text[:max_len].rstrip("-")) or "video"


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def run(cmd: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run a command, raising ClipKitError with the tail of stderr on failure.

    Stop (request_cancel) kills the command instead of waiting for it.
    """
    args = list(map(str, cmd))
    proc = subprocess.Popen(
        args,
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    while True:
        try:
            out, err = proc.communicate(timeout=0.3)
            break
        except subprocess.TimeoutExpired:
            if _cancel.is_set():
                proc.kill()
                proc.communicate()
                raise Cancelled() from None
    if proc.returncode != 0:
        tail = "\n".join((err or out or "").strip().splitlines()[-15:])
        raise ClipKitError(f"Command failed ({Path(args[0]).name}):\n{tail}")
    return subprocess.CompletedProcess(args, proc.returncode, out, err)


def setup_console() -> None:
    """Make non-ASCII text (e.g. Vietnamese titles) print correctly on the Windows console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
