# ClipKit

Turn long videos (podcasts, interviews, lectures, talk shows) into **captioned 9:16 short clips**, ready for TikTok, YouTube Shorts and Reels.

- **No API key, no tokens.** The best moments are picked by a scoring algorithm that runs on your machine.
- **yt-dlp** downloads from YouTube, TikTok, Facebook… (1,000+ sites), or use a video file you already have.
- **Whisper** (faster-whisper) transcribes with word-level timestamps and handles Vietnamese well.
- **FFmpeg** cuts, reframes to vertical, burns in captions that highlight the word being spoken, plus a title; uses NVIDIA GPU encoding (NVENC) automatically when available.
- FFmpeg and the Whisper model are downloaded automatically on first run.

Measured on a laptop with an i7-10750H + Quadro T1000: 6 minutes of Vietnamese video → 2 clips in 3 min 41 s (Whisper `small` on CPU took 3 min 11 s; each clip rendered in about 12 s).

## Installation

### Option 1: Download the installer (easiest)

1. Go to the [**Releases**](https://github.com/hiennv0298/ClipKit/releases/latest) page and download `ClipKit-Setup-<version>.exe` (or `ClipKit-<version>-portable.zip` if you'd rather not install).
2. Run the installer. No admin rights needed; the app installs to `%LOCALAPPDATA%\Programs\ClipKit` and bundles Python and FFmpeg.
3. Open **ClipKit** from the Start menu or desktop, paste a video link and answer 2–3 questions. When it's done, the folder with your clips opens automatically.

During setup, tick "Add ClipKit to PATH" to be able to type `clipkit` in a terminal.

> The installer is not code-signed yet, so Windows SmartScreen may warn you; click **More info → Run anyway**.

### Option 2: Run from the command line

Requires Python 3.10+ (`winget install Python.Python.3.12`).

**Install the `clipkit` command once, use it anywhere:**

```powershell
irm https://raw.githubusercontent.com/hiennv0298/ClipKit/main/scripts/install.ps1 | iex
```

To uninstall a `clipkit` command installed this way (from the repo folder):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Uninstall
```

**Or run it straight from the repo folder** (download the ZIP or `git clone`); dependencies are installed on first run:

```bat
run.cmd run "https://www.youtube.com/watch?v=..." -n 5
```

**Or with pip/pipx:**

```bash
pipx install git+https://github.com/hiennv0298/ClipKit
```

## Usage

```bash
clipkit run "https://www.youtube.com/watch?v=VIDEO_ID"           # 5 clips, 20–60 seconds each
clipkit run video.mp4 -n 8 --min 30 --max 90 --language vi       # local file
clipkit run "LINK" --section 10:00-30:00                         # only process part of the video
clipkit run "LINK" --layout crop --crop-x 0.4                    # fill the frame, shifted left
clipkit run "LINK" --model medium                                # more accurate captions, slower
clipkit render "C:\Users\you\Videos\ClipKit\video-name-abc123"   # re-render after editing segments.json
clipkit doctor                                                   # check FFmpeg and GPU
```

Output goes to `Videos\ClipKit\<video-name>\clips\`:

| File | Contents |
| :--- | :--- |
| `01_title.mp4` | 1080×1920 clip, 30 fps, loudness normalized to −14 LUFS |
| `01_title.srt` | Separate subtitle file to upload alongside the video |
| `01_title.jpg` | Cover image |
| `upload.txt` | Title, description and source credit line for each clip |

The video folder also contains `transcript.json` (full transcript) and `segments.json` (the selected segments). Running the same link again reuses the downloaded video and transcript instead of downloading and transcribing again.

### Main options

| Option | Default | Meaning |
| :--- | :--- | :--- |
| `-n, --clips` | 5 | Number of clips |
| `--min / --max` | 20 / 60 | Clip length in seconds |
| `--model` | small | `tiny`, `base`, `small`, `medium`, `large-v3`, `turbo` |
| `--language` | auto-detect | `vi`, `en`… setting it explicitly is more accurate |
| `--section` | whole video | e.g. `10:00-25:00` |
| `--layout` | blur | `blur` video centered on a blurred background · `crop` fill the frame · `fit` black bars |
| `--no-captions`, `--no-title`, `--uppercase` | | No captions, no title, uppercase captions |
| `--encoder` | auto | `nvenc` (NVIDIA GPU) or `cpu` |
| `--device` | auto | Run Whisper on `cuda` or `cpu` |
| `--selector` | heuristic | `llm` to let an AI model pick (see below) |
| `--cookies-from-browser` | | `chrome`/`edge`/`firefox` when the site requires login |

## How ClipKit picks moments

The transcript is split into sentences, then every window of consecutive sentences between `--min` and `--max` seconds long is scored on:

- **Speech density**: fast talking, few pauses.
- **Loudness**: parts louder than the video's average are usually the exciting ones.
- **Opening line**: questions, numbers and curiosity keywords ("why", "secret", "mistake", "money"… in Vietnamese and English) score higher; starting with a connective ("and", "but", "so"…) scores lower.
- **Ending on a complete sentence** and a length close to the middle of the range.

The highest-scoring non-overlapping windows are selected. This is fast and free but doesn't understand content the way a person does: review `segments.json`, edit `start`/`end`/`title` or set `"enabled": false` on segments you don't like, then run `clipkit render`.

### Optional: let an AI pick (`--selector llm`)

Works with any OpenAI-compatible API (OpenAI, DeepSeek, BytePlus ModelArk, OpenRouter, Ollama…). Only **one** request is sent per video, capped by `CLIPKIT_LLM_MAX_TOKENS` (default 2000 response tokens).

```powershell
$env:CLIPKIT_LLM_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
$env:CLIPKIT_LLM_API_KEY  = "..."
$env:CLIPKIT_LLM_MODEL    = "deepseek-v4-1-flash-260910"
clipkit run "LINK" --selector llm
```

## Copyright and monetization

ClipKit prints the video's license when downloading and warns you if it is not Creative Commons. Keep in mind:

- Only publish clips from content you own, have permission to use, or that is licensed **Creative Commons Attribution** (on YouTube: Filters → Creative Commons). CC BY requires credit; `upload.txt` already includes a credit line.
- YouTube does not monetize content that is simply re-uploaded without adding new value (commentary, editing, analysis), even when you are allowed to use it.

## Speed tips

- Whisper is the slowest step. `--model base` is about 2× faster than `small` but makes more spelling mistakes; use `--section` to process only the part you need.
- **Whisper on an NVIDIA GPU** needs the CUDA 12 libraries (cuBLAS, cuDNN 9). If they are missing, ClipKit falls back to CPU automatically and says so in the log.
- Rendering uses NVENC when an NVIDIA GPU is present, taking only a few seconds per clip.

The automatically downloaded Whisper models and FFmpeg are stored in `%LOCALAPPDATA%\ClipKit` (Windows) or `~/.clipkit`.

## Development

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

Build the Windows release (the installer needs [Inno Setup 6](https://jrsoftware.org/isinfo.php)):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -DownloadFfmpeg
```

This produces `dist\ClipKit-Setup-<ver>.exe` and `dist\ClipKit-<ver>-portable.zip`. Pushing a `v*` tag to GitHub makes the **Release** workflow build both files and attach them to the Releases page.

```
src/clipkit/
  cli.py         run / render / doctor commands, interactive mode when double-clicked
  pipeline.py    ties the steps together, caches intermediate results for fast re-runs
  download.py    yt-dlp, license detection
  transcribe.py  faster-whisper, loudness profile
  select.py      moment scoring and selection
  llm.py         AI-based selection via API (optional)
  captions.py    ASS captions with per-word highlight, SRT
  render.py      FFmpeg: 9:16 framing, NVENC/x264, loudness normalization
  deps.py        find or auto-download FFmpeg, GPU checks
```

MIT License.
