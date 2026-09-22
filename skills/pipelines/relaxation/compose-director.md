# Relaxation — Compose Director (`compose` stage)

Produces: `render_report`, and `output/final.mp4`

Render the approved timeline, then prove it is good — technically and
editorially — before anyone packages it.

## Route by the locked runtime

Read `edit_decisions.render_runtime` and route on it. **Never let the tool
choose**: `video_compose` otherwise falls back to legacy behaviour and silently
picks Remotion, which `AGENT_GUIDE.md` treats as a governance violation.

- **`ffmpeg`** — the expected value, and the only runtime that renders a
  60–180 minute 4K body in a sane time. Everything below assumes it.
- **`remotion`** — permitted only for a short declared overlay or end card,
  rendered separately and concatenated. Never for the full body.
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
*look at it*. Never launch a two-hour encode on an unverified configuration.

## Encode settings

Default on this machine:

```
-c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p
-r 30 -s 3840x2160
-c:a aac -b:a 320k -ar 48000 -ac 2
-movflags +faststart
```

Benchmarked at ~2.4× realtime at 4K30, so a 120-minute render is roughly 50
minutes. NVENC exists only via
`D:\VidQwik AI\Tools\ffmpeg-7.1.1-full_build\bin\ffmpeg.exe` — the system
FFmpeg 9.0.1 needs NVIDIA driver ≥ 610.00 and this machine has 591.86 — and
measured only ~15% faster with ~70% larger files. Use it for drafts, not the
deliverable. If you switch, tell the operator and log the decision.

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

## QC — technical

Probe the finished file and record in `render_report.qc`:

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
| Hierarchy | **Is the mix music-led?** Water audibly *supports* the music rather than matching or overpowering it; the supporting group is quiet. Verify against the channel's stated relationship with `BalancePlan.verify()`, checking the **group's** achieved offset, not only each layer's |
| Nature | Audible, coherent water foundation; forest ambience soft; birds occasional and restrained; wind very subtle with no intrusive hiss or harsh gusts; no contaminated native audio; no two water beds contradicting each other |
| Mix | Consistent gain; nothing masked; **no abrupt layer changes**; no overload, clipping or distortion; restrained dynamics; clean stereo and mono fold-down; music not ducked under the water |
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
report["qc"]["transitions"] = audit_report(obs, violations, discipline)
```

Check, and record:

- **every chunk join**, plus a representative sample of interior joins — not
  all several hundred boundaries of a two-hour film;
- **no unintended hard cuts** where a dissolve was approved;
- correct rendered transition **type and timing**;
- editorial suitability between the actual adjacent shots;
- consistent output format across chunk joins.

Then **look at the rendered boundary frames** for duplicated frames, black
flashes, abrupt exposure changes, broken overlaps, frozen frames and
unexpected hard cuts. A boundary that measures correctly can still look wrong.

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
Do not re-process the entire long-form film for every minor creative check.
Full-file technical QC still runs when a final video is produced.

## QC — brand

Consistent with the channel's `BRAND.md`, and still distinct from the channel's
previous uploads. A video that satisfies the brand by being identical to the
last one has failed this check.

This is editorial quality, **not** an attempt to influence monetisation systems,
and must never be described that way.

## If QC fails

Attempt safe fixes; re-render only the affected chunks where practical. **Do not
mark the stage complete until QC passes.** Editorial failures go back to `edit`
with specific, actionable findings — which cuts, which transitions, which
movement. "Add more variety" is not actionable.

Run `skills/meta/reviewer.md` and checkpoint. Then hand to `publish`.
**Do not upload anything.**
