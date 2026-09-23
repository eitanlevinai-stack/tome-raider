#!/usr/bin/env python3
"""Shared paths, config and lexicon loading for the audiobook pipeline.

Layout, one directory per book:

    books/<slug>/book/*.epub        source
    books/<slug>/audiobook/*.m4b    finished audiobook
    books/<slug>/book.json          per-book settings (optional)
    books/<slug>/lexicon.tsv        per-book pronunciations (optional)
    lexicon/shared.tsv              pronunciations reused across books
    build/<slug>/text|audio         intermediates, safe to delete
"""

import json
import re
from pathlib import Path

# The scripts live in pipeline/; books, build and lexicon sit beside it at
# the project root.
ROOT = Path(__file__).resolve().parent.parent
BOOKS = ROOT / "books"
BUILD = ROOT / "build"
SHARED_LEXICON = ROOT / "lexicon" / "shared.tsv"

# Sections almost no audiobook wants read aloud. Matched case-insensitively
# against the title the EPUB's own table of contents gives each section, so
# it works regardless of how the publisher named the files.
SKIP_TITLES = [
    r"^cover", r"^title", r"^copyright", r"^copy$", r"^contents$", r"^toc$",
    r"^table of contents", r"^nav$", r"^index\b", r"^notes?$", r"^endnotes?",
    r"^footnotes?", r"^footnt", r"^bibliograph", r"^further reading",
    r"^guide to books", r"^about\b", r"^acknowledg", r"^dedication",
    r"^also by", r"^advertisement", r"sign[_ ]?up", r"recommend",
    r"^newsletter", r"^colophon", r"^permissions", r"^credits",
    r"^fm\d*$", r"^bm\d*$",  # publisher front/back matter stubs
    r"^maps?$", r"^list of", r"^illustrations?\b", r"^plates\b",
    r"^praise for", r"^glossar", r"^timeline$", r"^chronology$",
    r"^appendix", r"^notes on", r"^picture credits",
]

DEFAULTS = {
    "title": None,          # falls back to EPUB metadata
    "author": None,
    "year": "",
    "voice": "af_heart",
    "speed": 1.0,
    "skip": [],             # extra title patterns to drop
    "keep": [],             # title patterns to rescue from SKIP_TITLES
    "strip_tables": True,   # tables read as noun soup once flattened
    "require_toc": True,    # a spine document the TOC omits is not content
    "append": {},           # section id -> text spoken at its end
}


class Book:
    """Everything the pipeline needs to know about one book."""

    def __init__(self, slug: str):
        self.slug = slug
        self.dir = BOOKS / slug
        self.source_dir = self.dir / "book"
        self.audiobook_dir = self.dir / "audiobook"
        self.build = BUILD / slug
        self.text = self.build / "text"
        self.audio = self.build / "audio"
        self.config = {**DEFAULTS, **self._load_config()}

    def _load_config(self) -> dict:
        path = self.dir / "book.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    @property
    def epub(self) -> Path:
        found = sorted(self.source_dir.glob("*.epub"))
        if not found:
            raise FileNotFoundError(f"no .epub in {self.source_dir}")
        return found[0]

    @property
    def m4b(self) -> Path:
        title = self.config.get("title") or self.slug
        return self.audiobook_dir / f"{safe_filename(title)}.m4b"

    @property
    def done(self) -> bool:
        return self.m4b.exists()

    def skip_patterns(self) -> list[str]:
        return SKIP_TITLES + list(self.config["skip"])

    def should_skip(self, title: str) -> bool:
        text = title.strip().lower()
        for pattern in self.config["keep"]:
            if re.search(pattern, text, re.I):
                return False
        return any(re.search(p, text, re.I) for p in self.skip_patterns())

    def lexicon_rules(self) -> list[tuple[str, str]]:
        """Shared entries first, then the book's own, which win on conflict."""
        entries: dict[str, str] = {}
        for path in (SHARED_LEXICON, self.dir / "lexicon.tsv"):
            entries.update(read_lexicon(path))
        return list(entries.items())

    def __repr__(self) -> str:
        return f"<Book {self.slug}>"


def read_lexicon(path: Path) -> dict[str, str]:
    """Parse a word<TAB>IPA<TAB>comment file; blank lines and # are ignored."""
    if not path or not path.exists():
        return {}
    entries = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] and parts[1]:
            entries[parts[0]] = parts[1]
    return entries


def safe_filename(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "-", name).strip()


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def all_books() -> list[Book]:
    if not BOOKS.exists():
        return []
    return [Book(p.name) for p in sorted(BOOKS.iterdir())
            if p.is_dir() and (p / "book").is_dir()]
