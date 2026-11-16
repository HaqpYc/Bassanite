# Stage 05 — Positioning (from clocks to a position)

**Goal of this stage:** combine the code timing of stage 03 and the messages of
stage 04 into **latitude, longitude, height and time**. This is the end of the
chain.

Code: [`../bassanite/position.py`](../bassanite/position.py). Command line:
[`solve.py`](solve.py).

---

## The concepts (short version)

### 1. Reading every satellite's clock at the same instant (`satellite_time`)
After stage 04, each code period of a track is labelled with satellite time, and
stage 03 knows the sample at which each code period starts (`code_phase_samp`). So
for any sample of the recording we can say what each satellite's clock showed in
the signal arriving just then, interpolating inside the code period. The readings
differ by up to 20 ms between satellites: that is the difference of their flight
times.

### 2. Pseudoranges
The receiver's only clock is its sample counter, and nobody set it. We set it once
by assuming the nearest satellite is 70 ms away (`receiver_clock_at_0`) and make
its error `δt` a fourth unknown:

    c · (t_receiver − t_satellite_i) = |s_i − r| + c·δt

Four unknowns (x, y, z, δt), so four satellites.

### 3. The corrections (`SolveOptions`)
Each can be switched off (`solve.py --without NAME`, or the Try-it of the Position
tab) to see what it is worth. Measured on `capture_20261110_143115.bin`, whose
weak geometry amplifies them:

| correction | what it is | position moves by |
|---|---|---:|
| `satellite_clock` | `a_f0, a_f1, a_f2` of subframe 1 (hundreds of µs) | 3,400 km |
| `relativity` | clock rate along the elliptical orbit, `F·e·√A·sin E` | 37 m |
| `earth_rotation` | the Earth turns 5 µrad during the flight | 19 m |
| `troposphere` | ~2.4 m at the zenith, more near the horizon | 11 m |
| `group_delay` | `T_GD`, for L1-only receivers | 8 m |

**Not corrected: the ionosphere** (typically 2–10 m by day). Its model is in
subframe 4, once per 12.5 minutes, longer than our recordings.

### 4. Solving (`solve_position`)
Newton's method with least squares, from the centre of the Earth: linearise the
range equations around the current guess (one row `[−u_i, 1]` per satellite, `u_i`
the unit vector towards it), solve, correct, repeat. On the real recording the
successive guesses are 1,200 km, 29 km, 25 m and 1.5 cm from the answer.

### 5. One position every 100 ms (`fix_series`, `summarize`)
A position uses one instant. Computing one every 100 ms across the recording gives
the answer (their mean) and its precision (their scatter). The drift of `δt` across
the recording is the relative error of the receiver's oscillator.

### 6. DOP (`dilution_of_precision`)
The geometry turns ranging noise into position noise: `HDOP`, `VDOP` are the
factors, from the diagonal of `(HᵀH)⁻¹` in east/north/up.

### 7. An independent check: the Doppler (`doppler_check`)
With exactly four satellites the equations always have an exact solution, right or
wrong, so there are no residuals to look at. But the carrier Doppler that stage 03
measured was never used. `predicted_doppler` computes what each satellite's Doppler
should be at the position and time found; the measurements must match up to one
offset common to all satellites (the receiver's own frequency error). A position
1 km off, or a time 1 s off, shows up as about 1 Hz of disagreement between
satellites.

---

## How to run

```bash
python 05_positioning/solve.py --iq recordings/<recording>.bin
python 05_positioning/solve.py --iq recordings/<recording>.bin --without earth_rotation
python 05_positioning/solve.py --iq recordings/<recording>.bin --truth LAT,LON,HEIGHT
```

It reads `results/<recording>/tracking/` and `results/<recording>/decode/` and
writes `results/<recording>/position/fix.json`, `fixes.csv`, `fix.meta.json` and
`fix.png`. In the web UI: the **Position** tab.

---

## Results

### The first real fix: `recordings/capture_20261110_143115.bin`
4 satellites (the minimum), 580 positions over 58 s. The position itself is in
`results/…/position/fix.json`, which is not in git.

| | |
|---|---|
| scatter of one position (1 σ) | 22 m east, 7 m north, 26 m up |
| geometry | HDOP 12.1, VDOP 14.0: all four satellites in the same part of the sky |
| ranging noise this implies | about 2 m per satellite |
| time of the first sample | agrees with the recording's timestamp to the second |
| receiver oscillator | −0.21 ppm (from the drift of the clock error) |
| Doppler check | measured − predicted = +309.7 Hz on all four, **spread 0.02 Hz** |

The Doppler agreement to 0.02 Hz is the evidence that position, time and orbits fit
together: the test is sensitive to about 1 Hz per km. The accuracy of the mean
position against the true antenna position is still to be measured by someone who
knows where the antenna was; with HDOP 12 and no ionosphere correction, expect tens
of metres.

**Open question.** The common Doppler offset (+309.7 Hz) and the offset predicted
from the clock drift (+331.5 Hz) differ by 22 Hz. Both should come from the same
oscillator. A likely cause, not verified, is the tuner: the HackRF's synthesiser
sets its frequency in steps of a few tens of hertz, which shifts the carrier but
not the sample clock.

### Verification on a synthetic sky
`bassanite/synthetic.py` makes a recording of five satellites on real GPS orbits,
each sending a real message, received at a known place with the true delays
(`write_sky_recording`). From the samples alone, through all five stages
(`tests/test_end_to_end.py`, run with `-m slow`, about 1 minute):

| | |
|---|---|
| error of the mean position | 0.0 m east, 0.5 m north, 1.5 m down |
| scatter of one position | 3.0 m horizontally, 3.7 m up (HDOP 1.5) |
| time of the first sample | within 50 ns |
| oscillator error | recovered (−0.3 ppm put in) |

The measurement-level tests (`tests/test_position.py`) recover the position to a
centimetre when there is no noise.

---

## Implementation notes

- **Time differences wrap with the week** (`wrap_week`): a reference time just
  before Sunday midnight, used just after, is seconds away, not a week.
- **The Earth-rotation correction is applied to the satellite's coordinates**, by
  the angle `Ω_e · flight time`, not to the receiver's.
- **The troposphere correction starts once the estimate is near the Earth's
  surface** (the elevation of a satellite means nothing from the Earth's centre).
- **`code_phase_samp` counts from the first sample tracking read.** If tracking
  skipped the start of the file, "sample 0" is that first sample.
- **Not done yet:** carrier smoothing of the code timing (would reduce the 2 m
  ranging noise), the ionosphere model, an elevation mask, velocity from Doppler.
