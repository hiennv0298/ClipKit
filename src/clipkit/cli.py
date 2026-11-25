"""Command line: `clipkit run <link|file>`, `clipkit render <folder>`, `clipkit gui`, `clipkit doctor`.

Started with no arguments it opens the app window (a local page served on 127.0.0.1).
"""
from __future__ import annotations

import argparse
import platform
import sys
import traceback
from pathlib import Path

from clipkit import __version__
from clipkit.pipeline import Options, render_all, run_all
from clipkit.render import LAYOUTS
from clipkit.util import ClipKitError, app_home, default_output_dir, log, parse_section, setup_console


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--layout", choices=LAYOUTS, default="blur",
                   help="blur: video centered on a blurred background (default), crop: fill the 9:16 frame, "
                        "fit: black bars")
    p.add_argument("--crop-x", type=float, default=0.5,
                   help="with --layout crop: horizontal crop position, 0 (left) to 1 (right)")
    p.add_argument("--no-captions", action="store_true", help="do not burn captions into the video")
    p.add_argument("--no-title", action="store_true", help="do not add the title at the top of the frame")
    p.add_argument("--uppercase", action="store_true", help="uppercase captions")
    p.add_argument("--encoder", choices=("auto", "cpu", "nvenc"), default="auto",
                   help="auto: use the NVIDIA GPU when available (much faster)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clipkit",
        description="Cut long videos into captioned 9:16 short clips (yt-dlp + Whisper + FFmpeg). "
                    "No API key needed. Run without arguments to open the app window.",
    )
    parser.add_argument("--version", action="version", version=f"clipkit {__version__}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="download, transcribe, pick moments and render clips")
    run.add_argument("source", help="a YouTube/TikTok/Facebook... link (any site yt-dlp supports) or a video file")
    run.add_argument("-n", "--clips", type=int, default=5, help="number of clips (default 5)")
    run.add_argument("--min", dest="min_len", type=float, default=20, help="minimum clip length in seconds (20)")
    run.add_argument("--max", dest="max_len", type=float, default=60, help="maximum clip length in seconds (60)")
    run.add_argument("--model", default="small",
                     help="Whisper model: tiny, base, small (default), medium, large-v3, turbo")
    run.add_argument("--language", default=None, help="language code such as vi or en (default: auto-detect)")
    run.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto", help="run Whisper on CPU or GPU")
    run.add_argument("--section", help="only process part of the video, e.g. 10:00-25:00")
    run.add_argument("--selector", choices=("heuristic", "llm"), default="heuristic",
                     help="heuristic: local scoring, no tokens (default); llm: ask a model through an API")
    run.add_argument("--max-height", type=int, default=1080, help="maximum download resolution (1080)")
    run.add_argument("--cookies-from-browser", choices=("chrome", "edge", "firefox", "brave"),
                     help="use your browser's cookies when the site requires login")
    run.add_argument("-o", "--out", type=Path, default=default_output_dir(), help="output folder")
    run.add_argument("--force", action="store_true", help="start over, ignore saved results")
    run.add_argument("--keep-audio", action="store_true", help="keep the extracted audio.wav")
    _add_common(run)

    render = sub.add_parser("render", help="re-render clips from segments.json (after editing it)")
    render.add_argument("workdir", type=Path, help="the video's work folder, containing segments.json")
    _add_common(render)

    sub.add_parser("gui", help="open the app window")
    sub.add_parser("doctor", help="check FFmpeg, GPU and where models are stored")
    return parser


def _options(args: argparse.Namespace, **extra) -> Options:
    return Options(layout=args.layout, crop_x=args.crop_x, captions=not args.no_captions,
                   title=not args.no_title, uppercase=args.uppercase, encoder=args.encoder, **extra)


def cmd_run(args: argparse.Namespace) -> None:
    if args.min_len >= args.max_len:
        raise ClipKitError("--min must be smaller than --max.")
    try:
        section = parse_section(args.section)
    except ValueError as exc:
        raise ClipKitError(f"--section: {exc}") from exc
    opts = _options(
        args, clips=args.clips, min_len=args.min_len, max_len=args.max_len, model=args.model,
        language=args.language, device=args.device, section=section,
        selector=args.selector, max_height=args.max_height, out=args.out, force=args.force,
        keep_audio=args.keep_audio, cookies_from_browser=args.cookies_from_browser,
    )
    run_all(args.source, opts)


def cmd_render(args: argparse.Namespace) -> None:
    from clipkit.deps import find_ffmpeg

    workdir = args.workdir.expanduser().resolve()
    if not (workdir / "segments.json").exists():
        raise ClipKitError(f"No segments.json in {workdir}")
    render_all(workdir, find_ffmpeg(), _options(args))
    log(f"Done. Clips are in: {workdir / 'clips'}")


def cmd_doctor() -> None:
    from clipkit.deps import cuda_devices, find_ffmpeg, nvenc_available
    from clipkit.transcribe import cuda_marker

    log(f"ClipKit {__version__} - Python {platform.python_version()} - {platform.system()} {platform.release()}")
    ff = find_ffmpeg()
    log(f"FFmpeg: {ff.ffmpeg}")
    log(f"NVIDIA GPU encoding (NVENC): {'yes' if nvenc_available(ff.ffmpeg) else 'no'}")
    if not cuda_devices():
        gpu = "no, using CPU"
    elif cuda_marker().exists():
        gpu = f"GPU found, but CUDA libraries were missing last time so CPU is used (delete {cuda_marker()} to retry)"
    else:
        gpu = "yes"
    log(f"Whisper on GPU (CUDA): {gpu}")
    import yt_dlp

    log(f"yt-dlp: {yt_dlp.version.__version__}")
    log(f"Models and tools are stored in: {app_home()}")
    log(f"Clips are saved to: {default_output_dir()}")


def cmd_gui() -> None:
    """Start the local app server and open its window."""
    from clipkit.gui import main as gui_main

    gui_main()


def main(argv: list[str] | None = None) -> int:
    setup_console()
    argv = sys.argv[1:] if argv is None else argv
    try:
        if not argv or argv == ["gui"]:
            cmd_gui()
        else:
            args = build_parser().parse_args(argv)
            if args.command == "run":
                cmd_run(args)
            elif args.command == "render":
                cmd_render(args)
            elif args.command == "doctor":
                cmd_doctor()
            else:
                build_parser().print_help()
        code = 0
    except ClipKitError as exc:
        log(f"\nERROR: {exc}")
        code = 1
    except KeyboardInterrupt:
        log("\nStopped.")
        code = 130
    except Exception:  # noqa: BLE001 - show the trace, never just close the window
        traceback.print_exc()
        code = 1
    return code
