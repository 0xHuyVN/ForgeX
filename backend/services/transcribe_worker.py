#!/usr/bin/env python3
import sys
import json
import argparse
import os

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio_path")
    parser.add_argument("--language", default="vi")
    parser.add_argument("--model", default="base")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--compute-type", default="auto")
    parser.add_argument("--whisperx", action="store_true", help="Use WhisperX for word-level alignment")
    args = parser.parse_args()
    device, compute_type = _resolve_runtime(args.device, args.compute_type)

    use_whisperx = args.whisperx
    if use_whisperx:
        try:
            import whisperx
        except ImportError:
            print(json.dumps({"error": "WhisperX requested but not installed. Run: pip install whisperx"}))
            sys.exit(1)

    if use_whisperx:
        try:
            # 1. Load model and transcribe
            model = whisperx.load_model(args.model, device, compute_type=compute_type, language=args.language)
            audio = whisperx.load_audio(args.audio_path)
            raw_result = model.transcribe(audio, batch_size=16)

            # 2. Align whisper output
            try:
                model_a, metadata = whisperx.load_align_model(language_code=raw_result["language"], device=device)
                aligned_result = whisperx.align(raw_result["segments"], model_a, metadata, audio, device, return_char_alignments=False)
                segments_data = aligned_result["segments"]
            except Exception as align_err:
                # Fallback to raw segments if alignment fails
                segments_data = raw_result["segments"]

            srt_lines = []
            for i, seg in enumerate(segments_data, 1):
                start = _fmt_time(seg.get("start", 0))
                end = _fmt_time(seg.get("end", 0))

                # Format text with word timing: Word (start_time)
                words_list = seg.get("words", [])
                if words_list:
                    word_parts = []
                    for w in words_list:
                        word_str = w.get("word", "").strip()
                        w_start = w.get("start")
                        if w_start is not None:
                            word_parts.append(f"{word_str} ({w_start:.2f})")
                        else:
                            word_parts.append(word_str)
                    text = " ".join(word_parts).strip()
                else:
                    text = seg.get("text", "").strip()

                srt_lines.append(f"{i}\n{start} --> {end}\n{text}\n")

            srt_content = "\n".join(srt_lines)
            full_text = " ".join(s.get("text", "") for s in segments_data)

            result = {
                "srt_path": "",
                "text": full_text,
                "segments": len(segments_data),
                "srt_content": srt_content,
                "language": raw_result.get("language", args.language),
                "whisperx_aligned": True
            }
            print(json.dumps(result, ensure_ascii=False))
            return
        except Exception as wx_err:
            print(json.dumps({"error": f"WhisperX failed: {wx_err}"}, ensure_ascii=False))
            sys.exit(1)

    # Standard faster-whisper pipeline
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print(json.dumps({"error": "faster-whisper not installed. Run: pip install faster-whisper"}))
        sys.exit(1)

    try:
        model = WhisperModel(args.model, device=device, compute_type=compute_type)
        segments, info = model.transcribe(args.audio_path, language=args.language)
        seg_list = list(segments)

        srt_lines = []
        for i, seg in enumerate(seg_list, 1):
            start = _fmt_time(seg.start)
            end = _fmt_time(seg.end)
            text = seg.text.strip()
            srt_lines.append(f"{i}\n{start} --> {end}\n{text}\n")

        srt_content = "\n".join(srt_lines)
        full_text = " ".join(s.text for s in seg_list)

        result = {
            "srt_path": "",
            "text": full_text,
            "segments": len(seg_list),
            "srt_content": srt_content,
            "language": info.language if hasattr(info, "language") else args.language,
            "whisperx_aligned": False
        }
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)


def _resolve_runtime(device_arg: str, compute_arg: str):
    device = (device_arg or "auto").lower()
    if device == "auto":
        device = "cpu"
        try:
            import torch
            if torch.cuda.is_available():
                device = "cuda"
        except Exception:
            pass

    compute_type = (compute_arg or "auto").lower()
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"

    return device, compute_type


def _fmt_time(secs):
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    ms = int((secs - int(secs)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


if __name__ == "__main__":
    main()
