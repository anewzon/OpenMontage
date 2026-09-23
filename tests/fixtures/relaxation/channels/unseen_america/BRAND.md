---
channel_id: channel_0002
channel_name: Unseen America
status: Active
format: Scenic long-form relaxation across overlooked American places
pipeline: relaxation
language: en
---

# channel_0002 — Unseen America (test fixture)

A fixture channel for the cross-channel tests: overlooked American places: less-seen cities and streets, scenic routes, historic districts, towns, mountains, deserts, forests, coasts, lakes and landmarks, seen from unusual perspectives.
It exercises the same `relaxation` pipeline as every other channel and
differs only through the blocks below.

## Channel policy (read by the pipeline)

```channel-policy
subjects:
  primary: [american cities, streets, historic districts, towns, scenic routes, roads, landmarks, mountains, deserts, coastlines, forests, lakes]
  allowed: [architecture, bridges, harbours, traffic, people in passing, railways, night city]
  optional: [water, wildlife, canyons, farmland]
  avoid: [people as subject, commercial brands, timelapses, stock lifestyle scenes]
composition:
  camera_movement: preferred
  static_composition: allowed
audio:
  principal_environment: optional
  environment_examples: [city ambience, traffic hum, wind, surf, rain]
  supporting_ambience: optional
  detail_layers: optional
narration: none
opening:
  required: true
  composition: ScenicOpening
  bed: "a wide establishing shot of this episode's place"
  text_roles: [brand_signature, welcome_message, episode_line]
```

## Channel mix settings (read by the pipeline)

```channel-mix
reference_role: A1-music
master_target_lufs: -16.0
principal:
  role: A2-ambience
  offset_db: -24.0
  band_db: [-27.0, -21.0]
supporting_group:
  name: environment_group
  offset_db: -30.0
  band_db: [-33.0, -27.0]
  members:
    A3-distant-traffic: 0.5
    A4-wind: 0.5
approved:
  project: fixture_unseen_america
```
