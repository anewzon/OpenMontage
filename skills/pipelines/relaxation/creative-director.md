# Relaxation — Creative Director (`idea` and `scene_plan` stages)

Produces: `brief` (idea stage), then `scene_plan` (scene_plan stage)

Read `asset_manifest` first. You are casting a film from a fixed pool of
footage. The concept must be **discovered in the material**, not imposed on it.

---

## Stage: `idea` → `brief`

### Find the concept in the footage

Read the inventory's usable ranges. Ask what this *particular* pool is about.
Heavy rain and dark canopy is a different film from alpine ridgelines and
high cloud. Let the answer come from the assets.

Write a one-sentence concept naming what is specific to this material. If your
concept sentence would fit any nature pool, it is not a concept yet.

### Invent a movement structure

Organise the runtime into **movements** — visual chapters with no on-screen
titles. Give each a name, an intent, a target length in minutes, and a stated
visual and sonic character.

The example progression in the channel brief (morning → awakening forest →
moving water → wider landscapes → details → golden hour → evening calm) is an
**illustration, not a template**. Do not reproduce it. Derive structure from
what you have. Legitimate alternatives include: descending altitude, a storm
arriving and clearing, following one river from source to sea, a slow tightening
from landscape to macro, or a single continuous dusk.

If you find yourself reaching for the example progression, check whether the
footage actually supports it or whether you are pattern-matching.

Movement count for a 60–180 minute piece is typically 4–8. Fewer drifts; more
fragments. Movement lengths should **not** be equal — unequal lengths are part
of the shape.

### Plan the soundscape arc

Music and ambience must **evolve across movements**, not loop unchanged for two
hours. For each movement, state the intended music character, primary ambience,
and whether secondary ambience or detail SFX are wanted. Plan where beds change
and how they cross over. A music change should land inside a shot, not on a cut.

### Confirm the defaults

No narration. No subtitles. No visible chapter titles. Only override if
`brief.txt` explicitly says so.

### Settle the render runtime here, with the operator

The `idea` stage is where `render_runtime` is decided and logged. Do not leave
it to the compose stage and do not let the tool default — a silent default to
Remotion is a governance violation under `AGENT_GUIDE.md`.

Present the real options for **this** brief and recommend one:

- **`ffmpeg`** — trims, normalises and concatenates. The recommended choice for
  long-form here: a 120-minute 4K timeline is ~216,000 frames, and this is the
  only runtime that renders it in a sane time. It cannot composite, so
  dissolves come from `video_stitch` and layered audio from `audio_mixer`.
- **`remotion`** — frame-accurate React rendering, true layering and titles.
  Correct for a short overlay or end-card segment; **not viable for a full
  60–180 minute body**.
- **`hyperframes`** — HTML/CSS/GSAP motion graphics. Built for kinetic
  typography and promos; it offers nothing a silent nature montage needs, so
  expect to reject it — but say so rather than ignoring it.

Check what is actually installed via `video_compose.get_info()["render_engines"]`
before presenting, and record the choice in `decision_log` as a
`render_runtime_selection` entry listing **all** runtimes considered, with
`rejected_because` on each one not chosen. A decision log naming only one
runtime when others were available is a critical reviewer finding.

Wait for the operator's approval before advancing.

### Output

Schema-valid `brief` with `metadata.movements[]` (name, intent, target minutes,
visual character, sonic character). Movement minutes must sum to within 5% of
target, and a `render_runtime_selection` entry must exist in the decision log.
Stop for operator approval.

---

## Stage: `scene_plan` → `scene_plan`

Now cast real clips into the approved movements.

### Build slots

For each movement, create an ordered list of slots. Every slot names:

- `asset_id` and the **specific usable in/out range** from the inventory
- shot scale (wide / medium / detail) and motion type
- target hold in seconds
- why it sits here, in this movement, at this point

### Vary deliberately

Within a movement, vary shot scale and motion. Avoid wide → wide → wide, and
avoid a mechanical wide/medium/detail rotation — a visible cycle is as
template-like as no variation at all.

Target holds for relaxation content typically run long (8–25 s), longer than
conventional editing. Vary them genuinely: a movement's opening and hero shots
hold longest, transitional shots shortest.

### Handle re-use honestly

With limited footage you will re-use assets. Make it deliberate:

- **Never re-use an asset twice inside one movement.**
- Across movements, re-use a **different sub-range**, or the same range at a
  different scale, grade or time-of-day treatment — and record why it reads as
  a different shot.
- Space re-uses as far apart as the structure allows.
- If the reuse factor forces re-use so tight it will be visible, **say so now**
  and recommend either a shorter target or more footage. Do not build a timeline
  you already know will look repetitive.

### Sound per movement

State the music and ambience intent per movement, and where beds hand over.

### Output

Schema-valid `scene_plan`. Every slot resolves to an `asset_id` present in
`asset_manifest`. Slot holds sum to within 10% of brief duration. Stop for
operator approval.
