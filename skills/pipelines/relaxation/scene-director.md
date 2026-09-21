# Relaxation — Scene Director (`scene_plan` stage)

Produces: `scene_plan`

Cast the approved concept onto the footage that actually arrived. Read
`proposal_packet` for the movements and `asset_manifest` for what exists.

The concept was written before anyone had seen the footage. Realise it honestly
against reality — and say so if reality does not support it, rather than quietly
producing something weaker than what was approved.

## Build slots

For each movement, an ordered list of slots. Every slot names:

- `asset_id` and the **specific usable in/out range** from the inventory
- shot scale (wide / medium / detail) and motion type
- target hold in seconds
- why it sits here, at this point, in this movement

## Vary deliberately

Within a movement, vary shot scale and motion. Avoid wide → wide → wide, and
equally avoid a mechanical wide/medium/detail rotation — a visible cycle is as
template-like as no variation at all.

Holds for relaxation run long (commonly 15–55 s), longer than conventional
editing. Derive each from the **strength and motion of the shot**: a rich slow
wide can hold far longer than a busy detail. A movement's opening and hero
shots hold longest; transitional shots shortest.

## Handle re-use honestly

With finite footage you will re-use assets. Make it deliberate:

- **Never re-use an asset twice inside one movement.**
- Across movements, use a **different sub-range**, or the same range at a
  different scale or in a different grade — and record why it reads as a new
  shot.
- Space re-uses as widely as the structure allows.
- If the reuse factor forces re-use tight enough to be visible, **say so now**
  and recommend either a shorter duration or more footage. Do not build a
  timeline you already know will look repetitive and leave the reviewer to
  find it.

## Sound per movement

State the music and ambience intent per movement, and where beds hand over.
Water ambience must match the visible water — a gentle-creek bed under a
waterfall is a mismatch, not a soundtrack.

## Output

Schema-valid `scene_plan`. Every slot resolves to an `asset_id` present in
`asset_manifest`; holds sum to within 10% of the approved duration.

Run `skills/meta/reviewer.md`, checkpoint, and stop for operator approval.
