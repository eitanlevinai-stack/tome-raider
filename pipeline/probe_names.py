#!/usr/bin/env python3
"""Show how Kokoro will pronounce the proper nouns in a book.

Names are where the grapheme-to-phoneme frontend actually fails, and a
history book is full of them. Run this after extract.py, skim the output,
and put anything wrong into the book's lexicon.tsv (or lexicon/shared.tsv
if it is a name other books will use too).

    python probe_names.py <slug> [--min 2] [--limit 200]
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from common import Book, read_lexicon, SHARED_LEXICON

SYSTEM_WORDS = Path("/usr/share/dict/words")

# A word capitalised only ever at the start of a sentence is capitalised by
# grammar, not because it is a name.
SENTENCE_START = re.compile(r'(?:^|[.!?"]\s+)\(?["\']?([A-Z][\w\'-]+)')
CAPITALISED = re.compile(r"\b([A-Z][\wÀ-ɏ'-]{2,})\b")
INLINE_IPA = re.compile(r"\[([^\]]+)\]\(/[^/]*/\)")


def english_words() -> set[str]:
    if not SYSTEM_WORDS.exists():
        return set()
    return {w.lower() for w in
            SYSTEM_WORDS.read_text(encoding="utf-8", errors="replace").split()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("--min", type=int, default=1,
                        help="only show names appearing at least this often")
    parser.add_argument("--limit", type=int, default=400)
    args = parser.parse_args()

    book = Book(args.slug)
    english = english_words()
    known = set(k.lower() for k in read_lexicon(SHARED_LEXICON)) | \
            set(k.lower() for k in read_lexicon(book.dir / "lexicon.tsv"))

    counts: Counter[str] = Counter()
    initial: Counter[str] = Counter()
    for path in sorted(book.text.glob("*.txt")):
        body = INLINE_IPA.sub(r"\1", path.read_text(encoding="utf-8"))
        counts.update(CAPITALISED.findall(body))
        initial.update(SENTENCE_START.findall(body))

    candidates = []
    for word, total in counts.items():
        if total < args.min or initial[word] >= total:
            continue
        if word.lower() in known:
            continue            # already has an override
        if word.lower() in english and word.isascii():
            continue            # an ordinary English word
        candidates.append((total, word))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    candidates = candidates[:args.limit]
    print(f"{len(candidates)} names to review "
          f"({len(known)} already in the lexicons)\n")

    from kokoro import KPipeline
    pipeline = KPipeline(lang_code="a", model=False)
    for total, word in candidates:
        phonemes = " ".join(ps for _, ps, _ in pipeline(word))
        print(f"{total:>4}x  {word:<24} {phonemes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
