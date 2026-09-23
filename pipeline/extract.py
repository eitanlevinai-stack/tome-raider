#!/usr/bin/env python3
"""Extract narration-ready text from an EPUB.

Reading order comes from the spine and titles from the book's own table of
contents, so this does not depend on how any one publisher marks up its
chapters. Front and back matter are dropped by title (see common.SKIP_TITLES).

    python extract.py <slug>
"""

import argparse
import html
import re
import sys
import zipfile
from pathlib import Path

from common import Book, all_books

ROMAN_CARDINAL = {
    "I": "One", "II": "Two", "III": "Three", "IV": "Four", "V": "Five",
    "VI": "Six", "VII": "Seven", "VIII": "Eight", "IX": "Nine", "X": "Ten",
    "XI": "Eleven", "XII": "Twelve", "XIII": "Thirteen", "XIV": "Fourteen",
    "XV": "Fifteen", "XVI": "Sixteen", "XVII": "Seventeen",
    "XVIII": "Eighteen", "XIX": "Nineteen", "XX": "Twenty",
}
ROMAN_ORDINAL = {
    "I": "First", "II": "Second", "III": "Third", "IV": "Fourth",
    "V": "Fifth", "VI": "Sixth", "VII": "Seventh", "VIII": "Eighth",
    "IX": "Ninth", "X": "Tenth", "XI": "Eleventh", "XII": "Twelfth",
    "XIII": "Thirteenth", "XIV": "Fourteenth", "XV": "Fifteenth",
    "XVI": "Sixteenth", "XVII": "Seventeenth", "XVIII": "Eighteenth",
}

ARABIC_CARDINAL = {str(i): w for i, w in enumerate(
    ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
     "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
     "Sixteen", "Seventeen", "Eighteen", "Nineteen", "Twenty"])}

# Regnal numbers ("Gregory VII", "COS III") are otherwise spelled out letter
# by letter as "vee eye eye".
REGNAL = re.compile(r"\b([A-Z][A-Za-z]+)\s+([IVX]{1,6})\b")

# Cross-references to the printed edition ("see pp. 241-2") are meaningless
# read aloud, and the page numbers are noise in the middle of a sentence.
PAGE_REF = re.compile(
    r"\s*\(\s*(?:see\s+)?pp?\.[^)]*\)|\s*\(\s*see\s+pp?\.[^)]*\)", re.I)

FRACTIONS = [(r"(?<=\d)\s+1/2\b", " and a half"),
             (r"(?<=\d)\s+1/4\b", " and a quarter"),
             (r"(?<=\d)\s+3/4\b", " and three quarters")]

PAUSE_MARKS = "–—…"  # en dash, em dash, ellipsis

# Only used to estimate finished length from a word count.
WORDS_PER_MINUTE = 150

ABBREVIATIONS = [
    (r"\bB\.\s*C\.(?:E\.)?", "B C"),
    # A bare "A" is swallowed as the article, so the letter is forced.
    (r"\bA\.\s*D\.", "[A](/ˈA/) D"),
    (r"\bi\.\s*e\.,?", "that is,"),
    (r"\be\.\s*g\.,?", "for example,"),
    (r"\bcf\.", "compare"),
    (r"\bSt\.\s+", "Saint "),
    (r"\bd\.\s+(?=\d|\[A\])", "died "),
    (r"\bb\.\s+(?=\d|\[A\])", "born "),
]


# --------------------------------------------------------------- EPUB reading

def opf_path(zf: zipfile.ZipFile) -> str:
    container = zf.read("META-INF/container.xml").decode("utf-8", "replace")
    return re.search(r'full-path="([^"]+)"', container).group(1)


def read_spine(zf: zipfile.ZipFile) -> tuple[list[str], str]:
    """Return document hrefs in reading order, plus the OPF's base directory."""
    opf_name = opf_path(zf)
    base = str(Path(opf_name).parent)
    opf = zf.read(opf_name).decode("utf-8", "replace")

    manifest = {}
    for item in re.findall(r"<item\b[^>]*>", opf):
        ident = re.search(r'id="([^"]+)"', item)
        href = re.search(r'href="([^"]+)"', item)
        if ident and href:
            manifest[ident.group(1)] = html.unescape(href.group(1))

    order = re.findall(r'<itemref\b[^>]*idref="([^"]+)"', opf)
    hrefs = [manifest[i] for i in order if i in manifest]
    return [join(base, h) for h in hrefs], base


def join(base: str, href: str) -> str:
    path = f"{base}/{href}" if base and base != "." else href
    parts: list[str] = []
    for part in path.split("/"):
        if part == "..":
            parts and parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    return "/".join(parts)


def read_toc(zf: zipfile.ZipFile, base: str) -> dict[str, str]:
    """Map document path -> title, from toc.ncx (EPUB2) or nav (EPUB3)."""
    titles: dict[str, str] = {}
    for name in zf.namelist():
        lower = name.lower()
        if lower.endswith(".ncx"):
            doc = zf.read(name).decode("utf-8", "replace")
            for point in re.findall(r"<navPoint\b.*?</navPoint>", doc, re.S):
                label = re.search(r"<text>(.*?)</text>", point, re.S)
                src = re.search(r'<content[^>]*src="([^"]+)"', point)
                if label and src:
                    key = join(str(Path(name).parent), src.group(1)).split("#")[0]
                    titles.setdefault(key, clean_text(label.group(1)))
        elif lower.endswith((".xhtml", ".html")) and "nav" in lower:
            doc = zf.read(name).decode("utf-8", "replace")
            for href, label in re.findall(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                                          doc, re.S):
                key = join(str(Path(name).parent), href).split("#")[0]
                titles.setdefault(key, clean_text(label))
    return titles


def metadata(zf: zipfile.ZipFile) -> dict[str, str]:
    opf = zf.read(opf_path(zf)).decode("utf-8", "replace")
    def grab(tag: str) -> str:
        m = re.search(rf"<dc:{tag}[^>]*>(.*?)</dc:{tag}>", opf, re.S | re.I)
        return clean_text(m.group(1)) if m else ""
    return {"title": grab("title"), "author": grab("creator"),
            "year": grab("date")[:4]}


# ------------------------------------------------------------- text cleaning

def clean_text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", fragment)).split())


def strip_markup(fragment: str, strip_tables: bool) -> str:
    """Flatten an XHTML fragment to plain text, one line per paragraph."""
    fragment = re.sub(r"<(script|style)\b.*?</\1>", "", fragment,
                      flags=re.S | re.I)
    # Note references are a bare digit welded to the following word, so they
    # must go as whole elements or they get spoken mid-sentence.
    fragment = re.sub(r"<sup\b.*?</sup>", "", fragment, flags=re.S | re.I)
    if strip_tables:
        # A table reads as a wall of disconnected nouns once flattened. Its
        # caption -- a short all-capitals line just above -- goes with it.
        fragment = re.sub(
            r"(?:<p\b[^>]*>(?:(?!</p>)[^a-z<])*?</p>\s*)?<table\b.*?</table>",
            "", fragment, flags=re.S | re.I,
        )
    fragment = re.sub(r"</(p|div|h[1-6]|li|tr)\s*>", "\n\n", fragment, flags=re.I)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return html.unescape(fragment)


def replace_pause_marks(text: str) -> str:
    """Dashes and ellipses become a spoken pause, without doubling up."""
    marks = f"[{PAUSE_MARKS}]+"
    text = re.sub(rf"\s*{marks}\s*(?=[,.;:!?])", " ", text)
    text = re.sub(rf"(?<=[,.;:!?])\s*{marks}\s*", " ", text)
    return re.sub(rf"\s*{marks}\s*", ", ", text)


def spell_regnal(text: str) -> str:
    return REGNAL.sub(
        lambda m: f"{m.group(1)} the {ROMAN_ORDINAL[m.group(2)]}"
        if m.group(2) in ROMAN_ORDINAL else m.group(0),
        text,
    )


def normalize(text: str, lexicon: list[tuple[str, str]]) -> str:
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    # Print-edition cross-references go before the range rule below, which
    # would otherwise turn "pp. 241-2" into the spoken "241 to 2".
    text = PAGE_REF.sub("", text)
    for pattern, replacement in FRACTIONS:
        text = re.sub(pattern, replacement, text)
    # Year ranges must not be read as a subtraction.
    text = re.sub(r"(\d)\s*[–—]\s*(\d)", r"\1 to \2", text)
    text = replace_pause_marks(text)
    text = spell_regnal(text)
    for pattern, replacement in ABBREVIATIONS:
        text = re.sub(pattern, replacement, text)
    text = apply_lexicon(text, lexicon)
    text = re.sub(r",\s*,+", ",", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\s+\)", ")", text)
    return text


def apply_lexicon(text: str, lexicon: list[tuple[str, str]]) -> str:
    for word, ipa in lexicon:
        pattern = re.compile(rf"\b{re.escape(word)}\b('s)?", re.IGNORECASE)
        def sub(m: re.Match[str], ipa=ipa) -> str:
            return f"[{m.group(0)}](/{ipa}{'z' if m.group(1) else ''}/)"
        text = pattern.sub(sub, text)
    return text


def spoken_heading(title: str) -> str:
    """'Chapter III: Biology and History' -> 'Chapter Three. Biology...'"""
    title = title.strip().rstrip(".")
    def numeral(m: re.Match[str]) -> str:
        word = ROMAN_CARDINAL.get(m.group(2).upper())
        return f"{m.group(1)} {word}" if word else m.group(0)
    title = re.sub(r"\b(Chapter|Part|Book)\s+([IVXL]+)\b", numeral, title, flags=re.I)
    # A bare leading numeral, roman ("III. Biology") or arabic ("1. Cicero"),
    # with no "Chapter" in front of it.
    title = re.sub(r"^([IVXL]+)[.:]\s*",
                   lambda m: f"Chapter {ROMAN_CARDINAL.get(m.group(1), m.group(1))}. ",
                   title)
    title = re.sub(r"^(\d{1,2})[.:]\s*",
                   lambda m: f"Chapter {ARABIC_CARDINAL.get(m.group(1), m.group(1))}. ",
                   title)
    title = title.replace(":", ".")
    return title if title.endswith(("?", "!", ".")) else title + "."


def looks_like_heading(block: str, title: str) -> bool:
    """True if a body block is just a restatement of the TOC title."""
    if len(block.split()) > 14:
        return False
    words = lambda s: set(re.findall(r"[a-z]{3,}", s.lower()))
    shared = words(block) & words(title)
    return bool(shared) and len(shared) >= max(1, len(words(title)) - 1)


# ----------------------------------------------------------------- extraction

def extract(book: Book) -> int:
    zf = zipfile.ZipFile(book.epub)
    spine, base = read_spine(zf)
    toc = read_toc(zf, base)
    lexicon = book.lexicon_rules()
    strip_tables = book.config["strip_tables"]
    appends = book.config["append"]

    book.text.mkdir(parents=True, exist_ok=True)
    for stale in book.text.glob("*.txt"):
        stale.unlink()

    index = 0
    total = 0
    for href in spine:
        if href not in zf.namelist():
            continue
        # The table of contents is the book's own statement of what its
        # content is; spine documents it omits are navigation, adverts or
        # publisher matter almost every time.
        if href not in toc and book.config["require_toc"]:
            continue
        title = toc.get(href) or Path(href).stem
        if book.should_skip(title):
            continue

        doc = zf.read(href).decode("utf-8", "replace")
        body = re.search(r"<body\b[^>]*>(.*)</body>", doc, re.S | re.I)
        if not body:
            continue

        blocks = [" ".join(b.split()) for b in
                  strip_markup(body.group(1), strip_tables).split("\n\n")]
        blocks = [b for b in blocks if b]
        if not blocks:
            continue
        # Drop the in-document heading; the TOC title is spoken instead.
        if looks_like_heading(blocks[0], title):
            blocks = blocks[1:]
        if not blocks or sum(len(b.split()) for b in blocks) < 40:
            continue

        paragraphs = [" ".join(normalize(b, lexicon).split()) for b in blocks]
        stem = Path(href).stem
        if stem in appends:
            paragraphs.append(normalize(appends[stem], lexicon))

        heading = normalize(spoken_heading(title), lexicon)
        out = book.text / f"{index:02d}_{stem}.txt"
        out.write_text(f"{heading}\n\n" + "\n\n".join(paragraphs) + "\n",
                       encoding="utf-8")
        words = sum(len(p.split()) for p in paragraphs)
        total += words
        print(f"  {out.name:<24}{words:>6} words  | {heading}")
        index += 1

    print(f"  {index} sections, {total} words, "
          f"~{total / WORDS_PER_MINUTE / 60:.1f} h")
    return index


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug", nargs="?", help="book directory under books/")
    args = parser.parse_args()

    books = [Book(args.slug)] if args.slug else all_books()
    if not books:
        print("no books found under books/")
        return 1

    for book in books:
        print(f"\n{book.slug}")
        meta = metadata(zipfile.ZipFile(book.epub))
        for key in ("title", "author", "year"):
            if not book.config.get(key) and meta.get(key):
                book.config[key] = meta[key]
        if not extract(book):
            print("  nothing extracted -- check skip patterns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
