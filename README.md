# ClipKit

Cắt video dài (podcast, phỏng vấn, bài giảng, talkshow) thành **clip ngắn 9:16 có phụ đề**, sẵn sàng đăng TikTok, YouTube Shorts, Reels.

- **Không cần API key, không tốn token.** Chọn đoạn hay bằng thuật toán chấm điểm chạy trên máy.
- **yt-dlp** tải video từ YouTube, TikTok, Facebook… (hơn 1.000 trang) hoặc dùng file có sẵn.
- **Whisper** (faster-whisper) tạo phụ đề theo từng từ, nhận tiếng Việt tốt.
- **FFmpeg** cắt, dựng khung dọc, chèn phụ đề tô sáng từng chữ đang nói và tiêu đề; tự dùng GPU NVIDIA (NVENC) nếu có.
- Tự tải FFmpeg và model Whisper ở lần chạy đầu.

Đo trên laptop i7-10750H + Quadro T1000: 6 phút video tiếng Việt → 2 clip trong 3 phút 41 giây (Whisper `small` trên CPU mất 3 phút 11 giây, mỗi clip dựng khoảng 12 giây).

## Cài đặt

### Cách 1: Tải file cài đặt (dễ nhất)

1. Vào trang **Releases** của repo, tải `ClipKit-Setup-<phiên bản>.exe` (hoặc `ClipKit-<phiên bản>-portable.zip` nếu không muốn cài).
2. Chạy file cài. Không cần quyền admin; app cài vào `%LOCALAPPDATA%\Programs\ClipKit`, đã kèm Python và FFmpeg.
3. Mở **ClipKit** từ Start menu hoặc desktop, dán link video, trả lời 2–3 câu hỏi. Xong sẽ tự mở thư mục chứa clip.

Lúc cài, chọn "Add ClipKit to PATH" để gõ được lệnh `clipkit` trong cmd.

> File cài chưa có chữ ký số nên Windows SmartScreen có thể cảnh báo; bấm **More info → Run anyway**.

### Cách 2: Chạy bằng cmd / PowerShell

Cần Python 3.10+ (`winget install Python.Python.3.12`).

**Cài lệnh `clipkit` một lần, dùng ở mọi nơi:**

```powershell
$env:CLIPKIT_REPO = "<github-user>/clipkit"
irm https://raw.githubusercontent.com/<github-user>/clipkit/main/scripts/install.ps1 | iex
```

**Hoặc chạy thẳng từ thư mục repo** (tải ZIP hoặc `git clone`), lần đầu tự cài thư viện:

```bat
run.cmd run "https://www.youtube.com/watch?v=..." -n 5
```

**Hoặc với pip/pipx:**

```bash
pipx install git+https://github.com/<github-user>/clipkit
```

## Cách dùng

```bash
clipkit run "https://www.youtube.com/watch?v=VIDEO_ID"           # 5 clip, 20–60 giây
clipkit run video.mp4 -n 8 --min 30 --max 90 --language vi       # file trên máy
clipkit run "LINK" --section 10:00-30:00                         # chỉ xử lý một khúc
clipkit run "LINK" --layout crop --crop-x 0.4                    # cắt đầy khung, lệch trái
clipkit run "LINK" --model medium                                # phụ đề chính xác hơn, chậm hơn
clipkit render "C:\Users\ban\Videos\ClipKit\ten-video-abc123"    # dựng lại sau khi sửa segments.json
clipkit doctor                                                   # kiểm tra FFmpeg, GPU
```

Kết quả nằm ở `Videos\ClipKit\<tên-video>\clips\`:

| File | Nội dung |
| :--- | :--- |
| `01_tieu-de.mp4` | Clip 1080×1920, 30 fps, âm lượng chuẩn hóa −14 LUFS |
| `01_tieu-de.srt` | Phụ đề rời, để tải lên kèm video |
| `01_tieu-de.jpg` | Ảnh bìa |
| `upload.txt` | Tiêu đề, mô tả, dòng ghi nguồn cho từng clip |

Thư mục video còn có `transcript.json` (phụ đề toàn bộ) và `segments.json` (các đoạn được chọn). Chạy lại cùng một link sẽ dùng lại video và phụ đề đã có, không tải hay nhận dạng lại.

### Tùy chọn chính

| Tùy chọn | Mặc định | Ý nghĩa |
| :--- | :--- | :--- |
| `-n, --clips` | 5 | Số clip |
| `--min / --max` | 20 / 60 | Độ dài mỗi clip (giây) |
| `--model` | small | `tiny`, `base`, `small`, `medium`, `large-v3`, `turbo` |
| `--language` | tự nhận | `vi`, `en`… đặt cố định thì chính xác hơn |
| `--section` | cả video | Ví dụ `10:00-25:00` |
| `--layout` | blur | `blur` video giữa nền mờ · `crop` cắt đầy khung · `fit` viền đen |
| `--no-captions`, `--no-title`, `--uppercase` | | Tắt phụ đề, tắt tiêu đề, phụ đề viết hoa |
| `--encoder` | auto | `nvenc` (GPU NVIDIA) hoặc `cpu` |
| `--device` | auto | Whisper chạy `cuda` hay `cpu` |
| `--selector` | heuristic | `llm` để nhờ model AI chọn (xem bên dưới) |
| `--cookies-from-browser` | | `chrome`/`edge`/`firefox` khi trang bắt đăng nhập |

## ClipKit chọn đoạn thế nào

Bản ghi được tách thành câu, rồi mọi cửa sổ gồm các câu liền nhau dài từ `--min` đến `--max` giây được chấm điểm theo:

- **Mật độ lời nói**: nói nhanh, ít khoảng lặng.
- **Âm lượng**: đoạn to hơn mức trung bình của video thường là lúc hào hứng.
- **Câu mở đầu**: câu hỏi, con số, từ khóa gây tò mò ("tại sao", "bí mật", "sai lầm", "tiền"…) được cộng điểm; mở bằng từ nối ("và", "nhưng", "thì", "vậy"…) bị trừ.
- **Kết thúc trọn câu** và độ dài gần mức giữa.

Các cửa sổ điểm cao nhất, không chồng lên nhau, được chọn. Cách này nhanh và miễn phí nhưng không hiểu nội dung như người; hãy xem `segments.json`, sửa `start`/`end`/`title` hoặc đặt `"enabled": false` cho đoạn không ưng, rồi chạy `clipkit render`.

### Tùy chọn: nhờ AI chọn đoạn (`--selector llm`)

Dùng được với mọi API tương thích OpenAI (OpenAI, DeepSeek, BytePlus ModelArk, OpenRouter, Ollama…). Chỉ gửi **một** yêu cầu mỗi video, giới hạn bởi `CLIPKIT_LLM_MAX_TOKENS` (mặc định 2000 token trả lời).

```powershell
$env:CLIPKIT_LLM_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
$env:CLIPKIT_LLM_API_KEY  = "..."
$env:CLIPKIT_LLM_MODEL    = "deepseek-v4-1-flash-260910"
clipkit run "LINK" --selector llm
```

## Bản quyền và kiếm tiền

ClipKit in giấy phép của video khi tải và cảnh báo nếu video không phải Creative Commons. Lưu ý:

- Chỉ đăng clip từ nội dung bạn sở hữu, được cấp phép, hoặc có giấy phép **Creative Commons Attribution** (lọc trên YouTube: Bộ lọc → Creative Commons). Với CC BY phải ghi nguồn; `upload.txt` đã có sẵn dòng ghi nguồn.
- YouTube không bật kiếm tiền cho nội dung chỉ đăng lại mà không thêm giá trị mới (bình luận, biên tập, phân tích), kể cả khi được phép dùng.

## Mẹo tốc độ

- Whisper là bước chậm nhất. `--model base` nhanh gấp khoảng 2 lần `small` nhưng sai chính tả nhiều hơn; `--section` để chỉ xử lý khúc cần.
- **Whisper trên GPU NVIDIA**: cần thêm thư viện CUDA 12 (cuBLAS, cuDNN 9). Nếu thiếu, ClipKit tự chuyển sang CPU và báo trong log.
- Dựng clip dùng NVENC khi có GPU NVIDIA, chỉ mất vài giây mỗi clip.

Model Whisper và FFmpeg tự tải được lưu ở `%LOCALAPPDATA%\ClipKit` (Windows) hoặc `~/.clipkit`.

## Phát triển

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

Build bản Windows (cần [Inno Setup 6](https://jrsoftware.org/isinfo.php) cho file cài):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -DownloadFfmpeg
```

Ra `dist\ClipKit-Setup-<ver>.exe` và `dist\ClipKit-<ver>-portable.zip`. Đẩy tag `v*` lên GitHub thì workflow **Release** tự build và đính kèm hai file vào trang Releases.

```
src/clipkit/
  cli.py         lệnh run / render / doctor, chế độ hỏi đáp khi mở bằng double-click
  pipeline.py    nối các bước, lưu kết quả trung gian để chạy lại nhanh
  download.py    yt-dlp, đọc giấy phép
  transcribe.py  faster-whisper, đo âm lượng
  select.py      chấm điểm và chọn đoạn
  llm.py         chọn đoạn bằng API (tùy chọn)
  captions.py    phụ đề ASS tô sáng từng từ, SRT
  render.py      FFmpeg: khung 9:16, NVENC/x264, chuẩn hóa âm lượng
  deps.py        tìm hoặc tự tải FFmpeg, kiểm tra GPU
```

Giấy phép MIT.
