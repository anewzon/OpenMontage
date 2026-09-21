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

- **`ffmpeg`** — trims, normalises, concatenates. The recommended path for
  long-form here: a 120-minute 4K timeline is ~216,000 frames and this is the
  only runtime that renders it in a sane time. It cannot composite, so
  dissolves come from `video_stitch` and layered audio from `audio_mixer`.
- **`remotion`** — frame-accurate React rendering, true layering, titles.
  Right for a short overlay or end card; **not viable for a full-length body**.
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
`production_plan` (including `render_runtime`), `cost_estimate` and `approval`.

Then run `skills/meta/reviewer.md` and checkpoint. Stop for operator approval —
`human_approval_default` is true on this stage.
