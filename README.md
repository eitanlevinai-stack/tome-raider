# epub2audiobook

Convert an EPUB into a chaptered `.m4b` audiobook on your own machine, using
[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M).

```bash
mkdir -p books/my-book/book
cp mybook.epub books/my-book/book/
python pipeline/run.py
# → books/my-book/audiobook/My Book.m4b
```

Everything runs locally. No account, no API, no upload.

## Why Kokoro

Kokoro is **non-autoregressive**. It predicts durations and decodes in a
single pass, so unlike LLM-based text-to-speech it cannot hallucinate a
sentence, skip a clause, or let the narrator's voice drift over a long book.
Its voices are frozen style vectors — the first chapter and the last use the
identical tensor.

The cost is no voice cloning: you choose from its 54 voices. For long-form
narration that is usually the right trade.

At 82M parameters it runs on a laptop CPU, several times faster than real
time, and needs no GPU.

## Requirements

- Python 3.10+
- `espeak-ng` — grapheme-to-phoneme, which Kokoro depends on
- `ffmpeg` — encodes the finished `.m4b`

```bash
brew install espeak-ng ffmpeg          # macOS
sudo apt install espeak-ng ffmpeg      # Debian/Ubuntu

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

English only. Kokoro supports eight languages, but this pipeline uses
American English and its text normalisation assumes it.

## Layout

One directory per book. You provide the EPUB; the pipeline makes the rest.

```
pipeline/                       the code
lexicon/*.tsv                   pronunciations applied to every book
books/<slug>/book/*.epub        you provide this
books/<slug>/audiobook/*.m4b    the result
books/<slug>/book.json          optional settings
books/<slug>/lexicon.tsv        optional pronunciations
build/<slug>/                   intermediates, safe to delete
```

## Usage

All commands run from the project root.

```bash
python pipeline/run.py                  # every book with no .m4b yet
python pipeline/run.py my-book          # just one
python pipeline/run.py --force          # rebuild one already done
python pipeline/run.py --keep-wavs      # keep intermediates
```

Every stage is resumable. Completed chapters are kept, and an interrupted
one leaves only a `.part` file — never a short chapter that looks finished.

## Two checks before a long render

Both take a few minutes and save discovering a problem hours in.

### 1. Check what it decided to narrate

```bash
python pipeline/extract.py my-book
```

Reading order and chapter titles come from the EPUB's own table of contents
rather than any publisher's markup, and **a spine document the TOC omits is
treated as front matter**. That single rule removes covers, adverts, signup
pages and navigation across most publishers. Titles matching the skip list
in `pipeline/common.py` — index, notes, bibliography, illustrations and
similar — are dropped too.

Read the output. If a section is missing or something unwanted survived,
adjust `skip` / `keep` in `book.json`.

### 2. Check the names

```bash
python pipeline/probe_names.py my-book
```

Kokoro never sees your letters, only phonemes. A name or loanword outside
its dictionary is guessed from English spelling rules, and the guess is
sometimes wrong — a surname may take its stress on the wrong syllable, or a
silent letter may get voiced. Some books have a handful of these; others
have hundreds.

`probe_names.py` lists every proper noun with the phonemes it will be given,
commonest first. Skim it and correct what is wrong:

```
lexicon/*.tsv               applied to every book
books/<slug>/lexicon.tsv    anything peculiar to one book
```

Any `.tsv` in `lexicon/` is loaded, so you can keep a personal file beside
the shared one; only `shared.tsv` is tracked by git.

Format is `word<TAB>IPA<TAB>intended sound`, for example:

```
Ramirez	ɹəmˈiɹɛz	ruh-MEE-rez
```

The third column is a note to yourself, so the file stays reviewable without
reading IPA. Kokoro's notation: `A`=ay, `I`=eye, `O`=oh, `W`=ow, `Y`=oy.
`lexicon/shared.tsv` documents the format and workflow in full.

Names repeat, so a single entry often fixes hundreds of utterances.

## Configuration

`book.json` is optional. Title, author and year default to the EPUB's own
metadata.

```json
{
  "title": "Book Title",
  "author": "Author Name",
  "year": "2020",
  "voice": "af_heart",
  "speed": 1.0,
  "skip": ["^appendix"],
  "keep": [],
  "strip_tables": true,
  "require_toc": true,
  "append": {"ch10": "Text spoken at the end of this section."}
}
```

| key | meaning |
|---|---|
| `title` `author` `year` | M4B metadata |
| `voice` | Kokoro voice id, default `af_heart` |
| `speed` | playback rate; 1.0 is a normal audiobook pace |
| `skip` / `keep` | regexes matched against TOC titles; `keep` overrides the built-in skip list |
| `strip_tables` | drop tables, which read as disconnected words once flattened |
| `require_toc` | treat spine documents the TOC omits as front matter |
| `append` | speak extra text at the end of a section, for a footnote worth keeping |

### Voices

`af_heart` is the default and the highest-graded. `af_bella` is the closest
alternative and `bm_fable` a British option. Grades for the full set are in
the model's
[VOICES.md](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md).

## Checking a finished book

```bash
python pipeline/verify.py my-book
```

Compares each section's duration against its word count — a dropped chunk
shows up as a section out of line with its neighbours — and checks levels
for clipping. It calibrates against the book's own pace rather than a fixed
words-per-minute.

```bash
ffmpeg -ss 00:30:00 -t 00:20:00 -i book.m4b -ar 24000 -ac 1 /tmp/x.wav
python pipeline/inspect_joins.py /tmp/x.wav
```

`inspect_joins.py` measures what a bad chunk join looks like mechanically: a
pause far longer than the ones the pipeline inserts, or a sudden level step
across one. A healthy stretch shows the deliberate pauses and nothing else.

**Neither tool can judge prosody** — odd stress, or a chunk boundary landing
somewhere awkward. Listen to a few minutes before committing to a whole
book.

## How it works

```
books/<slug>/book/*.epub
  │
  ├─ extract.py      EPUB → text, one file per section
  ├─ synth.py        text → 24 kHz WAV, one process per section
  ├─ verify.py       duration and level checks
  └─ package.py      → chaptered, loudness-normalised .m4b
```

`pipeline/run.py` chains them; each also runs standalone against a slug.

**Text normalisation** is most of `extract.py`. Superscript note markers are
removed as whole elements — left in, the bare digit gets spoken mid-sentence.
Roman numeral headings become spoken words, and regnal numbers become "the
Seventh" rather than being spelled out letter by letter. `B.C.`, `A.D.`,
`i.e.`, `e.g.` and `d.` are expanded, which also stops their full stops
creating false sentence boundaries. Dashes and ellipses become spoken pauses.
Print-edition cross-references such as `(see pp. 241–2)` are dropped.

**Chunking** targets the 100–200 phoneme-token window the model card calls
its goldilocks range: shorter and delivery goes flat, longer and it audibly
rushes. Sentences are never split mid-clause, since that is where a join is
most obvious.

**Output** is 64 kbps mono AAC, loudness-normalised to the usual audiobook
target.

## Troubleshooting

**A render slows down partway through.** A stalled render does not fail — it
keeps producing audio, just far too slowly, and does not recover on its own.
`synth.py` samples each section's output rate and restarts a section that
stays below threshold; the thresholds are constants at the top of the file.
If it keeps happening, check free memory: Kokoro holds a substantial working
set, and a long book will stall once the machine is into swap.

**A section narrates something unwanted.** Its title was in the table of
contents. Add a `skip` regex to `book.json`.

**Nothing is extracted.** The EPUB's table of contents may be missing or
broken. Set `"require_toc": false` and rely on the skip list instead.

**A name is still wrong after adding a lexicon entry.** Entries match
case-insensitively and handle possessives, but not across hyphens or inside
longer words. Re-run `probe_names.py` — it skips names already covered, so
anything still listed has not matched.

## Licence

MIT, see [LICENSE](LICENSE). Kokoro-82M is Apache-2.0 by
[hexgrad](https://github.com/hexgrad/kokoro); `espeak-ng` and `phonemizer`
are GPL-3.0 and installed separately.

Nothing here grants any rights in the books you convert.
