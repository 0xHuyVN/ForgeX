# ForgeX — Tool Tự Động Làm Video Review & Recap

> **Phiên bản:** 2.1.1 · **Nền tảng:** Windows · **Ngôn ngữ:** Python + JavaScript

ForgeX là tool **all-in-one** để tạo video review phim, recap, TikTok, YouTube Shorts — từ video gốc (Trung, Hàn, Nhật, Anh) ra thành phẩm có **phụ đề Việt + giọng đọc AI** chỉ trong vài click.

---

## Tool này làm được gì?

| Bước | Mô tả |
|------|-------|
| **1. Download** | Tải video từ YouTube, TikTok, Bilibili, Douyin... qua yt-dlp |
| **2. Tách lời (STT)** | Dùng Whisper AI nhận dạng giọng nói → xuất phụ đề SRT |
| **3. Dịch phụ đề** | Dịch sang tiếng Việt (hoặc ngôn ngữ khác) bằng NLLB, GPT, Gemini, Google... |
| **4. Giọng đọc AI (TTS)** | Tạo voiceover tự động bằng Edge TTS, Azure, CapCut, ElevenLabs, FPT, Valtec... |
| **5. Render** | Ghép video + phụ đề + giọng đọc + nhạc nền → xuất file hoàn chỉnh |
| **6. Đăng** | Đăng lên YouTube, TikTok, Facebook trực tiếp từ tool |

Toàn bộ chạy qua **giao diện web** mở trên trình duyệt, không cần cài thêm phần mềm edit.

---

## Yêu cầu hệ thống

| Thành phần | Yêu cầu |
|------------|---------|
| **OS** | Windows 10/11 (64-bit) |
| **Python** | 3.10 trở lên ([tải tại đây](https://www.python.org/downloads/)) — **nhớ tick "Add Python to PATH"** |
| **FFmpeg** | Bắt buộc. [Tải FFmpeg](https://www.gyan.dev/ffmpeg/builds/) → giải nén → thêm vào PATH |
| **RAM** | Tối thiểu 8GB (khuyến nghị 16GB nếu dùng Whisper large) |
| **GPU** | Không bắt buộc. Có GPU NVIDIA (CUDA) sẽ tăng tốc Whisper/TTS đáng kể |
| **Ổ cứng** | Tối thiểu **5GB** trống để cài đặt (source ~1MB + thư viện Python ~4GB + Whisper model ~150MB). Khuyến nghị **10GB+** vì cần thêm dung lượng cho video tải về, file render, cache. Nếu dùng Whisper `large-v3` cần thêm ~3GB cho model |

---

## Cài đặt

### Bước 1 — Clone repo

```bash
git clone https://github.com/0xHuyVN/ForgeX.git
cd ForgeX
```

### Bước 2 — Tạo môi trường ảo (khuyến nghị)

```bash
python -m venv .venv
.venv\Scripts\activate
```

### Bước 3 — Cài thư viện

```bash
pip install -r requirements.txt
```

> **Lưu ý:** Một số thư viện nặng (PyTorch, Whisper) sẽ tải lần đầu. Nếu có GPU NVIDIA, cài PyTorch bản CUDA:
> ```bash
> pip install torch --index-url https://download.pytorch.org/whl/cu121
> ```

### Bước 4 — Cấu hình `.env`

Copy file mẫu rồi chỉnh sửa:

```bash
copy .env.example .env
```

Mở file `.env` và điền các API key cần thiết (xem phần [Cấu hình](#cấu-hình) bên dưới).

### Bước 5 — Chạy

```bash
run.bat
```

Trình duyệt sẽ tự mở tại `http://127.0.0.1:7860`. Nếu không, mở thủ công.

---

## Cấu hình (file `.env`)

### Bắt buộc

| Key | Giá trị | Ghi chú |
|-----|---------|---------|
| `FFMPEG_PATH` | `ffmpeg` | Nếu FFmpeg đã trong PATH thì để mặc định |
| `FFPROBE_PATH` | `ffprobe` | Đi kèm FFmpeg |
| `YTDLP_PATH` | `yt-dlp` | Đã cài qua `pip install yt-dlp` |

### Whisper (Tách lời)

| Key | Giá trị | Ghi chú |
|-----|---------|---------|
| `WHISPER_MODEL` | `base` | Các mức: `tiny`, `base`, `small`, `medium`, `large-v3`. Lớn hơn = chính xác hơn nhưng chậm hơn |
| `WHISPER_DEVICE` | `auto` | `auto` tự chọn GPU/CPU. Set `cuda` để ép dùng GPU |
| `WHISPER_COMPUTE_TYPE` | `auto` | `float16` cho GPU, `int8` cho CPU yếu |
| `VOCAL_SEPARATION_ENABLED` | `false` | Set `true` để tách vocal trước khi nhận dạng (cần Demucs, tốn RAM) |

### API Key cho Dịch & AI

| Key | Để làm gì | Bắt buộc? |
|-----|-----------|-----------|
| `OPENAI_API_KEY` | Dịch bằng GPT, viết recap, tối ưu phụ đề | Không — có thì chất lượng dịch tốt hơn |
| `GEMINI_API_KEY` | Dịch bằng Gemini (miễn phí quota cao) | Không |
| `OPENROUTER_API_KEY` | Dùng nhiều model AI qua OpenRouter | Không |

> **Không có API key nào?** Tool vẫn hoạt động bình thường — dịch sẽ dùng NLLB (offline, chạy local) hoặc Google Translate (miễn phí).

### API Key cho Giọng Đọc (TTS)

| Key | Provider | Ghi chú |
|-----|----------|---------|
| `AZURE_TTS_KEY` | Azure Speech | Giọng tự nhiên, cần đăng ký Azure |
| `AZURE_TTS_REGION` | Azure | Mặc định `eastus` |
| `ELEVENLABS_API_KEY` | ElevenLabs | Giọng cực kỳ tự nhiên, có free tier |
| `FPT_API_KEY` | FPT.AI | Giọng Việt tốt |

> **Không có API key TTS?** Mặc định dùng **Edge TTS** (miễn phí, không cần key, giọng Việt khá tốt).

### Nâng cao

| Key | Mặc định | Ghi chú |
|-----|----------|---------|
| `PORT` | `7860` | Port chạy server |
| `MAX_QUEUE_WORKERS` | `1` | Số job xử lý song song. Tăng nếu máy mạnh |

---

## Cách sử dụng

### Quy trình cơ bản: Từ video gốc → video review hoàn chỉnh

#### 1. Tạo Project

Mở tool → bấm **Tạo Project mới** → đặt tên.

#### 2. Thêm video gốc

Có 2 cách:

- **Dán link**: Dán URL YouTube/TikTok/Bilibili → tool tự tải
- **Chọn file**: Bấm nút chọn file video từ máy tính

#### 3. Tách lời (Transcribe)

- Chọn **ngôn ngữ gốc** của video (ví dụ: `zh` = Trung Quốc, `ja` = Nhật, `ko` = Hàn, `en` = Anh)
- Bấm **Transcribe** → Whisper AI sẽ nhận dạng giọng nói → xuất phụ đề SRT
- Nếu video có nhạc nền ồn, bật **Vocal Separation** để tách vocal trước

#### 4. Dịch phụ đề

- Chọn **ngôn ngữ nguồn** và **ngôn ngữ đích** (thường là `vi`)
- Chọn **engine dịch**:
  - `nllb` — Offline, miễn phí, chạy local (mặc định)
  - `google` — Google Translate miễn phí
  - `gpt` — GPT-4o (cần API key, chất lượng cao nhất)
  - `gemini` — Gemini (cần API key, miễn phí quota lớn)
  - `deeplx` — DeepL qua proxy miễn phí
  - `ai_provider` — Bất kỳ AI provider nào bạn cấu hình
- Bấm **Translate**

#### 5. Tạo giọng đọc (TTS)

- Chọn **TTS Provider**:
  - `edge` — Microsoft Edge TTS (miễn phí, mặc định)
  - `fpt` — FPT.AI (giọng Việt tốt)
  - `azure` — Azure Speech (tự nhiên nhất)
  - `elevenlabs` — ElevenLabs (giọng premium)
  - `capcut` — Giọng CapCut (cần cài CapCut Desktop)
  - `valtec` — Giọng Việt offline
  - `google` — Google TTS
  - `clone` — Clone giọng nói (nâng cao)
- Chọn **giọng đọc** (ví dụ: `vi-VN-NamMinhNeural` cho nam, `vi-VN-HoaiMyNeural` cho nữ)
- Tùy chọn **Timeline Strategy**:
  - `subtitle_fit` — Giọng đọc khớp thời gian phụ đề (mặc định, phù hợp review)
  - `natural` — Giọng đọc tốc độ tự nhiên, tự dồn sang câu kế
- Bấm **Generate Voice**

#### 6. Render video

- Chọn các tùy chọn:
  - **Burn Subtitle**: Đóng phụ đề vào video
  - **Extend Video to TTS**: Kéo dài video nếu giọng đọc dài hơn
  - **Output Format**: `mp4` (mặc định)
  - **Quality**: `draft` (nhanh) hoặc `quality` (chất lượng cao)
- Bấm **Render** → Tool ghép tất cả lại thành video hoàn chỉnh

#### 7. Đăng video (tùy chọn)

- Hỗ trợ đăng trực tiếp lên **YouTube**, **TikTok**, **Facebook**
- Cần cấu hình OAuth/credentials cho từng nền tảng

---

### Quy trình nhanh: Full Pipeline

Thay vì làm từng bước, bạn có thể chạy **Full Pipeline** — tool tự động chạy tất cả:

```
Download → Transcribe → Translate → TTS → Render
```

Chỉ cần dán link video + chọn cài đặt → bấm 1 nút → ngồi chờ.

---

## Tính năng chi tiết

### Tách lời & Phụ đề

- **Faster-Whisper**: Nhận dạng giọng nói nhanh, hỗ trợ 99+ ngôn ngữ
- **WhisperX**: Căn chỉnh timestamp chính xác hơn (tùy chọn)
- **OCR Hardsub**: Trích xuất phụ đề cứng từ video (dùng RapidOCR)
- **Vocal Separation**: Tách vocal khỏi nhạc nền trước khi nhận dạng (Demucs)
- **Extract Subtitle Stream**: Trích xuất subtitle track từ file MKV/MP4

### Dịch thuật

- **10+ engine dịch**: NLLB, MarianMT, M2M100, SeamlessM4T (offline) + GPT, Gemini, Google, DeepLX, OpenRouter, Ollama (online)
- **Dịch thời gian thực**: Hiển thị tiến trình từng block
- **Semantic Segmentation**: Tự ngắt câu cho phụ đề dễ đọc hơn
- **Post-processing**: Tự sửa lỗi dịch phổ biến (đặc biệt Trung → Việt)

### Giọng đọc AI (TTS)

- **8 provider TTS**: Edge, Azure, ElevenLabs, FPT, Google, CapCut, Valtec, Voice Clone
- **Timeline-aligned TTS**: Giọng đọc tự đồng bộ với thời gian phụ đề
- **Auto tempo**: Tự tăng/giảm tốc giọng đọc để khớp timing
- **AI Subtitle Optimization**: GPT/Gemini tối ưu câu chữ cho giọng đọc tự nhiên hơn

### Video Processing

- **Scene Detection**: Tự động phát hiện cảnh (PySceneDetect + FFmpeg fallback)
- **Auto Reframe**: Tự chuyển đổi tỉ lệ khung hình (16:9 → 9:16 cho TikTok/Shorts)
- **Dynamic Templates**: Template video tự động cho recap, shorts
- **Merge Videos**: Ghép nhiều video thành một
- **Hard Subtitle Removal**: Xóa/blur phụ đề cứng trong video gốc
- **Quality Gate**: Kiểm tra chất lượng video sau render (tự động)

### AI & Nội dung

- **AI Recap**: Tự tạo kịch bản recap từ transcript
- **AI Rewrite**: Viết lại phụ đề theo phong cách review hấp dẫn
- **Title Generator**: Tạo tiêu đề clickbait cho YouTube
- **Hashtag Generator**: Tạo hashtag tự động
- **Speaker Detection**: Nhận dạng nhiều người nói (pyannote)
- **Face Detection**: Phát hiện khuôn mặt (MediaPipe)

### Quản lý & Hệ thống

- **Job Queue**: Hàng đợi xử lý, chạy nhiều job song song
- **Cache thông minh**: Kết quả STT, dịch, TTS, render đều được cache — chạy lại không mất thời gian
- **Preset system**: Lưu/tải bộ cài đặt sẵn (movie review, anime recap, TikTok recap...)
- **Asset Library**: Quản lý tất cả file video, audio, phụ đề theo project
- **Analytics**: Theo dõi API usage, token, chi phí
- **GPU Detection**: Tự phát hiện GPU NVIDIA/AMD

---

## Cấu trúc thư mục

```
ForgeX/
├── backend/                  # Backend Python (FastAPI)
│   ├── main.py               # Entry point, khởi tạo server
│   ├── config.py             # Cấu hình, đọc .env
│   ├── database.py           # SQLite database
│   ├── routers/              # API endpoints
│   │   ├── download.py       # API tải video
│   │   ├── subtitle.py       # API phụ đề
│   │   ├── voice.py          # API giọng đọc
│   │   ├── edit.py           # API chỉnh sửa
│   │   ├── export.py         # API xuất video
│   │   ├── pipeline.py       # API pipeline tự động
│   │   ├── publish.py        # API đăng video
│   │   └── ...
│   ├── services/             # Logic xử lý chính
│   │   ├── pipeline_service.py   # Dispatcher chạy các bước
│   │   ├── downloader.py         # Tải video (yt-dlp)
│   │   ├── whisper_stt.py        # Nhận dạng giọng nói
│   │   ├── translator.py         # Dịch phụ đề
│   │   ├── tts_engine.py         # Tổng hợp giọng nói
│   │   ├── ffmpeg_utils.py       # Render video (FFmpeg)
│   │   ├── ai_service.py         # AI recap, rewrite
│   │   └── ...
│   └── workers/              # Background workers
├── index.html                # Giao diện frontend
├── app.js                    # Logic frontend
├── style.css                 # Giao diện CSS
├── data/                     # Dữ liệu runtime
│   ├── presets/              # Các preset cài sẵn
│   ├── templates/            # Template video
│   └── ...
├── run.bat                   # Script khởi động (Windows)
├── run.ps1                   # Script khởi động (PowerShell)
├── update.bat                # Script push code lên GitHub
├── requirements.txt          # Thư viện Python
└── .env.example              # Mẫu cấu hình
```

---

## Preset có sẵn

| Preset | Mô tả |
|--------|-------|
| `movie_review` | Review phim dài — giọng đọc Việt, phụ đề burn |
| `anime_recap` | Recap anime — tốc độ nhanh, phụ đề kiểu anime |
| `tiktok_recap` | Video ngắn TikTok — auto reframe 9:16 |
| `shorts_auto` | YouTube Shorts — tối ưu cho mobile |
| `draft_fast` | Render nhanh bản nháp — chất lượng thấp, tốc độ cao |
| `quality` | Render chất lượng cao — tốn thời gian hơn |

---

## API Reference

Server chạy tại `http://127.0.0.1:7860`. Tài liệu API tự động tại:

```
http://127.0.0.1:7860/docs
```

### Endpoints chính

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `GET` | `/api/health` | Kiểm tra server |
| `POST` | `/api/download/` | Tải video |
| `POST` | `/api/subtitle/transcribe` | Tách lời |
| `POST` | `/api/subtitle/translate` | Dịch phụ đề |
| `POST` | `/api/voice/tts` | Tạo giọng đọc |
| `POST` | `/api/edit/render` | Render video |
| `POST` | `/api/pipeline/` | Chạy full pipeline |
| `POST` | `/api/publish/youtube` | Đăng YouTube |
| `POST` | `/api/publish/tiktok` | Đăng TikTok |
| `GET` | `/api/system/gpu` | Kiểm tra GPU |
| `GET` | `/api/system/info` | Thông tin hệ thống |
| `GET` | `/api/stats` | Thống kê sử dụng |

---

## Xử lý lỗi thường gặp

### "FFmpeg not found"

FFmpeg chưa được cài hoặc chưa thêm vào PATH.

```bash
# Kiểm tra
ffmpeg -version

# Nếu không có, tải từ https://www.gyan.dev/ffmpeg/builds/
# Giải nén → copy ffmpeg.exe vào C:\Windows hoặc thêm vào PATH
```

### "Whisper model download failed"

Lần chạy đầu, Whisper tải model (~140MB cho `base`). Cần internet ổn định.

```bash
# Thử tải trước
python -c "from faster_whisper import WhisperModel; WhisperModel('base')"
```

### "CUDA out of memory"

Model Whisper hoặc TTS quá lớn cho VRAM GPU.

```env
# Trong .env, đổi sang CPU
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
```

### "Port 7860 already in use"

```bash
# Đổi port trong .env
PORT=7861
```

Hoặc tool tự kill process cũ khi chạy `run.bat`.

### Video tải về bị lỗi / cần đăng nhập

```env
# Trong .env, chỉ đường dẫn đến file cookie
# Export cookie từ trình duyệt bằng extension "Get cookies.txt"
```

---

## Đóng góp

1. Fork repo
2. Tạo branch: `git checkout -b feature/ten-tinh-nang`
3. Commit: `git commit -m "Thêm tính năng X"`
4. Push: `git push origin feature/ten-tinh-nang`
5. Tạo Pull Request

---

## License

MIT License — Tự do sử dụng, chỉnh sửa, phân phối.

---

**Tác giả:** [0xHuyVN](https://github.com/0xHuyVN)
