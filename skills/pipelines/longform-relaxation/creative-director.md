# Longform Relaxation — Creative Director (`idea` and `scene_plan` stages)

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

### Output

Schema-valid `brief` with `metadata.movements[]` (name, intent, target minutes,
visual character, sonic character). Movement minutes must sum to within 5% of
target. Stop for operator approval.

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
