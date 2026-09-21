# Relaxation — Research Director (`research` stage)

Produces: `research_brief`

You are the first stage. You gather raw material; you do **not** pick the
concept — the Proposal Director does that downstream from your findings.

Follows the same pattern as `skills/pipelines/explainer/research-director.md`:
web search and web fetch, grounded in real sources. **Do not build a scraper.**

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Schema | `schemas/artifacts/research_brief.schema.json` | Artifact validation |
| Channel | `<channel_root>/BRAND.md` | What this channel is and is not |
| Channel | `<channel_root>/COMPETITORS.md` | Channel URLs to study, one per line |
| Channel | `<channel_root>/RESEARCH.md` | That channel's research rules |
| Tools | Web search, web fetch, browser | Research execution |

**The channel is resolved at runtime.** Read the three files belonging to *this*
project's channel. This pipeline hard-codes no channel, no competitor and no
brand — it must serve any relaxation channel.

`COMPETITORS.md` is a plain URL list: lines starting with `#` are comments or
headings; anything resembling a YouTube channel URL is a competitor. A human
adds one by pasting a URL and nothing else. Never rewrite that file into a
structured format, and never demand a channel ID.

## Step 0 — is there anything to research?

If the operator locked the topic, skip this stage, say so explicitly, and write
the checkpoint noting the skip. A locked topic is a human decision, not a
suggestion to improve on.

## Step 1 — reference video, if one is offered

If research surfaces a **specific** video as a strong creative reference, do not
imitate it from a title and thumbnail. Follow the native first-class workflow in
`skills/meta/video-reference-analyst.md` where your tools and access permit,
record a `reference_context` in the brief as the explainer pattern does, and
position the angles *against* it — what it missed, what has changed, what we
would do differently. **Never clone the reference.**

## Step 2 — landscape

Study the competitors listed for this channel plus the wider niche:

- popular (all-time best) videos and **recent** videos per competitor
- the YouTube topic landscape and the search language real viewers type
- title patterns · thumbnail patterns · durations
- visible performance signals and recent momentum
- seasonal relevance

## Step 3 — compare honestly

**Views per hour** (views ÷ hours since publication) is the fair comparator
across videos of different ages; raw views flatter old uploads. State which
measure each figure uses.

**Never fabricate.** If a signal is not visible to you — exact view counts,
publish dates, retention — record it as unavailable and reason from what you
can see. An invented number silently corrupts every decision built on it, and
is worse than an honest gap.

## Step 4 — find the gaps, not the copies

Competitors reveal **demand and patterns**. They are not a menu to reorder.

"Long slow forest-river pieces at sleep length are in demand, and nobody in this
set covers dawn mist specifically" is a finding you can act on. "Competitor X's
*Autumn River 8 Hours* did well, so make *Autumn River 8 Hours*" is not research,
it is plagiarism with extra steps.

Surface at least **3 genuinely different angles**, each grounded in a specific
finding and each viable inside this channel's `BRAND.md`.

## Output — `research_brief`

Reuse the canonical schema. Map this niche onto it:

| Field | Use for |
|---|---|
| `topic` | the niche area under study |
| `landscape` | competitor channels and what already exists |
| `trending` | momentum and seasonal signals |
| `data_points` | observed views, VPH, durations — each with a source |
| `audience_insights` | what viewers say they want (comments, forums) |
| `angles_discovered` | ≥3 original candidate concepts |
| `visual_references` | recurring thumbnail and framing patterns |
| `sources` | every URL actually consulted |
| `metadata` | competitor list used, and which signals were unavailable |

Then run `skills/meta/reviewer.md` and checkpoint per
`skills/meta/checkpoint-protocol.md`. Hand the angles to the Proposal Director —
do not pick one yourself.
