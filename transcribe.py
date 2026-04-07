"""
Whisper-based video transcription pipeline.
Runs fully local — no API keys, no cloud, no cost.

Usage:
    python transcribe.py [--model base] [--language en] [input.mp4]
"""

import argparse
import sys
import time
from pathlib import Path

import whisper


def format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments: list, path: Path):
    with open(path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            start = format_timestamp(seg["start"])
            end = format_timestamp(seg["end"])
            f.write(f"{i}\n{start} --> {end}\n{seg['text'].strip()}\n\n")


def write_txt_with_timestamps(segments: list, path: Path):
    with open(path, "w", encoding="utf-8") as f:
        for seg in segments:
            start = format_timestamp(seg["start"])
            end = format_timestamp(seg["end"])
            f.write(f"[{start} --> {end}]  {seg['text'].strip()}\n")


def write_plain_txt(text: str, path: Path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.strip())


def main():
    parser = argparse.ArgumentParser(description="Transcribe video/audio with Whisper")
    parser.add_argument("input", nargs="?",
                        default=r"c:\Users\vmandlik\Downloads\PS1_Shashwat.mp4",
                        help="Path to video/audio file")
    parser.add_argument("--model", default="base", choices=["tiny", "base", "small", "medium", "large"],
                        help="Whisper model size (default: base)")
    parser.add_argument("--language", default=None,
                        help="Language code (e.g. 'en'). Auto-detected if omitted.")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory (default: same as input file)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"File not found: {input_path}")
        sys.exit(1)

    output_dir = Path(args.output_dir) if args.output_dir else input_path.parent
    stem = input_path.stem

    print(f"Input:    {input_path}")
    print(f"Model:    {args.model}")
    print(f"Language: {args.language or 'auto-detect'}")
    print(f"Device:   CPU (PyTorch {whisper.torch.__version__})")
    print()

    print(f"Loading Whisper '{args.model}' model...")
    t0 = time.time()
    model = whisper.load_model(args.model)
    print(f"Model loaded in {time.time() - t0:.1f}s\n")

    print("Transcribing (this may take a few minutes on CPU)...")
    t0 = time.time()
    result = model.transcribe(
        str(input_path),
        language=args.language,
        verbose=True,
    )
    elapsed = time.time() - t0
    print(f"\nTranscription completed in {elapsed:.1f}s")

    detected_lang = result.get("language", "unknown")
    print(f"Detected language: {detected_lang}")

    txt_path = output_dir / f"{stem}_transcript.txt"
    ts_path = output_dir / f"{stem}_timestamped.txt"
    srt_path = output_dir / f"{stem}.srt"

    write_plain_txt(result["text"], txt_path)
    write_txt_with_timestamps(result["segments"], ts_path)
    write_srt(result["segments"], srt_path)

    print(f"\nOutputs:")
    print(f"  Plain text:  {txt_path}")
    print(f"  Timestamped: {ts_path}")
    print(f"  Subtitles:   {srt_path}")
    print(f"\nDone.")


if __name__ == "__main__":
    main()
