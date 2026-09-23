#!/usr/bin/env python3
"""Drive the whole pipeline over every book that has not been built yet.

    python run.py                 every unbuilt book, in order
    python run.py <slug>          just that book
    python run.py --force         rebuild even if the m4b exists
    python run.py --keep-wavs     leave intermediates in place

Each stage is resumable: extract rewrites its text, synth skips sections
whose WAV already exists, and package rebuilds from whatever audio is there.
Interrupting a long render loses only the section in flight.
"""

import argparse
import sys
import time
import traceback

import extract
import package
import synth
import verify
from common import Book, all_books


def process(book: Book, args, pipelines) -> bool:
    print(f"\n{'=' * 64}\n{book.slug}\n{'=' * 64}")

    print("\n[1/4] extract")
    import zipfile
    meta = extract.metadata(zipfile.ZipFile(book.epub))
    for key in ("title", "author", "year"):
        if not book.config.get(key) and meta.get(key):
            book.config[key] = meta[key]
    if not extract.extract(book):
        print("  nothing extracted -- check skip patterns in book.json")
        return False

    print("\n[2/4] synth")
    synth.synth_book_isolated(book)

    print("\n[3/4] verify")
    problems = verify.check(book)

    print("\n[4/4] package")
    if not package.build(book, clean=not args.keep_wavs):
        return False
    if problems:
        print(f"  NOTE: {problems} section(s) flagged -- worth a listen")
    return True


def main() -> int:
    # Python block-buffers stdout when it is redirected to a file, which
    # makes a long run look hung. Progress should be watchable.
    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser()
    parser.add_argument("slug", nargs="?")
    parser.add_argument("--force", action="store_true",
                        help="rebuild books that already have an m4b")
    parser.add_argument("--keep-wavs", action="store_true",
                        help="keep intermediate audio after packaging")
    args = parser.parse_args()

    books = [Book(args.slug)] if args.slug else all_books()
    # An empty books/ is a first run, not a finished one, and saying so is
    # the difference between a useful message and a baffling one.
    if not books:
        print("no books found. Put an EPUB in books/<name>/book/ and run again")
        return 1
    if not args.force:
        books = [b for b in books if not b.done]
    if not books:
        print("nothing to do -- every book is built (use --force to rebuild)")
        return 0

    # Loading Kokoro costs a few seconds, so it is shared across the batch
    # and deferred until a book actually needs it.
    cached: list = []
    def pipelines():
        if not cached:
            from kokoro import KPipeline
            cached.append(KPipeline(lang_code="a"))
            cached.append(KPipeline(lang_code="a", model=False))
        return cached[0], cached[1]

    print(f"{len(books)} book(s) to build: {', '.join(b.slug for b in books)}")
    started = time.time()
    built, failed = [], []

    for book in books:
        try:
            (built if process(book, args, pipelines) else failed).append(book.slug)
        except Exception:
            traceback.print_exc()
            failed.append(book.slug)
            print(f"  {book.slug} failed -- continuing with the rest")

    print(f"\n{'=' * 64}")
    print(f"built {len(built)} in {(time.time() - started) / 3600:.2f} h"
          + (f", failed {len(failed)}: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
