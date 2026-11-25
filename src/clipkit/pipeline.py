"""Glue: download -> transcribe -> select -> render, with caching between runs."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from clipkit import download, transcribe
from clipkit.captions import write_caption_files
from clipkit.deps import FFmpeg, find_ffmpeg
from clipkit.render import render_clip, write_cover
from clipkit.select import pick_segments
from clipkit.util import ClipKitError, default_output_dir, fmt_ts, log, read_json, slugify, write_json

CAPTION_Y = 1480  # bottom edge of captions, in a 1080x1920 frame


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
    ff = find_ffmpeg()
    log("[1/4] Lấy video")
    workdir = download.fetch(source, Path(opts.out), ff, opts.max_height, opts.cookies_from_browser)
    log(f"      Thư mục làm việc: {workdir}")
    log("[2/4] Tạo phụ đề bằng Whisper")
    transcript = ensure_transcript(workdir, ff, opts)
    log("[3/4] Chọn đoạn hay")
    segments = ensure_segments(workdir, transcript, opts)
    log("[4/4] Dựng clip")
    render_all(workdir, ff, opts, transcript, segments)
    log(f"Xong sau {fmt_ts(time.monotonic() - started)}. Clip ở: {workdir / 'clips'}")
    return workdir


def ensure_transcript(workdir: Path, ff: FFmpeg, opts: Options) -> dict:
    path = workdir / "transcript.json"
    want = {"model": opts.model, "section": list(opts.section) if opts.section else None,
            "language_opt": opts.language}
    if path.exists() and not opts.force:
        cached = read_json(path)
        if all(cached.get(k) == v for k, v in want.items()):
            log(f"      Dùng lại phụ đề đã có ({len(cached['segments'])} đoạn).")
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
            log("      Dùng lại segments.json (bạn có thể sửa file này rồi chạy `clipkit render`).")
            return cached["segments"]
    if opts.selector == "llm":
        from clipkit.llm import pick_segments_llm
        segments = pick_segments_llm(transcript, opts.clips, opts.min_len, opts.max_len)
    else:
        segments = pick_segments(transcript, opts.clips, opts.min_len, opts.max_len)
    if not segments:
        raise ClipKitError("Không tìm được đoạn nào đủ dài. Thử giảm --min hoặc dùng video dài hơn.")
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
    for n, seg in enumerate(todo, 1):
        start, end = float(seg["start"]), float(seg["end"])
        name = f"{int(seg.get('rank', n)):02d}_{slugify(seg.get('title') or 'clip', 40)}"
        out = clips_dir / f"{name}.mp4"
        ass = clips_dir / f"{name}.ass"
        t0 = time.monotonic()
        log(f"      Clip {n}/{len(todo)}: {fmt_ts(start)}–{fmt_ts(end)} → {out.name}")
        write_caption_files(transcript, start, end, seg.get("title") if opts.title else None, ass,
                            clips_dir / f"{name}.srt", CAPTION_Y, captions=opts.captions,
                            uppercase=opts.uppercase)
        try:
            render_clip(ff, video, start, end, out, opts.layout, opts.crop_x, ass, opts.encoder)
        finally:
            ass.unlink(missing_ok=True)
        write_cover(ff, out, clips_dir / f"{name}.jpg", at=min(1.5, (end - start) / 2))
        log(f"        xong trong {fmt_ts(time.monotonic() - t0)}")
        outputs.append(out)

    write_upload_notes(clips_dir / "upload.txt", info, todo)
    write_json(workdir / "options.json", {k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(opts).items()})
    return outputs


def write_upload_notes(path: Path, info: dict, segments: list[dict]) -> None:
    credit = ""
    if info.get("url"):
        credit = f"Nguồn: {info.get('title')} — {info.get('uploader')}\n{info.get('url')}"
        if info.get("license"):
            credit += f"\nGiấy phép: {info['license']}"
    blocks = []
    for i, s in enumerate(segments, 1):
        snippet = s.get("text", "")
        if len(snippet) > 280:
            snippet = snippet[:280].rsplit(" ", 1)[0] + "…"
        blocks.append("\n".join(filter(None, [
            f"=== Clip {int(s.get('rank', i)):02d} ===",
            f"Tiêu đề: {s.get('title', '')}",
            f"Mô tả: {snippet}",
            credit,
            "#shorts",
        ])))
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
