# Test fixture - a second, fictional channel with a deliberately different mix

Water-led rather than music-led: the water sits only 12 dB under the music,
there is no water darkening, and the supporting group is rain and wind.

```channel-mix
reference_role: M1-music
master_target_lufs: -14.0
water:
  role: W1-surf
  offset_db: -12.0
  band_db: [-14.0, -10.0]
supporting_group:
  name: weather
  offset_db: -20.0
  band_db: [-24.0, -18.0]
  members:
    R1-rain: 0.7
    R2-wind: 0.3
approved:
  project: fixture_second_channel
```
