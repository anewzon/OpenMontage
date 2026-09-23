# Test fixture - a channel BRAND.md reduced to its mix settings

A snapshot of the mix block the operator approved by ear on
channel_0001__video_0003, preview v7a (2026-09-23). It exists so a clean
checkout can prove that a new production solved from this block reproduces
v7a. The live channel file stays the authority; a contract test checks this
snapshot still matches it wherever the live file is present.

```channel-mix
reference_role: A1-music
master_target_lufs: -16.0
water:
  role: A2-water
  offset_db: -29.0
  band_db: [-31.0, -27.0]
  treatment_af: "highshelf=f=4000:g=-6:t=q:w=0.7,lowpass=f=9000:p=2"
supporting_group:
  name: ambience_group
  offset_db: -33.7
  band_db: [-36.0, -32.0]
  members:
    A4-forest: 0.42
    A3-birds: 0.48
    A3-wind: 0.10
approved:
  project: channel_0001__video_0003
  preview: v7a
  date: 2026-09-23
```
