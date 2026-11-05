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
| 04 | `04_decoding/` | Bit & frame sync, decode the navigation message (ephemeris, clock, time) | ⏳ planned |
| 05 | `05_positioning/` | Pseudoranges → solve receiver lat/lon/alt + clock bias (PVT) | ⏳ planned |

Shared, reusable pieces live in [`common/`](common/):
- [`common/ca_code.py`](common/ca_code.py) — GPS L1 C/A Gold-code (PRN) generator.
- [`common/iq_loader.py`](common/iq_loader.py) — load HackRF int8 / gr_complex IQ.

## Quick start

```bash
# verify the PRN-code generator (textbook self-test)
python common/ca_code.py

# scan all 32 PRNs in an IQ capture
python 02_acquisition/scan_prns.py

# track every detected satellite (carrier PLL + code DLL) -> 50 bps nav bits
python 03_tracking/track.py
```

Both `scan_prns.py` and `track.py` read a raw IQ recording (see
[`01_data_acquisition/`](01_data_acquisition/) for how to capture one with a HackRF
One — recordings aren't checked into this repo, so bring your own). Requires
Python 3 + numpy + matplotlib. Runs natively on Windows; no WSL2 needed for offline
analysis.

## Web UI (in progress)

A local web app walks through the pipeline one tab per stage, with plain-language
explanations and toggleable math. See [`docs/UI_PLAN.md`](docs/UI_PLAN.md) for the
full plan; for now it is the shell (navigation, stage status, explanations).

```bash
pip install -r requirements.txt   # ideally in a virtual environment
python ui/app.py                  # opens http://localhost:8080
python -m pytest ui               # UI tests
```

Explanation texts live in `ui/explain/<stage>/*.md` and are re-read on every page
load, so you can edit them and refresh the browser.

## Current status (2026-05-31)

Stages 02 and 03 are working. Stage 02 acquisition finds **6+ genuine GPS
satellites** in a 30 s outdoor capture (PRN 20, 5, 22, 21, 30, 14 strong; 7 and 15
moderate), each verified by persistence. Stage 03 then **locks 7 of those
satellites for the full 30 s** at a healthy **37–43 dB-Hz C/N0** (carrier PLL +
carrier-aided code DLL), demodulates the 50 bps data, and — the decisive check —
recovers the GPS subframe **preamble repeating every 300 bits** on all 7. That
confirms signal presence and strength are **not** a blocker, and the nav data is
cleanly decodable. The path to a fix now continues with stage 04 (frame sync +
ephemeris decode) → stage 05 (PVT). See
[`02_acquisition/NOTES.md`](02_acquisition/NOTES.md) and
[`03_tracking/NOTES.md`](03_tracking/NOTES.md) for the evidence.

## Hardware

- HackRF One SDR
- GPS L1 active patch antenna, powered via the HackRF's bias-tee
