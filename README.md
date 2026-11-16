# Bassanite — GPS from scratch, with a HackRF and Python

An **educational** project: take *raw* radio samples from a HackRF One + GPS L1
patch antenna and work, step by step, all the way to a **position fix** — building
and explaining each part of a GPS receiver in pure Python (numpy), so the process
is dissected rather than hidden inside a black box.

Commercial GPS receivers are sealed chips: samples go in, a lat/lon comes out, and
everything in between — signal acquisition, code/carrier tracking, navigation-message
decoding, pseudorange-based positioning — is invisible. Bassanite rebuilds that
chain one stage at a time, in readable Python, so each concept (Gold codes, Doppler
search, PLL/DLL tracking, subframe sync, trilateration) can be inspected, plotted,
and understood rather than taken on faith. It's meant to be read and experimented
with, not just run.

## How this repo is organized

GPS reception is strictly linear, so the work is laid out one folder per stage,
in order:

| Stage | Folder | What it does | Status |
|------:|--------|--------------|--------|
| 01 | [`01_data_acquisition/`](01_data_acquisition/) | Get raw IQ samples (live HackRF or a recording) | ✅ built |
| 02 | [`02_acquisition/`](02_acquisition/) | **Scan the PRNs** — detect which satellites are present (code phase + Doppler) | ✅ built |
| 03 | [`03_tracking/`](03_tracking/) | Follow each satellite (code DLL + carrier PLL) → 50 bps data stream | ✅ built |
| 04 | [`04_decoding/`](04_decoding/) | Frame sync, parity, decode the navigation message (ephemeris, clock, time) | ✅ built |
| 05 | [`05_positioning/`](05_positioning/) | Pseudoranges → solve receiver lat/lon/height + clock (PVT) | ✅ built |

Each stage's algorithm lives in the importable [`bassanite/`](bassanite/) package
(used by both the command-line scripts in the numbered folders and the web UI):
- [`bassanite/acquisition.py`](bassanite/acquisition.py) — stage 02 search.
- [`bassanite/tracking.py`](bassanite/tracking.py) — stage 03 loops, bit sync, C/N0.
- [`bassanite/navigation.py`](bassanite/navigation.py) — stage 04 frame sync, parity,
  fields, time of week; also the message encoder the synthetic recordings use.
- [`bassanite/position.py`](bassanite/position.py) — stage 05 satellite positions,
  corrections, least-squares solver, DOP, the Doppler cross-check.
- [`bassanite/results.py`](bassanite/results.py) — results go to
  `results/<recording>/<stage>/` (git-ignored), one folder per recording.
- [`bassanite/provenance.py`](bassanite/provenance.py) — each result file gets a
  `<name>.meta.json` recording which recording and settings produced it.
- [`bassanite/synthetic.py`](bassanite/synthetic.py) — synthetic recordings with known
  satellites, for tests and for the UI; and a synthetic sky (real orbits, real
  messages, a receiver at a known place) that checks the whole chain.

Lower-level pieces, in the same package:
- [`bassanite/ca_code.py`](bassanite/ca_code.py) — GPS L1 C/A Gold-code (PRN) generator.
- [`bassanite/iq_loader.py`](bassanite/iq_loader.py) — load HackRF int8 / gr_complex IQ.
- [`bassanite/signal_view.py`](bassanite/signal_view.py) — spectrum, waterfall, ADC levels,
  interference bursts.
- [`bassanite/hackrf.py`](bassanite/hackrf.py) — record from a HackRF (receive only),
  bias-tee check.

## Quick start

```bash
# once: install the package (editable) into your environment, with the plot extras
pip install -e ".[plots]"

# verify the PRN-code generator (textbook self-test)
python -m bassanite.ca_code

# scan all 32 PRNs in an IQ capture
python 02_acquisition/scan_prns.py

# track every detected satellite (carrier PLL + code DLL) -> 50 bps nav bits
python 03_tracking/track.py

# read each satellite's message: orbit, clock correction, time
python 04_decoding/decode.py

# solve the position (needs 4 satellites with a complete message)
python 05_positioning/solve.py
```

Each script takes `--iq <recording>` and writes to `results/<recording>/<stage>/`.
The default, `recordings/capture.bin`, is the 30 s reference recording: long enough
for stages 02 to 04, but a position needs **at least 36 s** (the clock correction
is sent once every 30 s).

`scan_prns.py` and `track.py` read a raw IQ recording (see
[`01_data_acquisition/`](01_data_acquisition/) for how to capture one with a HackRF
One — recordings aren't checked into this repo, so bring your own). Requires
Python 3.10+, numpy and matplotlib (`pip install -e ".[plots]"`). Runs natively on Windows; no WSL2 needed for offline
analysis.

## Web UI

A local web app walks through the pipeline one tab per stage, with plain-language
explanations and toggleable math. See [`docs/UI_PLAN.md`](docs/UI_PLAN.md) for the
full plan. The tabs: the Overview, **Signal** (spectrum, waterfall, ADC levels, live
HackRF capture), **Find satellites** (Gold codes step by step, the 32-PRN scan, the
correlation surface, Try-it sliders, the persistence check), **Track** (run
tracking, then replay each satellite's loops locking and its bits appearing),
**Decode** (the bit stream with its preambles, one subframe field by field, the
time) and **Position** (the result, the scatter of the positions, the solver's
iterations, the sky, and a Try-it that switches each correction or satellite off).

```bash
pip install -e ".[plots,ui,test]"   # ideally in a virtual environment
python ui/app.py                  # opens http://localhost:8080
python -m pytest ui               # UI tests
```

On Windows you can also **double-click `run_ui.bat`**. It uses the Python in
`%USERPROFILE%\.venvs\bassanite` if that venv exists (or the one named by the
`BASSANITE_PYTHON` environment variable), otherwise the `python` on your PATH. Close
its window to stop the server.

Explanation texts live in `ui/explain/<stage>/*.md` and are re-read on every page
load, so you can edit them and refresh the browser.

## Current status (2026-11-16)

All five stages work, and the chain has produced its **first real position**: from a
60 s recording made with the HackRF and the patch antenna, 4 satellites tracked at
39–43 dB-Hz, their messages decoded (parity 90/90 words each), and a position solved
every 100 ms.

- **Checked against a known answer.** A synthetic recording of five satellites on
  real GPS orbits, received at a known place, goes through all five stages from raw
  samples: the position comes out within 0.5 m horizontally and 1.5 m in height
  (`python -m pytest tests -m slow`).
- **Checked on the real recording.** The decoded time agrees with the recording's
  timestamp, and the Doppler that tracking measured (never used by the solver)
  matches the Doppler predicted from the position, time and orbits to 0.02 Hz on
  all four satellites.
- **Limits of that first fix.** Four satellites is the minimum, and they sat in the
  same part of the sky (HDOP 12): single positions scatter by about 20 m. The
  ionosphere is not corrected. A recording with six or more satellites spread across
  the sky is the next step.

Evidence and details, stage by stage: [`02_acquisition/NOTES.md`](02_acquisition/NOTES.md),
[`03_tracking/NOTES.md`](03_tracking/NOTES.md), [`04_decoding/NOTES.md`](04_decoding/NOTES.md),
[`05_positioning/NOTES.md`](05_positioning/NOTES.md).

A self-contained lecture notebook,
[`gnss_signal_theory_introduction.ipynb`](gnss_signal_theory_introduction.ipynb),
walks through the same chain on a simulated signal, independently of the package.

## Hardware

- HackRF One SDR
- GPS L1 active patch antenna, powered via the HackRF's bias-tee
