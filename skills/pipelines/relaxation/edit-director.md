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

- **straight cut** — the default, and most of the film. Two shots sharing light
  and rhythm cut cleanly, and a cut is calmer than a dissolve.
- **crossfade** — between related subjects or continuous water/cloud movement,
  and across movement boundaries. Long (1.5–3 s) for relaxation.
- **fade through black** — sparingly, for a real break. Perhaps once or twice
  in two hours.
- one optional fourth if the material genuinely calls for it.

Never alternate transitions on a fixed cycle to manufacture variety. A dissolve
between unrelated shots reads as a mistake. **When in doubt, cut.**

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

Any timeline over 20 minutes needs `metadata.chunk_plan[]` — chunk id, start and
end seconds, cut ids. Target 10–20 minutes. **Chunk boundaries must fall on
straight cuts, never inside a crossfade.**

## Output

Schema-valid `edit_decisions` carrying `render_runtime` **unchanged from the
approved proposal**, `renderer_family: "documentary-montage"` (the schema enum
has no relaxation value; this is the closest and is only a routing label),
`metadata.audio_layers[]`, `metadata.render_groups[]`, `metadata.chunk_plan[]`,
and `metadata.compose_target`.

Run `skills/meta/reviewer.md`, checkpoint, and stop for operator approval.
