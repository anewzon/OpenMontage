# Relaxation — Proposal Director (`proposal` stage)

Produces: `proposal_packet`

You turn the Research Director's angles into **one approved concept**, and you
lock the decisions the rest of the pipeline depends on: duration, structure,
and render runtime.

Read first: `research_brief`, and this project's channel `BRAND.md` (resolved at
runtime — this pipeline serves any relaxation channel).

## Concept

Offer the operator a small number of real options drawn from
`angles_discovered`, recommend one with reasons, and wait for approval. Do not
present a single option as a fait accompli, and do not present eight.

The selected concept must specify:

- **the original concept** — one sentence naming what is specific to it
- **target experience** — what the viewer feels, and when they would watch
- **approximate duration**
- **visual progression** — the movements, named, with rough minutes each
- **environment types** — which waters, which light, which weather
- **pacing direction** — where it breathes, where it moves
- **soundscape direction** — how music and ambience evolve across movements
- **packaging hypothesis** — a provisional title and thumbnail idea

Mark the packaging hypothesis **provisional**. Final packaging happens at
`publish`, against the video that actually got made.

## Duration is validated here (binding)

This pipeline serves **60 seconds to 5 hours** — `60 <= target_duration_seconds
<= 18000`, declared in `pipeline_defs/relaxation.yaml` under
`metadata.duration`. Read the bounds from the manifest rather than repeating
them from memory:

```python
from lib.relaxation_policy import validate_duration, DurationOutOfRange
validate_duration(target_duration_seconds)   # raises if out of range
```

Record the approved figure as `proposal_packet.metadata.target_duration_seconds`.

**A duration outside the range is surfaced clearly and the run stops for the
operator.** Do not clamp it, and do not proceed with a nearby value.

**Do not silently shorten a 5-hour request or stretch a 60-second one.** The
requested duration is the operator's production requirement. If the footage
reality later cannot support it, that is a *proposed revision* the operator
approves — raised explicitly, never applied quietly.

### Scale the concept to the duration

A 60-second piece and a 5-hour film use the **same pipeline** with a different
editorial scale. There is no duration template. These follow the runtime:

| Scales with duration | Not fixed by this pipeline |
|---|---|
| number of movements | a house movement count |
| number of shots | a house shot count |
| hold lengths | a house hold band |
| amount of music | a fixed track count |
| ambience coverage | a fixed layer count |
| sourcing quantity | a fixed clip count |
| reuse strategy | a fixed reuse factor |
| QC sampling density | a fixed sample count |
| rendering chunks | a fixed chunk count |

A 60-second piece may be one movement of a few shots with a single music cue
and no chunking at all. A 5-hour film needs many movements, a real reuse
strategy and a resumable chunk plan. Both are correct outputs of this pipeline.

## Carry the channel's opening requirement (binding)

Read the channel's `BRAND.md`. **If it requires a branded opening, that opening
is part of this production** — planned here, carried through the edit, rendered
and QC'd at compose. It is not an optional flourish, and a short runtime does
not excuse it.

`production_plan` is a closed schema object, so record the contract under
`proposal_packet.metadata.opening`:

```yaml
opening:
  required: true                  # read from the channel's BRAND.md
  intended_duration_seconds: 8
  composition: <the channel's composition id>
  runtime: remotion               # short segment only; the body stays ffmpeg
  roles:                          # the hierarchy the channel asks for
    brand_signature: "constant channel signature, small and quiet"
    welcome_message: "original per-episode line, the dominant element"
    episode_line: "one line about this episode, lightest"
  bed: "moving water from this episode's own footage"
```

Record `required: false` when the channel asks for no opening — an explicit
false, so a later stage can tell "not required" from "nobody looked".

**The opening's duration counts inside the approved total duration.** A
60-second production with an 8-second opening has 52 seconds of body.

Writing the actual *words* is the Edit Director's job, per episode. This stage
records the requirement and the roles, never fixed copy.

### Originality is the point

The concept must be **invented for this channel**, not a competitor video with
the nouns swapped. Reusing the same movement skeleton episode after episode is
the same failure one level up: a recognisable house style is good, a template is
not. Check the concept against the channel's previous uploads before proposing
it.

Stay inside `BRAND.md`. If research points somewhere off-brand, say so and
recommend against it rather than drifting.

## Lock the render runtime here (binding)

`AGENT_GUIDE.md` requires the runtime to be chosen in conversation at proposal
time, never defaulted silently. Check what is actually installed via
`video_compose.get_info()["render_engines"]`, then present **both** available
runtimes with an honest tradeoff for *this* brief:

- **`ffmpeg`** — trims, normalises, concatenates. The recommended path for the
  body at any length, and the only one that renders a multi-hour timeline in a
  sane time. It cannot composite, so dissolves come from `video_stitch` and
  layered audio from `audio_mixer`.
- **`remotion`** — frame-accurate React rendering, true layering, titles.
  Right for a **short channel opening or end card**, rendered as its own
  segment and concatenated; **not viable for a full-length body**. Using it for
  an approved opening while the body stays on `ffmpeg` is the documented
  pattern, **not** a silent runtime swap.
- **`hyperframes`** — HTML/CSS/GSAP motion graphics. Built for kinetic
  typography and promos; it offers nothing a silent nature montage needs.
  Expect to reject it — but say so rather than ignoring it.

Record the choice in `decision_log` as a `render_runtime_selection` entry
listing **every** runtime considered, with `rejected_because` on each one not
chosen. A decision log naming only one runtime when others were available is a
critical reviewer finding. Wait for explicit approval before advancing.

## Cost

`cost_estimate` is **0.00 USD**. This pipeline uses local licensed assets and
local FFmpeg. No generative video, image, music or TTS provider is called. If
something appears to require a paid provider, stop and raise it rather than
spending.

## Output

Schema-valid `proposal_packet` with `concept_options`, `selected_concept`,
`production_plan` (including `render_runtime`), `cost_estimate` and `approval`,
plus `metadata.target_duration_seconds` and `metadata.opening`.

`production_plan`, `selected_concept`, `cost_estimate` and `approval` are all
**closed** schema objects — adding a field to any of them fails validation.
Relaxation-specific information belongs under the open `metadata` object.

Then run `skills/meta/reviewer.md` and checkpoint. Stop for operator approval —
`human_approval_default` is true on this stage.
