"""Step 1: get the source video (yt-dlp for links, or a local file)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from clipkit.deps import FFmpeg
from clipkit.util import ClipKitError, check_cancel, log, read_json, run, slugify, write_json

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}


def is_url(source: str) -> bool:
    return bool(re.match(r"^https?://", source.strip(), re.I))


def probe(ff: FFmpeg, path: Path) -> dict:
    out = run([ff.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]).stdout
    data = json.loads(out)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not audio:
        raise ClipKitError(f"The video has no audio track, so it cannot be transcribed: {path}")
    return {
        "duration": float(data.get("format", {}).get("duration") or 0),
        "width": int(video["width"]) if video else 0,
        "height": int(video["height"]) if video else 0,
    }


def lookup(source: str) -> dict:
    """Read title/licence of a link without downloading it."""
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(source, download=False)
    except yt_dlp.utils.DownloadError as exc:
        reason = re.sub(r"^ERROR:\s*", "", str(exc)).strip()
        raise ClipKitError(f"Could not open this link. {reason}") from None
    return {
        "id": info.get("id"),
        "title": info.get("title") or "video",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "url": info.get("webpage_url") or source,
        "license": info.get("license") or "",
        "duration": float(info.get("duration") or 0),
    }


def fetch(source: str, out_root: Path, ff: FFmpeg, max_height: int = 1080,
          cookies_from_browser: str | None = None) -> Path:
    """Download (or register) the source and return its work folder.

    The work folder holds info.json + source video; re-running with the same
    link reuses it instead of downloading again.
    """
    out_root.mkdir(parents=True, exist_ok=True)
    if not is_url(source):
        return _register_local(Path(source).expanduser(), out_root, ff)

    import yt_dlp

    meta = lookup(source)
    workdir = out_root / f"{slugify(meta['title'])}-{meta['id']}"
    workdir.mkdir(parents=True, exist_ok=True)
    info_path = workdir / "info.json"
    existing = _existing_source(workdir)
    if existing and info_path.exists():
        log(f"Using the previously downloaded video: {existing.name}")
        return workdir

    log(f"Downloading: {meta['title']} ({meta['uploader']})")
    _warn_license(meta)
    last_pct = [-10]

    def hook(d: dict) -> None:
        check_cancel()
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                pct = int(d.get("downloaded_bytes", 0) * 100 / total)
                if pct >= last_pct[0] + 10:
                    last_pct[0] = pct
                    log(f"  downloaded {pct}%")

    opts = {
        "outtmpl": str(workdir / "source.%(ext)s"),
        "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        "ffmpeg_location": str(ff.folder),
        "overwrites": True,
    }
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([source])
    except yt_dlp.utils.DownloadError as exc:
        check_cancel()
        raise ClipKitError(f"yt-dlp could not download the video: {exc}") from exc

    video = _existing_source(workdir)
    if not video:
        raise ClipKitError("yt-dlp finished but no video file was found.")
    meta.update(probe(ff, video), source_file=video.name)
    write_json(info_path, meta)
    return workdir


def _register_local(path: Path, out_root: Path, ff: FFmpeg) -> Path:
    if not path.is_file():
        raise ClipKitError(f"File not found: {path}")
    workdir = out_root / slugify(path.stem)
    workdir.mkdir(parents=True, exist_ok=True)
    info_path = workdir / "info.json"
    if not info_path.exists() or read_json(info_path).get("source_path") != str(path.resolve()):
        meta = {"id": None, "title": path.stem, "uploader": "", "url": "", "license": "",
                "source_path": str(path.resolve())}
        meta.update(probe(ff, path))
        write_json(info_path, meta)
    return workdir


def _existing_source(workdir: Path) -> Path | None:
    for f in sorted(workdir.glob("source.*")):
        if f.suffix.lower() in VIDEO_EXTS:
            return f
    return None


def source_video(workdir: Path) -> Path:
    info = read_json(workdir / "info.json")
    if info.get("source_path"):
        return Path(info["source_path"])
    video = _existing_source(workdir)
    if not video:
        raise ClipKitError(f"No source video in {workdir}.")
    return video


def _warn_license(meta: dict) -> None:
    lic = meta.get("license") or ""
    if "creative commons" in lic.lower():
        log(f"  License: {lic}. The uploader marked this video for reuse with credit (ClipKit writes the credit "
            "to upload.txt). The label covers this video only; music in it can still be claimed.")
    else:
        log("  WARNING: this video is not Creative Commons licensed. Only publish clips if you have the rights,"
            " otherwise they may be taken down or demonetized.")
