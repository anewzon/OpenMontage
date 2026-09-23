# Relaxation — Edit Director (`edit` stage)

Produces: `edit_decisions`, plus the mixed soundscape and graded clips on disk

Turn the approved `scene_plan` into a concrete timeline, an authored
soundscape, and a coherent grade. Rendering happens next stage.

## Read the scene plan's two halves

`scene_plan.scenes[]` carries the canonical structure. **The source mapping —
asset ids, usable in/out ranges, measured camera and subject motion, scale,
season and light — lives in `scene_plan.metadata.relaxation_slots[]`.** Read it
explicitly; the canonical scene entries do not carry those fields, because the
schema forbids them.

Match each cut to its slot by `scene_id`. If a slot has no matching scene, or a
scene has no slot, the plan is inconsistent — send it back rather than guessing.

## Carry the channel's opening (binding)

Read `proposal_packet.metadata.opening`. When `required: true`, copy the
approved contract into **`edit_decisions.metadata.opening`** and write this
episode's actual copy into it — the welcome message and episode line are
written fresh per episode; the brand signature is constant.

```yaml
metadata:
  opening:
    required: true
    composition: <the channel's composition id>
    runtime: remotion
    duration_seconds: 8.0
    bed_asset_id: <an asset id from THIS episode's own footage>
    text:
      brand_signature: <the channel's constant signature, from BRAND.md>
      welcome_message: <written fresh for this episode>
      episode_line: <written fresh for this episode>
```

Every value above comes from the calling channel's `BRAND.md` and this
episode. None of them belongs in this Director.

Three consequences, all binding:

- **The timeline duration includes the opening.** Body + opening = the approved
  duration.
- **The audio plan covers the opening.** Music and ambience run across the join
  into the body; the opening is not silent and does not get its own mix.
- **Transition timing accounts for it.** Every transition position the Compose
  Director audits is offset by the opening's duration in the delivered file.

An opening the channel requires is **not optional** and a short runtime does not
excuse it. If it cannot be produced, that is a blocker to raise, not a thing to
drop quietly.

## Cuts

One cut per slot: `id`, `source` (asset id), `in_seconds`, `out_seconds`,
`layer: primary`, and a `reason`. The reason must describe a *decision* — "wide
hero, longest hold, establishes the valley" — not restate the shot. The
reviewer checks this.

`cuts[].layer` accepts only `primary | overlay | background`. Plan the primary
chain; treat any overlay as a separate declared segment.

## Transition vocabulary — at most four

- **straight cut** — the default, and most of the film. Use when shots connect
  naturally in composition, movement, lighting and visual meaning. A cut is
  calmer than a dissolve.
- **crossfade** — **selectively**, between compatible views or environments,
  when a gradual change genuinely improves the scene. Start with restrained
  durations chosen from the actual shots.
- **longer transition** — reserved for a deliberate major change of movement.
- **fade through black** — only for a genuine narrative or structural break,
  never as a recurring decorative device. Rare at any length: perhaps once or
  twice across a long film, and often not at all in a short one.

Never alternate transitions on a fixed cycle to manufacture variety. A dissolve
between unrelated shots reads as a mistake. **When in doubt, cut.**

Avoid wipes, flashy digital effects and repeated fixed transition patterns.

Where appropriate, **match incoming and outgoing motion direction, visual
emphasis and colour.** Two shots whose cameras move in opposite directions do
not dissolve well; neither do shots with a large exposure, colour or framing
jump. The manifest's measured `camera_motion` and `camera_direction` are what
you check this against.

### Do not make the dissolve the default (binding)

**Do not automatically apply a long 1.5–3 s dissolve to every boundary.** The
second test put an identical 1.2 s crossfade on 55 of 56 joins. Its own
approved proposal had said "straight cuts within movements, long crossfades at
boundaries" — the edit inverted that, and the film lost the straight cut
entirely.

Check the shape of your own map before handing it on:

```python
from lib.transition_audit import as_transitions, transition_discipline
d = transition_discipline(as_transitions(edit_decisions["transitions"]))
d.dissolve_share, d.longest_identical_run, d.distinct_durations, d.findings
```

Record `d.to_metadata()` in `metadata.transition_discipline`. **Findings must
be empty, or answered with a reason** — never carried forward silently.

### Transition positions must be overlap-corrected

A crossfade overlaps two shots, so **it consumes its own duration from the
timeline.** 56 slots totalling 918.3 s joined by 55 × 1.2 s crossfades run for
**852.3 s**, not 918.3 s.

The second test recorded the un-corrected figure and wrote a 926.3 s length
into `metadata.mix` and `metadata.mix_balance` for a picture that ran
864.03 s — a 62-second disagreement between the recorded metadata and the
executed mix.

Use the arithmetic, and put the corrected number everywhere:

```python
from lib.transition_audit import timeline_duration
body = timeline_duration([c["out_seconds"] - c["in_seconds"] for c in cuts],
                         transitions)
```

Transition `at_seconds` values must be positions on the **overlap-corrected**
timeline, so the Compose Director can find them in the rendered file. Positions
from a raw cumulative sum of holds cannot be audited against the render.

### How transitions are actually produced

`video_compose` on the `ffmpeg` runtime is **concat-only** — it cannot
dissolve. Dissolves come from **`video_stitch`** (`operation: stitch`,
`transition: crossfade | fade`), one transition type per call.

So build the body as **runs**: contiguous groups of cuts joined by the same
transition. Straight-cut runs go through `video_compose`; a crossfade run goes
through `video_stitch`. Record the grouping in `metadata.render_groups[]`.

> **`video_stitch` crossfade outputs `yuv444p`.** Verified on this
> installation: `_normalize_clip` pins `yuv420p` but the `xfade`/`acrossfade`
> paths do not, so the filter negotiates 4:4:4 and the result fails delivery
> spec. **ffprobe every stitch output.** For long-form this matters — fixing it
> afterwards means re-encoding the whole film. Avoid that: stitch only the
> short boundary regions, render the movements with `video_compose` (already
> correct), then join with a stream-copy concat. One encode for the bulk.

## Audio: the process, in order

Read `skills/meta/audio-mastering.md` before mixing — it owns the generic
engineering (gain staging, corrective processing, the four measurements,
two-pass loudness, mono fold-down). This section owns what is specific to
long-form relaxation.

```
source analysis -> music + nature selection -> cleanup where required
-> per-layer gain staging -> arrangement -> transition management
-> final mix -> loudness & true-peak control -> encoded-audio QC
```

The channel's `BRAND.md` describes the listening experience — commonly a music
programme and an environmental soundscape. Within the soundscape, distinguish
the channel's **principal environmental bed**, complementary recordings of it,
supporting ambience, and occasional scene-specific detail — named the way that
channel names them, never from a list in this file.

**Do not run every layer continuously.** Detail SFX are occasional by
definition; a detail sound playing for an hour stops being a detail and becomes
a texture the listener resents. Environmental sound must stay related to what
is visible.

**Do not duck music under the environmental bed.** Ambience is not narration.
Ducking makes the music lurch every time the bed swells — set a balance that
works statically and leave it.

## Audio layers

Declare them in `metadata.audio_layers[]` — role, asset id, volume,
`start_seconds`, `fade_in_seconds`, `fade_out_seconds`. Roles and their names
come from the channel's `BRAND.md` — typically a reference layer, a principal
environmental bed, secondary ambience, occasional detail SFX and occasional
texture. These are conceptual groups, not a requirement to use a fixed number
of files or mixer inputs.

### Balance: from a stated relationship, via measured BUILT stems

The first test sounded irritating for a structural reason, not an artistic one:
layers were mixed near their provider defaults, and final loudness
normalisation was left to sort it out. **Normalisation moves the whole mix; it
cannot fix the relationship between layers inside it.** A soundtrack whose
birds sit on top of the music is still wrong at -16 LUFS.

The second test fixed the method and still got the hierarchy wrong: it put the
**water at parity with the music** (both -20 LUFS). Read the calling channel's
`BRAND.md` for the intended relationship — which layer is the reference, and how
far below it everything else sits — and do not assume any two layers are peers.

Set the balance in this order:

1. **Assemble each stem first**, to the intended timeline coverage —
   concatenation, crossfades, any corrective treatment. The stem as it will
   appear in the mix.

2. **Measure the BUILT stem**, not the source files.

   ```python
   from lib.stem_balance import measure_stem
   m = measure_stem("work/stems/A2-water.wav")   # ebur128 on the assembly (role names are the channel's)
   ```

   **Do not regress to the mean loudness of the sources.** The second test
   proved why: a water stem built from files averaging -16.3 LUFS measured
   -9.9 LUFS once assembled. A gain derived from the source mean would have
   been 6 dB wrong. The built-stem method is the part of Test 2 worth keeping —
   keep it.

3. **Solve the relationship from the channel's own mix block** — the ONE
   fenced `channel-mix` block in the channel's `BRAND.md` — with the reference
   layer as the anchor and the supporting layers solved **as a group**:

   ```python
   from lib.stem_balance import channel_mix_from_file
   mix = channel_mix_from_file(r"<VidQwik root>\Channels\<channel_id>\BRAND.md")
   plan = mix.solve(measured_built_lufs)   # the channel's offsets AND bands
   plan.gains_db                           # hand these to audio_mixer
   ```

   **This is binding.** Never build a `BalanceSpec` by hand for a channel
   production, never type offsets or bands into the edit, and never call
   `solve_balance(..., principal_role=...)` without the channel's band — it
   now raises, because the old built-in 6–8 dB default silently pulled the
   principal layer back up. The roles are the channel's: a reference layer
   (`reference_role`), an optional principal environment layer (`principal:`
   — water for one channel, city ambience or a hearth's crackle for another,
   absent for a channel with none), an optional
   `supporting_group` and an optional `detail_group`. The block states the
   principal treatment too (`treatment_af`): apply it to the built principal
   stem **before** measuring it. A missing, duplicated or
   invalid block is a channel-policy defect: stop and report it; do not
   substitute numbers.

   **A percentage in a brief is a creative relationship, not a gain and not a
   LUFS target.** Never apply `volume=1.0 / 0.4 / 0.2` to raw recordings —
   different sources arrive at different loudness, and the result inverts the
   hierarchy it was meant to set.

4. **Group the supporting layers.** The layers the channel groups as
   supporting share **one** allowance between them. Giving each of them the group's full allowance makes
   their combined output roughly 5 dB louder than intended, because three
   equal sources sum. `solve_balance` distributes a group allowance by power so
   the members **sum** to it.

5. **Do not assume equal integrated LUFS means equal perceived prominence.**
   Broadband water and sparse piano at the same LUFS do not sit at the same
   apparent level. The derived figure is a starting point; refine inside the
   channel's stated band using representative passages.

6. **Inspect short-term behaviour and transients**, not just the integrated
   figure. `measure_stem` reports the loudest and quietest 3-second windows;
   a bird spike or a wind gust shows up there while the integrated number
   looks fine. Wind especially: broadband wind reads as hiss and turns harsh
   as it rises — keep it low, keep it occasional, roll off the top if it is
   bright next to the water.

7. **Verify the executed mix against the plan**, and check the relationship
   rather than only the individual numbers:

   ```python
   v = plan.verify(remeasured_after_gain)   # per-role error + achieved offsets
   mix.check(v)                             # [] only when every band holds (both edges)
   edit_decisions["metadata"]["mix_balance"] = mix.record(plan, v)
   ```

   `mix.record` stores the parsed block, its hash and its source file beside the
   solved plan and the verification. The publish gate re-derives the offsets
   from the channel's CURRENT block (`check_mix_record`) and blocks a mix that
   was solved from anything else or failed a band.

**Avoid heavy compression, and never duck the reference layer under the
principal environment.**

## Channel overlays: resolve, choose the moment, record (binding)

A channel may own persistent overlays - a subscribe animation, a logo, a
corner bug - declared in the `overlays:` list of its `channel-policy` block.
This pipeline knows no overlay by name; it reads the channel's list:

```python
from lib.channel_overlay import resolve_overlays, schedule_overlay
resolved = resolve_overlays(channel.policy.overlays, channel.root,
                            frame=(contract.width, contract.height))
edit_decisions["metadata"]["overlays"] = [
    schedule_overlay(r, runtime_seconds=timeline_seconds, opening_seconds=opening_seconds,
                     slots=metadata_relaxation_slots, frame=(contract.width, contract.height))
    for r in resolved]
```

- `resolve_overlays` INSPECTS each asset (dimensions, frame rate, codec,
  whether its alpha plane really varies, audio streams). A declared asset
  that is missing or unusable raises: **stop and report it as a channel-policy
  defect. Never omit the overlay silently and never substitute another file.**
- `schedule_overlay` chooses the moment from this episode: inside a wide or
  medium shot with a still or gentle subject, clear of the opening and of
  the final fade, with no cut under it - and says why in `rationale`. There
  is no house timestamp. A short piece gets the earliest honest window; a
  piece too short for the overlay is an error, not an omission.
- The record carries the asset's SHA-256, the window, the region and the
  audio decision (`excluded` unless the channel says otherwise), so compose
  is deterministic from it. The edit may move a corner placement to another
  corner; it never resizes, recolours or re-cuts a channel asset.
- A channel that declares no overlay gets `metadata.overlays = []` and
  nothing else changes.

Record the chosen targets, the measured built-stem figures and the applied
gains in `metadata.audio_layers[]` **and** in `metadata.mix_balance`, so a
later episode reproduces the balance instead of rediscovering it. Both must
describe **the mix that was actually executed** — the second test left a
926.3 s timeline in `metadata.mix` for an 864.03 s mix. A balance that is not
written down is not a channel standard; one written down wrongly is worse.

### Verify on a short preview before the full render

Render a **short representative preview** of the mixed soundscape — a couple of
minutes that includes the layers at their intended levels — and check it before
committing to the full encode. Measure it: per-band energy, the relationship
between stems, and whether any detail layer is louder than the foundation.

**State plainly what could not be verified.** Where the balance cannot actually
be judged by listening in this session, say so, attach the preview, and get
operator approval on the preview before the full render. An unverified balance
disclosed is fine; an unverified balance reported as checked is not.

### Native clip audio

The Asset Director classified each clip's own audio as `USE`,
`USE_AFTER_TREATMENT`, `REJECT` or `NO_AUDIO`. Honour it.

For `USE` / `USE_AFTER_TREATMENT` clips: extract the audio for the **same
in/out range the cut uses**, so it stays synchronised with the picture it
belongs to; treat it if needed; give its entry and exit a fade; and set its
level from the measured loudness the Asset Director recorded — **never a
blanket percentage**.

Where native environmental audio is used, **do not also run an equivalent
library or generated bed underneath it.** Two recordings of the same place
fight each other and produce a wide, phasey wash. Duck the other bed out under
the native section, or omit it there.

`REJECT` and `NO_AUDIO` clips contribute nothing to the mix.

**Not every moment carries every layer.** Thinning to music plus one ambience is
often the better choice. Detail SFX should land occasionally and quietly — a
bird that arrives once, not a loop.

**J-cuts and L-cuts** are audio offsets: bring the next movement's ambience in
2–4 s *before* its first shot, or let the outgoing bed run past the cut. Do this
with `start_seconds` and fades — **never** by trimming video to fake it.

Match the environmental bed to what is visible, and change it when the scene
changes character — crossing over inside a shot, never on a cut.

## Duration comes from the timeline (binding)

**Build the mix to the approved timeline's exact length**, including the
opening segment if one is concatenated. Compute it from the cut list; never
pick a round number.

Producing a fixed-length soundtrack and truncating it at mux time throws the
planned ending away — the final fade lands beyond the cut point, so the film
either ends abruptly or survives only because a second fade was applied
downstream by luck. Place every fade at its real timeline position and confirm
the last one falls **inside** the final duration.

The duration tolerance in the channel brief applies to *planning*. Once a
timeline is approved, **the audio and the video must agree exactly.**

## Long-form continuity

Plan coverage honestly before mixing:

- total unique music available
- total unique principal environmental bed available (native, library or generated source)
- other nature recordings available
- intended overlap
- planned reuse
- **actual coverage of the final timeline**

**Overlapping files do not add continuous coverage.** Three two-minute beds
playing simultaneously cover two minutes, not six. Count honestly, or the mix
runs out before the picture does.

Where reuse is necessary: identify the specific repeated sections, space them
deliberately, put music transitions at musical phrase boundaries rather than on
a timer, and avoid obvious environmental reset points where the whole
soundscape audibly restarts. Review every loop and crossfade.

**Never repeat a complete mixed programme to reach a duration**, and do not
hard-code a track count, loop interval or musical order for every upload.

### The music programme: accepted tracks, reprised with intent

Generated music is planned as a **programme**, not minute-for-minute: the
proposal generated a pool of accepted unique music
(`proposal_packet.metadata.paid_audio_plan.music`), and the runtime beyond it
is covered by **reprising** accepted tracks. Build the programme so reprise is
never audible as repetition:

- **No obvious short-track looping.** A few-minute track does not play twenty
  times.
- **No mechanical order.** Accepted tracks do not return in the same sequence;
  a track may come back later in a different position and context.
- **Phrase boundaries, not timers.** Enter and leave a track at a musical
  phrase boundary, with a crossfade shaped to the material.
- **The soundscape keeps evolving underneath.** Environmental beds follow the
  picture independently of the music, so a reprised track never lands on the
  same soundscape it had the first time.
- **Never duplicate the whole mastered programme** — reprise is placement of
  accepted material, not a copy of the first half.

Record the programme in `metadata.music_programme`: each placement's asset id,
source in/out, timeline position, and whether it is a first play or a reprise.
Where the pool genuinely cannot cover the runtime without audible repetition,
say so and propose the smallest additional generation — priced and approved
through the proposal budget — rather than looping.

### Generated SFX beds: loopable sources, built into stems

Generated SFX arrive as short sources (`loop: true` for beds, one-shots for
detail). Build each full-length stem from them here. **Generated loop edges
are quieter than their body** — measured on this installation, the first and
last ~0.1 s sat 3–4 dB low — and a linear crossfade of uncorrelated ambience
dips ~3 dB in the middle. So a bed is never built by butting passes together:

```python
from lib.ambience_loop import build_loop_bed, seam_report
bed = build_loop_bed(source, "work/stems/<role>_<n>.wav", seconds)   # trims edges,
report = seam_report(bed["path"], bed["seam_seconds"])               # equal-power fold
```

`build_loop_bed` trims the quiet edges, folds the source's tail into its head
with an **equal-power** crossfade and repeats the resulting unit;
`seam_report` measures every join and crossfade centre against the bed
around it. **A bed whose `report["passed"]` is False is not approved** — lengthen
the trim or the crossfade, or use another source; never ship it. Record each
bed's `unit_seconds`, trim, crossfade and seam report in
`edit_decisions.metadata.loop_seams`.

- alternate between compatible sources of the same bed where the pool allows,
  and vary the in-point so a seam never falls at one fixed interval for the
  whole film;
- change to a new matching source only where the picture changes;
- place one-shots occasionally and irregularly, never on a grid.

Then measure the **built** stem like any other (`measure_stem`).

## Build the mix

Call `audio_mixer` (`operation: "mix"`) with every declared layer, built to the
timeline length established above, then:

> **`audio_mixer` emits 192 kHz.** `loudnorm` leaves its internal rate on the
> output. Normalise before composing:
> `ffmpeg -i work/mixed.wav -ar 48000 -ac 2 -c:a pcm_s16le work/mixed48.wav`
> and ffprobe it to confirm `48000` / `2`.

> **Single-pass `loudnorm` misses its target.** Measured here: asking for −14
> produced −12.0 LUFS. Always finish with an explicit **two-pass** loudnorm —
> measure with `print_format=json`, re-apply with `measured_I`, `measured_TP`,
> `measured_LRA`, `measured_thresh`, `offset`, `linear=true`. That landed
> −14.1 LUFS / −3.4 dBTP. Do it on the audio file and remux with `-c:v copy`;
> it never costs a video re-encode.

### Leave headroom below the ceiling

Normalising the WAV to the ceiling is not enough: the assembly pass (grade,
crossfades) and the AAC encode both move peaks. Measured on this
installation — a mix normalised to −1.5 dBTP came back at **−1.1 dBFS** in the
delivered file, over the ceiling.

Target the mix a little under, and put a limiter in the final assembly
(`alimiter`) so the encoded result lands inside the channel's ceiling. Then
**verify the encoded file**, not the WAV — see the Compose Director.

## Grade

Use `color_grade` to bring clips from different cameras into agreement. This is
**normalisation, not styling**: exposure balance, white balance, moderate
contrast, restrained saturation. Prefer gentle `custom_vf` values tuned per clip
to match neighbours, e.g. `eq=contrast=1.04:saturation=1.06:brightness=0.01`.

**Do not apply a heavy LUT across good stock footage.** Grade only the clips
that need it; if a graded clip looks more processed than its neighbours, you
have gone too far. Write graded paths back into `asset_manifest`.

## Chunk plan

**Chunking is a rendering decision, scaled to the runtime — never a creative
one.**

| Timeline | Policy |
|---|---|
| ≤ 20 minutes | Chunking **optional**. Use it only when render complexity requires it, and say why. Do not chunk a 60-second piece. |
| > 20 minutes | `metadata.chunk_plan[]` **required**, so a late failure resumes instead of restarting. |
| multi-hour | Chunks of roughly 10–20 minutes are practical. That interval is a convenience, not a rule — the boundary positions come from the transition map. |

The threshold comes from the manifest, not from memory:

```python
from lib.relaxation_policy import chunking_required
chunking_required(timeline_seconds)   # True above metadata.chunking.threshold_seconds
```

**A 5-hour production must be resumable.** A failure at hour four must not
restart from zero: every completed chunk stays on disk and the run resumes from
the first missing one.

`metadata.chunk_plan[]` is **a list**, one entry per chunk, each with chunk id,
start and end seconds, and cut ids.

The second test wrote `{"enabled": true, "chunk_seconds": 240, "reason": ...}`
instead: a policy, not a plan. Nothing downstream could check it, so the
renderer invented its own boundaries at the movement joins — where the edit had
approved crossfades — and replaced all three with hard cuts.

**Chunk boundaries must fall on straight cuts, never inside or beside a
crossfade.** Validate it here, not after the render:

```python
from lib.transition_audit import validate_chunk_plan, safe_chunk_boundaries

violations = validate_chunk_plan(transitions, boundaries)
if violations:
    boundaries = safe_chunk_boundaries(
        transitions, target_chunk_seconds=900, total_seconds=body)
```

If no straight cut sits near a target position, **move the boundary or render
that boundary region as its own short stitched segment.** The editorial intent
is the fixed quantity; the chunk plan is what bends. Record any adjustment and
its reason in `metadata.chunk_plan`.

**A non-empty `violations` list is not permission to change the transition.**

## Output

Schema-valid `edit_decisions` carrying `render_runtime` **unchanged from the
approved proposal**, `renderer_family: "documentary-montage"` (the schema enum
has no relaxation value; this is the closest and is only a routing label),
`metadata.audio_layers[]`, `metadata.render_groups[]`, `metadata.chunk_plan[]`,
and `metadata.compose_target`.

Run `skills/meta/reviewer.md`, checkpoint, and stop for operator approval.
