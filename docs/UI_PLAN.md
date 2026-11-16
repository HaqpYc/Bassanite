# Bassanite UI plan

Plan for Bassanite's graphical interface. The UI grows with the backend: each
pipeline stage gets its tab when the stage is implemented.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| UI type | **Local web app** (Python server + browser on `localhost`) | Works on any OS with a browser, no per-OS packaging, viewable from a phone on the same network (handy with the antenna outdoors) |
| Framework | **NiceGUI** | The whole pipeline is Python/numpy; NiceGUI keeps the UI in Python, with built-in Plotly charts (incl. 3D), Leaflet map, and Markdown + LaTeX |
| Users | **Both beginners and experts** | Beginner / Expert mode toggle |
| Structure | **One tab per pipeline stage**, with **Overview as the first tab** | Teaches GNSS from A to Z, in order; Overview is home and summary |
| Input | Live HackRF **or a raw IQ recording** | Offline use without hardware |
| Math | Toggleable explanations on every visualisation | The user must understand how information is extracted from the raw signal |

Rejected alternatives: Qt desktop GUI (per-OS packaging), Electron/Tauri (heavy,
still needs the Python backend), terminal UI (no map, unfriendly to beginners),
Jupyter (cells run out of order, raw code shown), GNU Radio Companion (a DSP design
tool, not an end-user UI; regenerates code). GRC flowgraphs remain useful as
separate expert diagnostic tools (e.g. a bias-tee noise-floor check).

---

## 1. Layout of the app

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ BASSANITE   Source: capture.bin (int8, 4 MSPS, 30 s) [change]                │
│                         [Beginner ◉ Expert ○]  [∑ Show the math: off]  [?]   │
├──────────────────────────────────────────────────────────────────────────────┤
│ Overview │ 1 Signal │ 2 Find satellites │ 3 Track │ 4 Decode │ 5 Position    │
│          │    ✅    │        ✅         │   ✅    │    ✅    │     ✅        │
├──────────────────────────────────────────────────────────────────────────────┤
│  ┌─ Story strip (Beginner only) ───────────────────────────────────────────┐ │
│  │ "Each satellite hides its signal 20 dB below the noise. Here we..."     │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│  ┌─ Controls (Expert) ─┐ ┌─ Visualisations ─────────────────────────────────┐ │
│  │ params…             │ │  plots / tables                                  │ │
│  │ [Run stage]         │ │  each plot has a "∑ How is this computed?" tab   │ │
│  └─────────────────────┘ └──────────────────────────────────────────────────┘ │
│  ┌─ Outputs ───────────────────────────────────────────────────────────────┐ │
│  │ what this stage hands to the next one  ·  [Export CSV] [Save PNG]       │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                         [← Previous step]  [Next step →]     │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Header (always visible)
- **Signal source:** which IQ file or live device is in use, plus a button to change it.
- **Mode:** Beginner or Expert.
- **Show the math:** a global toggle that opens every explanation panel.
- **Stage badges:** ✅ done · ⚠️ ran but needs attention · ⏳ running · 🔒 not built
  yet or not run yet.

### Stage tab layout
Every stage tab has the same five blocks, so a new user learns the layout once:

1. **Story**: two or three plain sentences on what this step does and why it's needed.
2. **Controls**: hidden in Beginner mode, where defaults apply. Shown in Expert mode.
3. **Visualisations**: the main content.
4. **Outputs**: exactly what is passed to the next stage, such as the
   `(PRN, code phase, Doppler)` table. This makes the A-to-Z chain visible.
5. **Prev/Next navigation**: for the guided path.

### Beginner vs Expert

| | Beginner | Expert |
|---|---|---|
| Parameters | hidden, defaults used | full controls per stage |
| Running | "Run all" or "Next step", a guided path | run any stage, re-run with new settings |
| Text | story strip and plain-language captions | story collapsed, raw numbers and tables shown |
| SDR | Capture with sensible presets | gains, sample rate, bias-tee, ppm, duration |
| Extras | — | logs, exports, run comparison |

---

## 2. Math explanations: three levels

Each visualisation gets a **"∑ How is this computed?"** panel. The global toggle
opens all of them at once. Each panel has three tabs:

1. **Intuition**: a plain-language explanation, with a small diagram where it helps.
2. **Math**: the equations, rendered with LaTeX, with every symbol defined and the
   actual values from this run filled in. For example:
   "f_D = 2425 Hz → λ_L1 · f_D = 461 m/s line-of-sight velocity".
3. **Code**: the actual backend function, such as `acquire_prn()` or `track_prn()`,
   read from the source file at runtime (`inspect.getsource`). The explanation can
   never drift from the code that produced the plot.

### "Try it" widgets
Small playgrounds next to the explanation, each with a slider whose effect is
visible immediately:
- **Doppler mismatch slider:** the correlation peak collapses (sinc shape) as the
  carrier error grows.
- **Integration time slider (1–20 ms):** weak satellites rise out of the noise floor.
- **PLL bandwidth slider:** shows lock, then loss of lock.
- **Remove a satellite:** the position and DOP change.

### Where the text lives
In Markdown files (`ui/explain/<stage>/<panel>.md`), separate from the UI code, so
explanations can be edited without touching Python. The existing stage `NOTES.md`
files are the starting material.

---

## 3. The tabs

### Overview (first tab: home and summary)
- **Pipeline diagram A→Z:** antenna → IQ samples → which satellites? → follow them
  → read their messages → solve position. Each box shows its status and key number
  ("7 satellites locked", "C/N0 37–43 dB-Hz"), and clicking it opens that tab.
- **Input card:** the current source, with a big **"Load a recording"** button and a
  **"Capture live"** button.
- **Result card:** the position on a map once stage 05 exists. Until then it shows
  the furthest stage reached.
- **Run history:** past runs (one `results/<recording>/` folder each) with source, settings and results,
  and an "open" button.
- **First-run checklist (Beginner):** HackRF detected? Bias-tee on? Antenna outdoors?
  Or skip all that and use the bundled recording.

### 1. Signal (stage 01, data acquisition)
*Message: "what an SDR actually records, and why you can't see GPS in it."*

- **Source panel:** a file picker with format (int8 / complex64), sample rate and
  centre frequency. Format and rate are guessed from the file size and extension,
  and the user can override them. Or **Live HackRF**.
- **Waterfall + spectrum (PSD):** around 1575.42 MHz. It looks flat, and that is the
  lesson: GPS arrives at about −130 dBm, roughly 20 dB below the thermal noise in a
  2 MHz band (−174 dBm/Hz + 63 dB ≈ −111 dBm).
- **Time domain:** I and Q against time over a few microseconds.
- **IQ scatter (constellation):** a Gaussian blob, because GPS is invisible at this point.
- **I/Q histograms:** with the 8-bit ADC limits marked, to check gain and clipping.
- **Expert:**
  - LNA/VGA/amp gains, sample rate, bias-tee, duration, **Record**.
  - A **bias-tee check** tool: compare the noise floor with bias-tee on and off, to
    confirm the antenna's LNA is powered.
- **Math panels:**
  - complex baseband and mixing, `x(t) = I + jQ`
  - complex sampling and Nyquist
  - the noise-floor budget above
  - DC-offset removal (as `bassanite/iq_loader.py` does)

### 2. Find satellites (stage 02, acquisition)
*Message: "the signal is buried in noise. Each satellite is found by its unique code."*

- **"Meet the codes" sub-panel:**
  - the C/A code of a chosen PRN as chips
  - the G1/G2 shift-register diagram
  - autocorrelation (one sharp peak)
  - cross-correlation with another PRN (flat)

  This is where CDMA makes sense.
- **PRN bar chart:** peak/mean for all 32 PRNs, with the threshold line and the noise
  floor of about 3 (the existing `scan_plot.png`, made interactive).
- **Correlation surface:** a 3D view and a heatmap of code phase × Doppler for the
  selected PRN.
- **Slice at the best Doppler:** correlation against code phase, showing the peak.
- **Persistence check:** rescan at t = 0 / 14 / 28 s and plot Doppler and code phase
  over time. Real satellites drift smoothly; noise jumps around.
- **Expert:** Doppler range and step, integration ms, threshold, skip seconds.
- **Math panels:**
  - carrier wipe-off
  - FFT circular correlation `IFFT(FFT(x·e^{-j2πft}) · conj(FFT(c)))`
  - coherent vs non-coherent integration, and the 20 ms bit-flip limit
  - the peak/mean statistic
- **Try it:** the Doppler mismatch slider and the integration-time slider.

### 3. Track (stage 03, tracking)
*Message: "a satellite moves, so we lock onto it with feedback loops, and the data
bits appear."*

- **Satellite cards:** one small card per PRN showing C/N0, lock state and a
  sparkline. Clicking a card opens its detail view.
- **Detail view with a time scrubber** to replay the recording:
  - **Prompt I/Q scatter:** a spinning cloud that collapses into **two lobes** on the
    I axis as the PLL locks.
  - **Early/Prompt/Late correlator triangle:** shows the DLL centring itself.
  - **Prompt I against time:** the 20 ms bit flips are visible.
  - **Doppler and code phase against time.**
  - **C/N0 and the lock indicator** (`|Q|/|I|`).
  - **Bits strip:** the 50 bps output, with bit-sync energy for each of the 20
    possible offsets.
- **Expert:** PLL and DLL bandwidths, PRN selection, manual seeds, seconds.
- **Math panels:**
  - Costas discriminator `atan(Q/I)` vs `atan2`
  - the E−L discriminator
  - the second-order loop filter (`calc_loop_coef`)
  - carrier aiding `f_code = 1.023 MHz·(1 + f_D/f_L1)`
  - NWPR C/N0
  - bit sync

### 4. Decode (stage 04)
- **Bit stream:** with the **preamble `10001011` highlighted every 300 bits**, a
  direct visual of frame sync.
- **Subframe anatomy:** 10 words × 30 bits, TLM/HOW, and parity bits, coloured per field.
- **Field decoder table:** raw bits → integer → scaled value (e.g. `√A`, `e`, `M₀`,
  TOW, week), with the scale factor shown.
- **Math panels:** the parity (Hamming) equations, and what the ephemeris parameters
  mean physically.

### 5. Position (stage 05)
- **Sky plot:** azimuth/elevation of the satellites, computed from the ephemeris.
- **Pseudorange table:** transmit time, receive time and range, plus the corrections
  applied.
- **Least-squares solver animation:** the estimate starts at Earth's centre and
  converges in a few iterations, shown on a globe or map with the residual at each step.
- **Map:** the fix, the uncertainty circle and DOP values.
- **Try it:** remove a satellite and see the effect.
- **Math panels:**
  - the pseudorange equation `ρ = ‖s − r‖ + c·δt`
  - why 4 unknowns need 4 satellites
  - linearisation, least squares and DOP

---

## 4. Outputs and history
- Each stage writes to `results/<recording>/<stage>/` (git-ignored), one folder per
  recording, with a provenance file naming the recording and the settings
  (`bassanite/results.py`, `bassanite/provenance.py`). The Overview's run history
  lists these folders.
- A result older than the result of the step before it is flagged (steps 4 and 5):
  the earlier step was run again.
- Not built: exports (KML/GPX of the position, a one-click HTML session report).
  See section 8.

---

## 5. Backend changes the UI needed (all done)

| Need | What was done |
|---|---|
| Import stages from the UI | The algorithms live in the `bassanite/` package; the numbered folders hold the command lines and the notes |
| Correlation surface plot | `acquire_prn(return_surface=True)` |
| E/P/L triangle, discriminator plots | `track_prn(detail=True)` returns the Early/Late correlators and both discriminators |
| Progress bar, no frozen UI | Tracking runs as a separate process (`track.py --progress`) and reads the recording block by block |
| Responsive plots | Curves are decimated before they go to the browser; the waterfall is computed on the server |
| Live view | The live preview records short stretches to a temporary file (`hackrf_transfer -r -` corrupts binary data on Windows) |
| Stages 04 and 05 | `bassanite/navigation.py`, `bassanite/position.py`; both run in well under a second, in the server process |

The files (`scan_results.csv`, `track_PRN*.npz`, `navigation.json`, `fix.json`) are
the contract between stages, so the UI and the command-line scripts stay
interchangeable.

### Code layout
- `ui/app.py`: the app shell (header, toggles, tabs)
- `ui/tabs/<stage>.py`: one module per tab
- `ui/explain/<stage>/<panel>.md`: the explanation text
- `ui/state.py`: current source and per-stage results

---

## 6. Build order
1. ✅ **Shell:** header, mode and math toggles, Overview with the pipeline diagram, and
   tab placeholders.
2. ✅ **Signal tab with file loading** (offline first), then live capture and the
   bias-tee check.
3. ✅ **Backend refactor** into importable modules (`bassanite/`), plus the surface
   and correlator outputs, progress callbacks, and provenance files for results.
4. ✅ **Acquisition tab** with "Meet the codes" (shift-register stepper, auto/cross
   correlation), the PRN bar chart, the correlation surface and slices, the Try-it
   sliders (measured against sinc²) and the persistence check with its drift test.
5. ✅ **Tracking tab** with the time scrubber (play, step by bit, window length), the
   satellite cards, the PLL-bandwidth Try-it, and tracking run as a separate process
   (`track.py --progress`) whose results replace the old ones only on success.
6. ✅ **Stages 04 and 05**, each with its tab:
   - **Decode:** the bit stream (300 bits per row, preambles lined up, chance matches
     marked), one subframe field by field, the field table, the time of week.
   - **Position:** the result, the scatter and time series of the single positions,
     the pseudorange table, the solver's iterations, the sky and DOP, the Try-it
     (switch a correction or a satellite off), the Doppler check, the map (off until
     switched on, because it loads tiles from openstreetmap.org).
   - The Overview's result card shows the position.

---

## 7. Review recommendations (2026-11-10)

From a review of stages 01–03 and their tabs against the project's goal: modular
and educational. Items marked ✅ are built. Within each table, the most important
item comes first.

### Finding: `capture.bin` is too short for a position fix

Checked on the bits saved in `03_tracking/track_PRN*.npz`:

- All 7 locked satellites pass parity on **49 of 49** complete words, so the bits
  are clean and decoding should be straightforward.
- The recording starts 27 bits (0.54 s) before a subframe 2. It holds subframes
  **2, 3, 4 and 5 complete**, then **subframe 1 cut by the end of the file**: words
  1–9 are there, word 10 is not.
- Word 10 of subframe 1 carries `af0`, the satellite clock bias (up to about ±1 ms,
  which is ±300 km of range, different for each satellite). Of its 30 bits, 27 are
  at the start of the file, 2 at the end, and 1 was never recorded.

1499 bits can never hold five complete subframes (1500 bits), so a 30 s recording
always cuts one. Subframes 1, 2 and 3 are each complete at least once only from
**36 s** of signal (plus pull-in time).

### Backend and structure

| # | Recommendation | Why |
|---|---|---|
| B1 ✅ (code side) | **Record 40–60 s outdoors** and use it as the reference for stages 04–05. Change the tracking default (`DEFAULT_SECONDS = 30` in `ui/tabs/tracking.py`, whose comment says 30 s holds every subframe) | See the finding above: `capture.bin` lacks `af0` |
| B2 ✅ | **Read the recording in blocks** (memory-map the int8 file, convert each block) instead of loading it all as complex64 | 30 s costs about 960 MB, and 60 s would cost about 1.9 GB, more than this machine has free. Tracking only touches about 4000 samples per step. This removes the reason for the memory warning and the 10 s `reconnect_timeout` |
| B3 ✅ | **One results folder per recording**, git-ignored (the `runs/` idea of section 4, keyed by recording rather than by time alone) | Scan and Track write into the git-tracked `02_acquisition/` and `03_tracking/`, so a button press overwrites committed files, and only one recording's results can exist at a time. Each new stage adds more files to that single slot |
| B4 ✅ | **Define each file format in one place**, with save and load next to the algorithm in `bassanite/` | `scan_results.csv` has three readers (`bassanite/acquisition.py`, `bassanite/tracking.py`, `ui/stages.py`). The track `.npz` is written in `03_tracking/track.py` and read in `ui/tabs/tracking.py`. The summary CSV is written in `track.py` and read in two UI files. Stage 04 would be one more reader |
| B5 ✅ | **Move receiver algorithms out of the UI:** `persistence()` and `refine_doppler()` from `ui/tabs/acquisition.py` into `bassanite/acquisition.py`, with a command-line option for the persistence check. Make the refined Doppler part of what acquisition hands over | Tracking has its own, different `fine_doppler()`, so there are two fine-Doppler searches. The persistence check is how a weak detection is confirmed, yet only the UI can run it |
| B6 ✅ | **Split `track_prn` into the blocks of the textbook diagram:** correlators, discriminators, loop filter, oscillator, with one `step()` per satellite and per millisecond | The loop-filter update is written inline twice, so the "loops" panel's Code tab shows only `calc_loop_coef`, not the equation its Math tab explains. Named blocks also allow the "break it" switches (U4) and tracking every satellite in one pass over the file |
| B7 ✅ | **Packaging** (low priority): a `pyproject.toml` with an editable install, `common/` folded into `bassanite/`, backend tests out of `ui/tests/` | 11 `sys.path.insert` calls, 43 `noqa: E402` markers, and the L1 frequency defined four times |

### Explanations and UI

| # | Recommendation | Why |
|---|---|---|
| U1 ✅ | **Show the Intuition text by default in Beginner mode**, next to its plot. Keep Math and Code behind "Show the math" | Intuition is the best teaching content, but it sits inside collapsed "How is this computed?" panels opened by a switch labelled "math". A beginner sees only the story strip and short captions |
| U2 ✅ | **Fill the numbers in from the current run** (the "actual values from this run" of section 2): placeholders in the `.md` files, filled by the tab. Label `capture.bin` figures as a worked example | The panels hardcode `capture.bin` values and defaults (4 MSPS, 324,000 cells, 10 ms, PRN 20's figures: 12 mentions across 8 panels). With another recording, sample rate or setting they are wrong |
| U3 ✅ | **A despreading demo between steps 1 and 2:** the spectrum of the raw samples, then of the same samples multiplied by the aligned code, where a spike appears at the Doppler frequency | The Signal tab ends on "no FFT size can make it appear". This shows the moment it does appear, and why the Doppler search is a frequency search |
| U4 ✅ | **Track tab: a block diagram of the two loops** as the tab's map (like the shift-register drawing), **and "break it" switches:** `atan2` instead of `atan`, carrier aiding off, a rounded number of samples per code period | These replay the real bugs described in `03_tracking/NOTES.md`. Watching the lock fail teaches more than the bandwidth slider alone. Needs B6 |
| U5 ✅ | **Make the synthetic signal a selectable source** (move `ui/tests/synthetic.py` into `bassanite/`), with a signal-strength slider | A simulator with known truth shows a satellite sinking into the noise, and gives stages 04 and 05 an end-to-end test with a known answer (known ephemeris, known position) |

---

## 8. What is next (2026-11-16)

Stages 04 and 05 and their tabs were built on 2026-11-16. Each item below says what
to do and how to know it is done. The most useful come first.

| # | Item | What to do | Done when |
|---|---|---|---|
| N1 | **Look at the two new tabs in a browser** | They are tested with NiceGUI's simulated user only (the test server was stopped for lack of memory before a browser check). Start `python ui/app.py --no-browser --port 8081`, load `capture_20261110_143115.bin`, open Decode and Position. Check: the bit-stream colours and subframe numbers; that the field names of "One subframe, field by field" sit above their bits without overlapping (`anatomy_figure`, `yshift`); the sky plot (north up, east to the right); the map switch; the Try-it | Every figure reads correctly at 1280 px and at phone width |
| N2 | **A recording with 6 or more satellites** | 60 s or more, antenna outdoors with an open sky. On 2026-11-16 the antenna, where it stood, saw 2 satellites and interference bursts. Then scan, track, decode, solve | Residuals are shown (more than 4 satellites), HDOP below 3, and "leave a satellite out" works in the Try-it |
| N3 | **Compare with the true antenna position** | `python 05_positioning/solve.py --iq <file> --truth LAT,LON,HEIGHT` (height above the ellipsoid) | The error is written in `05_positioning/NOTES.md`, without the coordinates |
| N4 | **Detections that can be trusted** | The threshold is a fixed 5. (a) In `capture_20261110_223146.bin` (bursts) every one of the 32 PRNs scores 7 to 8 and is "detected": in `bassanite/acquisition.py::scan`, use `max(threshold, 1.5 x median peak/mean of the PRNs searched)` when at least 16 PRNs are searched, store the value used in the provenance file, and draw it in the bar chart. (b) In `capture_20261110_143115.bin` one noise peak of 5.5 passes any such threshold: once tracking has run, show a detection that did not lock as "not confirmed" in the Find satellites status and on the Overview | (a) That bursty recording gives 1 detection and `capture.bin` still gives 8, with tests. (b) The Overview says "5 detected, 4 confirmed by tracking" for the second recording |
| N5 | **The synthetic sky as a source** | Offer `bassanite.synthetic.write_sky_recording` in the source dialog (40 s at 4 MSPS is 320 MB and takes about a minute to write: run it like a recording, with progress). The Position tab already compares with the truth file | A user without hardware goes from the dialog to a position, and the tab shows the error against the truth |
| N6 | **Smoother code timing** | Save the carrier phase per millisecond in the track files (`ChannelTracker`: accumulate `carr_f × blksize / fs`), then smooth each pseudorange with it (Hatch filter) in `bassanite/position.py` | On the synthetic sky the scatter of one position drops well below the 3 m it has now, and the mean stays unbiased |
| N7 | **Exports** | KML or GPX of `fixes.csv`; a one-page HTML report of the Overview and the key plots | A button on the Position tab writes the file next to `fix.json` |
| N8 | **The 22 Hz question** | The receiver's frequency error measured from the Doppler (+310 Hz) and from the clock drift (+332 Hz) differ. Check whether the HackRF's tuning step explains it: compare several recordings, or record a known carrier | `05_positioning/NOTES.md` states the cause, or rules the tuner out |
| N9 | **Small tidying** | `components._placeholder` and `_BUILD_STEP` are unused now that every stage is built. `ui/tabs/tracking.py` and `ui/tabs/acquisition.py` are over 1,200 lines: their figure builders could move to modules of their own | No behaviour changes; the tests pass unchanged |

Not possible with short recordings: the ionosphere correction. Its model is
broadcast once per 12.5 minutes (6 GB at 4 MSPS).
