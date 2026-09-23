# EPUB to audiobook

Turns EPUBs into chaptered `.m4b` audiobooks with [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M),
locally, on Apple Silicon.

Kokoro is non-autoregressive, so unlike LLM-based TTS it cannot hallucinate,
skip a sentence, or let the narrator's voice drift over a long book. The
tradeoff is that it cannot clone a voice — you pick from its 54.

## Adding a book

```
books/<slug>/book/whatever.epub
```

That is the only required step. Then:

```bash
python run.py                    # build every book that has no m4b yet
python run.py <slug>             # just one
python run.py --force            # rebuild something already built
```

Output lands in `books/<slug>/audiobook/`. Intermediates go to
`build/<slug>/` and are deleted after packaging unless you pass
`--keep-wavs`.

## Stages

`run.py` chains these; each also runs standalone against a slug.

| | |
|---|---|
| `extract.py` | EPUB → `build/<slug>/text/*.txt` |
| `probe_names.py` | how Kokoro will say each proper noun |
| `synth.py` | text → `build/<slug>/audio/*.wav` |
| `verify.py` | catches dropped chunks, clipping, silence |
| `package.py` | → chaptered, loudness-normalised `.m4b` |
| `inspect_joins.py` | pause and level analysis on finished audio |

Every stage is resumable. `synth.py` skips sections already rendered, so an
interrupted run loses only the section in flight.

## Getting a good result

**Reading order and titles come from the EPUB's own table of contents.** A
spine document the TOC does not list is treated as publisher matter and
dropped — that alone removes covers, ads, signup pages and navigation across
every publisher tried so far. Titles matching `common.SKIP_TITLES` (index,
notes, bibliography, illustrations, praise-for…) go too.

**Check the section list before rendering.** Run `extract.py <slug>` on its
own and read the output. Nineteen hours of audio is a long time to discover
you narrated the index.

**Then check the names.** `probe_names.py <slug>` prints every proper noun
with its phonemes. This is where quality is won or lost in non-fiction —
Kokoro guesses non-English names from English spelling rules and gets
Goethe, Hegel and Engels wrong, while handling Thucydides, Nietzsche and
Versailles correctly. Fix what is wrong in a lexicon:

```
lexicon/shared.tsv          real names, reused by every book
books/<slug>/lexicon.tsv    anything peculiar to one book
```

Format is `word<TAB>IPA<TAB>intended sound`. The third column is a note to
yourself, so the file stays reviewable without reading IPA. Kokoro's
notation: `A`=ay, `I`=eye, `O`=oh, `W`=ow, `Y`=oy.

## book.json

Optional, per book. Title, author and year default to the EPUB's metadata.

```json
{
  "title": "The Lessons of History",
  "author": "Will Durant and Ariel Durant",
  "year": "1968",
  "voice": "af_heart",
  "speed": 1.0,
  "skip": ["^about will"],
  "keep": [],
  "strip_tables": true,
  "require_toc": true,
  "append": {"ch10": "Note. Some historians consider..."}
}
```

`skip` and `keep` are regex matched against TOC titles; `keep` overrides the
built-in skip list. `append` speaks extra text at the end of a section, for
a footnote worth keeping.

## Voices

`af_heart` (grade A) is the default and the best of them. `af_bella` (A-) is
the closest alternative, `bm_fable` the British option, a little faster.
Everything else in Kokoro's set is materially worse — the grades are in the
model's [VOICES.md](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md).

## What it costs

Roughly **0.28 real-time on an M1** — a 3-hour book renders in about 50
minutes, a 19-hour book in about five and a half. Intermediate WAVs run
~160 MB per hour of audio, so `--clean` (the default) matters at scale.
Output is 64 kbps mono AAC at −20 LUFS, about 30 MB per hour.

## Memory

Kokoro holds about a gigabyte while rendering, and sections run in separate
processes so nothing accumulates across a book. That bounds what the
pipeline itself uses; it does not create headroom that is not there.

On an 8 GB machine a long book needs the memory to actually be free. A
19-hour book rendered at RTF 0.21 until the system ran out and went into
swap, then collapsed to RTF 3.96 — roughly twenty times slower. If
throughput falls off partway through a long book, check free memory before
anything else:

```bash
vm_stat | head -4        # "Pages free" in 16 KB pages
sysctl vm.swapusage
```

Renders are resumable, so the fix is to close what else is running and rerun
`run.py <slug>`. Completed chapters are kept and an interrupted one leaves
only a `.part` file, never a short chapter that looks finished.

## The stall watchdog

A stalled render does not fail — it keeps producing audio, just twenty times
too slowly. One chapter here spent 2h14m on 35 minutes of audio; a restarted
process did the whole chapter in 15 minutes. Another spent 17.5 hours
reaching 81 of 96 minutes, then finished in 19 minutes after a restart.

Why restarting helps is not settled. It was not a cross-chapter leak (fresh
processes stalled too) and not duration-dependent (a 3,000-word section
stalled as readily as a 15,000-word one). What is reproducible is the
remedy, so `synth.py` acts on that: it samples each section's output rate
once a minute, and after `STALL_CHECKS` consecutive samples below
`STALL_RATE` × realtime it kills and restarts that section. The last of
`MAX_ATTEMPTS` runs unwatched, since finishing slowly beats looping.

Healthy is around 5× realtime. Thresholds are constants at the top of
`synth.py`.

## Checking a finished book

`verify.py` compares each section's duration against its word count, which
catches dropped chunks, and checks levels for clipping. Neither it nor
anything else here can judge **prosody** — odd stress, or a chunk boundary
falling somewhere awkward.

`inspect_joins.py` gets closer by measuring what a bad join looks like
mechanically: a pause far longer than the ones the pipeline inserts, or a
sudden level step across one.

```bash
ffmpeg -ss 08:00:00 -t 00:20:00 -i book.m4b -ar 24000 -ac 1 /tmp/x.wav
python inspect_joins.py /tmp/x.wav
```

A healthy stretch looks like the deliberate pauses and nothing else — median
near the 0.55 s paragraph gap, almost nothing past the 1.1 s heading gap, and
no level steps. Anything beyond that is worth listening to.

## Limits

`verify.py` checks duration and levels, which catches dropped chunks and
clipping. Nothing here checks **prosody** — an oddly stressed sentence or an
awkward chunk join only shows up on playback. Spot-check a few minutes of a
long chapter before committing to a whole book.
