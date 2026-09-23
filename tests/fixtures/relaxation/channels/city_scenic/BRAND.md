---
channel_id: channel_0004
channel_name: Slow City
status: Active
format: Urban scenic relaxation without narration
pipeline: relaxation
language: en
---

# channel_0004 — Slow City (test fixture)

A fixture channel for the cross-channel tests: cities as scenery: architecture, streets, traffic light trails, bridges, rooftops, night skylines and people passing through.
It exercises the same `relaxation` pipeline as every other channel and
differs only through the blocks below.

## Channel policy (read by the pipeline)

```channel-policy
subjects:
  primary: [architecture, streets, skylines, bridges, rooftops, night city, traffic]
  allowed: [people in passing, trams, harbours, parks, rain on streets, roads]
  optional: [water, trees]
  avoid: [people as subject, commercial brands, timelapses, protests, accidents]
composition:
  camera_movement: preferred
  static_composition: allowed
audio:
  principal_environment: optional
  environment_examples: [city ambience, traffic hum, rain, distant voices]
  supporting_ambience: optional
  detail_layers: optional
narration: none
opening:
  required: true
  composition: ScenicOpening
  bed: "a slow skyline or street shot from this episode"
  text_roles: [brand_signature, welcome_message]
```

## Channel mix settings (read by the pipeline)

```channel-mix
reference_role: A2-city
master_target_lufs: -16.0
supporting_group:
  name: music_bed
  offset_db: -12.0
  band_db: [-15.0, -9.0]
  members:
    A1-music: 1.0
approved:
  project: fixture_city_scenic
```
