# Relaxation — Scene Director (`scene_plan` stage)

Produces: `scene_plan`

Cast the approved concept onto the footage that actually arrived. Read
`proposal_packet` for the movements and `asset_manifest` for what exists.

The concept was written before anyone had seen the footage. Realise it honestly
against reality — and say so if reality does not support it, rather than quietly
producing something weaker than what was approved.

## Plan a sequence, not a collection of good shots

The unit of this stage is the **journey**, not the shot. A run of individually
attractive clips is not a scene plan — the second test produced exactly that,
and it read as a stock playlist.

A relaxation scene plan must demonstrate:

- a **coherent progression** through the selected natural environment;
- **changes of perspective and scale**;
- **deliberate use of aerial, moving-camera and closer immersive footage**;
- **compatible season, weather and lighting** throughout;
- shot durations chosen from **actual visual interest**;
- **no obvious repeating footage and no mechanical shot pattern**.

**Use the strongest footage to establish and advance the journey.** Aerial,
tracking and gliding shots carry the sense of travel; detail shots provide
variety and breathing room between them. Do not spend the best moving footage
on transitional positions.

**Do not force a prewritten movement structure onto footage that cannot support
it.** If the pool's `movement_profile` cannot deliver the concept's promised
movement or its season coherence, **say so now** and recommend either more
footage or a changed concept. Building a timeline you already know is
repetitive and leaving the reviewer to discover it is the failure this
paragraph exists to stop.

## Build slots — the schema-valid shape (binding)

`scene_plan.scenes[]` is a **closed** schema object: `asset_id`, `shot_scale`,
`camera_motion`, `subject_motion`, `season`, `light` and `hold` are **not**
canonical scene fields and adding them fails validation.

So the plan has two halves, and they must agree:

**1. `scenes[]` — canonical OpenMontage scene entries.** Use the real fields:
`id`, `type` (`broll` for footage), `description`, `start_seconds`,
`end_seconds`, `shot_intent`, `transition_in`, `transition_out`,
`required_assets[]`, and `shot_language` where its enums genuinely fit.

**2. `metadata.relaxation_slots[]` — the source mapping**, one entry per scene:

```yaml
metadata:
  relaxation_slots:
    - scene_id: M1_001            # must match a scenes[] id
      asset_id: pexels_4318716    # must exist in asset_manifest
      usable_in_seconds: 0.6
      usable_out_seconds: 18.6
      shot_scale: wide
      camera_motion: tracking     # MEASURED, carried from the manifest
      camera_direction: down
      subject_motion: strong      # recorded separately from camera motion
      season: indeterminate
      weather: overcast
      light: "diffuse overcast"
      target_hold_seconds: 18.0
      editorial_reason: "opens on the pool's only unambiguous moving-camera
        shot; leading with movement establishes the journey"
```

**Never produce two contradictory plans.** `scenes[]` timings and the slots'
holds describe the same timeline; if they disagree, the plan is wrong. The Edit
Director reads `metadata.relaxation_slots[]` explicitly for source ranges and
measured motion, and `scenes[]` for structure.

Carry `camera_motion`, `camera_direction` and `subject_motion` **from the asset
manifest's measured values** — do not re-measure and do not re-guess them here.

Every slot must resolve to a real asset id **and a real usable in/out range**
present in `asset_manifest.metadata.asset_analysis`. A slot pointing at a range
nobody inspected is not a plan.

## Vary deliberately

Within a movement, vary shot scale and motion. Avoid wide → wide → wide, and
equally avoid a mechanical wide/medium/detail rotation — a visible cycle is as
template-like as no variation at all.

**Avoid long runs of similar stationary close-ups, even when the subject
itself is moving.** Subject motion is not shot variety: three locked-off
close-ups of the same kind of thing in a row are one idea stated three times,
however different the details are.

## Screen the plan before anyone renders

Answer these in the checkpoint, **with numbers**. They are cheap now and
expensive after a long encode.

| Check | Fails when |
|---|---|
| Camera-movement variety | only one or two genuinely different camera motions appear across the film |
| Moving-camera presence | the plan is dominated by locked-off shots while the concept promised a journey |
| Longest static run | a long stretch of consecutive locked-off shots, whatever the water is doing |
| Scale variety | one scale dominates, or scales rotate mechanically |
| Season / light coherence | slots with incompatible season, weather or light sit together, or contradict the concept |
| Duration pattern | holds follow a visible repeating rhythm |
| Asset resolution | any slot's asset id or in/out range is absent from the manifest |
| Repetition | an asset or a near-duplicate reappears close enough to notice |

**A plan that fails any of these goes back now, with the specific slots
named.** "Add more variety" is not actionable; "slots M2_004 through M2_009 are
all static detail shots of the same creek" is.

### Holds come from the shot and the runtime, never from a band

Derive every hold from:

- the **total approved runtime** — a 60-second piece cannot spend 40 seconds on
  one shot, a 5-hour film can hold far longer than any conventional edit;
- the shot's **visual strength** and how much it rewards looking;
- its **camera movement** — a slow move can carry a long hold, a busy locked
  detail cannot;
- its **role** — establishing, advancing, hero, breathing room;
- the channel's `BRAND.md` and this episode's concept.

Long-form relaxation holds often land somewhere around 15–55 s, **but that is
an observation about long-form, not a rule and not a floor.** Do not apply it
to a short production: a 60-second piece with 15-second minimum holds is four
shots by arithmetic, not by judgement. A movement's opening and hero shots hold
longest; transitional shots shortest.

**Avoid mechanical duration patterns.** A visible repeating rhythm is as much a
failure as no variation at all.

### Scale the plan to the duration

Shot count, movement count and reuse strategy all follow the approved runtime.
Do not carry a long-form structure onto a short piece, or a short piece's
simplicity onto a multi-hour film.

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
