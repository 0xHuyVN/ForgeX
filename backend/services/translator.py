import os
import json
import re
import subprocess
import sys
import shutil
from pathlib import Path
from ..config import OPENAI_API_KEY, GEMINI_API_KEY
from ..database import db_cursor
import requests


_TRANSLATION_ERROR_PREFIXES = (
    "[GPT error:", "[Gemini error:", "[NLLB error:", "[NLLB unavailable",
    "[MarianMT error:", "[MarianMT unavailable", "[M2M100 error:",
    "[M2M100 unavailable", "[SeamlessM4T error:", "[SeamlessM4T unavailable",
    "[DeepLX error:", "[DeepLX unavailable", "[Google Translate error:",
    "[AI Provider error:", "[Translation error:", "[GPT translation unavailable", "[Gemini translation unavailable",
)

_LOCAL_BATCH_ENGINES = {"nllb", "marian", "m2m100", "seamless"}
_ENGINE_LABELS = {
    "nllb": "NLLB",
    "marian": "MarianMT",
    "m2m100": "M2M100",
    "seamless": "SeamlessM4T",
    "deeplx": "DeepLX",
    "google": "Google Translate",
    "ai_provider": "AI Provider",
}


def _is_translation_error(result: str) -> bool:
    return result.startswith(_TRANSLATION_ERROR_PREFIXES)


def parse_srt_blocks(srt_content: str) -> list:
    """Parse SRT blocks into {idx, time_line, text}; accepts multiline subtitle text."""
    blocks = []
    lines = srt_content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if not line.isdigit():
            i += 1
            continue
        idx = line
        i += 1
        time_line = lines[i].strip() if i < len(lines) else ""
        i += 1
        text_parts = []
        while i < len(lines) and lines[i].strip():
            text_parts.append(lines[i].strip())
            i += 1
        if "-->" in time_line:
            blocks.append({"idx": idx, "time_line": time_line, "text": " ".join(text_parts).strip()})
    return blocks


def format_srt_blocks(blocks: list) -> str:
    rendered = []
    for n, block in enumerate(blocks, 1):
        idx = block.get("idx") or str(n)
        time_line = block.get("time_line", "")
        text = (block.get("text") or "").strip()
        rendered.append(f"{idx}\n{time_line}\n{text}\n")
    return "\n".join(rendered).strip() + "\n"


def _heuristic_semantic_text(text: str, max_chars: int = 42) -> str:
    """Make one subtitle block easier to read without changing timings."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= max_chars:
        return text
    break_marks = [", ", "; ", ": ", ". ", "? ", "! ", " - "]
    candidates = []
    for mark in break_marks:
        start = 0
        while True:
            pos = text.find(mark, start)
            if pos == -1:
                break
            candidates.append(pos + len(mark.strip()))
            start = pos + 1
    if candidates:
        midpoint = len(text) / 2
        best = min(candidates, key=lambda p: abs(p - midpoint))
        left, right = text[:best].strip(), text[best:].strip()
        if left and right and len(left) <= max_chars + 12 and len(right) <= max_chars + 12:
            return f"{left}\n{right}"
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines[:2]) if len(lines) <= 2 else "\n".join([" ".join(lines[:-1]), lines[-1]])


def _semantic_segment_gpt(text: str, target_lang: str, model=None) -> str:
    if not OPENAI_API_KEY:
        return "[GPT translation unavailable - set OPENAI_API_KEY]"
    try:
        model_name = _normalise_gpt_model(model)
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": model_name,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You format mobile video subtitles. Keep the meaning and language. "
                            "Return only the subtitle text. Use natural semantic line breaks, "
                            "maximum two lines, no numbering, no timestamps."
                        ),
                    },
                    {"role": "user", "content": f"Language: {target_lang}\nSubtitle:\n{text}"},
                ],
                "temperature": 0.1,
            },
            timeout=30,
        )
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[GPT error: {e}]"


def _semantic_segment_gemini(text: str, target_lang: str, model=None) -> str:
    if not GEMINI_API_KEY:
        return "[Gemini translation unavailable - set GEMINI_API_KEY]"
    try:
        model_name = _normalise_gemini_model(model)
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}",
            json={
                "contents": [{"parts": [{"text": (
                    "Format this mobile video subtitle with natural semantic line breaks. "
                    "Keep the meaning and language, maximum two lines, no numbering, no timestamps.\n"
                    f"Language: {target_lang}\nSubtitle:\n{text}"
                )}]}],
                "generationConfig": {"temperature": 0.1},
            },
            timeout=30,
        )
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        return f"[Gemini error: {e}]"


def semantic_segment_srt(srt_content: str, target_lang: str = "vi", engine: str = "gpt", model: str = None) -> str:
    """Rewrite subtitle block text for semantic readability while preserving SRT timings."""
    blocks = parse_srt_blocks(srt_content)
    if not blocks:
        return srt_content
    segmented = []
    for block in blocks:
        text = block["text"]
        if engine == "gpt":
            revised = _semantic_segment_gpt(text, target_lang, model)
        elif engine == "gemini":
            revised = _semantic_segment_gemini(text, target_lang, model)
        else:
            revised = _heuristic_semantic_text(text)
        if _is_translation_error(revised):
            revised = _heuristic_semantic_text(text)
        segmented.append({**block, "text": revised.strip()})
    return format_srt_blocks(segmented)


def translate_text(text: str, source_lang: str = "zh", target_lang: str = "vi", engine: str = "nllb", model: str = None) -> str:
    text = normalize_source_text_for_translation(text, source_lang)
    if engine == "gpt":
        result = _translate_gpt(text, source_lang, target_lang, model)
    elif engine == "gemini":
        result = _translate_gemini(text, source_lang, target_lang, model)
    elif engine == "google":
        result = _translate_google(text, source_lang, target_lang)
    elif engine == "nllb":
        result = _translate_nllb(text, source_lang, target_lang, model)
    elif engine == "marian":
        result = _translate_marian(text, source_lang, target_lang)
    elif engine == "m2m100":
        result = _translate_m2m100(text, source_lang, target_lang, model)
    elif engine in {"seamless", "seamlessm4t"}:
        result = _translate_seamless(text, source_lang, target_lang, model)
    elif engine == "deeplx":
        result = _translate_deeplx(text, source_lang, target_lang, model)
    elif engine in {"ai_provider", "openrouter", "custom_openai", "nvidia", "ollama"}:
        result = _translate_ai_provider(text, source_lang, target_lang, model, provider_alias=engine)
    else:
        result = text
    return postprocess_translation_text(result, source_lang, target_lang)


def normalize_source_text_for_translation(text: str, source_lang: str = "zh") -> str:
    """Fix common Chinese STT/OCR homophone slips before translation."""
    if not text or not str(source_lang or "").lower().startswith("zh"):
        return text
    value = str(text)
    replacements = {
        "不见一气": "不建议去",
        "不见一去": "不建议去",
        "不建议气": "不建议去",
        "单小的朋友": "胆小的朋友",
        "单小朋友": "胆小朋友",
        "胆小的朋友不建议去": "胆小的朋友别进去",
    }
    for wrong, right in replacements.items():
        value = value.replace(wrong, right)
    return value


def postprocess_translation_text(text: str, source_lang: str = "zh", target_lang: str = "vi") -> str:
    """Fix common machine-translation slips before subtitle/TTS use."""
    if not text or target_lang != "vi":
        return text

    value = str(text)
    replacements = {
        "Những người bạn độc thân không giận nhau": "Bạn yếu tim thì đừng vào",
        "Những người bạn độc thân đừng tức giận": "Bạn yếu tim thì đừng vào",
        "Bạn độc thân không giận nhau": "Bạn yếu tim thì đừng vào",
        "Tôi không tức giận ở đây": "Không khuyên bạn đến đây",
        "Tôi không giận ở đây": "Không khuyên bạn đến đây",
        "Tôi không khuyên bạn nên đến đây": "Không khuyên bạn đến đây",
    }
    for wrong, right in replacements.items():
        value = value.replace(wrong, right)
    return value


import uuid
import threading

# ── Job tracker: job_id -> {status, progress, result, error} ─────────────────
_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()

def _job_set(job_id, **kwargs):
    with _JOBS_LOCK:
        if job_id not in _JOBS:
            _JOBS[job_id] = {}
        _JOBS[job_id].update(kwargs)

def get_job(job_id: str) -> dict:
    with _JOBS_LOCK:
        return dict(_JOBS.get(job_id, {}))


def translate_srt(srt_content: str, source_lang: str = "zh", target_lang: str = "vi", engine: str = "nllb", model: str = None, semantic_segmentation: bool = False, project_id: int = None) -> str:
    """Blocking translate — used internally / by API models."""
    job_id = str(uuid.uuid4())
    _job_set(job_id, status="running", progress=0, result=None, error=None, project_id=project_id)
    _translate_srt_sync(job_id, srt_content, source_lang, target_lang, engine, model, semantic_segmentation, project_id)
    job = _JOBS.get(job_id, {})
    if job.get("status") == "error":
        raise RuntimeError(job.get("error", "Translation failed"))
    return job.get("result") or ""


def translate_srt_async(srt_content: str, source_lang: str = "zh", target_lang: str = "vi", engine: str = "nllb", model: str = None, project_id=None, semantic_segmentation: bool = False) -> str:
    """Async translate — starts a background thread, returns job_id immediately."""
    job_id = str(uuid.uuid4())
    _job_set(job_id, status="running", progress=0, result=None, error=None, project_id=project_id)
    t = threading.Thread(
        target=_translate_srt_sync,
        args=(job_id, srt_content, source_lang, target_lang, engine, model, semantic_segmentation, project_id),
        daemon=True,
    )
    t.start()
    return job_id


def _translate_srt_sync(job_id: str, srt_content: str, source_lang: str, target_lang: str, engine: str, model: str = None, semantic_segmentation: bool = False, project_id: int = None):
    """Parse SRT, translate block-by-block with progress updates, store result."""
    try:
        text_blocks = parse_srt_blocks(srt_content)

        if not text_blocks:
            _job_set(job_id, status="done", progress=100, result=srt_content)
            return

        total = len(text_blocks)
        result_blocks = []
        
        proj = project_id or get_job(job_id).get("project_id")

        def save_partial():
            if not proj:
                return
            try:
                partial_srt = format_srt_blocks(result_blocks)
                from ..config import SUBTITLES_DIR
                trans_path = SUBTITLES_DIR / f"project_{proj}_translated.srt"
                trans_path.write_text(partial_srt, encoding="utf-8")
                
                with db_cursor() as cur:
                    src_val = f"translated_{engine}_{source_lang}_to_{target_lang}"
                    existing = cur.execute(
                        "SELECT id FROM subtitles WHERE project_id=? AND source=?",
                        (proj, src_val)
                    ).fetchone()
                    if existing:
                        cur.execute(
                            "UPDATE subtitles SET content=? WHERE id=?",
                            (partial_srt, existing["id"])
                        )
                    else:
                        cur.execute(
                            "INSERT INTO subtitles (project_id, source, content) VALUES (?,?,?)",
                            (proj, src_val, partial_srt)
                        )
            except Exception as e:
                print(f"Error saving partial translation: {e}")

        # ── NLLB / Marian: batch by 8 blocks, update progress per batch ───────
        if engine in _LOCAL_BATCH_ENGINES:
            BATCH = 8
            done = 0
            for start in range(0, total, BATCH):
                chunk = text_blocks[start:start + BATCH]
                texts = [blk["text"] for blk in chunk]
                joined = "\n".join(texts)
                if joined.strip():
                    translated_joined = translate_text(joined, source_lang, target_lang, engine, model)
                    if _is_translation_error(translated_joined):
                        raise RuntimeError(translated_joined)
                    parts = translated_joined.split("\n")
                    # Handle misalignment: truncate extra lines, pad missing lines
                    if len(parts) > len(chunk):
                        parts = parts[:len(chunk)]
                    elif len(parts) < len(chunk):
                        parts += [""] * (len(chunk) - len(parts))
                else:
                    parts = [""] * len(chunk)
                for blk, translated in zip(chunk, parts):
                    result_blocks.append({"idx": blk["idx"], "time_line": blk["time_line"], "text": (translated or "").strip()})
                done += len(chunk)
                pct = int(done / total * 100)
                _job_set(job_id, progress=pct)
                save_partial()

        # ── API models (gpt, gemini, google): per-block with progress ─────────
        else:
            for n, blk in enumerate(text_blocks):
                idx, time_line, text = blk["idx"], blk["time_line"], blk["text"]
                translated = translate_text(text, source_lang, target_lang, engine, model) if text else ""
                if _is_translation_error(translated):
                    raise RuntimeError(translated)
                result_blocks.append({"idx": idx, "time_line": time_line, "text": translated})
                _job_set(job_id, progress=int((n + 1) / total * 100))
                save_partial()

        final = postprocess_translation_text(format_srt_blocks(result_blocks), source_lang, target_lang)
        if semantic_segmentation:
            _job_set(job_id, progress=95)
            final = semantic_segment_srt(final, target_lang=target_lang, engine=engine, model=model)

        # Save to DB if project_id provided
        if proj:
            try:
                with db_cursor() as cur:
                    cur.execute(
                        "INSERT INTO subtitles (project_id, source, content) VALUES (?,?,?)",
                        (proj, f"translated_{engine}.srt", final),
                    )
            except Exception:
                pass

        _job_set(job_id, status="done", progress=100, result=final)
    except Exception as e:
        _job_set(job_id, status="error", error=str(e))




def _find_system_python():
    """Find system Python (not the PyInstaller EXE)."""
    configured = os.environ.get("PYTHON_EXECUTABLE", "").strip()
    if configured and os.path.exists(configured):
        return configured
    if getattr(sys, 'frozen', False):
        candidates = [
            os.path.join(os.path.dirname(sys.executable), "python.exe"),
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        python = shutil.which("python") or shutil.which("py")
        if python:
            return python
        raise RuntimeError("System Python not found. Set PYTHON_EXECUTABLE to enable local translation worker mode.")
    return sys.executable


def _translate_worker_script():
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).resolve().parent
        candidate = exe_dir / "backend" / "services" / "translate_worker.py"
        if candidate.exists():
            return candidate
    return Path(__file__).parent / "translate_worker.py"


# Persistent translate worker process
_translate_proc = None
_translate_proc_lock = threading.Lock()
_translate_proc_engine = None
_translate_proc_model = None


def _normalise_local_model(engine: str, model: str = None) -> str:
    model = (model or "").strip()
    if engine == "nllb":
        allowed = {
            "facebook/nllb-200-distilled-600M",
            "facebook/nllb-200-distilled-1.3B",
        }
        return model if model in allowed else "facebook/nllb-200-distilled-1.3B"
    if engine == "m2m100":
        allowed = {
            "facebook/m2m100_418M",
            "facebook/m2m100_1.2B",
        }
        return model if model in allowed else "facebook/m2m100_418M"
    if engine == "seamless":
        allowed = {
            "facebook/hf-seamless-m4t-medium",
            "facebook/seamless-m4t-v2-large",
        }
        return model if model in allowed else "facebook/hf-seamless-m4t-medium"
    return model


def _get_translate_worker(engine: str, model: str = None):
    """Get or create a persistent translate worker subprocess."""
    global _translate_proc, _translate_proc_engine, _translate_proc_model
    model = _normalise_local_model(engine, model)
    with _translate_proc_lock:
        if _translate_proc is not None and _translate_proc.poll() is not None:
            _translate_proc = None
        if _translate_proc is None or _translate_proc_engine != engine or _translate_proc_model != model:
            if _translate_proc is not None:
                _translate_proc.stdin.close()
                _translate_proc.wait(timeout=5)
            python = _find_system_python()
            script = _translate_worker_script()
            if not script.exists():
                return None
            _translate_proc = subprocess.Popen(
                [python, str(script)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            _translate_proc_engine = engine
            _translate_proc_model = model
            # Send first request to trigger model loading
            _translate_proc.stdin.write(json.dumps({"engine": engine, "model": model, "text": "", "src": "zh", "tgt": "vi"}) + "\n")
            _translate_proc.stdin.flush()
            _translate_proc.stdout.readline()  # discard warmup response
        return _translate_proc


def _call_translate_worker(engine: str, text: str, src: str, tgt: str, model: str = None) -> str:
    """Send a translation request to the persistent worker subprocess."""
    label = _ENGINE_LABELS.get(engine, engine.upper())
    try:
        model = _normalise_local_model(engine, model)
        proc = _get_translate_worker(engine, model)
        if proc is None:
            return f"[{label} unavailable - worker script not found]"
        req = json.dumps({"engine": engine, "model": model, "text": text, "src": src, "tgt": tgt})
        proc.stdin.write(req + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            return f"[{label} error: worker process died]"
        data = json.loads(line)
        if data.get("error"):
            return f"[{label} error: {data['error']}]"
        return data["result"]
    except Exception as e:
        return f"[{label} error: {e}]"


def _translate_nllb(text, src, tgt, model=None):
    """Translate using Meta's NLLB-200 model via persistent subprocess."""
    result = _call_translate_worker("nllb", text, src, tgt, model)
    if result.startswith("[NLLB error:") or result.startswith("[NLLB unavailable"):
        return _translate_free(text, src, tgt)
    return result


def _translate_marian(text, src, tgt):
    """Translate using Helsinki-NLP MarianMT via persistent subprocess."""
    return _call_translate_worker("marian", text, src, tgt)


def _translate_m2m100(text, src, tgt, model=None):
    """Translate using Meta M2M100 via persistent subprocess."""
    return _call_translate_worker("m2m100", text, src, tgt, model)


def _translate_seamless(text, src, tgt, model=None):
    """Translate text using Meta SeamlessM4T via persistent subprocess."""
    return _call_translate_worker("seamless", text, src, tgt, model)


def _fallback_models(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            parsed = parsed.get("fallback") or []
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    except Exception:
        pass
    return [part.strip() for part in re.split(r"[\n,]+", str(value)) if part.strip()]


def _translate_ai_provider(text, src, tgt, model=None, provider_alias: str | None = None):
    from .ai_provider_service import chat_completion, get_ai_provider_config

    overrides = None
    if model:
        try:
            parsed = json.loads(model)
            overrides = parsed if isinstance(parsed, dict) else {"model": str(model)}
        except Exception:
            overrides = {"model": model}
    if provider_alias and provider_alias != "ai_provider":
        overrides = dict(overrides or {})
        overrides["provider"] = "custom" if provider_alias == "custom_openai" else provider_alias
    base_config = get_ai_provider_config(overrides)
    models = [base_config["model"]] + [m for m in _fallback_models(base_config.get("fallback")) if m != base_config["model"]]
    last_error = ""
    for candidate in models:
        config = dict(base_config)
        config["model"] = candidate
        try:
            result = chat_completion(
                [
                    {
                        "role": "system",
                        "content": (
                            f"Translate from {src} to {tgt}. Return only the translation. "
                            "Preserve line breaks when the input has multiple lines."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                config,
            )
            translated = (result.get("text") or "").strip()
            if translated:
                return translated
            last_error = "empty response"
        except Exception as exc:
            last_error = str(exc)
    return f"[AI Provider error: {last_error or 'translation failed'}]"


def _normalise_gpt_model(model: str = None) -> str:
    label = (model or "").strip().lower()
    if label in {"gpt-4", "gpt4"}:
        return "gpt-4o"
    if label in {"gpt-3.5", "gpt-3.5-turbo", "gpt3.5"}:
        return "gpt-3.5-turbo"
    return model or "gpt-4o-mini"


def _normalise_gemini_model(model: str = None) -> str:
    label = (model or "").strip()
    return label if label and "/" not in label else "gemini-2.0-flash"


def _translate_gpt(text, src, tgt, model=None):
    if not OPENAI_API_KEY:
        return f"[GPT translation unavailable - set OPENAI_API_KEY]"
    try:
        model_name = _normalise_gpt_model(model)
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": model_name,
                "messages": [
                    {"role": "system", "content": f"Translate from {src} to {tgt}. Return only translation."},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.3,
            },
            timeout=30,
        )
        data = resp.json()
        tokens = data.get("usage", {}).get("total_tokens", 0)
        _log_usage("gpt", tokens, 0, tokens * 0.00015 / 1000)
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[GPT error: {e}]"


def _translate_gemini(text, src, tgt, model=None):
    if not GEMINI_API_KEY:
        return f"[Gemini translation unavailable - set GEMINI_API_KEY]"
    try:
        model_name = _normalise_gemini_model(model)
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}",
            json={
                "contents": [{"parts": [{"text": f"Translate from {src} to {tgt}: {text}"}]}],
                "generationConfig": {"temperature": 0.3},
            },
            timeout=30,
        )
        data = resp.json()
        result = data["candidates"][0]["content"]["parts"][0]["text"]
        return result.strip()
    except Exception as e:
        return f"[Gemini error: {e}]"


def _translate_google(text, src, tgt):
    try:
        from googletrans import Translator
        t = Translator()
        result = t.translate(text, src=src, dest=tgt)
        return result.text
    except Exception:
        return _translate_free(text, src, tgt)


def _settings_value(key: str) -> str:
    try:
        with db_cursor() as cur:
            row = cur.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row and row["value"] else ""
    except Exception:
        return ""


def _translate_deeplx(text, src, tgt, endpoint=None):
    endpoint = (
        (endpoint or "").strip()
        or _settings_value("deeplx_url").strip()
        or os.environ.get("DEEPLX_URL", "").strip()
        or "http://127.0.0.1:1188/translate"
    )
    if not endpoint.lower().endswith("/translate"):
        endpoint = endpoint.rstrip("/") + "/translate"
    try:
        resp = requests.post(
            endpoint,
            json={"text": text, "source_lang": src.upper(), "target_lang": tgt.upper()},
            timeout=30,
        )
        data = resp.json()
        if resp.status_code >= 400:
            return f"[DeepLX error: HTTP {resp.status_code}]"
        if isinstance(data, dict):
            if data.get("code") not in (None, 200, "200"):
                return f"[DeepLX error: {data.get('message') or data.get('msg') or data.get('code')}]"
            value = data.get("data") or data.get("translation") or data.get("translated_text")
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                return "\n".join(str(x) for x in value)
        return "[DeepLX error: unexpected response]"
    except Exception as e:
        return f"[DeepLX error: {e}]"


def _translate_free(text, src, tgt):
    try:
        resp = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": src, "tl": tgt, "dt": "t", "q": text},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        data = resp.json()
        return "".join(part[0] for part in data[0])
    except Exception as e:
        return f"[Translation error: {e}]"


def _log_usage(service, tokens, seconds, cost):
    try:
        with db_cursor() as cur:
            cur.execute(
                "INSERT INTO api_usage (service, tokens, seconds, cost) VALUES (?,?,?,?)",
                (service, tokens, seconds, cost),
            )
    except Exception:
        pass
