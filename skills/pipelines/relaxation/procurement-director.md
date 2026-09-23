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

## Sourcing modes

This stage supports **two** sourcing approaches. The mode comes from the
approved `proposal_packet` (`metadata.sourcing`), never from a
hard-coded channel rule — the same pipeline serves both.

| Mode | What it means |
|---|---|
| `licensed_manual` | Paid/licensed stock a human must buy (e.g. Envato). Produce an exact-item `ASSET_LIST.md`, checkpoint `awaiting_human`, and stop for the employee to license and download. |
| `free_auto` | Permitted free stock acquired through OpenMontage's own tools (`pexels_video`, `pixabay_video`, `direct_clip_search`, `pixabay_music`). The agent downloads into the project workspace; the human gate becomes **approving the acquired pool**, not fetching files. |

**In `free_auto`, never produce an Envato download list.** Asking the employee
to fetch files the agent can legitimately acquire itself is wasted work.

**In either mode the human gate remains.** `free_auto` still checkpoints
`awaiting_human` with the acquired pool summarised for approval before
production continues — the operator sees what was obtained and can reject it.

### `free_auto` obligations

Acquisition is the easy half; these are the parts that matter.

- **Use the native tools.** Never write a scraper or a downloader, and never
  work around a provider's access restrictions. If a provider blocks access or
  its tool is broken, report the specific missing capability and stop — do not
  substitute an unverified route.
- **Always pass an explicit `output_path`/`output_dir`** under the project
  workspace. Tools that default elsewhere will write to the repo root and
  violate the workspace contract.
- **Record provenance per asset**: provider, item/page URL, creator, and the
  licence as the provider states it. Free does not mean unattributed or
  unrestricted, and provenance is what makes a later licence question
  answerable.
- **Inspect what actually downloaded.** A search result is a claim; the file is
  the evidence. Probe every file and screen it on its real content — the same
  two-pass discipline as `meta/asset-procurement.md`, applied to bytes on disk
  rather than to item pages.
- **Work in bounded units** and save recoverable progress via `in_progress`
  checkpoints and `metadata.partial_progress`, so a long acquisition run can
  resume. Chat memory is not state.
- **Do not claim free stock guarantees monetisation.** Licences permit use;
  they say nothing about a platform's monetisation decisions.

## Why this stage exists

The canonical `assets` stage produces an `asset_manifest`, which requires real
local file paths. Before a human has downloaded anything there are no paths, so
the request cannot live there. That is the whole justification for this being a
stage of its own, and it is the only non-canonical stage in this pipeline. It
produces **no new artifact schema** — the request travels in checkpoint
metadata, with a Markdown view for the employee.

---

## What good procurement means for long-form relaxation

Relaxation is unusual: shots are held far longer than in any other format, and
the viewer often has the video on for hours. That changes what counts as a good
asset.

> **This section previously asked for the opposite of what the channel
> wants.** It preferred "static, or a drift slow enough to be unnoticed" and
> "movement supplied by the subject (water, leaves, mist), not by the camera".
> That is why the second test's pool measured 47 of 56 clips locked-off with
> only 4 usable moving-camera shots — the pool was not an accident, it was
> requested. **Calm is not the same as motionless.** Read the calling channel's
> `BRAND.md` for the movement it actually wants, and do not assume relaxation
> means a still camera.

**Prefer, where the concept and the channel call for it:**

- **Long usable shots.** A held shot is the unit of this format. A clip whose
  steady section is only a few seconds is nearly worthless here even if it is
  beautiful, because it cannot carry a hold.
- **Smooth, unhurried camera movement** — slow aerial and drone reveals,
  gliding and tracking moves, gentle pans, forward movement through a
  landscape. This is what gives long-form relaxation a sense of travel; a pool
  without it cannot deliver a journey.
- **Genuine variety of camera movement** — several different kinds, not one
  kind repeated.
- **Stable, settled composition** that survives a long look.
- **Environmental continuity** — light, season and weather that can sit next to
  the neighbouring shots without jarring.
- **Wide / medium / detail variety** across the pool, so the edit has scales to
  cut between.
- **Some beautiful static compositions** for atmosphere and breathing room —
  valuable as punctuation, **not as the pool's dominant character**.
- **Ambience with genuine usable length** and a clean, un-processed character.
- **Low visual distraction** — nothing that pulls the eye and breaks the spell.

**Avoid, unless the concept explicitly wants it:**

- aggressive or fast camera movement; racing footage; rapid or shaky handheld
- distracting speed ramps; artificial slow motion
- commercial or lifestyle framing (models, products, staged activity)
- timelapses
- baked-in heavy grades or stylised edits
- clips that already loop
- every subject the channel's `BRAND.md` `channel-policy` block lists under
  `subjects.avoid` (a river channel may avoid roads and buildings; a city or
  scenic-America channel lists roads, architecture and traffic as primary -
  the pipeline itself avoids nothing on the channel's behalf)

## Search for movement, then verify it on frames

**Write search intent that can actually find the movement the channel asks
for** (`channel-policy composition.camera_movement`). Provider metadata is thin,
so the query is the main lever. Combine one of the channel's primary subjects
with a movement term, and try several movement phrasings rather than one. For
example, a water-led channel might search:

```
aerial river valley slow reveal      drone flying over forest river
gliding over mountain stream         tracking shot along river
```

and a city or landmark channel:

```
slow drive down historic main street   skyline drift at blue hour
gliding along coastal highway          tracking shot across old town square
```

Where the channel allows static composition (a fireplace, a hearth, a still
lake at dawn), search for steady locked-off footage instead and do not treat
its stillness as a shortfall.

Use the provider's own filters where they exist (length, orientation,
resolution). Raising the minimum clip length remains the single most effective
way to keep the clip count sane.

### A title is not a measurement (binding)

**Never accept a clip because its title, tags or description contain "drone",
"cinematic", "aerial" or the channel's subject word.** In one channel's pool,
two of the three clips whose titles said "drone" measured as fully locked-off
static shots.

Screen the **downloaded file**:

```python
from lib.camera_motion import analyse_clip, movement_profile
m = analyse_clip(downloaded_path)
m.is_moving_camera                  # False for a locked-off shot of rapids
m.is_relaxation_suitable_movement   # moving, graceful/brisk AND steady
m.camera_motion, m.camera_direction, m.subject_motion, m.loop_suspected
```

Inspect actual frames and usable ranges too — a measured move across four
unusable seconds is still unusable.

**Report the acquired pool's `movement_profile()` in the procurement
checkpoint**, so the operator approves a pool whose movement character is
visible instead of discovering it in the finished film.

### Concept-to-footage match is part of procurement

If the concept promises morning light, autumn, mist or a particular landscape,
**acquire footage that actually supports that promise**, and check season,
weather and light on frames before accepting.

Where the available pool cannot fulfil the approved concept, **acquire more
appropriate footage, or surface the creative shortfall at this gate.** Do not
hand a mismatched pool downstream and leave the edit to force it into a
timeline — that is how autumn foliage, bare winter trees and a saturated purple
sunset ended up inside one "autumn morning".

**Reject or deprioritise clips that are technically usable but visually
repetitive.** Rank near-duplicates; do not admit all of them.

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

## Audio sourcing follows the approved plan

Read `proposal_packet.metadata.paid_audio_plan` first.

- **Music the plan generates is not procured here.** When the approved plan
  generates music (the pipeline's preferred path is `suno_music`), this stage
  sources no music at all; generation happens at `assets`, after the footage
  exists, under the approved budget.
- **SFX the plan generates is not procured here either.** Generated SFX
  (`elevenlabs_sfx`) is derived at `assets` from the real footage. Clip audio
  that arrives with licensed footage is assessed there too.
- Everything below applies only to music or ambience this stage is actually
  asked to acquire — stock music in `free_auto`, or a licensed item the plan
  names.

## Music needs approval, not just retrieval

**The stock tools return the first matching result. That is retrieval, not
creative approval** — and music is the layer where an unsuitable pick is most
damaging, because a single vocal line ruins an hour of footage.

Work through this before any track is accepted:

1. **Establish the episode's musical direction** from the approved proposal —
   instrumentation, mood, and how the music should sit against the channel's
   principal environment layer (its `channel-mix` block), if it has one.
2. **Discover several candidates**, not one. Vary the query and the page; the
   tools always take result[0], so a single call gives you a single opinion.
3. **Inspect actual metadata and playable content** — duration, structure, and
   what the recording genuinely contains.
4. **Reject vocals, lyrics and unsuitable arrangements.** See below.
5. **Check mood, musical transitions, duration and quality** across the whole
   track, not just its opening.
6. **Verify provenance and intended-use terms** — provider, creator, licence,
   and whether that licence actually covers a music-led long-form upload.
7. **Approve a coherent programme** before editing starts, not track by track
   as the edit proceeds.

### Screening music — automated, honest, bounded

Music selection is **automated**. It runs on provider metadata, titles,
descriptions, tags, duration and licensing, plus any lightweight preview the
provider already exposes. No vocal-detection model, no transcription pass and
no per-track operator audition.

Screen in this order and record which step actually decided it:

- **Reject on metadata.** Drop anything naming vocals, lyrics, a singer,
  vocal chops, choir, or a mood the channel forbids — dramatic, cinematic
  rises, aggressive percussion. Rejection on metadata is cheap and reliable.
- **Reject on measured content.** Probe the downloaded file: duration,
  loudness, dynamic range and spectral balance. A track that is too loud, too
  dynamic or too bright for a sleep-adjacent programme is rejected on numbers,
  not vibes.
- **Choose from a pool, never result[0].** The tools return the first match;
  vary the query and compare several candidates before accepting one.
- **Record the evidence honestly.** State exactly what was checked. Metadata
  screening does **not** prove a track contains no vocals, and the record must
  never imply a detector ran when none did. Say what was checked, and stop —
  the limitation is disclosed, not treated as a blocker.

**Metadata-based selection is approved and sufficient for routine
procurement.** There is no `vocals_unverified` hard stop: an uncertain track
is simply an ordinary candidate that later review may replace.

**If a track proves unsuitable during audio review or QC, replace it.** That
is the correction path — a normal, cheap swap late in the process, not a gate
early in it.

The channel's prohibited-content list is in its `BRAND.md` and is binding —
this pipeline does not restate a house musical taste, because another
relaxation channel may legitimately want ocean, rain, a different instrumental
style, or no music at all.

### Licensing

Record any material licensing or Content ID uncertainty against the asset. A
generic stock licence does **not** automatically permit every kind of
music-led relaxation upload, and a free licence guarantees nothing about a
platform's monetisation decisions.

**Never silently substitute unsuitable music to get a video finished.** If the
configured providers genuinely cannot supply enough appropriate candidates,
report that capability gap and the smallest native change that would close it.

## Sound: an evolving bed, not one file on repeat

The soundscape must change across the runtime. A single ambience file looping
for an hour is audible and is the format's other classic failure.

Derive from the approved concept: **each movement wants its own environment
character** (water, streets, wind, habitat, crackle - whatever the channel's
`channel-policy audio` names), plus secondary layers and occasional detail. Where
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

For every clip in `licensed_manual`, fill in wherever it is reliably known: a
short human-readable **purpose**, the exact item **title**, the exact
**item-page URL**, the usable visual **subject** wanted, the **camera
movement** wanted, the **resolution** wanted, the approximate **usable
duration** needed, the absolute **folder** to save into, and any **avoid or
reject** note. Footage goes into the project's canonical footage folder,
`projects/<project_id>/assets/video/` (the layout `init_project()` creates), and
licence receipts into `projects/<project_id>/licenses/` — always written as
absolute paths, never a parallel folder named after how someone describes it.
The employee should be able to work down the list without knowing anything
about this pipeline.

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
