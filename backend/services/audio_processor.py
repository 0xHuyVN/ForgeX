from pathlib import Path
import time

from ..config import DOWNLOADS_DIR
from ..services.ffmpeg_utils import run_ffmpeg


def _processed_output(input_path: str, suffix: str) -> str:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    src = Path(input_path)
    ext = src.suffix or ".mp3"
    return str(DOWNLOADS_DIR / f"{src.stem}_{suffix}_{int(time.time())}{ext}")


def process_music(input_path: str, volume: float = 1.0, fade_in: float = 0, fade_out: float = 0, normalize: bool = False):
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(input_path)

        if volume != 1.0:
            audio = audio.apply_gain(20 * (volume - 1.0))

        if fade_in > 0:
            audio = audio.fade_in(int(fade_in * 1000))
        if fade_out > 0:
            audio = audio.fade_out(int(fade_out * 1000))
        if normalize:
            from pydub.effects import normalize
            audio = normalize(audio)

        out = _processed_output(input_path, "processed")
        audio.export(out, format=Path(input_path).suffix[1:] or "mp3")
        return out
    except ImportError:
        return _ffmpeg_process(input_path, volume, fade_in, fade_out, normalize)


def _ffmpeg_process(input_path, volume, fade_in, fade_out, normalize):
    out = _processed_output(input_path, "processed")
    filters = [f"volume={volume}"]
    if fade_in > 0:
        filters.append(f"afade=t=in:d={fade_in}")
    if fade_out > 0:
        filters.append(f"afade=t=out:st=0:d={fade_out}")
    if normalize:
        filters.append("loudnorm=I=-16:LRA=11:TP=-1.5")

    filter_str = ",".join(filters)
    cmd = ["-i", input_path, "-af", filter_str, "-y", out]
    run_ffmpeg(cmd)
    return out


def auto_duck(music_path: str, voice_path: str):
    """Reduce music volume when voice is active."""
    out = _processed_output(music_path, "ducked")
    cmd = [
        "-i", voice_path, "-i", music_path,
        "-filter_complex",
        f"[1:a]volume=1.0[mus];[0:a]asplit=2[voice1][voice2];"
        f"[voice2]compand=attacks=0.01:decays=0.1:points=-80/-80|-30/-30|-20/-10|0/-3[sidechain];"
        f"[mus][sidechain]sidechaincompress=threshold=-20:ratio=4:attack=5:release=100[ducked]",
        "-map", "[ducked]", "-y", out,
    ]
    run_ffmpeg(cmd)
    return out
