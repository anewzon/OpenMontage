# Relaxation — Research Director (`research` stage)

Produces: `work/research.md`, plus the filled-in topic fields in `brief.txt`

Answers one question: **what should this channel make next?**

## First: is there anything to decide?

Read `brief.txt`.

- **`TOPIC LOCKED: YES`** → the human has chosen. Use their `PROJECT TITLE` and
  `VISUAL THEME` exactly. **Skip this stage entirely.** Do not research, do not
  suggest alternatives, do not "improve" their topic. Say you skipped it and
  move to `inventory`.
- **`TOPIC LOCKED: NO`**, or the topic fields are `[TBD]` → research and select.

A locked topic is a human decision. Respect it.

## Read the channel's own rules first

Channel identity comes from the folder, never from memory:

1. `Channels\<channel_id>\BRAND.md` — what this channel is and is not
2. `Channels\<channel_id>\COMPETITORS.md` — channel URLs, one per line
3. `Channels\<channel_id>\RESEARCH.md` — that channel's research rules

`RESEARCH.md` governs this stage. It is per-channel: read the one belonging to
**this** project's channel and no other. Everything below is the generic
method; where the channel's `RESEARCH.md` is more specific, it wins.

**`COMPETITORS.md` is a plain list of URLs.** Lines starting with `#` are
comments or headings — skip them. Anything that looks like a YouTube channel
URL is a competitor. A human must be able to add one by pasting a URL and
nothing else: never ask for a channel ID, and never rewrite that file into a
structured format.

## Method

Study competitors to understand **demand and patterns** — popular videos,
recent videos, breakout performers, durations, title and thumbnail shapes,
momentum, seasonal timing.

Compare fairly. **Views per hour** (views ÷ hours since publication) is the
honest comparator across videos of different ages; raw views flatter old
uploads. State which measure you used.

Once the channel has published videos of its own, **our own performance data
outranks competitor inference.** Our audience is the one that matters.

### Do not fabricate

Report only what you actually observed. If view counts, publish dates or
demand signals are unavailable, **say the source was unavailable** and reason
from what you do have. An honest "I could not measure this" is useful; an
invented number silently corrupts every decision built on it.

## Then decide, don't just report

Generate several candidate concepts internally. Choose **one**, and be able to
say why it beat the others.

**Build an original concept.** Competitor research tells you what people want —
long slow forest-river pieces, say, or rain-on-water at sleep length. It does
not tell you what to copy. Reproducing a competitor's video with a new title is
a failure of this stage, not a shortcut through it.

Check the selection against three things before committing:

- **Brand.** Inside `BRAND.md`? If research points off-brand, report it and
  recommend against it — do not quietly drift.
- **Feasibility.** Can the operator actually obtain licensed footage for this?
  A concept needing shots nobody sells is not a concept.
- **Distinctness.** Does this repeat our own last upload? Varying across
  uploads is a brand requirement, not a nicety.

## Output

Write `work/research.md` in exactly this shape. **Keep it short** — this is a
decision record, not a corpus. No database, no scraped dumps.

```
# Research Summary

Selected Topic:
...

Why Selected:
...

Patterns Observed:
...

Original Angle:
...

Suggested Visuals:
...

Suggested Duration:
...

Suggested Packaging Direction:
...
```

`Suggested Packaging Direction` is an early note for the `package` stage, not a
decision. **Final title and thumbnail are chosen after `final.mp4` exists**, by
the packaging director, from the video that actually got made.

Then write the selection into `brief.txt` — `PROJECT TITLE` and `VISUAL THEME`
— and leave the rest of the brief alone.

Update `STATUS.md`: `Research: COMPLETE`, `Brief: COMPLETE`, `Topic:` set to the
chosen topic, `Stage: RESEARCH_COMPLETE`.

---

## Then say exactly what to buy: `work/ASSET_LIST.md`

The concept is worthless until someone can source footage for it. Translate it
into a shopping list a **low-skilled employee** can execute without judgement
calls.

Work out what the edit actually needs — how many distinct visual setups, which
environments, which shot scales, roughly how many minutes of usable footage
(see the reuse-factor guidance in `asset-director.md`), what music, what
ambience. Be specific: "misty river establishing wide, 4K, static or very slow
push" is actionable; "some river footage" is not.

**On links.** Prefer exact asset-page URLs when you can genuinely locate them.
If you cannot verify a URL, **do not invent one** — a fabricated link wastes the
employee's time and destroys their trust in the list. Give precise search
instructions instead: the site, the search terms, and what a good result looks
like. Say which you are doing.

**Do not bulk-download Envato assets automatically.** Purchasing and downloading
is the operator's action.

Write it in this shape:

```
# ASSET LIST

Project:
channel_0001 / video_0003

Channel:
River Flow Naturescapes

Video Concept:
[concept]

Target Duration:
[duration]


==================================================
VISUALS
==================================================

Download these assets and put them inside:

visuals\

1. [Exact Envato item link, or precise search instructions]
   Purpose: Misty river establishing shot
   Preferred: 4K
   Status: NOT DOWNLOADED

2. [...]
   Purpose: Forest stream close-up
   Preferred: 4K
   Status: NOT DOWNLOADED


==================================================
MUSIC
==================================================

Put music files inside:

music\

[requirements / links if appropriate]


==================================================
SFX / AMBIENCE
==================================================

Put files inside:

sfx\

1. [link]
   Purpose: Gentle river ambience

2. [link]
   Purpose: Forest birds


==================================================
WHEN FINISHED
==================================================

Return to the AI agent and say:

Assets added, continue.
```

Then set `Asset List: COMPLETE` and `Stage: WAITING_FOR_ASSETS` in `STATUS.md`,
and **stop** — see the stop format in the project's `RUN_PRODUCTION.md`. Do not
continue into `inventory`.

Stop for operator approval. They may override your topic; if they do, take
theirs without argument.
