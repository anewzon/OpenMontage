# Relaxation — Procurement Director (`procurement` stage)

Produces: `work/ASSET_LIST.md`, and an `awaiting_human` checkpoint

This is the **human gate**. The footage is licensed stock that a person must
buy. Your job is to say exactly what to buy, then stop.

## Why this stage exists

The canonical `assets` stage produces an `asset_manifest`, and that artifact
requires real local file paths. Before a human has downloaded anything there are
no paths, so the request cannot live in `asset_manifest` — it has to happen
before it. That is the whole justification for this being a stage of its own,
and it is the only non-canonical stage in this pipeline. It deliberately
produces **no new artifact schema**: the request list travels in the
checkpoint's `metadata`, and the operator reads the Markdown view.

## What you may and may not do

- You **may** browse and search Envato to find candidate items.
- You **may not** automate authenticated or bulk downloads. Licensing and
  downloading are the human's actions, deliberately.

## Derive the list from the approved concept

Read `proposal_packet`. Work out what the edit actually needs: how many distinct
visual setups, which environments, which shot scales and camera moves, roughly
how many minutes of usable footage, what music, what ambience.

Size it honestly. For relaxation content a reuse factor above ~0.5 (usable
minutes ÷ target minutes) allows a varied edit; around 0.25 forces deliberate
spaced re-use; below ~0.15 the target duration should come down instead. Ask
for enough that the `assets` stage is not set up to fail.

Be specific. "Misty river establishing wide, 4K, static or very slow push,
dawn light, 20 s+ usable" is actionable. "Some river footage" is not.

## On links — do not invent URLs

Prefer exact Envato item-page URLs **when you have actually verified them**.

If you cannot verify an item page, **give precise search instructions instead**:
the site, the exact search terms, the filters, and what a good result looks
like. Say which you are doing for each entry.

A fabricated link costs the employee ten minutes and costs you their trust in
the whole list. An honest "search Envato Elements for X, filter 4K, pick a
static wide" is more useful than a plausible URL that 404s.

## `work/ASSET_LIST.md` — the operator view

This is a convenience view, **not** a parallel state system. The checkpoint is
the state. Write it in this shape:

```
# ASSET LIST

Project:      <channel_id> / <video_id>
Channel:      <channel name>
Video Concept: <one line>
Target Duration: <duration>

==================================================
VISUALS
==================================================

Put these in:  <absolute path to the visuals folder>

1. <verified Envato item URL, or precise search instructions>
   Purpose: Misty river establishing shot, dawn
   Download: 4K
   Put in: <folder>

2. ...

==================================================
MUSIC
==================================================

Put these in:  <absolute path>

...

==================================================
SFX / AMBIENCE
==================================================

Put these in:  <absolute path>

...

==================================================
WHEN FINISHED
==================================================

Reply to the AI agent:

Assets added, continue.
```

Use **absolute paths** for every destination so the employee never has to work
out the layout.

Also tell them to save licence receipts — anything without evidence gets flagged
at the `assets` stage and cannot ship.

## Then stop

Write the checkpoint with **`status: "awaiting_human"`**, `human_approval_required: true`,
and the structured request list in `metadata` (per request: media type, purpose,
preferred spec, URL or search instruction, destination folder).

Then **end the turn.** Tell the operator:

```
WAITING FOR ASSETS

Project:    <channel_id> / <video_id>
Asset list: <full path to ASSET_LIST.md>

Download the requested assets into the folders listed there, then reply:

Assets added, continue.
```

**Do not continue into `assets`. Do not render. Do not invent or substitute
media.** An empty assets folder is a reason to wait, not to improvise.

## On resumption

When the operator says assets are added: verify the files are actually there and
sufficient for the concept. If they are clearly short, say precisely what is
still missing and remain at `awaiting_human` — do not limp forward with too
little footage. If sufficient, record the human approval on the checkpoint and
continue into `assets`.
