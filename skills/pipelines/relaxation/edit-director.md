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

## Audio layers

Declare them in `metadata.audio_layers[]` — role, asset id, volume,
`start_seconds`, `fade_in_seconds`, `fade_out_seconds`. Roles follow the
channel brief: main music (A1), principal water ambience (A2), secondary
environmental ambience (A3), detail SFX such as birds or wind (A4), occasional
texture (A5).

**Not every moment carries every layer.** Thinning to music plus one ambience is
often the better choice. Detail SFX should land occasionally and quietly — a
bird that arrives once, not a loop.

**J-cuts and L-cuts** are audio offsets: bring the next movement's ambience in
2–4 s *before* its first shot, or let the outgoing bed run past the cut. Do this
with `start_seconds` and fades — **never** by trimming video to fake it.

Match the water bed to the visible water, and change it when the water changes
character — crossing over inside a shot, never on a cut.

## Build the mix

Call `audio_mixer` (`operation: "mix"`) with every declared layer, then:

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
