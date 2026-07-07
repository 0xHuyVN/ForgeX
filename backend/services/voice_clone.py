import os
import json
import shutil
import warnings
import numpy as np
import subprocess
import sys
from pathlib import Path
from ..config import VOICES_DIR, VOICE_CLONE_PYTHON

_bark_loaded = False
_torch_patched = False


def _get_torch():
    global _torch_patched
    import torch
    if not _torch_patched:
        _orig_torch_load = torch.load

        def _safe_torch_load(f, *a, **kw):
            kw.pop("weights_only", None)
            return _orig_torch_load(f, *a, **kw, weights_only=False)

        torch.load = _safe_torch_load
        _torch_patched = True
    return torch

def _ensure_models():
    global _bark_loaded
    if _bark_loaded:
        return
    warnings.filterwarnings("ignore")
    os.environ["SUNO_USE_SMALL_MODELS"] = "True"
    _get_torch()
    from bark import preload_models
    preload_models(text_use_small=True, coarse_use_small=True, fine_use_small=True)
    _bark_loaded = True


def wav_to_history_prompt(audio_path: str) -> dict:
    torch = _get_torch()
    from bark import generation, SAMPLE_RATE
    import scipy.io.wavfile as wavfile

    _ensure_models()

    sr, audio = wavfile.read(audio_path)
    if audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0

    if sr != SAMPLE_RATE:
        import scipy.signal
        audio = scipy.signal.resample(audio, int(len(audio) * SAMPLE_RATE / sr))

    max_samples = SAMPLE_RATE * 10
    if len(audio) > max_samples:
        audio = audio[:max_samples]

    codec_model = generation.load_codec_model(use_gpu=torch.cuda.is_available())
    device = next(codec_model.parameters()).device

    audio_tensor = torch.from_numpy(audio).float().unsqueeze(0).unsqueeze(0).to(device)
    with torch.no_grad():
        encoded_frames = codec_model.encode(audio_tensor)

    all_codes = [frame[0].cpu() for frame in encoded_frames]
    fine_codes = torch.cat(all_codes, dim=-1)

    fine_prompt = fine_codes[0].numpy().astype(np.uint16)
    coarse_prompt = fine_codes[0, :2].numpy().astype(np.uint16)

    semantic_rate = generation.SEMANTIC_RATE_HZ
    n_semantic = max(int(len(audio) / SAMPLE_RATE * semantic_rate), 10)

    coarse_ch0 = coarse_prompt[0]
    if len(coarse_ch0) >= n_semantic:
        semantic_vals = coarse_ch0[:n_semantic]
    else:
        semantic_vals = np.pad(
            coarse_ch0,
            (0, n_semantic - len(coarse_ch0)),
            "constant",
            constant_values=generation.SEMANTIC_PAD_TOKEN,
        )

    return {
        "semantic_prompt": semantic_vals.astype(np.uint16),
        "coarse_prompt": coarse_prompt,
        "fine_prompt": fine_prompt,
    }


def clone_voice(sample_path: str, text: str, output_path: str):
    if _should_use_external_worker():
        _run_external_worker(["clone-bark", "--sample-path", sample_path, "--text", text, "--output-path", output_path])
        return
    try:
        _ensure_models()
        from bark import generate_audio, SAMPLE_RATE
        import scipy.io.wavfile as wavfile

        history_prompt = wav_to_history_prompt(sample_path)
        audio_arr = generate_audio(text, history_prompt=history_prompt)
        wavfile.write(output_path, SAMPLE_RATE, audio_arr)
    except Exception as e:
        print(f"[Voice Clone] Error: {e}")
        from .tts_engine import synthesize
        synthesize(text, "edge", "vi-VN-NamMinhNeural", 1.0, output_path)


def train_clone(sample_path: str, name: str):
    if _should_use_external_worker():
        _run_external_worker(["train-bark", "--sample-path", sample_path, "--name", name])
        return
    clones_dir = VOICES_DIR / "clones" / name
    clones_dir.mkdir(parents=True, exist_ok=True)
    src = os.path.abspath(sample_path)
    if os.path.exists(src):
        sample_copy = clones_dir / f"sample_{os.path.basename(src)}"
        if not sample_copy.exists():
            shutil.copy2(src, sample_copy)

    try:
        _ensure_models()
        from bark import SAMPLE_RATE, save_as_prompt
        import scipy.io.wavfile as wavfile

        prompt_path = str(clones_dir / "voice_prompt.npz")
        history_prompt = wav_to_history_prompt(sample_path)
        save_as_prompt(prompt_path, history_prompt)

        preview_text = "Xin chào, đây là giọng nói clone."
        from bark import generate_audio
        audio_arr = generate_audio(preview_text, history_prompt=history_prompt)
        wavfile.write(str(clones_dir / "preview.wav"), SAMPLE_RATE, audio_arr)

        (clones_dir / "done.txt").write_text("training complete")
    except Exception as e:
        print(f"[Voice Clone Train] Error: {e}")
        (clones_dir / "done.txt").write_text(f"error: {e}")


def train_f5_clone(sample_path: str, name: str, ref_text: str = ""):
    clones_dir = VOICES_DIR / "clones" / name
    clones_dir.mkdir(parents=True, exist_ok=True)
    src = os.path.abspath(sample_path)
    if not os.path.exists(src):
        raise FileNotFoundError(f"Sample audio not found: {sample_path}")

    ref_audio = clones_dir / "f5_ref.wav"
    try:
        from ..config import FFMPEG_PATH
        import subprocess

        cmd = [
            FFMPEG_PATH, "-y",
            "-i", src,
            "-vn",
            "-ac", "1",
            "-ar", "24000",
            str(ref_audio),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        suffix = os.path.splitext(src)[1].lower() or ".wav"
        ref_audio = clones_dir / f"f5_ref{suffix}"
        shutil.copy2(src, ref_audio)

    profile = {
        "engine": "f5",
        "name": name,
        "ref_audio": str(ref_audio),
        "ref_text": (ref_text or "").strip(),
    }
    (clones_dir / "f5_profile.json").write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    (clones_dir / "done.txt").write_text("f5 profile ready", encoding="utf-8")
    return profile


def _should_use_external_worker() -> bool:
    return bool(VOICE_CLONE_PYTHON and os.path.exists(VOICE_CLONE_PYTHON))


def _run_external_worker(args: list):
    worker = Path(__file__).with_name("voice_clone_worker.py")
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    repo_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = repo_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    cmd = [VOICE_CLONE_PYTHON, str(worker), *args]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        env=env,
    )
    payload = _parse_worker_json(result.stdout)
    if result.returncode != 0 or payload.get("ok") is False:
        raise RuntimeError(payload.get("error") or result.stderr.strip() or result.stdout.strip())
    return payload


def _parse_worker_json(stdout: str) -> dict:
    text = (stdout or "").strip()
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}
