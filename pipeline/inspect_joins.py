#!/usr/bin/env python3
"""Look for the mechanical symptoms of bad chunk joins.

Prosody itself can only be judged by listening. What can be measured is the
shape of the silences between utterances and the level either side of them:
a chunk that was cut badly tends to show up as a pause far longer than the
ones the pipeline inserts deliberately, or as a sudden jump in level across
one.

    python inspect_joins.py <audio file> [--quiet-db -45] [--min-gap 0.25]
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

# Pauses the pipeline inserts on purpose (see synth.py).
PAUSE_PARAGRAPH = 0.55
PAUSE_HEADING = 1.10


def find_gaps(path: Path, quiet_db: float, min_gap: float):
    """Yield (start, duration, level_before, level_after) for each silence."""
    info = sf.info(path)
    rate = info.samplerate
    window = int(rate * 0.02)                 # 20 ms envelope
    threshold = 10 ** (quiet_db / 20)

    envelope = []
    for block in sf.blocks(path, blocksize=rate * 60, dtype="float32"):
        if block.ndim > 1:
            block = block.mean(axis=1)
        usable = len(block) - len(block) % window
        if usable:
            envelope.append(np.abs(block[:usable]).reshape(-1, window).max(axis=1))
    if not envelope:
        return [], 0.0
    envelope = np.concatenate(envelope)
    quiet = envelope < threshold

    gaps = []
    start = None
    for i, is_quiet in enumerate(quiet):
        if is_quiet and start is None:
            start = i
        elif not is_quiet and start is not None:
            length = (i - start) * window / rate
            if length >= min_gap:
                before = envelope[max(0, start - 25):start]
                after = envelope[i:i + 25]
                gaps.append((start * window / rate, length,
                             float(before.max()) if len(before) else 0.0,
                             float(after.max()) if len(after) else 0.0))
            start = None
    return gaps, len(envelope) * window / rate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--quiet-db", type=float, default=-45)
    parser.add_argument("--min-gap", type=float, default=0.25)
    args = parser.parse_args()

    gaps, duration = find_gaps(Path(args.audio), args.quiet_db, args.min_gap)
    if not gaps:
        print("no silences found -- check --quiet-db")
        return 1

    lengths = np.array([g[1] for g in gaps])
    print(f"{Path(args.audio).name}: {duration / 60:.1f} min, {len(gaps)} pauses")
    print(f"  median {np.median(lengths):.2f}s  "
          f"p90 {np.percentile(lengths, 90):.2f}s  max {lengths.max():.2f}s")

    # A pause well past the deliberate heading pause is the suspicious case.
    long_gaps = [g for g in gaps if g[1] > PAUSE_HEADING * 1.6]
    print(f"  {len(long_gaps)} pauses over {PAUSE_HEADING * 1.6:.1f}s "
          f"({len(long_gaps) / duration * 3600:.1f} per hour)")
    for start, length, before, after in long_gaps[:8]:
        print(f"    {int(start // 60):3d}:{start % 60:05.2f}  {length:.2f}s")

    # A join that cuts mid-phrase often leaves a step in level across it.
    steps = [g for g in gaps
             if g[2] > 0.01 and g[3] > 0.01 and
             abs(20 * np.log10(g[3] / g[2])) > 12]
    print(f"  {len(steps)} pauses with >12 dB level step across them "
          f"({len(steps) / duration * 3600:.1f} per hour)")
    for start, length, before, after in steps[:8]:
        step = 20 * np.log10(after / before)
        print(f"    {int(start // 60):3d}:{start % 60:05.2f}  {step:+.0f} dB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
