# Relaxation — Compose Director (`compose` stage)

Produces: `render_report`, and `output/final.mp4`

Render the approved timeline, then prove it is good — technically and
editorially — before anyone packages it.

## Route by the locked runtime

Read `edit_decisions.render_runtime` and route on it. **Never let the tool
choose**: `video_compose` otherwise falls back to legacy behaviour and silently
picks Remotion, which `AGENT_GUIDE.md` treats as a governance violation.

- **`ffmpeg`** — the expected value, and the only runtime that renders a
  multi-hour body in a sane time. Everything below assumes it.
- **`remotion`** — permitted for a **short declared segment** — a channel
  opening or an end card — rendered separately and concatenated. Never for the
  full body. Rendering an approved opening this way while the body stays on
  `ffmpeg` is the documented pattern and **is not** a silent runtime swap; the
  governance rule it must not break is swapping the *body's* runtime.
- **`hyperframes`** — not used by this pipeline: an HTML/CSS/GSAP motion
  graphics runtime with nothing a silent nature montage needs. If
  `edit_decisions` arrives with it set, **stop and surface that** rather than
  substituting a runtime yourself.

If the locked runtime is unavailable or fails, that is a blocker to escalate —
swapping runtimes without approval is forbidden. Re-log any approved change as
a new `render_runtime_selection` entry.

### Known composition limitation

True simultaneous video overlay (V2/V3) is **not available** on the FFmpeg
path: `video_compose._compose` trims, normalises and concatenates, with no
`overlay`, `xfade` or `amix`. Re-check `video_compose.get_info()` each run in
case the toolchain has gained it. Until then, achieve depth through shot
selection, grading and pacing — or a short Remotion segment — and **state the
limitation rather than implying layering that is not there.** Do not build a
custom compositor.

## Before the long encode

Confirm the timeline was approved. Render **one chunk first**, ffprobe it, and
*look at it*. Never launch a long encode on an unverified configuration.

## FFmpeg comes from PATH

`ffmpeg` and `ffprobe` are ordinary OpenMontage command dependencies —
`cmd:ffmpeg`, `cmd:ffprobe`, resolved with `shutil.which` in
`tools/base_tool.py::check_dependencies`. There is **no project-local FFmpeg
and no override variable.** If a binary is missing, that is an environment
problem to fix by installing FFmpeg on the system, never by pinning a copy
inside the project: a pinned copy lets these helpers use a different binary
from the one the tools use, and lets tests pass against a runtime production
does not have.

The additive helpers follow the same contract via `lib/ffmpeg_runtime.py`.

## Encode settings

The delivery canvas and frame rate come from the **channel's `BRAND.md`** and
the approved proposal — the figures below show the shape of the command, not a
house resolution.

```
-c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p
-r <approved fps> -s <approved canvas>
-c:a aac -b:a 320k -ar 48000 -ac 2
-movflags +faststart
```

**CPU `libx264` is the production path.** Do not make a production depend on
GPU encoding. Where NVENC is available it is a draft-speed convenience only;
where it is unavailable, that is an optional acceleration limitation and not a
blocker. If you switch encoders, tell the operator and log the decision.

Measure encode throughput on the machine you are actually rendering on rather
than quoting a remembered figure — it changes with canvas, FFmpeg build and
hardware, and a stale benchmark in a plan is worse than no benchmark.

## Chunked render and assembly

Render each chunk from `metadata.chunk_plan[]` into `work/chunks/`. After each:
ffprobe it, confirm duration and stream parameters, and **leave it on disk**. If
chunk 9 of 11 fails, chunks 1–8 must still be there — that is the entire point.

Do not send a 120- or 180-minute film through a single enormous `xfade` graph.
Use this chunk/resume path.

All chunks share codec, resolution, fps, pixel format and audio parameters, so
assemble with the concat demuxer and **stream copy**:

```
ffmpeg -f concat -safe 0 -i work/chunks/list.txt -c copy output/final.mp4
```

**Never re-encode at assembly.** Mux the normalised 48 kHz stereo soundscape
with `-c:v copy`.

Write the deliverable to **`output/final.mp4`** — that exact name; the `publish`
stage and the operator both depend on it. Chunks and intermediates stay in
`work/`, never in `output/`.

### The chunk plan must not edit the film (binding)

The concat demuxer **cannot dissolve**. A chunk boundary placed on an approved
crossfade therefore becomes a hard cut, and the film that ships is not the edit
that was approved.

That is exactly what the second test did. Its render plan recorded the
substitution as a statement of fact — *"movement boundaries are straight cuts;
all other transitions are 1.2s crossfades inside the chunks"* — and nothing
failed. Measured on the delivered file, the three movement joins show
single-frame differences of 20.2, 23.6 and 26.2 against local baselines of
1.0–2.2: three hard cuts at 10.7× to 26.3× the surrounding change, where the
edit had approved dissolves. The dropped overlaps also pushed the file 3.7 s
past its prediction, which is why an 852.3 s timeline arrived as 864.03 s.

Before rendering:

```python
from lib.transition_audit import validate_chunk_plan, safe_chunk_boundaries
violations = validate_chunk_plan(transitions, boundaries)
```

If there are violations, **do not proceed and do not substitute a cut.** Either
adjust the chunk plan — `safe_chunk_boundaries` picks boundaries that fall only
on straight cuts — or render that boundary region as its own short
`video_stitch` segment and concatenate it. Both preserve the editorial intent;
dropping the dissolve does not.

**Place chunk boundaries at technically safe positions without silently
dropping or replacing approved transitions.** Account for crossfade overlap
when computing chunk durations and cut offsets:
`lib.transition_audit.timeline_duration` is the arithmetic. A chunk whose
length is the raw sum of its holds will not line up.

## Render the channel opening (binding)

Read `edit_decisions.metadata.opening`. When `required: true`:

1. **Render it** — the named composition on its declared runtime, as its own
   short segment into `work/`.
2. **Inspect the rendered frames.** Sample them with `frame_sampler` and look:
   confirm all three text roles are present and ranked as the channel's
   `BRAND.md` asks, and that the bed is the footage `BRAND.md` asks for, taken
   from this episode's own pool.
3. **Match the canvas — a mismatch is a blocker.** ffprobe the rendered opening
   and the body: width, height, fps and pixel format must be identical, and
   equal to the approved delivery canvas. An opening composition that could
   not read its bed may fall back to a default canvas (for example 1080p under
   a 4K body). **Never scale, pad or concatenate mismatched segments to get
   past it** — stop and report the blocker with both probes.
4. **Concatenate it** ahead of the body, with the same codec, resolution, fps
   and pixel format so the join is a stream copy.
5. **Keep the approved audio continuous** across the join. The opening does
   not get its own mix and is not silent.
6. **Offset every audited transition position** by the opening's duration when
   checking boundaries in the delivered file.

**An opening the channel requires is never silently omitted.** If it cannot be
rendered, that is a blocker to raise — the stage fails rather than shipping a
film missing its channel identity.

When `required: false`, record that the channel asked for none. An absent
`metadata.opening` is not the same as "not required": treat a missing contract
as an upstream defect and send it back.

## Where QC goes (binding)

The canonical `render_report` schema sets `additionalProperties: false` and
declares **no top-level `qc` field**. Writing `render_report.qc` fails
validation.

All relaxation QC goes under the open `metadata` object:

```
render_report.metadata.qc.technical     resolution, fps, pix_fmt, codecs,
                                        streams, duration, decode, sync
render_report.metadata.qc.audio         loudness, true peak, balance, fade
render_report.metadata.qc.transitions   the rendered-boundary audit
render_report.metadata.qc.editorial     repetition, motion variety, coherence
render_report.metadata.qc.brand         BRAND.md consistency, opening present
```

Keep the canonical top-level fields — `version`, `outputs[]`,
`verification_notes[]`, `warnings[]` — schema-valid and populated.

## QC — technical

Probe the finished file and record in `render_report.metadata.qc.technical`:

- resolution and 30 fps, `yuv420p`, H.264
- audio AAC, 48 000 Hz, 2 channels
- duration within 2 s of plan
- integrated loudness within 1 LU of target, true peak ≤ −1.5 dBTP
- **clean full-file decode** (`ffmpeg -v error -i final.mp4 -f null -`) — any
  output means corrupt frames
- A/V sync at start, middle and end
- sampled frames per movement — no black, frozen or ungraded frames

## QC — audio

Audio gets its own pass. A film can be technically perfect and unlistenable.

**Keep the approved mix authoritative.** When muxing, map video from the
picture and audio **only** from the approved mix (`-map 0:v -map 1:a`). Never
map `0:a` from an assembled body: intermediate concatenations carry the source
clips' own audio, and mapping it would put rejected native sound underneath the
master. Confirm the delivered file has **exactly one audio stream**.

**Measurements — name them correctly.** Report integrated loudness (LUFS),
**true peak** from `ebur128=peak=true`, short-term loudness and LRA.
**Sample peak is not true peak** — do not substitute one for the other. Measure
the **encoded** file; a WAV that met the ceiling can exceed it after AAC.

**Content checks:**

| Area | Check |
|---|---|
| Music | No vocals or lyrics where the channel forbids them; mood and intensity appropriate; progression smooth |
| Hierarchy | **Does the mix follow the channel's stated hierarchy?** For a music-led channel, the principal environmental bed audibly *supports* the music rather than matching or overpowering it; the supporting group is quiet. Verify against the channel's stated relationship with `BalancePlan.verify()`, checking the **group's** achieved offset, not only each layer's |
| Environment | A coherent principal bed that matches the picture; supporting ambience at the level `BRAND.md` asks for; detail SFX occasional and restrained, with no intrusive hiss or harsh transients; no contaminated native audio; no two equivalent beds (native, library or generated) contradicting each other |
| Mix | Consistent gain; nothing masked; **no abrupt layer changes**; no overload, clipping or distortion; restrained dynamics; clean stereo and mono fold-down; music not ducked under the environmental bed |
| Continuity | Every music boundary, environmental crossover and reused-audio junction; the opening and the ending; **music and environmental audio continuous across picture transitions**; no gaps, no truncated fade |
| Technical | Correct duration, sample rate, channels, integrated loudness, measured true peak, encoded audio, A/V sync at start, middle and end |
| Consistency | `metadata.audio_layers[]` and `metadata.mix_balance` describe **the mix that was executed** — same duration, same gains. The second test recorded a 926.3 s timeline for an 864.03 s mix |

**Duration:** the mixed audio must match the approved timeline. A mix longer
than the picture means the ending was planned somewhere the viewer never
reaches; a mix shorter means a silent tail. Both are failures, not roundings.

**Verify the final fade actually survived** into the delivered file by
measuring levels across the last seconds — not by trusting that a fade filter
was in the command.

> **Honesty rule.** A passing `ffprobe`, a successful FFmpeg exit code and a
> plausible LUFS figure are **not** a pass. Where a check needs listening and
> this session cannot listen, say so and request a listening review. **Never
> record a pass for a check that did not run.**

## QC — transitions

Does the picture match the **approved transition map**? This is a measurement,
not an impression.

```python
from lib.transition_audit import (
    as_transitions, audit_rendered_boundaries, unexpected_cuts, audit_report)

obs = audit_rendered_boundaries(
    "output/final.mp4", transitions,
    offset_seconds=opening_seconds,          # concatenated opening
    only_at=chunk_boundaries + sampled_interior_joins)

assert not unexpected_cuts(obs)              # the Test 2 defect
report["metadata"]["qc"]["transitions"] = audit_report(obs, violations, discipline)
```

Check, and record:

- **every chunk join**, plus a representative sample of interior joins — not
  all several hundred boundaries of a two-hour film;
- **no unintended hard cuts** where a dissolve was approved;
- correct rendered transition **type and timing**;
- editorial suitability between the actual adjacent shots;
- consistent output format across chunk joins.

Then **look at the rendered boundary frames** — extract them with
`frame_sampler` and inspect for duplicated frames, black flashes, abrupt
exposure changes, broken overlaps, frozen frames and unexpected hard cuts.
`visual_qa` is available for structured frame assessment where it helps. A
boundary that measures correctly can still look wrong.

**Inspect every chunk boundary and every approved critical transition.** Sample
the rest.

## QC — editorial

The technical pass says the file is valid. This one says it is worth watching.
Sample across the finished video and answer honestly:

- obvious mechanical repetition in shot length or transition rhythm?
- **genuine camera-movement variety, or is the film dominated by locked-off
  shots?** Re-measure a sample with `lib.camera_motion` rather than trusting
  the manifest.
- **is there aerial or moving-camera footage where the concept promised it?**
- **ASMR-loop dominance** — long runs of stationary water close-ups?
- **coherent season, light and environment** across the film, and consistent
  with the concept?
- visual progression and shot diversity across movements?
- poor shot choices, or shots that should have been rejected at `assets`?
  (The second test passed a road/causeway shot and a shot whose primary
  subject is a person — both avoid-by-default in `BRAND.md`.)
- jarring transitions, or dissolves between unrelated subjects?
- sequences that read as repeated?
- ambience mismatched to the visible environment?
- **does this feel like a stock-footage playlist rather than a directed film?**

**Use lightweight analysis and representative excerpts for early screening.**
Do not re-process the entire film for every minor creative check, and do not
add per-frame AI analysis simply because a production is long.

The split that keeps a multi-hour production practical:

| Check | Scope |
|---|---|
| Decode integrity, probe, loudness, true peak, duration, sync | **Full file**, once, on the final encode |
| Chunk boundaries and approved critical transitions | **Every one** |
| Editorial: motion variety, repetition, coherence, ASMR dominance | **Representative excerpts** |
| Frame inspection | Boundaries plus a sample per movement |

Reuse what earlier stages already measured — the asset manifest's motion and
season data is authoritative and does not need re-deriving here. Where a
finding needs the source, sample it; do not re-analyse the whole pool.

For audio-only corrections, **remux** with `-c:v copy`; never re-encode the
video to fix a mix.

## QC — brand

Consistent with the channel's `BRAND.md`, and still distinct from the channel's
previous uploads. A video that satisfies the brand by being identical to the
last one has failed this check.

**If `BRAND.md` requires an opening, verify it is actually in the delivered
file** — the right duration, the three text roles present and correctly ranked,
moving-water bed, audio continuous into the body. **A missing required opening
is a stage failure**, recorded in `metadata.qc.brand`, not a warning.

This is editorial quality, **not** an attempt to influence monetisation systems,
and must never be described that way.

## If QC fails

Attempt safe fixes; re-render only the affected chunks where practical. **Do not
mark the stage complete until QC passes.** Editorial failures go back to `edit`
with specific, actionable findings — which cuts, which transitions, which
movement. "Add more variety" is not actionable.

Run `skills/meta/reviewer.md` and checkpoint. Then hand to `publish`.
**Do not upload anything.**
