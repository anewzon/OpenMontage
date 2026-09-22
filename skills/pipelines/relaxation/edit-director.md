# Relaxation — Edit Director (`edit` stage)

Produces: `edit_decisions`, plus the mixed soundscape and graded clips on disk

Turn the approved `scene_plan` into a concrete timeline, an authored
soundscape, and a coherent grade. Rendering happens next stage.

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
  never as a recurring decorative device. Perhaps once or twice in two hours.

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

The film is a **two-part listening experience**: a music programme and a nature
soundscape. Within nature, distinguish **principal water**, complementary water
recordings, forest ambience, birds, wind and foliage, and occasional
scene-specific detail.

**Do not run every layer continuously.** Birds and wind are occasional by
definition; a bird bed playing for an hour stops being a bird and becomes a
texture the listener resents. Nature sound must stay related to what is
visible.

**Do not duck music under the water.** Water is not narration. Ducking makes
the music lurch every time the river swells — set a balance that works
statically and leave it.

## Audio layers

Declare them in `metadata.audio_layers[]` — role, asset id, volume,
`start_seconds`, `fade_in_seconds`, `fade_out_seconds`. Roles follow the
channel brief: main music (A1), principal water ambience (A2), secondary
environmental ambience (A3), detail SFX such as birds or wind (A4), occasional
texture (A5). These are conceptual groups, not a requirement to use exactly
five files or five mixer inputs.

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
   m = measure_stem("work/stems/A2-water.wav")   # ebur128 on the assembly
   ```

   **Do not regress to the mean loudness of the sources.** The second test
   proved why: a water stem built from files averaging -16.3 LUFS measured
   -9.9 LUFS once assembled. A gain derived from the source mean would have
   been 6 dB wrong. The built-stem method is the part of Test 2 worth keeping —
   keep it.

3. **Solve the relationship**, with the reference layer as the anchor and the
   supporting layers solved **as a group**:

   ```python
   from lib.stem_balance import BalanceSpec, GroupSpec, solve_balance
   plan = solve_balance(spec, measured_built_lufs, water_role="A2-water")
   plan.gains_db          # hand these to audio_mixer
   plan.to_metadata()     # record in metadata.mix_balance
   ```

   **A percentage in a brief is a creative relationship, not a gain and not a
   LUFS target.** Never apply `volume=1.0 / 0.4 / 0.2` to raw recordings —
   different sources arrive at different loudness, and the result inverts the
   hierarchy it was meant to set.

4. **Group the supporting layers.** Forest, birds and wind share **one**
   allowance between them. Giving each of them the group's full allowance makes
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
   v.achieved_group_offsets_db              # the group's real distance below music
   ```

**Avoid heavy compression, and never duck the music under the water.**

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

Where native water is used, **do not also run an equivalent library water bed
underneath it.** Two recordings of the same river fight each other and produce
a wide, phasey wash. Duck the library bed out under the native section, or omit
it there.

`REJECT` and `NO_AUDIO` clips contribute nothing to the mix.

**Not every moment carries every layer.** Thinning to music plus one ambience is
often the better choice. Detail SFX should land occasionally and quietly — a
bird that arrives once, not a loop.

**J-cuts and L-cuts** are audio offsets: bring the next movement's ambience in
2–4 s *before* its first shot, or let the outgoing bed run past the cut. Do this
with `start_seconds` and fades — **never** by trimming video to fake it.

Match the water bed to the visible water, and change it when the water changes
character — crossing over inside a shot, never on a cut.

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
- total unique principal water available
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

Any timeline over 20 minutes needs `metadata.chunk_plan[]` — **a list**, one
entry per chunk, each with chunk id, start and end seconds, and cut ids. Target
10–20 minutes.

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
