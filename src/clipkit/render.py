"""Step 4b: cut, reframe to 9:16, burn captions and encode with FFmpeg."""
from __future__ import annotations

from pathlib import Path

from clipkit.captions import HEIGHT, WIDTH
from clipkit.deps import FFmpeg, nvenc_available
from clipkit.util import ClipKitError, run

LAYOUTS = ("blur", "crop", "fit")


def source_fps(ff: FFmpeg, video: Path) -> str:
    """Frame rate to render at: the source's own (YouTube recommends keeping it), 30 if unusual."""
    try:
        rate = run([ff.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                    "stream=avg_frame_rate", "-of", "csv=p=0", video]).stdout.strip()
        num, _, den = rate.partition("/")
        value = float(num) / float(den or 1)
    except (ClipKitError, ValueError, ZeroDivisionError):
        return "30"
    return rate if 23 <= value <= 60.5 else "30"


def gop_size(fps: str) -> int:
    """Keyframe every half second, as YouTube's upload guidelines recommend."""
    num, _, den = fps.partition("/")
    return max(1, round(float(num) / float(den or 1) / 2))


def video_filter(layout: str, crop_x: float, ass_name: str | None, fps: str = "30") -> str:
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
        raise ClipKitError(f"Invalid layout: {layout} (choose from: {', '.join(LAYOUTS)})")
    graph += f",setsar=1,fps={fps}"
    if ass_name:
        graph += f",ass={ass_name}"
    return graph + "[v]"


def encoder_args(ff: FFmpeg, encoder: str, fps: str = "30") -> list[str]:
    """H.264 High, 2 B-frames, closed GOP of half the frame rate, VBR around 8-12 Mbps.

    This follows YouTube's recommended upload encoding settings; captions burned
    into the picture also survive YouTube's re-encode better at this bitrate.
    """
    if encoder == "auto":
        encoder = "nvenc" if nvenc_available(ff.ffmpeg) else "cpu"
    gop = str(gop_size(fps))
    common = ["-profile:v", "high", "-bf", "2", "-g", gop, "-maxrate", "12M", "-bufsize", "24M"]
    if encoder == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "8M",
                "-strict_gop", "1", *common]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-keyint_min", gop, "-sc_threshold", "0",
            "-flags", "+cgop", *common]


def render_clip(ff: FFmpeg, video: Path, start: float, end: float, out: Path, layout: str = "blur",
                crop_x: float = 0.5, ass: Path | None = None, encoder: str = "auto",
                normalize_audio: bool = True, fps: str = "30") -> None:
    """Render one clip. Runs in the clip folder so the ASS path needs no escaping."""
    workdir = out.parent
    if ass is not None and ass.parent != workdir:
        raise ClipKitError("The caption file must be in the same folder as the clip.")
    tmp = out.with_suffix(".part.mp4")
    cmd = [
        ff.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{end - start:.3f}",
        "-filter_complex", video_filter(layout, crop_x, ass.name if ass else None, fps),
        "-map", "[v]", "-map", "0:a:0",
        *encoder_args(ff, encoder, fps),
        "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
    ]
    if normalize_audio:
        cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    cmd += ["-movflags", "+faststart", tmp.name]
    try:
        run(cmd, cwd=workdir)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    tmp.replace(out)


def write_cover(ff: FFmpeg, clip: Path, cover: Path, at: float = 1.0) -> None:
    run([ff.ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(clip),
         "-frames:v", "1", "-q:v", "3", str(cover)])
