# epub2audiobook

Turn an EPUB into a chaptered `.m4b` audiobook on your own machine, using
[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M).

Built for non-fiction — history, philosophy, science — where the hard part
is not the synthesis but the hundreds of proper nouns a text-to-speech
frontend will mispronounce.

```bash
cp mybook.epub books/my-book/book/
python pipeline/run.py
# → books/my-book/audiobook/My Book.m4b
```

## Why Kokoro

Kokoro is **non-autoregressive**. It predicts durations and decodes in one
pass, so unlike LLM-based text-to-speech it cannot hallucinate a sentence,
skip a clause, or let the narrator's voice drift over eighteen hours. Its
voices are frozen style vectors — chapter one and chapter four hundred use
the identical tensor.

The cost of that is no voice cloning. You pick from its 54 voices or you
use something else. For long-form narration it is usually the right trade:
a voice that never wanders beats a voice that could have been yours.

At 82M parameters it runs comfortably on a laptop CPU, around **5× faster
than real time** on Apple Silicon — a three-hour book in about forty
minutes.

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

English only at present. Kokoro supports eight languages, but this pipeline
hardcodes American English (`lang_code="a"`) and its normalisation rules
assume it.

## Usage

One directory per book. Drop in an EPUB and run:

```
books/<slug>/book/anything.epub      you provide this
books/<slug>/audiobook/*.m4b         the result
books/<slug>/book.json               optional settings
books/<slug>/lexicon.tsv             optional pronunciations
```

```bash
python pipeline/run.py                  # every book with no .m4b yet
python pipeline/run.py my-book          # just one
python pipeline/run.py --force          # rebuild one already done
python pipeline/run.py --keep-wavs      # keep intermediates
```

Every stage is resumable. Completed chapters are kept, and an interrupted
one leaves only a `.part` file — never a short chapter that looks finished.

## Getting a good result

Two checks before committing to a long render. On an eighteen-hour book
they cost ten minutes and save you finding out at hour nine.

### 1. Check what it decided to narrate

```bash
python pipeline/extract.py my-book
```

Reading order and chapter titles come from the EPUB's own table of contents,
not from any publisher's markup, and **a spine document the TOC omits is
treated as front matter**. That one rule removes covers, adverts, signup
pages, indexes and navigation across every publisher tried so far. Titles
matching the skip list in `pipeline/common.py` (index, notes, bibliography,
illustrations, "praise for…") go too.

Read the output. If a chapter is missing or an index survived, fix it with
`skip` / `keep` in `book.json`.

### 2. Check the names

```bash
python pipeline/probe_names.py my-book
```

This is where quality in non-fiction is won or lost. Kokoro never sees your
letters, only phonemes, so every mispronunciation is a frontend problem.
Names outside its dictionary get guessed from English spelling rules, which
works for some and mangles others:

| | |
|---|---|
| already correct | Thucydides, Nietzsche, Versailles, Machiavelli, Xerxes, Charlemagne |
| wrong by default | Goethe → "GOHTH", Hegel → "HEJ-ul", Engels → "EN-julz", Cowper → "KOW-per" |

`pipeline/probe_names.py` prints every proper noun with the phonemes it will get.
Skim it, and put anything wrong in a lexicon:

```
lexicon/shared.tsv          real names, applied to every book
books/<slug>/lexicon.tsv    anything peculiar to one book
```

Format is `word<TAB>IPA<TAB>intended sound`. The third column is a note to
yourself, so the file stays reviewable without reading IPA. Kokoro's
notation: `A`=ay, `I`=eye, `O`=oh, `W`=ow, `Y`=oy.

The repository ships a starter `lexicon/shared.tsv` of ~100 entries built
while narrating a Roman history and a philosophy of history — European
philosophers, classical figures, French and German loanwords. Non-fiction
repeats its vocabulary relentlessly, so one entry can fix four hundred
utterances.

## Configuration

`book.json` is optional; title, author and year default to the EPUB's own
metadata. See `examples/book.json`.

| key | meaning |
|---|---|
| `title` `author` `year` | M4B metadata |
| `voice` | Kokoro voice id, default `af_heart` |
| `speed` | 1.0 is ~150 wpm, standard audiobook pace |
| `skip` / `keep` | regexes matched against TOC titles; `keep` overrides the built-in skip list |
| `strip_tables` | drop tables, which read as noun soup once flattened |
| `require_toc` | treat spine documents the TOC omits as front matter |
| `append` | speak extra text at the end of a section, for a footnote worth keeping |

### Voices

`af_heart` (grade A) is the default and the best of them; `af_bella` (A−) is
the closest alternative and `bm_fable` the British option. The rest of
Kokoro's set is materially worse — the grades are in the model's
[VOICES.md](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md).

## Checking a finished book

```bash
python pipeline/verify.py my-book
```

Compares each section's duration against its word count — a dropped chunk
shows up as a section out of line with its neighbours — and checks levels
for clipping. It calibrates to the book's own pace rather than a fixed
words-per-minute, because pace varies by book and voice.

```bash
ffmpeg -ss 08:00:00 -t 00:20:00 -i book.m4b -ar 24000 -ac 1 /tmp/x.wav
python pipeline/inspect_joins.py /tmp/x.wav
```

`pipeline/inspect_joins.py` measures what a bad chunk join looks like mechanically: a
pause far longer than the ones the pipeline inserts, or a sudden level step
across one. A healthy stretch shows the deliberate pauses and nothing else —
median near the 0.55 s paragraph gap, little past the 1.1 s heading gap, no
level steps.

**Neither tool can judge prosody** — odd stress, or a chunk boundary landing
somewhere awkward. Listen to a few minutes of a long chapter before
committing to the whole thing.

## How it works

```
pipeline/            the code
lexicon/shared.tsv   pronunciations applied to every book
books/<slug>/        one directory per book, created by you
build/<slug>/        intermediates, safe to delete
```

```
books/<slug>/book/*.epub
  │
  ├─ extract.py      EPUB → text, one file per section
  ├─ synth.py        text → 24 kHz WAV, one process per section
  ├─ verify.py       duration and level checks
  └─ package.py      → chaptered, loudness-normalised .m4b
```

`pipeline/run.py` chains them; each also runs standalone against a slug.
All commands are run from the project root.

**Text normalisation** is most of `pipeline/extract.py`. Superscript note markers are
removed as whole elements (left in, the bare digit gets spoken mid-sentence).
Roman numeral headings become "Chapter Three"; regnal numbers become
"Gregory the Seventh" rather than "Gregory vee eye eye". `B.C.`, `A.D.`,
`i.e.`, `e.g.` and `d.` are expanded, which also stops their full stops
creating false sentence boundaries. Dashes and ellipses become spoken
pauses. Print-edition cross-references like `(see pp. 241–2)` are dropped.

**Chunking** targets the 100–200 phoneme-token window the model card calls
its goldilocks range — shorter and delivery goes flat, longer and it audibly
rushes. Sentences are never split mid-clause, since that is where a join is
most obvious.

**Output** is 64 kbps mono AAC at −20 LUFS, the usual audiobook target,
about 30 MB per hour.

## Troubleshooting

**Renders slow down partway through a long book.** Healthy is around 5×
real time. A stalled render does not fail — it keeps producing audio about
twenty times too slowly. `pipeline/synth.py` samples each section's rate once a
minute and restarts a section that stays below threshold, which has
reliably restored full speed; thresholds are constants at the top of the
file. Why restarting helps is not established: it is not a cross-chapter
leak, and it is not duration-dependent. If it persists, check free memory —
Kokoro needs about a gigabyte, and on an 8 GB machine a long book will
stall once the system is into swap.

```bash
vm_stat | head -4          # "Pages free", in 16 KB pages
sysctl vm.swapusage
```

**A chapter narrates the index.** The TOC listed it. Add a `skip` regex.

**Nothing is extracted.** The EPUB's TOC may be missing or broken; set
`"require_toc": false` and lean on the skip list instead.

**A name is still wrong after a lexicon entry.** Entries are matched
case-insensitively with possessives handled, but not across hyphens or
inside other words. Check `pipeline/probe_names.py` output again — it skips names
already covered.

## Licence

MIT, see [LICENSE](LICENSE). Kokoro-82M is Apache-2.0 by
[hexgrad](https://github.com/hexgrad/kokoro); `espeak-ng` and `phonemizer`
are GPL-3.0 and installed separately.

Nothing here grants rights in the books you feed it.
