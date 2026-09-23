#!/usr/bin/env python3
"""Assemble rendered chapter audio into a chaptered .m4b audiobook.

Chapter marks come from the section files, so a player opens the book with a
real table of contents and remembers its position.

    python package.py [<slug>] [--clean]
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from common import Book, all_books

# 24 kHz mono speech; 64k AAC is transparent for this material.
BITRATE = "64k"

# Kokoro renders quieter than is comfortable to listen to. EBU R128
# normalisation lifts it to the usual audiobook target with 3 dB of headroom.
LOUDNORM = "loudnorm=I=-20:TP=-3:LRA=11"


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def heading(book: Book, stem: str) -> str:
    """The spoken heading doubles as the chapter title, minus its full stop."""
    text = (book.text / f"{stem}.txt").read_text(encoding="utf-8")
    return text.split("\n", 1)[0].strip().rstrip(".")


def build(book: Book, clean: bool) -> bool:
    waves = sorted(book.audio.glob("*.wav"))
    if not waves:
        print("  no rendered audio -- run synth.py first")
        return False

    book.audiobook_dir.mkdir(parents=True, exist_ok=True)
    concat = book.build / "concat.txt"
    concat.write_text("".join(f"file '{w.resolve()}'\n" for w in waves),
                      encoding="utf-8")

    config = book.config
    lines = [";FFMETADATA1",
             f"title={config['title'] or book.slug}",
             f"artist={config['author'] or ''}",
             f"album={config['title'] or book.slug}",
             f"date={config['year']}",
             f"composer=Kokoro {config['voice']}",
             "genre=Audiobook"]

    start = 0.0
    for wave in waves:
        end = start + duration(wave)
        lines += ["[CHAPTER]", "TIMEBASE=1/1000",
                  f"START={int(start * 1000)}", f"END={int(end * 1000)}",
                  f"title={heading(book, wave.stem)}"]
        start = end

    meta = book.build / "chapters.txt"
    meta.write_text("\n".join(lines) + "\n", encoding="utf-8")

    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-i", str(meta), "-map_metadata", "1",
        "-af", LOUDNORM,
        "-c:a", "aac", "-b:a", BITRATE, "-ac", "1",
        "-movflags", "+faststart", str(book.m4b),
    ], check=True)

    print(f"  {book.m4b.name}  {book.m4b.stat().st_size / 1e6:.0f} MB  "
          f"{start / 3600:.2f} h  {len(waves)} chapters")

    if clean:
        freed = sum(w.stat().st_size for w in waves)
        for wave in waves:
            wave.unlink()
        print(f"  removed {len(waves)} intermediate WAVs ({freed / 1e9:.1f} GB)")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug", nargs="?")
    parser.add_argument("--clean", action="store_true",
                        help="delete intermediate WAVs once the m4b is built")
    args = parser.parse_args()

    books = [Book(args.slug)] if args.slug else all_books()
    for book in books:
        print(f"\n{book.slug}")
        build(book, args.clean)
    return 0


if __name__ == "__main__":
    sys.exit(main())
