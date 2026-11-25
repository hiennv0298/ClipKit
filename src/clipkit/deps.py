"""Locate (or fetch) FFmpeg and detect hardware encoding."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from clipkit.util import ClipKitError, app_home, log

# Gyan.dev "essentials" build: includes libx264, libass and NVENC. A .sha256 file
# is published next to it and we verify the archive before extracting.
FFMPEG_ZIP_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
FFMPEG_SHA_URL = FFMPEG_ZIP_URL + ".sha256"
_EXE = ".exe" if os.name == "nt" else ""


@dataclass(frozen=True)
class FFmpeg:
    ffmpeg: Path
    ffprobe: Path

    @property
    def folder(self) -> Path:
        return self.ffmpeg.parent


def _pair(folder: Path) -> FFmpeg | None:
    ff, fp = folder / f"ffmpeg{_EXE}", folder / f"ffprobe{_EXE}"
    return FFmpeg(ff, fp) if ff.is_file() and fp.is_file() else None


def _candidates() -> list[Path]:
    folders: list[Path] = []
    if os.environ.get("CLIPKIT_FFMPEG"):
        folders.append(Path(os.environ["CLIPKIT_FFMPEG"]).expanduser())
    if getattr(sys, "frozen", False):  # PyInstaller build: ffmpeg ships next to the exe
        exe_dir = Path(sys.executable).parent
        folders += [exe_dir / "ffmpeg", exe_dir]
    folders.append(app_home() / "ffmpeg" / "bin")
    return folders


@lru_cache(maxsize=1)
def find_ffmpeg(auto_download: bool = True) -> FFmpeg:
    for folder in _candidates():
        found = _pair(folder)
        if found:
            return found
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ff and fp:
        return FFmpeg(Path(ff), Path(fp))
    if auto_download and os.name == "nt":
        return _download_ffmpeg()
    raise ClipKitError(
        "Không tìm thấy FFmpeg. Cài FFmpeg rồi chạy lại "
        "(Windows: winget install Gyan.FFmpeg · macOS: brew install ffmpeg · Linux: apt install ffmpeg)."
    )


def _download_ffmpeg() -> FFmpeg:
    target = app_home() / "ffmpeg"
    archive = app_home() / "ffmpeg-download.zip"
    log("Chưa có FFmpeg, đang tải bản build của gyan.dev (khoảng 100 MB, chỉ một lần)…")
    try:
        expected = urllib.request.urlopen(FFMPEG_SHA_URL, timeout=30).read().decode().split()[0].lower()
        with urllib.request.urlopen(FFMPEG_ZIP_URL, timeout=60) as resp, open(archive, "wb") as out:
            digest = hashlib.sha256()
            while chunk := resp.read(1 << 20):
                digest.update(chunk)
                out.write(chunk)
    except OSError as exc:
        raise ClipKitError(f"Tải FFmpeg thất bại: {exc}. Hãy cài thủ công: winget install Gyan.FFmpeg") from exc
    if digest.hexdigest() != expected:
        archive.unlink(missing_ok=True)
        raise ClipKitError("File FFmpeg tải về không khớp mã SHA-256, đã hủy để an toàn.")
    shutil.rmtree(target, ignore_errors=True)
    (target / "bin").mkdir(parents=True)
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            base = Path(name).name
            if base in (f"ffmpeg{_EXE}", f"ffprobe{_EXE}") and "/bin/" in name:
                (target / "bin" / base).write_bytes(zf.read(name))
    archive.unlink(missing_ok=True)
    found = _pair(target / "bin")
    if not found:
        raise ClipKitError("Giải nén FFmpeg thất bại: không thấy ffmpeg.exe trong file tải về.")
    log(f"Đã cài FFmpeg vào {found.folder}")
    return found


@lru_cache(maxsize=1)
def nvenc_available(ffmpeg: Path) -> bool:
    """True when h264_nvenc actually works on this machine (not just compiled in)."""
    try:
        proc = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
             "color=c=black:s=320x240:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def cuda_devices() -> int:
    try:
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count())
    except Exception:  # noqa: BLE001 - any import/driver problem means "no CUDA"
        return 0
