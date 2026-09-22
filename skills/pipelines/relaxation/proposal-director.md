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
- **environment types** — which settings, which light, which weather
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

## Cost — estimated here, before anything is spent (binding)

Production cost is **not** automatically zero any more. Footage, mixing and
rendering cost nothing metered, but generated music and generated SFX are paid
API calls. **Every paid call in this production is priced at this stage and
approved by the operator before the first one runs.**

The pipeline's preferred paid providers are `suno_music` (music) and
`elevenlabs_sfx` (SFX), both ordinary registry tools. Whether an episode uses
them at all, and how much, comes from the channel's `BRAND.md` and this
concept — a channel may want no music, or no generated SFX.

### Plan music as a programme, not minute-for-minute

**Do not assume `final duration == unique generated music`.** Read the
channel's music policy in `BRAND.md` and decide, for this production:

- how many seconds of **accepted unique** music to generate;
- the requested length of each generated candidate;
- how many candidates one paid request returns and how many you expect to
  accept (the tool's `supports.multiple_candidates` — every candidate is kept);
- a stated **retry/rejection allowance** — rejected candidates are real money.

The rest of the runtime is **reprised** in the edit (see the Edit Director). A
short production may reasonably generate unique music for its whole length; a
long one should not scale generation linearly. There is no house track count.

### Plan SFX in source seconds, from this episode

Generated SFX is planned from what **this** episode needs — the channel's
`BRAND.md`, the approved concept's movements, and the footage you expect —
never from a fixed list of sound types. Long beds are built later from short
**loopable** sources, so the plan counts generated source seconds, not film
runtime. Where good native clip audio is expected to carry a scene, plan no
generated equivalent for it.

### Compute it with the tools' own prices

```python
from lib.relaxation_policy import plan_paid_audio, budget_summary, PaidCostUnavailable

plan = plan_paid_audio(
    target_duration_seconds=target,
    music={"tool": "suno_music", "tool_inputs": {...},
           "unique_music_seconds": ..., "seconds_per_generation": ...,
           "candidates_per_generation": ..., "accepted_per_generation": ...,
           "retry_allowance": ...},
    sfx={"tool": "elevenlabs_sfx", "tool_inputs": {...},
         "sources": [{"purpose": ..., "duration_seconds": ..., "count": ...}],
         "retry_allowance": ...},
)
proposal_packet["cost_estimate"] = plan["cost_estimate"]
proposal_packet["metadata"]["paid_audio_plan"] = plan["metadata"]
print(budget_summary(plan))
```

Omit `music` or `sfx` when the episode does not generate it. Prices come from
each tool's `estimate_cost()` — never type a price into this plan.

**If a paid provider cannot be priced, `PaidCostUnavailable` is raised. Stop
and report the setting the operator must confirm** (the error names it). Never
make an unpriced paid call to discover what it costs.

Manually licensed footage is recorded as a zero-cost line marked **externally
managed** — the human's subscription or licence is not an API charge and is not
counted as automated spend unless the operator defines a per-project
allocation. Local mixing, rendering and QC carry no metered cost.

### The operator approves the concept AND the maximum budget

Show the `budget_summary()` block with the concept options. The operator
approves the concept and a **maximum paid budget**. Record the figure as
`approval.approved_budget_usd` — the existing schema field; do not create a
separate budget file — and log the choice as a `budget_tradeoff` decision.

Once approved, generation proceeds automatically **inside** that cap. If the
plan later changes materially — a longer programme, more SFX, a different
provider — append a new decision-log entry, recalculate with
`plan_paid_audio`, and get the revised budget approved **before** spending.

### No silent provider substitution

If a preferred paid provider is unavailable (no credential, unpriced, failing),
report it, list the registry's alternatives for that capability
(`registry.get_by_capability("music_generation")`, `("sfx_generation")`, and the
music library/search capabilities) with their cost and quality differences, and
wait for the operator's choice. Record it as a `provider_selection` decision. A
provider that is merely unconfigured is a setup step, not a code fault.

## Output

Schema-valid `proposal_packet` with `concept_options`, `selected_concept`,
`production_plan` (including `render_runtime`), `cost_estimate` and `approval`
(with `approved_budget_usd` whenever any line is paid), plus
`metadata.target_duration_seconds`, `metadata.opening` and
`metadata.paid_audio_plan`.

`production_plan`, `selected_concept`, `cost_estimate` and `approval` are all
**closed** schema objects — adding a field to any of them fails validation.
Relaxation-specific information belongs under the open `metadata` object.

Then run `skills/meta/reviewer.md` and checkpoint. Stop for operator approval —
`human_approval_default` is true on this stage.
