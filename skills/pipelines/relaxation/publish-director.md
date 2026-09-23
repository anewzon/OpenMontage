# Relaxation — Publish Director (`publish` stage)

Produces: `publish_log` (internal), `output/thumbnail.jpg`, `output/publish.txt`

**Export only. Nothing is uploaded.** This stage packages the finished video so
a human can publish it, and records the canonical `publish_log` internally.

## The publish gate comes first (binding)

**The project is publish-ready only when the executable gate says so.** Before
writing any packaging, run it on the finished master:

```python
from lib.relaxation_publish_gate import assess_publish_readiness, write_publish_gate
readiness = assess_publish_readiness(
    project_dir, proposal_packet=..., asset_manifest=..., edit_decisions=...,
    render_report=..., channel_brand=r"<VidQwik root>\Channels\<channel_id>\BRAND.md")
write_publish_gate(pipeline_dir, project_id, readiness)
```

It blocks - and `write_publish_gate` writes the publish checkpoint as
`failed` with every blocker - when:

- **any asset the final edit uses lacks licence or provenance evidence.**
  Licensed stock needs its receipt in the project's `licenses/` folder,
  recorded with `record_licence_evidence` (SHA-256 checked every time); free
  stock needs its licence and source URL; generated audio needs its accepted
  record in the paid-audio ledgers. Media the edit uses that the manifest does
  not list is unknown provenance. **A missing receipt is a blocker, not a
  note.** Unused downloaded alternates are listed separately and never block.
- **the mix was not solved from the channel's current `channel-mix` block**,
  or its verification failed a band (`check_mix_record`);
- **the master fails the delivery contract** (`delivery_qc`: canvas, fps,
  pixel format, colour, SAR, duration, one audio stream, loudness, true peak,
  black/frozen spans, decode, A/V length, opening/body match).

Only a report with no blockers is written `awaiting_human` - the operator's
publish gate. **Never write the publish checkpoint yourself**, never
describe a blocked project as ready, and never "fix" a blocker by editing the
manifest or the report: report the blockers to the operator and stop. After
the operator approves, record it with `approve_publish(...)`, which re-runs
the gate and refuses a master that changed since it was presented.

## Run only after the video exists

`output/final.mp4` must exist and have passed QC. Packaging describes **the
video that actually got made**, not the one that was planned. A title promising
golden-hour light over a video that never reached golden hour is a defect,
however good the title is.

Re-read before writing anything:

- `proposal_packet` — the concept, and its **provisional** packaging hypothesis
- `research_brief` — title and thumbnail patterns actually observed
- the channel's `BRAND.md`, resolved at runtime
- **the finished file itself** — sample frames across it, note the real
  duration, the footage actually used, the strongest moments

## Deliver one, not a menu

Evaluate alternatives internally — several titles, several candidate frames.
**The operator receives exactly one** of each. They are not a review board.

## Title

Accurate · fits the channel · attractive without misleading clickbait · natural
search language · leads with the primary environment or experience · **varies
across uploads**.

Check the channel's previous upload. If your title is the same formula with the
nouns swapped, write a different one. A recognisable house style is good; a
mechanical template is not. **Do not copy competitor titles**, reworded or
otherwise.

## Description

Ready to paste, natural and useful: a short opening description of the scene,
what the viewer will experience, relaxation/sleep/study context where genuinely
relevant, the environment, the channel's identity, a few relevant hashtags.

No keyword stuffing. **No competitor names, no research notes, no pipeline
detail** — the description is for viewers.

## Tags and hashtags

A concise comma-separated set, roughly six to twelve. Not hundreds.
Style: `relaxing river sounds, peaceful nature, forest river, waterfall sounds,
nature relaxation, calming river`. Then about three relevant hashtags.

## Thumbnail → `output/thumbnail.jpg`

**Prefer a real frame from the finished video.** It is honest by construction,
costs nothing, and matches what the viewer gets — and it needs no paid provider
to ship today.

Use the native **`frame_sampler`** tool to extract candidate frames across the
finished video — not an ad-hoc extraction — then look at them and pick the
strongest composition. Enhance *restrainedly* with the existing tools —
exposure and contrast, the same philosophy as the grade, not a different look.
Crop if it strengthens the composition. Export JPEG at the video's delivery
resolution, high quality, and confirm it reads back.

Scale the sampling to the runtime: a handful of candidates across a 60-second
piece, a wider spread across a multi-hour film. Sampling every minute of a
5-hour video to choose one thumbnail is wasted work.

Prefer: strong water subject · clean composition · cinematic natural appearance
· minimal clutter · no misleading imagery · little or no text unless research
supports it.

If a configured image-generation provider is already available it may be
offered under the normal provider/decision rules — announce it, get approval,
and it **must accurately represent the actual video**. A generated scene that
never appears is misleading packaging. Never require a paid provider just to
ship. A paid call is only possible inside the approved budget, through
`approved_budget_tracker(...).run_tool(...)`, and a provider absent from the
approved estimate needs a re-approved budget first. **Do not clone competitor
thumbnails.**

## `output/publish.txt`

Fill the documented structure exactly — the operator copies straight into
YouTube Studio. Plain text, no JSON.

```
==================================================
VIDEO TITLE
==================================================

[final title]


==================================================
DESCRIPTION
==================================================

[ready-to-paste description]


==================================================
TAGS
==================================================

[tag 1, tag 2, tag 3...]


==================================================
HASHTAGS
==================================================

[#tag1 #tag2...]


==================================================
YOUTUBE SETTINGS
==================================================

Channel ID:
Channel Name:
Video ID:
Category:
Language:
Visibility:
Made for Kids:
Playlist:


==================================================
THUMBNAIL
==================================================

thumbnail.jpg


==================================================
VIDEO FILE
==================================================

final.mp4


==================================================
OPTIONAL NOTES
==================================================

[only when needed]
```

Replace every placeholder. `Visibility: Private` by default — a human decides
when to go public. `Made for Kids: No`. Leave `Playlist:` blank if undetermined.
Pull channel and video identity from the project marker and the channel's
`BRAND.md`, never from memory. Use `OPTIONAL NOTES` only when something is
genuinely useful; otherwise leave it empty.

**No operator-facing metadata JSON.** The machine-readable `publish_log` stays
internal, in the project's artifacts.

## Final check

`output/` must contain **exactly three operator files**:

```
final.mp4
thumbnail.jpg
publish.txt
```

Verify that. Anything else — work files, chunk leftovers, an alternative cut —
moves to `work/` or goes. The operator opens that folder and sees three files
and no decisions to make.

Run `skills/meta/reviewer.md`, then write the checkpoint **through the gate**
(`write_publish_gate`) - never directly - and report the three paths, the
chosen title, the gate's result and anything they should check before
publishing.
