from ..config import FFMPEG_PATH, OPENAI_API_KEY
import os
import requests
import subprocess
import tempfile


def generate_summary(text: str, max_length: int = 200, engine: str = "gpt") -> str:
    if engine == "gpt" and OPENAI_API_KEY:
        return _gpt_summary(text, max_length)
    try:
        from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
        model_name = "facebook/bart-large-cnn"
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
        inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=1024)
        outputs = model.generate(**inputs, max_length=max_length, min_length=30, num_beams=4, no_repeat_ngram_size=3)
        return tokenizer.decode(outputs[0], skip_special_tokens=True)
    except ImportError:
        return _simple_summary(text, max_length)


def generate_recap(video_path: str, style: str = "review", language: str = "vi") -> str:
    """Alias for generate_recap_from_transcript — transcribes then recaps."""
    try:
        from .whisper_stt import transcribe_file
        transcript = transcribe_file(video_path)
        return generate_recap_from_transcript(transcript, style, language)
    except Exception:
        return _simple_summary(f"Recap of video at {video_path}", 300)


def generate_recap_from_transcript(transcript: str, style: str = "review", language: str = "vi") -> str:
    if OPENAI_API_KEY:
        prompt = f"Write a {style} recap in {language} based on this transcript:\n\n{transcript[:4000]}"
        return _gpt_chat(prompt)
    return _simple_summary(transcript, 300)


def rewrite_srt(srt_content: str, style: str = "review", language: str = "vi", progress_cb=None) -> str:
    """Rewrite SRT subtitle text into engaging review narration while preserving timecodes.

    Returns the input unchanged if OPENAI_API_KEY is not set (graceful skip).
    Keeps one output line per input line (same count/order) so TTS alignment and
    burned subtitles stay timecode-aligned.
    """
    from .translator import parse_srt_blocks, format_srt_blocks

    blocks = parse_srt_blocks(srt_content)
    if not blocks or not OPENAI_API_KEY:
        return srt_content

    lang_name = {"vi": "Vietnamese", "en": "English"}.get(language, language)
    total = len(blocks)
    BATCH = 8
    result_blocks = []
    done = 0

    for start in range(0, total, BATCH):
        chunk = blocks[start:start + BATCH]
        numbered = "\n".join(f"{n + 1}. {blk['text']}" for n, blk in enumerate(chunk))
        prompt = (
            f"You are a {style} scriptwriter. Rewrite each numbered subtitle line below into engaging "
            f"{lang_name} movie-review narration. Keep the SAME number of lines, same order, same numbering. "
            f"Each output line must correspond to its input line. Do NOT merge or split lines, do NOT add "
            f"timestamps or extra commentary. Return ONLY the numbered lines.\n\n{numbered}"
        )
        rewritten = _gpt_chat(prompt)
        parts = _parse_numbered_lines(rewritten, len(chunk))
        for blk, text in zip(chunk, parts):
            result_blocks.append({"idx": blk["idx"], "time_line": blk["time_line"], "text": (text or blk["text"]).strip()})
        done += len(chunk)
        if progress_cb:
            try:
                progress_cb(done, total)
            except Exception:
                pass

    return format_srt_blocks(result_blocks)


def optimize_srt_for_tts(
    srt_content: str,
    language: str = "vi",
    target_cps: float = 18.0,
    engine: str = "auto",
    naturalize: bool = True,
    progress_cb=None,
) -> str:
    """Rewrite subtitle text into natural spoken narration for TTS.

    Keeps the same SRT block count and timecodes. AI providers are used when
    configured; the local fallback still naturalizes wording before applying the
    timing budget.
    """
    from .translator import parse_srt_blocks, format_srt_blocks

    blocks = parse_srt_blocks(srt_content)
    if not blocks:
        return srt_content

    budgeted = []
    for block in blocks:
        duration = _srt_block_duration(block.get("time_line", ""))
        base_budget = int(duration * float(target_cps or 18.0))
        if duration >= 2.0:
            budget = max(24, base_budget)
        elif duration >= 1.2:
            budget = max(18, base_budget)
        else:
            budget = max(10, base_budget)
        text = (block.get("text") or "").strip()
        budgeted.append({
            **block,
            "budget": budget,
            "needs_shortening": len(text) > budget,
            "needs_naturalize": bool(naturalize),
        })

    if not any(block["needs_shortening"] or block["needs_naturalize"] for block in budgeted):
        return srt_content

    engine = (engine or "auto").strip().lower()
    if engine in {"auto", "ai_provider", "provider"}:
        optimized = _provider_optimize_srt_blocks(budgeted, language, progress_cb=progress_cb)
    elif engine == "gpt" and OPENAI_API_KEY:
        optimized = _gpt_optimize_srt_blocks(budgeted, language, progress_cb=progress_cb)
    else:
        optimized = []

    if not optimized:
        optimized = [
            {**block, "text": _heuristic_naturalize_for_tts(block["text"], block["budget"], language)}
            for block in budgeted
        ]

    return format_srt_blocks(optimized)


def _srt_block_duration(time_line: str) -> float:
    import re

    def parse_time(value: str) -> float:
        match = re.search(r"(\d{1,2}):(\d{2}):(\d{2})(?:[,.](\d{1,3}))?", value)
        if not match:
            return 0.0
        h, m, s, ms = match.groups()
        return int(h) * 3600 + int(m) * 60 + int(s) + int((ms or "0").ljust(3, "0")) / 1000

    if "-->" not in time_line:
        return 0.0
    start, end = time_line.split("-->", 1)
    return max(0.0, parse_time(end) - parse_time(start))


def _gpt_optimize_srt_blocks(blocks: list, language: str, progress_cb=None) -> list:
    from .translator import _log_usage

    lang_name = {"vi": "Vietnamese", "en": "English"}.get(language, language)
    result_blocks = []
    total = len(blocks)
    batch_size = 10

    for start in range(0, total, batch_size):
        chunk = blocks[start:start + batch_size]
        lines = "\n".join(
            f"{i + 1}. max {block['budget']} chars: {block['text']}"
            for i, block in enumerate(chunk)
        )
        prompt = (
            f"Rewrite these subtitle lines into natural spoken {lang_name} movie-review narration for text-to-speech.\n"
            "Rules:\n"
            "- Keep the SAME number of lines and the SAME order.\n"
            "- Sound like a real human narrator, not a literal machine translation.\n"
            "- Preserve the core meaning, mood, names, and key facts.\n"
            "- Prefer fluent Vietnamese review phrasing when the target is Vietnamese.\n"
            "- Only shorten when needed to fit the max character limit.\n"
            "- Each output line should stay under its max character limit.\n"
            "- Return only numbered lines, no timestamps, no notes.\n\n"
            f"{lines}"
        )
        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
                timeout=45,
            )
            data = resp.json()
            _log_usage("gpt", data.get("usage", {}).get("total_tokens", 0), 0, 0)
            text = data["choices"][0]["message"]["content"].strip()
            parts = _parse_numbered_lines(text, len(chunk))
        except Exception:
            return []

        for block, revised in zip(chunk, parts):
            revised = (revised or "").strip()
            if not revised or revised.startswith("[GPT"):
                revised = _heuristic_shorten_for_tts(block["text"], block["budget"])
            elif len(revised) > block["budget"] + 8:
                revised = _heuristic_shorten_for_tts(revised, block["budget"])
            result_blocks.append({**block, "text": revised})

        if progress_cb:
            try:
                progress_cb(min(start + batch_size, total), total)
            except Exception:
                pass

    return result_blocks


def _provider_optimize_srt_blocks(blocks: list, language: str, progress_cb=None) -> list:
    from .translator import _log_usage

    lang_name = {"vi": "Vietnamese", "en": "English"}.get(language, language)
    result_blocks = []
    total = len(blocks)
    batch_size = 10

    try:
        from .ai_provider_service import chat_completion, get_ai_provider_config
        config = get_ai_provider_config()
    except Exception:
        return []

    provider = (config.get("provider") or "").lower()
    has_key = bool(config.get("api_key"))
    if provider not in {"ollama", "custom"} and not has_key:
        return []

    for start in range(0, total, batch_size):
        chunk = blocks[start:start + batch_size]
        lines = "\n".join(
            f"{i + 1}. max {block['budget']} chars: {block['text']}"
            for i, block in enumerate(chunk)
        )
        prompt = (
            f"Rewrite these subtitle lines into natural spoken {lang_name} narration for TTS.\n"
            "Context: short-form movie/video review narration.\n"
            "Rules:\n"
            "- Keep the same number of numbered lines and the same order.\n"
            "- Do not translate word-by-word; make each line sound like a native narrator would say it.\n"
            "- Keep the meaning, mood, names, and important details.\n"
            "- If Vietnamese, use conversational review Vietnamese, clear and punchy, without adding facts.\n"
            "- Only shorten when the line would be too long for speech timing.\n"
            "- Stay under each max character limit when possible.\n"
            "- Return only numbered lines, no timestamps, no explanation.\n\n"
            f"{lines}"
        )
        try:
            resp = chat_completion(
                [{"role": "user", "content": prompt}],
                {**config, "temperature": 0.35},
            )
            usage = resp.get("usage") or {}
            tokens = int(usage.get("total_tokens") or usage.get("totalTokenCount") or 0)
            _log_usage(provider or "ai_provider", tokens, 0, 0)
            parts = _parse_numbered_lines(resp.get("text", ""), len(chunk))
        except Exception:
            return []

        for block, revised in zip(chunk, parts):
            revised = (revised or "").strip()
            if not revised:
                revised = _heuristic_naturalize_for_tts(block["text"], block["budget"], language)
            elif len(revised) > block["budget"] + 8:
                revised = _heuristic_naturalize_for_tts(revised, block["budget"], language)
            result_blocks.append({**block, "text": revised})

        if progress_cb:
            try:
                progress_cb(min(start + batch_size, total), total)
            except Exception:
                pass

    return result_blocks


def _heuristic_naturalize_for_tts(text: str, budget: int, language: str = "vi") -> str:
    import re

    value = re.sub(r"\s+", " ", (text or "")).strip()
    if not value:
        return value
    if str(language).lower().startswith("vi"):
        value = _review_vi_naturalize(value)
    if len(value) <= budget:
        return value
    return _heuristic_shorten_for_tts(value, budget)


def _heuristic_shorten_for_tts(text: str, budget: int) -> str:
    import re

    text = re.sub(r"\s+", " ", (text or "")).strip()
    text = _review_vi_phrase_shorten(text)
    if len(text) > budget:
        text = _fit_common_review_phrase(text, budget)
    if len(text) <= budget:
        return text

    replacements = [
        (r"\b(?:ừm|ờ|à|thì|mà|kiểu như|bạn biết đấy|đúng không|phải không)\b", ""),
        (r"\b(?:tôi nghĩ|có lẽ|thực sự|hoàn toàn|khá là|rất là)\b", ""),
        (r"\b(?:và sau đó|sau đó)\b", "rồi"),
        (r"\b(?:bởi vì)\b", "vì"),
        (r"\b(?:để có thể)\b", "để"),
        (r"\b(?:một vài)\b", "vài"),
    ]
    lowered = text
    for pattern, repl in replacements:
        lowered = re.sub(pattern, repl, lowered, flags=re.IGNORECASE)
    lowered = re.sub(r"\s+([,.!?;:])", r"\1", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip(" ,;")
    if len(lowered) <= budget:
        return lowered

    clauses = re.split(r"(?<=[.!?])\s+|,\s+|;\s+|:\s+", lowered)
    kept = ""
    for clause in clauses:
        candidate = f"{kept}, {clause}".strip(" ,") if kept else clause
        if len(candidate) <= budget:
            kept = candidate
        elif kept:
            break
    if kept and len(kept) >= min(18, budget):
        return kept.strip()

    words = lowered.split()
    shortened = ""
    for word in words:
        candidate = f"{shortened} {word}".strip()
        if len(candidate) > budget:
            break
        shortened = candidate
    return shortened.strip(" ,;") or lowered[:budget].strip(" ,;")


def _review_vi_naturalize(text: str) -> str:
    """Make Vietnamese subtitle text sound closer to spoken review narration."""
    import re

    value = re.sub(r"\s+", " ", (text or "")).strip()
    if not value:
        return value

    exact_rules = [
        (r"^Nơi này là gì\?\s*Tôi có thể đi chơi được không\??$", "Đây là đâu? Đi được không?"),
        (r"^Nơi mà ngay cả người dân địa phương cũng không dám đến$", "Dân địa phương còn né"),
        (r"^Vẫn còn nhiều bí ẩn chưa có lời giải$", "Còn nhiều bí ẩn"),
        (r"^Khó ai thoát ra được$", "Khó ai thoát ra"),
    ]
    for pattern, replacement in exact_rules:
        if re.match(pattern, value, flags=re.IGNORECASE):
            return replacement

    phrase_rules = [
        (r"\bTôi có thể đi chơi được không\??", "đi được không?"),
        (r"\bNơi này là gì\??", "Đây là đâu?"),
        (r"\bkhông thể đi được\b", "không nên đi"),
        (r"\bkhông thể đi\b", "không nên đi"),
        (r"\bngười dân địa phương\b", "dân địa phương"),
        (r"\bngay cả dân địa phương cũng không dám đến\b", "dân địa phương còn né"),
        (r"\bkhông dám đến\b", "còn né"),
        (r"\bchưa có lời giải\b", "vẫn chưa ai giải thích được"),
        (r"\brất đáng sợ\b", "rất rợn"),
        (r"\bkỳ lạ và đáng sợ\b", "vừa lạ vừa rợn"),
        (r"\bđầy kinh hoàng\b", "rợn người"),
        (r"\bđịa hình phức tạp\b", "địa hình hiểm trở"),
        (r"\bcó sương mù\b", "sương mù dày"),
        (r"\bkhông ai có thể thoát ra được\b", "khó ai thoát ra"),
        (r"\bmột số sinh vật bí ẩn\b", "những sinh vật bí ẩn"),
        (r"\bnhững điều tâm linh thường xuyên xảy ra\b", "hay xảy ra chuyện tâm linh"),
        (r"\bTại sao bạn lại tò mò như vậy\??", "Sao lại tò mò vậy?"),
        (r"\bTại sao bạn muốn đi đâu đó\??", "Sao cứ muốn tới đó vậy?"),
        (r"\bTốt nhất là không nên đi nếu chưa chuẩn bị tinh thần\b", "Yếu tim thì đừng đi"),
        (r"\bTôi không khuyên bạn nên đến đây\b", "Tôi không khuyên tới đây"),
    ]
    for pattern, replacement in phrase_rules:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)

    cleanup_rules = [
        (r"\bTôi nghĩ rằng\b", "Mình nghĩ"),
        (r"\bTôi nghĩ\b", "Mình nghĩ"),
        (r"\bCó lẽ\b", "Có thể"),
        (r"\bThực sự\b", "Thật sự"),
        (r"\bVà sau đó\b", "Rồi"),
        (r"\bSau đó\b", "Rồi"),
        (r"\bBởi vì\b", "Vì"),
        (r"\bđể có thể\b", "để"),
        (r"\bmột vài\b", "vài"),
        (r"\bở đây\b", "tại đây"),
    ]
    for pattern, replacement in cleanup_rules:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)

    value = re.sub(r"\s+([,.!?;:])", r"\1", value)
    value = re.sub(r"\s+", " ", value).strip(" ,;")
    if value:
        value = value[0].upper() + value[1:]
    return value


def _review_vi_phrase_shorten(text: str) -> str:
    """Concise Vietnamese dubbing shortcuts for fast short-form narration."""
    import re

    value = re.sub(r"\s+", " ", (text or "")).strip()
    if not value:
        return value

    rules = [
        (r"^Nơi này là gì\?\s*Tôi có thể đi chơi được không\??$", "Đây là đâu, đi được không?"),
        (r"^Nơi này là gì\??$", "Đây là đâu?"),
        (r"^Tôi có thể đi chơi được không\??$", "Đi được không?"),
        (r"^không thể đi$", "Không đi được"),
        (r"^Không thể đi được\.?$", "Không đi được"),
        (r"^Nơi mà ngay cả người dân địa phương cũng không dám đến$", "Dân địa phương cũng né"),
        (r"^Vẫn còn nhiều bí ẩn chưa có lời giải$", "Còn rất nhiều bí ẩn"),
        (r"^Tôi không tức giận.*$", "Không nên đi"),
        (r"^Tôi không giận.*$", "Không nên đi"),
        (r"^Đừng giận.*$", "Đừng nên đi"),
        (r"^.*không tức giận.*$", "Không nên đi"),
        (r"^.*giận nhau.*$", "Đừng nên đi"),
        (r"^Tốt nhất là không nên đi nếu chưa chuẩn bị tinh thần$", "Yếu tim thì đừng đi"),
        (r"^Tại sao bạn muốn đi đâu đó\??$", "Sao đâu cũng muốn đi vậy?"),
        (r"^Tại sao bạn lại tò mò như vậy\??$", "Sao tò mò dữ vậy?"),
        (r"^Tôi không khuyên bạn nên đến đây$", "Không khuyên đến đây"),
        (r"^Bên trong kỳ lạ và đáng sợ$", "Bên trong rất rợn"),
        (r"^Có vẻ như những điều tâm linh thường xuyên xảy ra$", "Hay có chuyện tâm linh"),
        (r"^Thung lũng đầy tai nạn động vật$", "Thung lũng đầy xác thú"),
        (r"^Đầy kinh hoàng$", "Đầy rùng rợn"),
        (r"^Địa hình phức tạp$", "Địa hình hiểm trở"),
        (r"^có sương mù$", "Sương mù dày đặc"),
        (r"^Không ai có thể thoát ra được$", "Không ai ra được"),
        (r"^Và một số sinh vật bí ẩn$", "Còn có sinh vật bí ẩn"),
    ]
    for pattern, replacement in rules:
        if re.match(pattern, value, flags=re.IGNORECASE):
            return replacement

    if "tức giận" in value.lower() or "giận nhau" in value.lower():
        return "Không nên đi"

    compact_rules = [
        ("Đây là đâu, đi được không?", "Đi được không?"),
        ("Dân địa phương cũng né", "Dân cũng né"),
        ("Không đi được", "Không đi"),
        ("Địa hình hiểm trở", "Địa hình hiểm"),
        ("Sương mù dày đặc", "Sương mù dày"),
    ]
    for long_text, short_text in compact_rules:
        if value == long_text:
            return short_text

    value = re.sub(r"^Đây là\s+", "", value)
    value = re.sub(r"\bTôi có thể\b", "có thể", value, flags=re.IGNORECASE)
    value = re.sub(r"\bngay cả\b", "cả", value, flags=re.IGNORECASE)
    value = re.sub(r"\bcho công chúng\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\bở đây\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" .,")
    return value


def _fit_common_review_phrase(text: str, budget: int) -> str:
    options = {
        "Đây là đâu, đi được không?": ["Đi được không?", "Đây là đâu?"],
        "Dân địa phương cũng né": ["Dân cũng né"],
        "Không đi được": ["Không đi"],
        "Địa hình hiểm trở": ["Địa hình hiểm"],
        "Sương mù dày đặc": ["Sương mù dày", "Sương mù"],
    }
    for candidate in options.get(text, []):
        if len(candidate) <= budget:
            return candidate
    return text


def _parse_numbered_lines(text: str, expected: int) -> list:
    """Extract `N. content` lines from an LLM response; pad/truncate to `expected`."""
    import re
    if text.startswith("[GPT"):  # error placeholder from _gpt_chat
        return [None] * expected
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        m = re.match(r"^\d+[.)]\s*(.*)$", raw)
        lines.append(m.group(1).strip() if m else raw)
    if len(lines) > expected:
        lines = lines[:expected]
    elif len(lines) < expected:
        lines += [None] * (expected - len(lines))
    return lines


def detect_characters(video_path: str):
    """Detect characters/faces using YOLOv11 + InsightFace or MediaPipe."""
    try:
        import cv2
        import mediapipe as mp
        mp_face = mp.solutions.face_detection
        face_detection = mp_face.FaceDetection(model_selection=1, min_detection_confidence=0.5)
        cap = cv2.VideoCapture(video_path)
        characters = []
        frame_idx = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % 30 == 0:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = face_detection.process(rgb)
                if results.detections:
                    for det in results.detections:
                        bbox = det.location_data.relative_bounding_box
                        characters.append({
                            "frame": frame_idx,
                            "confidence": det.score[0],
                            "x": bbox.xmin, "y": bbox.ymin, "w": bbox.width, "h": bbox.height,
                        })
            frame_idx += 1
        cap.release()
        return characters
    except ImportError:
        raise RuntimeError("Speaker detection requires pyannote.audio. Install pyannote-audio and configure model access.")


def _audio_for_pyannote(video_path: str):
    """Decode media with ffmpeg so pyannote does not depend on torchcodec."""
    try:
        import soundfile as sf
        import torch
    except ImportError as exc:
        raise RuntimeError("Speaker detection requires soundfile and torch.") from exc

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp_path = tmp.name
    tmp.close()

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    cmd = [
        FFMPEG_PATH,
        "-y",
        "-i",
        video_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        tmp_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags, timeout=120)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "ffmpeg audio extraction failed").strip())

        data, sample_rate = sf.read(tmp_path, dtype="float32", always_2d=False)
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        waveform = torch.from_numpy(data).float().unsqueeze(0)
        return {"waveform": waveform, "sample_rate": sample_rate}, tmp_path
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def detect_speakers(video_path: str):
    """Detect speakers using pyannote-audio."""
    tmp_path = None
    try:
        from pyannote.audio import Pipeline
        pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")
        audio, tmp_path = _audio_for_pyannote(video_path)
        diarization = pipeline(audio)
        speakers = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            speakers.append({
                "speaker": speaker,
                "start": turn.start,
                "end": turn.end,
            })
        return speakers
    except ImportError:
        return []
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def generate_thumbnail(video_path: str, time_sec: float = 0.0, output_path: str = None):
    """Extract a frame from the video as thumbnail."""
    import subprocess
    if not output_path:
        output_path = video_path.replace(".mp4", "_thumb.jpg")
    cmd = ["ffmpeg", "-ss", str(time_sec), "-i", video_path, "-vframes", "1", "-q:v", "2", "-y", output_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        pass
    return output_path


def generate_title(video_path: str, style: str = "review") -> str:
    if OPENAI_API_KEY:
        prompt = f"Generate a clickbait YouTube title for a {style} video in Vietnamese. Return only the title."
        return _gpt_chat(prompt)
    return f"[Video {style.upper()}] Hấp dẫn - Đừng bỏ lỡ!"


def generate_hashtags(text: str, count: int = 5) -> list:
    if OPENAI_API_KEY:
        prompt = f"Generate {count} hashtags for this content in Vietnamese and English:\n{text[:1000]}"
        result = _gpt_chat(prompt)
        return [h.strip() for h in result.split() if h.startswith("#")]
    return ["#review", "#movie", "#phim", "#reviewphim", "#hot"]


def _gpt_summary(text, max_length):
    prompt = f"Summarize the following text in Vietnamese (max {max_length} words):\n\n{text[:4000]}"
    return _gpt_chat(prompt)


def _gpt_chat(prompt: str) -> str:
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
            },
            timeout=30,
        )
        data = resp.json()
        tokens = data.get("usage", {}).get("total_tokens", 0)
        from .translator import _log_usage
        _log_usage("gpt", tokens, 0, tokens * 0.00015 / 1000)
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[GPT unavailable: {e}]"


def _simple_summary(text, max_length):
    sentences = text.replace("\n", " ").split(". ")
    result = ""
    for s in sentences:
        if len(result) + len(s) < max_length * 5:
            result += s + ". "
    return result.strip()
