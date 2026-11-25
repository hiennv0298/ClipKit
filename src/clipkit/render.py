"""Step 4b: cut, reframe to 9:16, burn captions and encode with FFmpeg."""
from __future__ import annotations

from pathlib import Path

from clipkit.captions import HEIGHT, WIDTH
from clipkit.deps import FFmpeg, nvenc_available
from clipkit.util import ClipKitError, run

LAYOUTS = ("blur", "crop", "fit")


def video_filter(layout: str, crop_x: float, ass_name: str | None) -> str:
    """Filter graph that turns any input into a 1080x1920 frame."""
    if layout == "blur":
        # Sharp video in the middle, a blurred zoomed copy fills the rest.
        graph = (
            "[0:v]split=2[bg][fg];"
            f"[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:2,"
            f"scale={WIDTH}:{HEIGHT},eq=brightness=-0.12[bgb];"
            f"[fg]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease[fgs];"
            "[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
        )
    elif layout == "crop":
        x = min(1.0, max(0.0, crop_x))
        graph = (f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
                 f"crop={WIDTH}:{HEIGHT}:(iw-{WIDTH})*{x:.3f}:(ih-{HEIGHT})/2")
    elif layout == "fit":
        graph = (f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
                 f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:black")
    else:
        raise ClipKitError(f"Layout không hợp lệ: {layout} (chọn: {', '.join(LAYOUTS)})")
    graph += ",setsar=1,fps=30"
    if ass_name:
        graph += f",ass={ass_name}"
    return graph + "[v]"


def encoder_args(ff: FFmpeg, encoder: str) -> list[str]:
    if encoder == "auto":
        encoder = "nvenc" if nvenc_available(ff.ffmpeg) else "cpu"
    if encoder == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]


def render_clip(ff: FFmpeg, video: Path, start: float, end: float, out: Path, layout: str = "blur",
                crop_x: float = 0.5, ass: Path | None = None, encoder: str = "auto",
                normalize_audio: bool = True) -> None:
    """Render one clip. Runs in the clip folder so the ASS path needs no escaping."""
    workdir = out.parent
    if ass is not None and ass.parent != workdir:
        raise ClipKitError("File phụ đề phải nằm cùng thư mục với clip.")
    tmp = out.with_suffix(".part.mp4")
    cmd = [
        ff.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{end - start:.3f}",
        "-filter_complex", video_filter(layout, crop_x, ass.name if ass else None),
        "-map", "[v]", "-map", "0:a:0",
        *encoder_args(ff, encoder),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
    ]
    if normalize_audio:
        cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    cmd += ["-movflags", "+faststart", tmp.name]
    run(cmd, cwd=workdir)
    tmp.replace(out)


def write_cover(ff: FFmpeg, clip: Path, cover: Path, at: float = 1.0) -> None:
    run([ff.ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(clip),
         "-frames:v", "1", "-q:v", "3", str(cover)])
