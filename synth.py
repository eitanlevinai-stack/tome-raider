#!/usr/bin/env python3
"""Render extracted text to audio with Kokoro.

Chunks land inside the 100-200 phoneme-token range the model card calls its
goldilocks window: shorter and the delivery goes flat, longer and it audibly
rushes. Sentences are never split mid-clause, since that is where a join is
most obvious.

Audio streams straight to disk. A long chapter can run over an hour, and
holding that as float32 in memory costs hundreds of megabytes it does not
need to.

    python synth.py <slug>              whole book
    python synth.py <slug> --only 03    one section
"""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from common import Book, all_books

SAMPLE_RATE = 24_000
TARGET_TOKENS = 180
MAX_TOKENS = 200

PAUSE_PARAGRAPH = 0.55
PAUSE_HEADING = 1.10

# Terminal punctuation, optional closing quote, then space and a capital.
# Inline overrides like [Pius](/pˈIəs/) carry no periods, so they cannot
# produce a false boundary here.
SENTENCE_END = re.compile(r'(?<=[.!?])["\']?\s+(?=[A-Z"\'\[])')


def sentences(paragraph: str) -> list[str]:
    return [s.strip() for s in SENTENCE_END.split(paragraph) if s.strip()]


def chunk(paragraph: str, counter) -> list[str]:
    """Greedily pack sentences into chunks under MAX_TOKENS phonemes."""
    packed: list[str] = []
    current, current_len = "", 0

    for sentence in sentences(paragraph):
        length = sum(len(ps) for _, ps, _ in counter(sentence))
        # A single sentence over the cap goes alone; splitting it mid-clause
        # would sound worse than letting the model hurry through it.
        if current and current_len + length > MAX_TOKENS:
            packed.append(current)
            current, current_len = sentence, length
        else:
            current = f"{current} {sentence}".strip()
            current_len += length
        if current_len >= TARGET_TOKENS:
            packed.append(current)
            current, current_len = "", 0

    if current:
        packed.append(current)
    return packed


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32)


def render(path: Path, out: Path, voice: str, pipeline, counter,
           speed: float = 1.0) -> float:
    """Render one section file, writing blocks out as they are produced.

    Output goes to a .part file that is renamed only once the section is
    complete. An interrupted render therefore leaves nothing that a later
    run could mistake for finished audio -- which would otherwise ship a
    truncated chapter silently.
    """
    blocks = [b.strip() for b in path.read_text(encoding="utf-8").split("\n\n")]
    blocks = [b for b in blocks if b]
    frames = 0
    partial = out.with_suffix(".wav.part")

    # format is explicit: soundfile infers it from the extension, and the
    # .part suffix tells it nothing.
    with sf.SoundFile(partial, "w", samplerate=SAMPLE_RATE, channels=1,
                      format="WAV", subtype="PCM_16") as sink:
        for index, block in enumerate(blocks):
            for text in chunk(block, counter):
                for _, _, audio in pipeline(text, voice=voice, speed=speed):
                    if audio is not None:
                        data = np.asarray(audio, dtype=np.float32)
                        sink.write(data)
                        frames += len(data)
            # The heading is the first block and earns a longer beat after it.
            pause = silence(PAUSE_HEADING if index == 0 else PAUSE_PARAGRAPH)
            sink.write(pause)
            frames += len(pause)

    partial.rename(out)
    return frames / SAMPLE_RATE


def synth_book_isolated(book: Book, only: str | None = None) -> None:
    """Render each section in a fresh process.

    Kokoro needs roughly a gigabyte while it runs, so on a small machine a
    long book is one memory-hungry process among many. Handing each section
    to its own process lets the OS reclaim everything in between, which
    costs a few seconds of model loading and bounds what the render can
    hold at once.

    It does not rescue a machine that is already out of memory. A 19-hour
    book here went from RTF 0.21 to 3.96 once the system was into swap, and
    a fresh process per chapter made no difference -- at that point the fix
    is free memory, not tidier processes.
    """
    files = sorted(book.text.glob("*.txt"))
    if only:
        files = [f for f in files if f.name.startswith(only)]
    pending = [f for f in files if not (book.audio / f"{f.stem}.wav").exists()]
    done = len(files) - len(pending)
    if done:
        print(f"  {done} section(s) already rendered, {len(pending)} to go")

    for path in pending:
        render_section_watched(book, path)


# A render that has stalled produces audio far slower than real time. Healthy
# runs here sit near 5x; a stalled one fell to 0.25x and stayed there for
# hours. Restarting the process has reliably restored full speed, so the
# watchdog does that rather than waiting the stall out.
STALL_RATE = 1.0        # x realtime, below which a render counts as stalled
STALL_CHECKS = 3        # consecutive slow samples before restarting
CHECK_SECONDS = 60
WARMUP_SECONDS = 120    # model loading produces no audio; do not judge it
MAX_ATTEMPTS = 3


def render_section_watched(book: Book, path: Path) -> None:
    """Run one section in a subprocess, restarting it if it stalls.

    The final attempt is left alone however slow it gets: at that point a
    restart has already failed twice, and finishing slowly beats looping.
    """
    out = book.audio / f"{path.stem}.wav"
    partial = out.with_suffix(".wav.part")
    command = [sys.executable, str(Path(__file__).resolve()), book.slug,
               "--only", path.name[:2], "--in-process"]

    for attempt in range(1, MAX_ATTEMPTS + 1):
        partial.unlink(missing_ok=True)
        process = subprocess.Popen(command, cwd=Path(__file__).resolve().parent)
        watch = attempt < MAX_ATTEMPTS
        started = time.time()
        slow = 0
        last_size = 0

        while process.poll() is None:
            time.sleep(CHECK_SECONDS)
            if not watch or time.time() - started < WARMUP_SECONDS:
                continue
            size = partial.stat().st_size if partial.exists() else 0
            rate = (size - last_size) / CHECK_SECONDS / (SAMPLE_RATE * 2)
            last_size = size
            slow = slow + 1 if rate < STALL_RATE else 0
            if slow >= STALL_CHECKS:
                print(f"  {path.stem}: stalled at {rate:.2f}x realtime, "
                      f"restarting (attempt {attempt + 1}/{MAX_ATTEMPTS})")
                process.kill()
                process.wait()
                break
        else:
            if process.returncode == 0 and out.exists():
                return
            print(f"  {path.stem} failed (exit {process.returncode})")
            return

    print(f"  {path.stem}: gave up after {MAX_ATTEMPTS} attempts")


def synth_book(book: Book, only: str | None, pipeline, counter) -> None:
    files = sorted(book.text.glob("*.txt"))
    if only:
        files = [f for f in files if f.name.startswith(only)]
    if not files:
        print(f"  no text for {book.slug} -- run extract.py first")
        return

    book.audio.mkdir(parents=True, exist_ok=True)
    voice = book.config["voice"]
    speed = book.config["speed"]
    total_audio = total_time = 0.0

    for path in files:
        out = book.audio / f"{path.stem}.wav"
        if out.exists():
            print(f"  {out.name:<26} skipped (already rendered)")
            continue
        start = time.time()
        seconds = render(path, out, voice, pipeline, counter, speed)
        elapsed = time.time() - start
        total_audio += seconds
        total_time += elapsed
        print(f"  {out.name:<26}{seconds / 60:6.1f} min audio "
              f"{elapsed / 60:6.1f} min render  RTF {elapsed / seconds:.2f}")

    if total_audio:
        print(f"  {total_audio / 3600:.2f} h audio in "
              f"{total_time / 3600:.2f} h  (voice {voice})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug", nargs="?")
    parser.add_argument("--only", help="render just this section prefix")
    parser.add_argument("--in-process", action="store_true",
                        help="render in this process instead of one per section")
    args = parser.parse_args()

    books = [Book(args.slug)] if args.slug else all_books()
    if not books:
        print("no books found under books/")
        return 1

    if not args.in_process:
        for book in books:
            print(f"\n{book.slug}")
            synth_book_isolated(book, args.only)
        return 0

    from kokoro import KPipeline
    pipeline = KPipeline(lang_code="a")
    counter = KPipeline(lang_code="a", model=False)

    for book in books:
        synth_book(book, args.only, pipeline, counter)
    return 0


if __name__ == "__main__":
    sys.exit(main())
