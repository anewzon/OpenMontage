# Relaxation — Edit Director (`edit`, `finish`, `compose` stages)

Produces: `edit_decisions` (edit), updated `asset_manifest` (finish),
`render_report` (compose)

---

## Stage: `edit` → `edit_decisions`

Turn the approved `scene_plan` into a concrete timeline.

### Cuts

One cut per slot: `id`, `source` (asset id), `in_seconds`, `out_seconds`,
`layer: primary`, and a `reason`. The reason is not decoration — the review
director checks it.

`cuts[].layer` only accepts `primary | overlay | background`. There is no V1–V4.
Plan the primary chain and treat any overlay as a separate declared segment.

### Transition vocabulary — at most four

Pick a small vocabulary and use each where it is *motivated*:

- **straight cut** — the default. Use it most. Two shots that share light and
  rhythm cut cleanly, and a cut is calmer than a dissolve.
- **crossfade** — between shots of related subject or continuous water/cloud
  movement, and across movement boundaries. Long (1.5–3 s) for relaxation.
- **fade through black** — sparingly, for a real break in the piece. Perhaps
  once or twice in two hours.
- one optional fourth, if the material genuinely calls for it.

Never alternate transitions on a fixed cycle to manufacture variety. A dissolve
between two unrelated shots reads as a mistake. **When in doubt, cut.**

### How transitions are actually produced

`video_compose` with `render_runtime: ffmpeg` is **concat-only** — it cannot
dissolve. Dissolves come from **`video_stitch`** (`operation: stitch`,
`transition: crossfade | fade`, `transition_duration`), which applies one
transition type per call.

So build the body as a sequence of **runs**: contiguous groups of cuts joined by
the same transition. Straight-cut runs go through `video_compose`; a crossfade
run goes through `video_stitch` with that transition; then join the runs.
Record this grouping in `metadata.render_groups[]` so compose can execute it and
a later session can reconstruct it.

> **`video_stitch` crossfade outputs yuv444p.** Verified on this installation.
> `video_stitch._normalize_clip` pins `yuv420p`, but the `xfade` / `acrossfade`
> paths (`_stitch_crossfade`, `_chain_xfade`) do not, so the filter negotiates
> `yuv444p` and the result fails the delivery spec. **Always ffprobe the output
> of a stitch call.**
>
> For long-form this matters a lot, because fixing it after the fact means
> re-encoding the whole film. Avoid that: apply crossfades to the **boundary
> regions only** — render the movements with `video_compose` (which is already
> correct), stitch only the short overlapping pair around each boundary, then
> join everything with a stream-copy concat. One encode for the bulk, not two.

### Audio layers

Declare layers in `metadata.audio_layers[]`. Each layer: role, asset id, volume,
`start_seconds`, `fade_in_seconds`, `fade_out_seconds`. Roles map to the channel
brief: main music, principal ambience, secondary ambience, detail SFX, optional
texture.

**Not every moment carries every layer.** Thinning to music plus one ambience is
a legitimate and often better choice. Detail SFX should be occasional and quiet —
a bird or a water accent that lands once, not a loop.

**J-cuts and L-cuts** are expressed as audio offsets: bring the next movement's
ambience in 2–4 seconds *before* its first shot (J-cut), or let the outgoing bed
run a few seconds *past* the cut (L-cut). Do this with `start_seconds` and fades
on the layers — **never** by trimming video to fake it.

### Chunk plan

Any timeline over 20 minutes needs `metadata.chunk_plan[]`: chunk id, start and
end seconds, and the cut ids in it. Target 10–20 minute chunks. **Chunk
boundaries must fall on straight cuts, never inside a crossfade** — splitting a
dissolve across a chunk boundary breaks it.

### Output

Schema-valid `edit_decisions` with `render_runtime: "ffmpeg"`,
`renderer_family: "documentary-montage"` (the schema enum has no relaxation
value; this is the closest and is only a routing label), `metadata.audio_layers[]`,
`metadata.render_groups[]`, `metadata.chunk_plan[]`, and
`metadata.compose_target: {width: 3840, height: 2160, fit: "pad"}`.
Stop for operator approval.

---

## Stage: `finish` → soundscape + grade

### Build the soundscape

Call `audio_mixer` with `operation: "mix"`, passing every layer from
`metadata.audio_layers[]` with its volume, `start_seconds` and fades. Set
`loudnorm_target: -14` for YouTube.

**Then normalise the result — this is mandatory:**

```
ffmpeg -y -i work/mixed.wav -ar 48000 -ac 2 -c:a pcm_s16le work/mixed48.wav
```

`audio_mixer` emits **192 kHz mono** because `loudnorm` leaves its internal rate
on the output. Verify with ffprobe that the normalised file reports
`48000` Hz and `2` channels before continuing. Skipping this ships a file
YouTube will re-encode badly.

Then verify integrated loudness with `ebur128` and confirm it is within 1 LU of
-14 LUFS with true peak at or below -1.5 dBTP.

> **`audio_mixer`'s single-pass `loudnorm` will miss the target.** Measured on
> this installation: asking for -14 produced -12.0 LUFS, 2 LU hot. Single-pass
> `loudnorm` is a dynamic estimator, not an exact normaliser.
>
> Always finish with an explicit **two-pass** `loudnorm`: measure with
> `print_format=json`, then re-apply passing `measured_I`, `measured_TP`,
> `measured_LRA`, `measured_thresh`, `offset` and `linear=true`. That landed
> -14.1 LUFS / -3.4 dBTP on the test render. Do this on the audio file and then
> remux with `-c:v copy` — it never costs a video re-encode.

### Grade

Use `color_grade` to bring clips from different cameras into agreement. This is
**normalisation, not styling**: exposure balance, white balance, moderate
contrast, restrained saturation.

Prefer `custom_vf` with gentle values, e.g.
`eq=contrast=1.04:saturation=1.06:brightness=0.01`, tuned per clip to match
neighbours. **Do not apply a heavy LUT across good stock footage.** If a graded
clip looks more processed than its neighbours, you have gone too far.

Grade only the clips that need it. A clip that already matches is left alone.

Write graded paths back into `asset_manifest`.

---

## Stage: `compose` → `render_report`

### Route by the runtime that was locked at `idea`

Read `edit_decisions.render_runtime` and route on it. **Never let the tool
choose** — `video_compose` will otherwise fall back to its legacy behaviour and
silently pick Remotion.

- **`ffmpeg`** — the expected value here, and the only runtime that renders a
  60–180 minute 4K body in a sane time. Everything below assumes it.
- **`remotion`** — permitted only for a short declared overlay or end-card
  segment, rendered separately and concatenated. Never for the full body.
- **`hyperframes`** — not used by this pipeline. It is an HTML/CSS/GSAP motion
  graphics runtime with no end-tag or long-form concat parity here, and it
  offers nothing a silent nature montage needs. If `edit_decisions` somehow
  arrives with `render_runtime: "hyperframes"`, **stop and surface that to the
  operator** rather than substituting a runtime yourself.

If the locked runtime is unavailable or fails, that is a blocker to escalate —
swapping runtimes without the operator's approval is a governance violation.
Re-log any approved change as a new `render_runtime_selection` entry.

### Before the long encode

Confirm `review` passed. Render **one chunk first**, probe it, and look at it.
Do not launch a two-hour encode on an unverified configuration.

### Encode settings

Default, on this machine:

```
-c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p
-r 30 -s 3840x2160
-c:a aac -b:a 320k -ar 48000 -ac 2
-movflags +faststart
```

Benchmarked here at ~2.4× realtime at 4K30, so a 120-minute render is roughly
50 minutes. NVENC is available only through
`D:\VidQwik AI\Tools\ffmpeg-7.1.1-full_build\bin\ffmpeg.exe` (the system
FFmpeg 9.0.1 needs a newer NVIDIA driver) and measured only ~15% faster with
larger files — use it for drafts, not for the deliverable. If you switch, tell
the operator and log the decision.

### Chunked render and assembly

Render each chunk from `metadata.chunk_plan[]` into `work/chunks/`. After each
chunk: ffprobe it, confirm duration and stream parameters, and **leave it on
disk**. If chunk 9 of 11 fails, chunks 1–8 must still be there — that is the
entire point.

All chunks share identical codec, resolution, fps, pixel format and audio
parameters, so assemble with the concat demuxer and **stream copy**:

```
ffmpeg -f concat -safe 0 -i work/chunks/list.txt -c copy output/final.mp4
```

**Never re-encode at assembly.** A second lossy pass over the whole film for no
reason is exactly the quality loss the channel brief forbids.

Mux the normalised 48 kHz stereo soundscape over the assembled video, again
without re-encoding video (`-c:v copy`).

### Post-render QC

Probe the finished file and record in `render_report.qc`:

- resolution 3840×2160, 30 fps, yuv420p, H.264
- audio AAC, 48 000 Hz, 2 channels
- duration within 2 s of the planned timeline
- integrated loudness within 1 LU of -14 LUFS, true peak ≤ -1.5 dBTP
- A/V sync at start, middle and end
- sampled frames from each movement — confirm no black frames, no frozen
  frames, no missing grade

Record every chunk with its size in `render_report.chunks[]`. Write the
deliverable to **`output/final.mp4`** — that exact name; the operator and the
`package` stage both depend on it. Keep chunks and intermediates in `work/`,
never in `output/`.

Report the path and the QC results, then hand over to the `package` stage,
which produces `thumbnail.jpg` and `publish.txt` alongside it. **Do not upload
anything.**
