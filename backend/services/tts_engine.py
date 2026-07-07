import os
import re
import subprocess
import threading
import asyncio
import shutil
import sys
import warnings
from pathlib import Path
from ..config import AZURE_TTS_KEY, AZURE_TTS_REGION, ELEVENLABS_API_KEY, VALTEC_TTS_DIR, CAPCUT_TTS_DIR, CAPCUT_SSCRONET_DLL
from .text_normalizer import normalize_for_tts


TTS_TIMEOUT = 300  # seconds (increased for chunked synthesis)
CHUNK_MAX_CHARS = 2000  # max characters per edge-tts request
CHUNK_MAX_RETRIES = 3  # retry attempts per chunk
CHUNK_RETRY_DELAY = 2  # seconds between retries (doubles each attempt)
TIMELINE_MAX_TEMPO = float(os.environ.get("TTS_TIMELINE_MAX_TEMPO", "2.0"))
TIMELINE_STRATEGY = os.environ.get("TTS_TIMELINE_STRATEGY", "subtitle_fit").strip().lower()
TIMELINE_TRIM_OVERFLOW = os.environ.get("TTS_TIMELINE_TRIM_OVERFLOW", "0").strip().lower() in {"1", "true", "yes", "on"}
_valtec_tts_instance = None


def _run_with_timeout(fn, args=(), kwargs=None, timeout=TTS_TIMEOUT):
    """Run fn in a thread; if it doesn't finish in `timeout` seconds, return None."""
    if kwargs is None:
        kwargs = {}
    result = [None]
    exc = [None]
    done = threading.Event()

    def worker():
        try:
            result[0] = fn(*args, **kwargs)
        except Exception as e:
            exc[0] = e
        finally:
            done.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    ok = done.wait(timeout)
    if not ok:
        print(f"[TTS] Timeout after {timeout}s — falling back")
        return False
    if exc[0]:
        raise exc[0]
    return True if result[0] is None else result[0]


def _split_text_for_tts(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list:
    """Split text into chunks on sentence boundaries, each <= max_chars."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    # Split by sentence endings (. ? ! or newline), keeping delimiter attached
    sentences = re.split(r'(?<=[.!?\n])', text)

    current_chunk = ""
    for sentence in sentences:
        if not sentence:
            continue
        if len(current_chunk) + len(sentence) <= max_chars:
            current_chunk += sentence
        else:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            # If a single sentence exceeds max_chars, split by words
            if len(sentence) > max_chars:
                words = sentence.strip().split()
                word_chunk = ""
                for word in words:
                    if len(word_chunk) + len(word) + 1 <= max_chars:
                        word_chunk = f"{word_chunk} {word}".strip()
                    else:
                        if word_chunk.strip():
                            chunks.append(word_chunk.strip())
                        word_chunk = word
                current_chunk = word_chunk if word_chunk else ""
            else:
                current_chunk = sentence

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return [c for c in chunks if c]


def _concat_audio_ffmpeg(file_paths: list, output_path: str) -> bool:
    """Concatenate multiple audio files using FFmpeg concat demuxer."""
    from ..config import FFMPEG_PATH
    list_path = Path(output_path).parent / f"_concat_{os.getpid()}.txt"
    try:
        lines = [f"file '{Path(p).resolve().as_posix()}'" for p in file_paths]
        list_path.write_text("\n".join(lines), encoding="utf-8")
        cmd = [
            FFMPEG_PATH,
            "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_path), "-c", "copy", output_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except Exception as e:
        print(f"[TTS] FFmpeg concat error: {e}")
        return False
    finally:
        list_path.unlink(missing_ok=True)


def synthesize(text: str, provider: str, voice: str, speed: float, output_path: str, api_key: str = None):
    if api_key == "__redacted__":
        api_key = None
    lang = (voice or "vi").split("-")[0].lower()
    text = normalize_for_tts(text, lang=lang)
    if provider == "edge":
        _edge_tts(text, voice, speed, output_path)
    elif provider == "fpt":
        if not api_key:
            from ..config import FPT_API_KEY
            api_key = FPT_API_KEY
        _fpt_tts(text, voice, speed, api_key, output_path)
    elif provider == "azure":
        _azure_tts(text, voice, speed, output_path)
    elif provider == "elevenlabs":
        _elevenlabs_tts(text, voice, output_path)
    elif provider == "google":
        ok = _run_with_timeout(_google_tts, (text, voice, output_path))
        if not ok or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            _fallback_tts(text, output_path)
    elif provider == "valtec":
        ok = _run_with_timeout(_valtec_tts, (text, voice, speed, output_path))
        if not ok or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            _edge_tts(text, "vi-VN-HoaiMyNeural", speed, output_path)
    elif provider == "capcut":
        from .job_logger import job_log
        output = Path(output_path)
        temp_output = output.with_name(f"{output.stem}_capcut_{os.getpid()}_{os.urandom(4).hex()}{output.suffix}")
        try:
            ok = _run_with_timeout(_capcut_tts, (text, voice, speed, str(temp_output)))
            if ok and temp_output.exists() and temp_output.stat().st_size > 0:
                shutil.move(str(temp_output), output_path)
            else:
                job_log("warning", f"CapCut TTS timeout/failed (voice={voice}). Falling back to Vietnamese Edge TTS.")
                _edge_tts(text, "vi-VN-HoaiMyNeural", speed, output_path)
        except Exception as e:
            job_log("warning", f"CapCut TTS failed (voice={voice}): {e}. Falling back to Vietnamese Edge TTS.")
            _edge_tts(text, "vi-VN-HoaiMyNeural", speed, output_path)
        finally:
            try:
                temp_output.unlink(missing_ok=True)
            except Exception:
                pass
    elif provider == "clone":
        _clone_tts(text, voice, output_path)
    else:
        _edge_tts(text, voice, speed, output_path)


def _edge_tts(text, voice, speed, out):
    out_path = Path(out)
    try:
        import edge_tts
    except ImportError:
        print("[TTS] edge_tts not available, using fallback")
        _fallback_tts(text, out)
        return

    rate = f"+{int((speed - 1) * 100)}%" if speed >= 1 else f"{int((speed - 1) * 100)}%"
    chunks = _split_text_for_tts(text, CHUNK_MAX_CHARS)
    print(f"[TTS] edge_tts: voice={voice}, rate={rate}, text_len={len(text)}, chunks={len(chunks)}")

    # Single chunk — no concat needed
    if len(chunks) == 1:
        temp_out = out_path.with_name(f"{out_path.stem}_edge_{os.getpid()}_{os.urandom(4).hex()}{out_path.suffix}")
        try:
            ok = _run_with_timeout(
                lambda: asyncio.run(_synth_chunk_with_retry(edge_tts, chunks[0], voice, rate, temp_out)),
                timeout=TTS_TIMEOUT,
            )
            if not ok or not temp_out.exists() or temp_out.stat().st_size == 0:
                raise RuntimeError("Edge TTS produced empty output")
            shutil.move(str(temp_out), out)
        except Exception as e:
            print(f"[TTS] edge_tts failed ({e}), using fallback audio")
            try:
                import time
                time.sleep(0.1)
                temp_out.unlink(missing_ok=True)
                out_path.unlink(missing_ok=True)
            except Exception as unlink_err:
                print(f"[TTS] Failed to remove corrupt file: {unlink_err}")
            try:
                _fallback_tts(text, out)
            except Exception as fallback_err:
                print(f"[TTS] Failed to write fallback: {fallback_err}")
        finally:
            try:
                temp_out.unlink(missing_ok=True)
            except Exception:
                pass
        return

    # Multiple chunks — synthesize each, then concat with FFmpeg
    temp_files = []
    base, ext = os.path.splitext(out)
    try:
        for idx, chunk in enumerate(chunks):
            chunk_path = f"{base}_chunk_{idx}{ext}"
            print(f"[TTS] Synthesizing chunk {idx + 1}/{len(chunks)} ({len(chunk)} chars)...")
            _run_with_timeout(
                lambda cp=chunk_path, ch=chunk: asyncio.run(
                    _synth_chunk_with_retry(edge_tts, ch, voice, rate, cp)
                ),
                timeout=TTS_TIMEOUT,
            )
            if not os.path.exists(chunk_path) or os.path.getsize(chunk_path) == 0:
                raise RuntimeError(f"Chunk {idx + 1} synthesis failed (empty output)")
            temp_files.append(chunk_path)

        # Concat all chunks
        if not _concat_audio_ffmpeg(temp_files, out):
            raise RuntimeError("FFmpeg concat failed")
        print(f"[TTS] Saved merged audio to {out} ({os.path.getsize(out)} bytes)")
    except Exception as e:
        print(f"[TTS] _edge_tts error: {e}")
        raise
    finally:
        for f in temp_files:
            try:
                os.remove(f)
            except OSError:
                pass


async def _synth_chunk_with_retry(edge_tts, text, voice, rate, out_path):
    """Synthesize a single text chunk with retry on NoAudioReceived."""
    delay = CHUNK_RETRY_DELAY
    last_err = None
    for attempt in range(1, CHUNK_MAX_RETRIES + 1):
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            await communicate.save(out_path)
            return  # success
        except Exception as e:
            last_err = e
            if attempt < CHUNK_MAX_RETRIES:
                print(f"[TTS] Chunk attempt {attempt} failed ({e}), retrying in {delay}s...")
                await asyncio.sleep(delay)
                delay *= 2
            else:
                print(f"[TTS] Chunk failed after {CHUNK_MAX_RETRIES} attempts: {e}")
    raise last_err


def _azure_tts(text, voice, speed, out):
    if not AZURE_TTS_KEY:
        return
    try:
        import azure.cognitiveservices.speech as speechsdk
        config = speechsdk.SpeechConfig(subscription=AZURE_TTS_KEY, region=AZURE_TTS_REGION)
        config.speech_synthesis_voice_name = voice
        audio_config = speechsdk.audio.AudioOutputConfig(filename=out)
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=audio_config)
        synthesizer.speak_text_async(text).get()
    except ImportError:
        _edge_tts(text, "vi-VN-NamMinhNeural", speed, out)


def _elevenlabs_tts(text, voice, out):
    if not ELEVENLABS_API_KEY:
        _fallback_tts(text, out)
        return
    try:
        from elevenlabs import generate, save, Voice
        audio = generate(text=text, voice=voice, api_key=ELEVENLABS_API_KEY)
        save(audio, out)
    except ImportError:
        _fallback_tts(text, out)


def _google_tts(text, voice, out):
    try:
        from gtts import gTTS
        lang = voice.split("-")[0] if "-" in voice else "vi"
        tts = gTTS(text, lang=lang)
        tts.save(out)
    except ImportError:
        _fallback_tts(text, out)


def _write_audio_array(audio, sample_rate, out):
    try:
        import soundfile as sf
        sf.write(out, audio, sample_rate)
        return
    except ImportError:
        pass
    try:
        from scipy.io import wavfile
        wavfile.write(out, sample_rate, audio)
        return
    except ImportError:
        pass
    raise RuntimeError("Valtec generated raw audio but soundfile/scipy is not installed")


def _load_valtec_tts():
    global _valtec_tts_instance
    if _valtec_tts_instance is not None:
        return _valtec_tts_instance

    if VALTEC_TTS_DIR:
        valtec_dir = Path(VALTEC_TTS_DIR).expanduser().resolve()
        if valtec_dir.exists():
            sys.path.insert(0, str(valtec_dir))

    try:
        from valtec_tts import TTS
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            warnings.filterwarnings(
                "ignore",
                module=r"torch\.nn\.utils\.weight_norm",
                category=FutureWarning,
            )
            _valtec_tts_instance = ("package", TTS())
        return _valtec_tts_instance
    except Exception as package_error:
        try:
            from app import TTSInterface
            _valtec_tts_instance = ("space", TTSInterface())
            return _valtec_tts_instance
        except Exception as space_error:
            raise RuntimeError(
                "Valtec TTS is not available. Install/clone it and set VALTEC_TTS_DIR. "
                f"package_error={package_error}; space_error={space_error}"
            )


def _valtec_tts(text, voice, speed, out):
    speaker = voice if voice in {"NF", "SF", "NM1", "SM", "NM2"} else "NF"
    try:
        kind, engine = _load_valtec_tts()
        valtec_speed = max(0.5, min(2.0, float(speed or 1.0)))

        if kind == "package":
            engine.speak(text, output_path=out, speaker=speaker, speed=valtec_speed)
            return

        result_path, status = engine.synthesize(text, speaker, valtec_speed, 0.667, 0.8, 0.0)
        if result_path and os.path.exists(result_path):
            shutil.copy2(result_path, out)
            return
        raise RuntimeError(status or "Valtec returned no audio")
    except Exception as e:
        print(f"[TTS] Valtec error: {e}, falling back to edge_tts")
        _edge_tts(text, "vi-VN-HoaiMyNeural", speed, out)


def _find_capcut_sscronet() -> str:
    if CAPCUT_SSCRONET_DLL and Path(CAPCUT_SSCRONET_DLL).exists():
        return CAPCUT_SSCRONET_DLL
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        return ""
    apps_dir = Path(local_appdata) / "CapCut" / "Apps"
    if not apps_dir.exists():
        return ""
    matches = sorted(apps_dir.glob("**/sscronet.dll"), key=lambda p: str(p), reverse=True)
    return str(matches[0]) if matches else ""


def _parse_capcut_voice(voice: str):
    parts = (voice or "").split("|")
    name = parts[0] if len(parts) > 0 and parts[0] else "BV074_streaming_dsp"
    resource_id = parts[1] if len(parts) > 1 and parts[1] else "7550087831092251920"
    platform = parts[2] if len(parts) > 2 and parts[2] else "sami"
    return name, resource_id, platform


def _capcut_tts(text, voice, speed, out):
    capcut_dir = Path(CAPCUT_TTS_DIR).expanduser().resolve()
    win_dir = capcut_dir / "capcut_windows"
    script = win_dir / "capcut_tts_ctypes.py"
    helper = win_dir / "cronet_helper.dll"
    sscronet = _find_capcut_sscronet()

    if not script.exists():
        raise RuntimeError(f"CapCut TTS script not found: {script}")
    if not helper.exists():
        raise RuntimeError(
            f"CapCut cronet_helper.dll not found: {helper}. "
            "Run vendor\\capcut-tts-api\\capcut_windows\\build.bat from a Visual Studio Developer Command Prompt."
        )
    if not sscronet:
        raise RuntimeError(
            "CapCut sscronet.dll not found. Install CapCut Desktop or set CAPCUT_SSCRONET_DLL "
            "to ...\\AppData\\Local\\CapCut\\Apps\\<version>\\sscronet.dll."
        )

    name, resource_id, platform = _parse_capcut_voice(voice)
    final_out = Path(out)
    download_out = final_out if final_out.suffix.lower() == ".mp3" else final_out.with_suffix(".capcut.mp3")

    # Set environment variables for config.py and capcut_tts_ctypes.py
    os.environ["CAPCUT_TTS_OUTPUT"] = str(download_out)
    os.environ["CAPCUT_SSCRONET_DLL"] = sscronet
    os.environ["CAPCUT_VOICE_NAME"] = name
    os.environ["CAPCUT_VOICE_RESOURCE_ID"] = resource_id
    os.environ["CAPCUT_VOICE_PLATFORM"] = platform
    os.environ["CAPCUT_VOICE_RATE"] = str(max(0.5, min(2.0, float(speed or 1.0))))

    # Call CapCut TTS in-process to avoid python interpreter dependency
    import importlib.util

    orig_sys_path = list(sys.path)
    win_dir_str = str(win_dir)
    if win_dir_str not in sys.path:
        sys.path.insert(0, win_dir_str)

    try:
        # Force reload modules to pick up updated environment variables
        for mod_name in ["config", "cronet_client", "capcut_tts_ctypes"]:
            if mod_name in sys.modules:
                del sys.modules[mod_name]

        spec = importlib.util.spec_from_file_location("capcut_tts_ctypes", str(script))
        capcut_module = importlib.util.module_from_spec(spec)
        sys.modules["capcut_tts_ctypes"] = capcut_module
        spec.loader.exec_module(capcut_module)

        capcut_module.process_tts(text)
    except Exception as e:
        raise RuntimeError(f"CapCut TTS in-process failed: {e}")
    finally:
        sys.path = orig_sys_path

    if not download_out.exists() or download_out.stat().st_size == 0:
        raise RuntimeError("CapCut TTS did not create output file.")

    if download_out != final_out:
        from .ffmpeg_utils import run_ffmpeg
        if not run_ffmpeg(["-i", str(download_out), "-ar", "22050", "-ac", "1", "-y", str(final_out)]):
            raise RuntimeError("CapCut TTS audio conversion failed")
        try:
            download_out.unlink(missing_ok=True)
        except Exception:
            pass


def _clone_tts(text, voice, out):
    """TTS using a trained voice clone (Bark)."""
    try:
        from ..services.voice_clone import clone_voice
        from ..config import VOICES_DIR
        clone_dir = VOICES_DIR / "clones" / voice
        prompt_path = clone_dir / "voice_prompt.npz"
        if prompt_path.exists():
            from bark import generate_audio, SAMPLE_RATE
            import scipy.io.wavfile as wavfile
            audio_arr = generate_audio(text, history_prompt=str(prompt_path))
            wavfile.write(out, SAMPLE_RATE, audio_arr)
        else:
            sample = list(clone_dir.glob("sample_*.wav")) + list(clone_dir.glob("*.wav"))
            if sample:
                clone_voice(str(sample[0]), text, out)
            else:
                _fallback_tts(text, out)
    except Exception as e:
        print(f"[TTS] Clone error: {e}")
        _fallback_tts(text, out)


def _fallback_tts(text, out):
    """Fail loudly unless a developer explicitly enables placeholder audio."""
    if os.environ.get("ALLOW_SILENT_TTS_FALLBACK", "").strip().lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError(
            "TTS engine failed and silent placeholder fallback is disabled. "
            "Set ALLOW_SILENT_TTS_FALLBACK=1 only for local debugging."
        )
    import wave
    import struct
    import math
    duration = max(len(text) * 0.08, 1.0)
    sample_rate = 22050
    n_samples = int(sample_rate * duration)
    with wave.open(out, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for i in range(n_samples):
            t = i / sample_rate
            val = int(16000 * math.sin(2 * math.pi * 220 * t) * max(0, 1 - t / duration))
            wf.writeframes(struct.pack("<h", val))


def _parse_srt_time(t_str: str) -> float:
    match = re.search(r"(\d{1,2}):(\d{2}):(\d{2}(?:[,.]\d{1,3})?)", t_str.strip())
    if not match:
        raise ValueError(f"Invalid SRT time: {t_str}")
    t_str = ":".join(match.groups()).replace(",", ".")
    parts = t_str.split(":")
    h = float(parts[0])
    m = float(parts[1])
    s = float(parts[2])
    return h * 3600 + m * 60 + s


def _parse_srt(srt_content: str) -> list:
    if not srt_content:
        return []
    # Normalize line endings to \n
    normalized = srt_content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    # Split by blank lines (double or more newlines)
    blocks = re.split(r'\n\s*\n', normalized)
    results = []
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        # Find the line containing the time arrow '-->'
        time_line_idx = -1
        for i, line in enumerate(lines):
            if "-->" in line:
                time_line_idx = i
                break
        if time_line_idx != -1:
            time_line = lines[time_line_idx]
            t_parts = time_line.split("-->")
            if len(t_parts) == 2:
                try:
                    start = _parse_srt_time(t_parts[0])
                    end = _parse_srt_time(t_parts[1])
                    text = " ".join(lines[time_line_idx + 1:]).strip()
                    if text and end > start:
                        results.append({"start": start, "end": end, "text": text})
                except Exception:
                    pass
    return sorted(results, key=lambda item: item["start"])


def _format_srt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    s = total_seconds % 60
    total_minutes = total_seconds // 60
    m = total_minutes % 60
    h = total_minutes // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _segments_to_srt(segments: list) -> str:
    blocks = []
    for idx, seg in enumerate(segments, 1):
        start = _format_srt_time(seg["start"])
        end = _format_srt_time(max(seg["end"], seg["start"] + 0.05))
        blocks.append(f"{idx}\n{start} --> {end}\n{seg['text']}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _get_audio_duration(path: str) -> float:
    from ..config import FFPROBE_PATH
    import json
    cmd = [
        FFPROBE_PATH,
        "-v", "quiet", "-print_format", "json", "-show_format", path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        data = json.loads(result.stdout)
        fmt = data.get("format", {})
        return float(fmt.get("duration", 0))
    except Exception:
        try:
            import wave
            with wave.open(path, "rb") as wf:
                return wf.getnframes() / wf.getframerate()
        except Exception:
            return 0.0


def _audio_tempo_filters(tempo: float) -> list:
    filters = []
    remaining = max(0.5, float(tempo or 1.0))
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    if abs(remaining - 1.0) > 0.01:
        filters.append(f"atempo={remaining:.2f}")
    return filters


def _read_wav_i16(path: str) -> list:
    import wave
    import struct
    with wave.open(path, "rb") as wf:
        frames = wf.readframes(wf.getnframes())
    if not frames:
        return []
    count = len(frames) // 2
    return list(struct.unpack("<" + ("h" * count), frames[:count * 2]))


def _write_wav_i16(path: str, samples: list, sample_rate: int):
    import wave
    import struct
    with wave.open(path, "wb") as out_wf:
        out_wf.setnchannels(1)
        out_wf.setsampwidth(2)
        out_wf.setframerate(sample_rate)
        if samples:
            out_wf.writeframes(struct.pack("<" + ("h" * len(samples)), *samples))


def _process_single_segment(idx, seg, provider, voice, speed, base_dir, api_key, sample_rate, job_id=None, fit_to_slot=False, max_tempo=None) -> tuple:
    from ..config import FFMPEG_PATH
    if job_id:
        from .job_logger import set_current_job_id
        set_current_job_id(job_id)
    start_time = seg["start"]
    end_time = seg["end"]
    target_end_time = seg.get("_tts_target_end", end_time)
    lang = (voice or "vi").split("-")[0].lower()
    text = normalize_for_tts(seg["text"], lang=lang)
    
    temp_raw = os.path.join(base_dir, f"_temp_raw_{idx}_{os.getpid()}.wav")
    temp_norm = os.path.join(base_dir, f"_temp_norm_{idx}_{os.getpid()}.wav")
    
    try:
        synthesize(text, provider, voice, speed, temp_raw, api_key=api_key)
        if not os.path.exists(temp_raw) or os.path.getsize(temp_raw) == 0:
            return idx, None, None, "No raw audio generated"
            
        synth_dur = _get_audio_duration(temp_raw)
        target_dur = target_end_time - start_time
        
        if fit_to_slot and synth_dur > target_dur and target_dur > 0.1:
            tempo_limit = max(1.0, float(max_tempo or TIMELINE_MAX_TEMPO))
            tempo = min(synth_dur / target_dur, tempo_limit)
            filters = _audio_tempo_filters(tempo)
                
            filter_str = ",".join(filters)
            cmd = [
                FFMPEG_PATH, "-y",
                "-i", temp_raw,
                "-filter:a", filter_str,
                "-ar", str(sample_rate), "-ac", "1",
                "-c:a", "pcm_s16le", temp_norm
            ]
        else:
            cmd = [
                FFMPEG_PATH, "-y",
                "-i", temp_raw,
                "-ar", str(sample_rate), "-ac", "1",
                "-c:a", "pcm_s16le", temp_norm
            ]
            
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        subprocess.run(cmd, capture_output=True, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW)
        
        if not os.path.exists(temp_norm) or os.path.getsize(temp_norm) == 0:
            from .job_logger import job_log
            job_log("warning", f"[TTS] Segment {idx} normalization failed (raw file may be corrupt). Regenerating with fallback placeholder.")
            
            for path in [temp_raw, temp_norm]:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        import time
                        time.sleep(0.2)
                        try:
                            os.remove(path)
                        except Exception:
                            pass
            
            try:
                _fallback_tts(text, temp_raw)
                subprocess.run(cmd, capture_output=True, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW)
            except Exception as fallback_err:
                job_log("error", f"[TTS] Segment {idx} fallback generation failed: {fallback_err}")
        
        if not os.path.exists(temp_norm) or os.path.getsize(temp_norm) == 0:
            return idx, temp_raw, None, "No normalized audio generated"
            
        return idx, temp_raw, temp_norm, None
    except Exception as e:
        return idx, (temp_raw if os.path.exists(temp_raw) else None), (temp_norm if os.path.exists(temp_norm) else None), str(e)


def synthesize_timeline(
    srt_content: str,
    provider: str,
    voice: str,
    speed: float,
    output_path: str,
    api_key: str = None,
    progress_cb=None,
    strategy: str = None,
    trim_overflow: bool = None,
    max_tempo: float = None,
):
    """Synthesize subtitle segments and assemble them on the timeline.

    The default strategy keeps the TTS voice at a consistent natural rate and
    lets narration flow forward. Set TTS_TIMELINE_STRATEGY=subtitle_fit to force
    each segment into its subtitle slot.
    """
    import wave
    import json
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from ..config import FFMPEG_PATH
    
    segments = _parse_srt(srt_content)
    if not segments:
        text = " ".join(line for line in srt_content.split("\n") if not line.strip().isdigit() and "-->" not in line)
        synthesize(text, provider, voice, speed, output_path, api_key=api_key)
        return None
    for idx, seg in enumerate(segments):
        next_start = segments[idx + 1]["start"] if idx + 1 < len(segments) else seg["end"]
        seg["_tts_target_end"] = max(seg["start"] + 0.05, min(seg["end"], next_start))

    temp_files = []
    sample_rate = 22050
    bytes_per_sample = 2
    base_dir = os.path.dirname(output_path)
    
    try:
        requested_strategy = (strategy or TIMELINE_STRATEGY or "subtitle_fit").strip().lower()
        strategy = requested_strategy if requested_strategy in {"natural", "subtitle_fit"} else "subtitle_fit"
        fit_to_slot = strategy == "subtitle_fit"
        max_tempo = max(1.0, float(max_tempo or TIMELINE_MAX_TEMPO))
        trim_overflow = TIMELINE_TRIM_OVERFLOW if trim_overflow is None else bool(trim_overflow)
        # Run synthesis and processing tasks in parallel using a thread pool
        futures = {}
        completed_count = 0
        results = [None] * len(segments)
        
        from .job_logger import get_current_job_id, job_log
        job_id = get_current_job_id()
        # Set max_workers based on provider: network providers should use fewer workers to avoid rate limits
        max_workers = 3 if provider in ["edge", "fpt", "azure", "elevenlabs", "google"] else 8
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for idx, seg in enumerate(segments):
                f = executor.submit(
                    _process_single_segment,
                    idx, seg, provider, voice, speed, base_dir, api_key, sample_rate, job_id, fit_to_slot, max_tempo
                )
                futures[f] = idx
                
            for f in as_completed(futures):
                idx = futures[f]
                completed_count += 1
                if progress_cb:
                    try:
                        progress_cb(completed_count, len(segments))
                    except Exception:
                        pass
                
                try:
                    res_idx, raw_p, norm_p, err = f.result()
                    if err:
                        job_log("error", f"[TTS] Segment {res_idx} failed: {err}")
                    results[res_idx] = (raw_p, norm_p)
                    if raw_p:
                        temp_files.append(raw_p)
                    if norm_p:
                        temp_files.append(norm_p)
                except Exception as e:
                    print(f"[TTS] Segment {idx} threw exception: {e}")

        # Assemble by timeline position. In natural mode, preserve voice speed
        # and move the next segment forward when the previous line runs long.
        # In subtitle_fit mode, start at the original subtitle cue. Do not cut
        # overflow by default; cutting is what makes the voice swallow words.
        timeline_frames = max(int((segments[-1]["end"] + 1.0) * sample_rate), 1)
        mix = [0] * timeline_frames
        cursor_frame = 0
        gap_frames = int(0.08 * sample_rate)
        dropped = 0
        trimmed = 0
        overflowed = 0
        shifted = 0
        aligned_segments = []

        for idx, seg in enumerate(segments):
            res = results[idx]
            if not res or not res[1]:
                dropped += 1
                aligned_segments.append({
                    "start": seg["start"],
                    "end": seg["start"] + 0.01,
                    "text": seg["text"],
                })
                continue

            subtitle_start_frame = max(0, int(seg["start"] * sample_rate))
            start_frame = subtitle_start_frame
            end_frame = max(start_frame + 1, int(seg["end"] * sample_rate))
            if strategy == "natural":
                start_frame = max(subtitle_start_frame, cursor_frame)
                if start_frame > subtitle_start_frame + gap_frames:
                    shifted += 1
                slot_end = end_frame
                max_frames = None
            else:
                next_start = (
                    int(segments[idx + 1]["start"] * sample_rate)
                    if idx + 1 < len(segments)
                    else end_frame
                )
                start_frame = subtitle_start_frame
                slot_end = max(start_frame + 1, min(end_frame, next_start))
                max_frames = max(1, slot_end - start_frame) if trim_overflow else None

            samples = _read_wav_i16(res[1])
            slot_frames = max(1, slot_end - start_frame)
            if strategy == "subtitle_fit" and len(samples) > slot_frames:
                overflowed += 1
            if max_frames is not None and len(samples) > max_frames:
                samples = samples[:max_frames]
                trimmed += 1
            if not samples:
                dropped += 1
                aligned_segments.append({
                    "start": start_frame / sample_rate,
                    "end": (start_frame / sample_rate) + 0.01,
                    "text": seg["text"],
                })
                continue

            needed = start_frame + len(samples)
            if needed > len(mix):
                mix.extend([0] * (needed - len(mix)))

            for offset, sample in enumerate(samples):
                pos = start_frame + offset
                value = mix[pos] + sample
                if value > 32767:
                    value = 32767
                elif value < -32768:
                    value = -32768
                mix[pos] = value
            cursor_frame = start_frame + len(samples) + gap_frames
            aligned_segments.append({
                "start": start_frame / sample_rate,
                "end": (start_frame + len(samples)) / sample_rate,
                "text": seg["text"],
            })

        if dropped:
            job_log("warning", f"[TTS] {dropped}/{len(segments)} subtitle segments produced no audio.")
        if trimmed:
            job_log("warning", f"[TTS] Trimmed {trimmed}/{len(segments)} segments at timeline end.")
        if overflowed and not trim_overflow:
            job_log("warning", f"[TTS] Let {overflowed}/{len(segments)} segments overflow subtitle timing to avoid swallowing words.")
        if shifted:
            job_log("warning", f"[TTS] Shifted {shifted}/{len(segments)} segments forward to keep narration speed consistent.")

        _write_wav_i16(output_path, mix, sample_rate)
        return _segments_to_srt(aligned_segments) if aligned_segments else None
                    
    finally:
        for f in temp_files:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except OSError:
                pass


def _fpt_tts(text: str, voice: str, speed: float, api_key: str, out_path: str):
    import requests
    import time
    if not api_key:
        print("[TTS] FPT API Key is missing, falling back to edge_tts")
        _edge_tts(text, "vi-VN-HoaiMyNeural", speed, out_path)
        return

    fpt_speed = 0
    if speed > 1.4:
        fpt_speed = 2
    elif speed > 1.1:
        fpt_speed = 1
    elif speed < 0.7:
        fpt_speed = -2
    elif speed < 0.9:
        fpt_speed = -1

    headers = {
        "api-key": api_key,
        "voice": voice,
        "speed": str(fpt_speed),
        "format": "mp3"
    }
    url = "https://api.fpt.ai/hmi/tts/v5"
    try:
        response = requests.post(url, headers=headers, data=text.encode("utf-8"), timeout=15)
        if response.status_code != 200:
            raise RuntimeError(f"FPT API returned status code {response.status_code}")
        data = response.json()
        if not data.get("async"):
            raise RuntimeError(f"FPT API failed: {data.get('message', 'Unknown error')}")
        async_url = data["async"]
        
        for _ in range(30):
            time.sleep(1)
            poll_resp = requests.get(async_url, timeout=10)
            if poll_resp.status_code == 200:
                with open(out_path, "wb") as f:
                    f.write(poll_resp.content)
                return
        raise TimeoutError("FPT TTS synthesis timed out")
    except Exception as e:
        print(f"[TTS] FPT error: {e}, falling back to edge_tts")
        _edge_tts(text, "vi-VN-HoaiMyNeural", speed, out_path)
