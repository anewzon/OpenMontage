# Relaxation — Procurement Director (`procurement` stage)

Produces: `work/ASSET_LIST.md`, and an `awaiting_human` checkpoint

This is the **human gate**. The footage is licensed stock a person must buy.
Say exactly what to buy, then stop.

## Read this first

**`skills/meta/asset-procurement.md` defines the process** — deriving search
intent, inspecting provider filters, building hard/preferred/unset profiles,
two-pass screening, exact-item verification, cross-list diversity review, the
`ASSET_LIST.md` format, and the honesty rules about what was actually
inspected.

Follow it. **This skill does not repeat it** — it only says what *good*
procurement means for long-form relaxation.

| Layer | Resource | Purpose |
|---|---|---|
| Meta | `meta/asset-procurement.md` | How to discover, evaluate and verify assets |
| Artifact | `proposal_packet` | The approved concept — every requirement derives from it |
| Channel | `<channel_root>/BRAND.md` | Subject priorities and what to avoid |
| Protocol | `meta/checkpoint-protocol.md` | The `awaiting_human` gate |

## Why this stage exists

The canonical `assets` stage produces an `asset_manifest`, which requires real
local file paths. Before a human has downloaded anything there are no paths, so
the request cannot live there. That is the whole justification for this being a
stage of its own, and it is the only non-canonical stage in this pipeline. It
produces **no new artifact schema** — the request travels in checkpoint
metadata, with a Markdown view for the employee.

---

## What good procurement means for long-form relaxation

Relaxation is unusual: the camera is mostly still, shots are held far longer
than in any other format, and the viewer often has the video on for hours.
That changes what counts as a good asset.

**Prefer, where the concept calls for it:**

- **Long usable shots.** A held shot is the unit of this format. A clip whose
  steady section is only a few seconds is nearly worthless here even if it is
  beautiful, because it cannot carry a hold.
- **Calm camera movement** — static, or a drift slow enough to be unnoticed.
- **Stable, settled composition** that survives a long look.
- **Environmental continuity** — light, season and weather that can sit next to
  the neighbouring shots without jarring.
- **Natural visual rhythm** — movement supplied by the subject (water, leaves,
  mist), not by the camera.
- **Wide / medium / detail variety** across the pool, so the edit has scales to
  cut between.
- **Ambience with genuine usable length** and a clean, un-processed character.
- **Low visual distraction** — nothing that pulls the eye and breaks the spell.

**Avoid, unless the concept explicitly wants it:**

- aggressive or fast camera movement; rapid handheld
- commercial or lifestyle framing (models, products, staged activity)
- speed ramps and timelapses
- baked-in heavy grades or stylised edits
- clips that already loop
- prominent human activity

## Duration is the constraint that bites

The most common failure in this format is **buying far too little footage**.

Work it out explicitly and write the arithmetic into the checkpoint:

```
unique screen time needed  = approved duration
usable seconds per clip    = realistic steady section AFTER trimming the
                             unsteady head and tail — not the listed length
clips needed               = screen time ÷ usable seconds, × a reject margin
```

Two rules that follow:

- **Do not assume a clip's listed duration is usable duration.** Stock clips
  routinely open or close with a bump, a focus hunt or an exposure shift.
- **If the concept promises no repetition, the footage must actually support
  it.** Either request enough for the full duration, or say plainly that the
  duration should come down. **Never close the gap by repeating shots while
  still claiming the video is unrepeated**, and never by stretching footage
  with artificial slow motion.

Where the provider exposes a **length filter**, use it — raising the minimum
clip length is the single most effective way to keep the clip count sane.

## Sound: an evolving bed, not one file on repeat

The soundscape must change across the runtime. A single ambience file looping
for an hour is audible and is the format's other classic failure.

Derive from the approved concept: **each movement wants its own water or
environment character**, plus secondary layers and occasional detail. Where
several similar files are needed, say explicitly that they must be *genuinely
different recordings*, not the same source repeated.

State a ceiling — how much of the film any single ambience file may cover — and
require beds to cross over **inside a shot, never on a cut**.

## What comes from where

Nothing in this skill fixes the subject, season, resolution, duration or shot
mix. Those come from:

- the **approved `proposal_packet`** — concept, duration, movements, canvas
- the **channel's `BRAND.md`** — subjects to prioritise and avoid
- the **current asset requirement** being filled
- the **provider's actual filters** at runtime

Another relaxation channel may want ocean, rain, fireplace or night scenery at
a different length and canvas, through this same pipeline. If you find yourself
writing a specific subject, season or resolution into *this file*, it belongs
in that channel's `BRAND.md` instead.

## The gate

Write `work/ASSET_LIST.md` in the format `meta/asset-procurement.md` defines —
exact item links, one per entry, absolute destination folders, no internal
filter complexity, no creative decisions left to the employee.

Also tell them to save licence receipts; anything without evidence gets flagged
at `assets` and cannot ship.

Then write the checkpoint with `status: "awaiting_human"`,
`human_approval_required: true`, and the structured request list plus the
footage and audio arithmetic in `metadata`. **End the turn:**

```
WAITING FOR ASSETS

Project:    <channel_id> / <video_id>
Asset list: <full path to ASSET_LIST.md>

Download the requested assets into the folders listed there, then reply:

Assets added, continue.
```

**Do not continue into `assets`. Do not render. Do not invent or substitute
media.** An empty assets folder is a reason to wait, not to improvise.

## On resumption

Verify the files are actually present and sufficient for the concept. If they
are clearly short, say precisely what is missing and remain at
`awaiting_human` — do not limp forward with too little footage, and do not
quietly shorten the film without saying so. If sufficient, record the human
approval on the checkpoint and continue into `assets`.
