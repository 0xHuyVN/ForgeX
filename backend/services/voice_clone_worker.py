#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
os.environ["VOICE_CLONE_PYTHON"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    train = sub.add_parser("train-bark")
    train.add_argument("--sample-path", required=True)
    train.add_argument("--name", required=True)

    clone = sub.add_parser("clone-bark")
    clone.add_argument("--sample-path", required=True)
    clone.add_argument("--text", required=True)
    clone.add_argument("--output-path", required=True)

    args = parser.parse_args()

    try:
        if args.command == "train-bark":
            from backend.services.voice_clone import train_clone
            train_clone(args.sample_path, args.name)
            print(json.dumps({"ok": True}, ensure_ascii=False))
        elif args.command == "clone-bark":
            from backend.services.voice_clone import clone_voice
            clone_voice(args.sample_path, args.text, args.output_path)
            print(json.dumps({"ok": True, "output": args.output_path}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
