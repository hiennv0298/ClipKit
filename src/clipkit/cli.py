"""Command line: `clipkit run <link|file>`, `clipkit render <folder>`, `clipkit doctor`.

Started with no arguments (e.g. double-clicking ClipKit.exe) it asks a few
questions instead.
"""
from __future__ import annotations

import argparse
import os
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
                   help="blur: video giữa + nền mờ (mặc định) · crop: cắt đầy khung 9:16 · fit: viền đen")
    p.add_argument("--crop-x", type=float, default=0.5, help="với --layout crop: vị trí cắt ngang 0 (trái) … 1 (phải)")
    p.add_argument("--no-captions", action="store_true", help="không chèn phụ đề vào video")
    p.add_argument("--no-title", action="store_true", help="không chèn tiêu đề ở đầu khung")
    p.add_argument("--uppercase", action="store_true", help="phụ đề viết hoa")
    p.add_argument("--encoder", choices=("auto", "cpu", "nvenc"), default="auto",
                   help="auto: dùng GPU NVIDIA nếu có (nhanh hơn nhiều)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clipkit",
        description="Cắt video dài thành clip ngắn 9:16 có phụ đề (yt-dlp + Whisper + FFmpeg). Không cần API key.",
    )
    parser.add_argument("--version", action="version", version=f"clipkit {__version__}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="tải, tạo phụ đề, chọn đoạn và dựng clip")
    run.add_argument("source", help="link YouTube/TikTok/Facebook… (mọi trang yt-dlp hỗ trợ) hoặc đường dẫn file video")
    run.add_argument("-n", "--clips", type=int, default=5, help="số clip muốn tạo (mặc định 5)")
    run.add_argument("--min", dest="min_len", type=float, default=20, help="độ dài tối thiểu mỗi clip, giây (20)")
    run.add_argument("--max", dest="max_len", type=float, default=60, help="độ dài tối đa mỗi clip, giây (60)")
    run.add_argument("--model", default="small",
                     help="model Whisper: tiny, base, small (mặc định), medium, large-v3, turbo")
    run.add_argument("--language", default=None, help="mã ngôn ngữ, ví dụ vi hoặc en (mặc định: tự nhận)")
    run.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto", help="chạy Whisper trên CPU hay GPU")
    run.add_argument("--section", help="chỉ xử lý một khúc, ví dụ 10:00-25:00")
    run.add_argument("--selector", choices=("heuristic", "llm"), default="heuristic",
                     help="heuristic: tự chấm điểm, không tốn token (mặc định) · llm: hỏi model qua API")
    run.add_argument("--max-height", type=int, default=1080, help="độ phân giải tải về tối đa (1080)")
    run.add_argument("--cookies-from-browser", choices=("chrome", "edge", "firefox", "brave"),
                     help="dùng cookie trình duyệt khi trang bắt đăng nhập")
    run.add_argument("-o", "--out", type=Path, default=default_output_dir(), help="thư mục kết quả")
    run.add_argument("--force", action="store_true", help="làm lại từ đầu, bỏ qua kết quả đã lưu")
    run.add_argument("--keep-audio", action="store_true", help="giữ file audio.wav")
    _add_common(run)

    render = sub.add_parser("render", help="dựng lại clip từ segments.json (sau khi bạn sửa tay)")
    render.add_argument("workdir", type=Path, help="thư mục video, nơi có segments.json")
    _add_common(render)

    sub.add_parser("doctor", help="kiểm tra FFmpeg, GPU và nơi lưu model")
    return parser


def _options(args: argparse.Namespace, **extra) -> Options:
    return Options(layout=args.layout, crop_x=args.crop_x, captions=not args.no_captions,
                   title=not args.no_title, uppercase=args.uppercase, encoder=args.encoder, **extra)


def cmd_run(args: argparse.Namespace) -> None:
    if args.min_len >= args.max_len:
        raise ClipKitError("--min phải nhỏ hơn --max.")
    opts = _options(
        args, clips=args.clips, min_len=args.min_len, max_len=args.max_len, model=args.model,
        language=args.language, device=args.device, section=parse_section(args.section),
        selector=args.selector, max_height=args.max_height, out=args.out, force=args.force,
        keep_audio=args.keep_audio, cookies_from_browser=args.cookies_from_browser,
    )
    run_all(args.source, opts)


def cmd_render(args: argparse.Namespace) -> None:
    from clipkit.deps import find_ffmpeg

    workdir = args.workdir.expanduser().resolve()
    if not (workdir / "segments.json").exists():
        raise ClipKitError(f"Không thấy segments.json trong {workdir}")
    render_all(workdir, find_ffmpeg(), _options(args))
    log(f"Xong. Clip ở: {workdir / 'clips'}")


def cmd_doctor() -> None:
    from clipkit.deps import cuda_devices, find_ffmpeg, nvenc_available

    log(f"ClipKit {__version__} · Python {platform.python_version()} · {platform.system()} {platform.release()}")
    ff = find_ffmpeg()
    log(f"FFmpeg: {ff.ffmpeg}")
    log(f"Mã hóa bằng GPU NVIDIA (NVENC): {'có' if nvenc_available(ff.ffmpeg) else 'không'}")
    from clipkit.transcribe import cuda_marker

    if not cuda_devices():
        gpu = "không, sẽ chạy CPU"
    elif cuda_marker().exists():
        gpu = (f"có GPU nhưng lần trước thiếu thư viện CUDA nên dùng CPU "
               f"(xóa {cuda_marker()} để thử lại)")
    else:
        gpu = "có"
    log(f"Whisper trên GPU (CUDA): {gpu}")
    import yt_dlp
    log(f"yt-dlp: {yt_dlp.version.__version__}")
    log(f"Model và công cụ lưu ở: {app_home()}")
    log(f"Clip mặc định lưu ở: {default_output_dir()}")


def interactive() -> None:
    log(f"ClipKit {__version__} — cắt video dài thành clip ngắn có phụ đề\n")
    source = input("Dán link video hoặc kéo thả file video vào đây rồi Enter:\n> ").strip().strip('"')
    if not source:
        return
    n = input("Số clip muốn tạo [5]: ").strip()
    layout = input("Kiểu khung: 1 = video giữa nền mờ, 2 = cắt đầy khung [1]: ").strip()
    lang = input("Ngôn ngữ video (vi, en… để trống = tự nhận): ").strip() or None
    opts = Options(clips=int(n) if n.isdigit() else 5, layout="crop" if layout == "2" else "blur", language=lang)
    workdir = run_all(source, opts)
    if os.name == "nt":
        os.startfile(workdir / "clips")  # noqa: S606 - open the result folder for the user


def main(argv: list[str] | None = None) -> int:
    setup_console()
    argv = sys.argv[1:] if argv is None else argv
    double_clicked = not argv
    try:
        if double_clicked:
            interactive()
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
        log(f"\nLỖI: {exc}")
        code = 1
    except KeyboardInterrupt:
        log("\nĐã dừng.")
        code = 130
    except Exception:  # noqa: BLE001 - show the trace, never just close the window
        traceback.print_exc()
        code = 1
    if double_clicked and getattr(sys, "frozen", False):
        input("\nNhấn Enter để đóng…")
    return code
