import os
import sys
import subprocess
import wave
import numpy as np
from pathlib import Path
from ..config import CACHE_DIR


def separate_vocals(audio_path: str, model: str = "htdemucs") -> str:
    """Separate vocals from background music using Demucs. Returns path to vocals WAV.

    Features:
    - Smart cache: returns cached result if already processed.
    - Auto-skip: if audio is mostly clean speech, returns original path.
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    output_dir = CACHE_DIR / "separated"
    input_stem = Path(audio_path).stem
    vocals_path = output_dir / model / input_stem / "vocals.wav"

    # Smart cache
    if vocals_path.exists():
        return str(vocals_path)
    vocals_mp3 = output_dir / model / input_stem / "vocals.mp3"
    if vocals_mp3.exists():
        return str(vocals_mp3)

    # Auto fallback: skip Demucs if audio is mostly clean speech
    if _is_clean_speech(audio_path):
        return audio_path

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(
            [sys.executable, "-m", "demucs", "--two-stems", "vocals", "-o", str(output_dir), str(audio_path)],
            check=True, capture_output=True, text=True, timeout=600,
        )
    except FileNotFoundError:
        raise RuntimeError("demucs not found. Install with: pip install demucs")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Demucs separation failed: {e.stderr[:500]}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Demucs separation timed out after 600s")

    if vocals_path.exists():
        return str(vocals_path)
    if vocals_mp3.exists():
        return str(vocals_mp3)
    raise FileNotFoundError(f"Vocals output not found at {vocals_path}")


def _is_clean_speech(audio_path: str, threshold: float = 0.8) -> bool:
    """Analyze audio RMS dynamics to estimate if it's mostly clean speech.

    Speech has high energy variance (alternating loud/quiet with pauses),
    while music has more consistent energy. Returns True if speech-like.
    """
    try:
        with wave.open(audio_path, 'rb') as wf:
            rate = wf.getframerate()
            frames = wf.getnframes()
            n = min(frames, rate * 30)
            audio = np.frombuffer(wf.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:
        return False

    if len(audio) < rate:  # Less than 1 second
        return False

    # RMS in 100ms windows
    win = max(1, int(rate * 0.1))
    rms_list = []
    for i in range(0, len(audio) - win + 1, win):
        seg = audio[i:i+win]
        rms_list.append(float(np.sqrt(np.mean(seg**2))))
    rms = np.array(rms_list)

    if len(rms) < 3:
        return False

    mean_rms = float(np.mean(rms))
    if mean_rms < 0.001:
        return False

    cv = float(np.std(rms) / mean_rms)
    silence_ratio = float(np.mean(rms < mean_rms * 0.3))

    # Heuristic scoring
    if cv > 0.6:
        score = 0.7 + silence_ratio * 0.3
    elif cv > 0.4:
        score = 0.3 + silence_ratio * 0.5
    else:
        score = silence_ratio * 0.4

    return min(score, 1.0) > threshold
