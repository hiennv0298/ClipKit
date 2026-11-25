"""Glue: download -> transcribe -> select -> render, with caching between runs."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from clipkit import download, transcribe
from clipkit.captions import write_caption_files
from clipkit.deps import FFmpeg, find_ffmpeg
from clipkit.render import render_clip, source_fps, write_cover
from clipkit.select import pick_segments
from clipkit.util import (ClipKitError, check_cancel, default_output_dir, fmt_ts, log, read_json, slugify,
                          write_json)

# Bottom edge of captions in the 1080x1920 frame. YouTube's title, channel name and
# Subscribe button cover roughly the bottom 400-500 px, the like/share buttons the
# right ~180 px and the top bar ~250 px, so captions and title stay inside those.
CAPTION_Y = 1400


@dataclass
class Options:
    clips: int = 5
    min_len: float = 20.0
    max_len: float = 60.0
    model: str = "small"
    language: str | None = None
    device: str = "auto"
    layout: str = "blur"
    crop_x: float = 0.5
    captions: bool = True
    title: bool = True
    uppercase: bool = False
    section: tuple[float, float] | None = None
    selector: str = "heuristic"
    encoder: str = "auto"
    max_height: int = 1080
    out: Path = field(default_factory=default_output_dir)
    force: bool = False
    keep_audio: bool = False
    cookies_from_browser: str | None = None


def run_all(source: str, opts: Options) -> Path:
    started = time.monotonic()
    workdir, transcript, segments = prepare(source, opts)
    log("[4/4] Rendering clips")
    render_all(workdir, find_ffmpeg(), opts, transcript, segments)
    log(f"Done in {fmt_ts(time.monotonic() - started)}. Clips are in: {workdir / 'clips'}")
    return workdir


def prepare(source: str, opts: Options, on_workdir=None) -> tuple[Path, dict, list[dict]]:
    """Steps 1-3: download, transcribe, pick segments (no rendering yet).

    `on_workdir(path)` is called as soon as the work folder is known (the app uses
    it to open the project page while transcription is still running).
    """
    ff = find_ffmpeg()
    log("[1/4] Getting the video")
    workdir = download.fetch(source, Path(opts.out), ff, opts.max_height, opts.cookies_from_browser)
    log(f"      Work folder: {workdir}")
    if on_workdir:
        on_workdir(workdir)
    log("[2/4] Transcribing with Whisper")
    transcript = ensure_transcript(workdir, ff, opts)
    log("[3/4] Picking the best moments")
    if opts.max_len > 60:
        log("      Note: a Short longer than 60 s is blocked worldwide if it gets any Content ID claim. "
            "Keep clips to 60 s unless you own the content.")
    segments = ensure_segments(workdir, transcript, opts)
    return workdir, transcript, segments


def clip_name(seg: dict, n: int = 1) -> str:
    """File name (without extension) of a segment's rendered clip, e.g. 03_why-this-works."""
    return f"{int(seg.get('rank') or n):02d}_{slugify(seg.get('title') or 'clip', 40)}"


def save_segments(workdir: Path, segments: list[dict]) -> None:
    """Write edited segments back, keeping the params that produced them."""
    path = workdir / "segments.json"
    params = read_json(path).get("params", {}) if path.exists() else {}
    write_json(path, {"params": params, "segments": segments})


def ensure_transcript(workdir: Path, ff: FFmpeg, opts: Options) -> dict:
    path = workdir / "transcript.json"
    want = {"model": opts.model, "section": list(opts.section) if opts.section else None,
            "language_opt": opts.language}
    if path.exists() and not opts.force:
        cached = read_json(path)
        if all(cached.get(k) == v for k, v in want.items()):
            log(f"      Reusing the existing transcript ({len(cached['segments'])} segments).")
            return cached
    video = download.source_video(workdir)
    wav = workdir / "audio.wav"
    transcribe.extract_audio(ff, video, wav, opts.section)
    offset = opts.section[0] if opts.section else 0.0
    data = transcribe.transcribe(wav, opts.model, opts.language, opts.device, offset)
    data.update(want)
    transcribe.save_transcript(workdir, data, transcribe.energy_profile(wav), offset)
    if not opts.keep_audio:
        wav.unlink(missing_ok=True)
    return read_json(path)


def ensure_segments(workdir: Path, transcript: dict, opts: Options) -> list[dict]:
    path = workdir / "segments.json"
    params = {"clips": opts.clips, "min_len": opts.min_len, "max_len": opts.max_len,
              "selector": opts.selector, "section": list(opts.section) if opts.section else None}
    if path.exists() and not opts.force:
        cached = read_json(path)
        if cached.get("params") == params:
            log("      Reusing segments.json (edit it, then run `clipkit render`).")
            return cached["segments"]
    if opts.selector == "llm":
        from clipkit.llm import pick_segments_llm
        segments = pick_segments_llm(transcript, opts.clips, opts.min_len, opts.max_len)
    else:
        segments = pick_segments(transcript, opts.clips, opts.min_len, opts.max_len)
    if not segments:
        raise ClipKitError("No segment is long enough. Lower --min or use a longer video.")
    write_json(path, {"params": params, "segments": segments})
    for s in segments:
        log(f"      #{s['rank']} {fmt_ts(s['start'])}–{fmt_ts(s['end'])} ({s['end'] - s['start']:.0f}s) {s['title']}")
    return segments


def render_all(workdir: Path, ff: FFmpeg, opts: Options, transcript: dict | None = None,
               segments: list[dict] | None = None) -> list[Path]:
    transcript = transcript or read_json(workdir / "transcript.json")
    if segments is None:
        segments = read_json(workdir / "segments.json")["segments"]
    info = read_json(workdir / "info.json")
    video = download.source_video(workdir)
    clips_dir = workdir / "clips"
    clips_dir.mkdir(exist_ok=True)

    outputs: list[Path] = []
    todo = [s for s in segments if s.get("enabled", True)]
    if not todo:
        raise ClipKitError("No segments are selected for rendering.")
    fps = source_fps(ff, video)
    for n, seg in enumerate(todo, 1):
        check_cancel()
        start, end = float(seg["start"]), float(seg["end"])
        name = clip_name(seg, n)
        out = clips_dir / f"{name}.mp4"
        ass = clips_dir / f"{name}.ass"
        t0 = time.monotonic()
        log(f"      Clip {n}/{len(todo)}: {fmt_ts(start)}-{fmt_ts(end)} -> {out.name}")
        write_caption_files(transcript, start, end, seg.get("title") if opts.title else None, ass,
                            clips_dir / f"{name}.srt", CAPTION_Y, captions=opts.captions,
                            uppercase=opts.uppercase)
        try:
            render_clip(ff, video, start, end, out, opts.layout, opts.crop_x, ass, opts.encoder, fps=fps)
        finally:
            ass.unlink(missing_ok=True)
        write_cover(ff, out, clips_dir / f"{name}.jpg", at=min(1.5, (end - start) / 2))
        log(f"        done in {fmt_ts(time.monotonic() - t0)}")
        outputs.append(out)

    write_upload_notes(clips_dir / "upload.txt", info, todo)
    write_json(workdir / "options.json", {k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(opts).items()})
    return outputs


def write_upload_notes(path: Path, info: dict, segments: list[dict]) -> None:
    credit = ""
    if info.get("url"):
        credit = f"Source: {info.get('title')} - {info.get('uploader')}\n{info.get('url')}"
        if info.get("license"):
            credit += f"\nLicense: {info['license']}"
    blocks = []
    for i, s in enumerate(segments, 1):
        snippet = s.get("text", "")
        if len(snippet) > 280:
            snippet = snippet[:280].rsplit(" ", 1)[0] + "…"
        blocks.append("\n".join(filter(None, [
            f"=== Clip {int(s.get('rank', i)):02d} ===",
            f"Title: {s.get('title', '')}",
            f"Description: {snippet}",
            credit,
        ])))
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
