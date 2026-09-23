#!/usr/bin/env python3
"""Check rendered audio against the text it came from.

A chunk that fails to synthesise produces no error -- it just goes missing,
and the only trace is a section that runs short. Comparing each section's
duration against its word count catches that; the level checks catch a
clipped or silent render.

    python verify.py [<slug>]
"""

import argparse
import re
import sys

import numpy as np
import soundfile as sf

from common import Book, all_books

SAMPLE_RATE = 24_000

# Speaking pace varies by book, voice and speed -- two books narrated with
# the same voice came out at 147 and 159 words per minute -- so the baseline
# is the book's own median rather than a constant. A dropped chunk then
# shows up as a section far off its neighbours, which is what is worth
# catching.
FALLBACK_WPM = 150
TOLERANCE = 0.15   # a section may run 15% off the book's pace before it is
                   # suspicious

INLINE_IPA = re.compile(r"\[([^\]]+)\]\(/[^/]*/\)")


def spoken_words(book: Book, stem: str) -> int:
    text = (book.text / f"{stem}.txt").read_text(encoding="utf-8")
    return len(INLINE_IPA.sub(r"\1", text).split())


def measure(book: Book, files: list) -> list[tuple]:
    """Collect (path, words, seconds) for each rendered section."""
    rows = []
    for path in files:
        rows.append((path, spoken_words(book, path.stem), sf.info(path).duration))
    return rows


def book_wpm(rows: list[tuple]) -> float:
    """Median pace across sections, so one bad section cannot skew it."""
    paces = sorted(w / (s / 60) for _, w, s in rows if s > 1)
    if not paces:
        return FALLBACK_WPM
    mid = len(paces) // 2
    return paces[mid] if len(paces) % 2 else (paces[mid - 1] + paces[mid]) / 2


def check(book: Book) -> int:
    files = sorted(book.audio.glob("*.wav"))
    if not files:
        print("  nothing rendered")
        return 0

    rows = measure(book, files)
    wpm = book_wpm(rows)

    print(f"  {'section':<26}{'words':>7}{'actual':>9}{'expect':>9}"
          f"{'wpm':>6}{'peak':>6}  status")
    problems = 0
    total = 0.0

    for path, words, seconds in rows:
        total += seconds
        expected = words / wpm * 60
        # Read in blocks: a chapter can run over an hour, and loading one
        # whole costs hundreds of megabytes for a single number.
        peak = 0.0
        for block in sf.blocks(path, blocksize=SAMPLE_RATE * 60,
                               dtype="float32"):
            if len(block):
                peak = max(peak, float(np.abs(block).max()))

        issues = []
        if not (1 - TOLERANCE) < seconds / max(expected, 1) < (1 + TOLERANCE):
            issues.append("LENGTH")
        if peak > 0.99:
            issues.append("CLIPPING")
        if peak < 0.05:
            issues.append("SILENT")
        problems += bool(issues)

        print(f"  {path.stem:<26}{words:>7}{seconds:>8.0f}s{expected:>8.0f}s"
              f"{words / (seconds / 60):>6.0f}{peak:>6.2f}  "
              f"{' '.join(issues) if issues else 'ok'}")

    print(f"  {len(files)} sections, {total / 3600:.2f} h, "
          f"{wpm:.0f} wpm, {problems} needing a listen")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug", nargs="?")
    args = parser.parse_args()

    books = [Book(args.slug)] if args.slug else all_books()
    problems = 0
    for book in books:
        print(f"\n{book.slug}")
        problems += check(book)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
