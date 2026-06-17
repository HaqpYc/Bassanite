# GPS from scratch — a HackRF + Python walkthrough

An **educational** project: take *raw* radio samples from a HackRF One + GPS L1
patch antenna and work, step by step, all the way to a **position fix** — building
and explaining each part of a GPS receiver in pure Python (numpy), so the process
is dissected rather than hidden inside a black box.

## How this repo is organized — two axes

**1. The pipeline (numbered stages).** GPS reception is strictly linear, so the
work is laid out one folder per stage, in order:

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

**2. The troubleshooting log** — [`issues/`](issues/). A separate axis: real
problems hit along the way and how they were diagnosed (e.g. the long
"clock drift" investigation that turned out to be a no-signal / weak-signal /
GNSS-SDR story). Kept as the project's lab notebook.

## Quick start

```bash
# verify the PRN-code generator (textbook self-test)
python common/ca_code.py

# scan all 32 PRNs in the bundled 30 s outdoor capture
python 02_acquisition/scan_prns.py

# track every detected satellite (carrier PLL + code DLL) -> 50 bps nav bits
python 03_tracking/track.py
```
Requires Python 3 + numpy + matplotlib. Runs natively on Windows; no WSL2 needed
for offline analysis.

## Current status (2026-05-31)

Stages 02 and 03 are working. Stage 02 acquisition finds **6+ genuine GPS
satellites** in the reference capture `recordings/capture.bin` (PRN 20, 5, 22, 21,
30, 14 strong; 7 and 15 moderate), each verified by persistence. Stage 03 then
**locks 7 of those satellites for the full 30 s** at a healthy **37–43 dB-Hz C/N0**
(carrier PLL + carrier-aided code DLL), demodulates the 50 bps data, and — the
decisive check — recovers the GPS subframe **preamble repeating every 300 bits** on
all 7. That **confirms** the revision of the earlier `issues/clock_drift/`
conclusion ("~1 solid SV / too weak"): signal presence and strength are **not** the
blocker, and the nav data is cleanly decodable. The path to a fix now continues with
stage 04 (frame sync + ephemeris decode) → stage 05 (PVT). See
[`02_acquisition/NOTES.md`](02_acquisition/NOTES.md) and
[`03_tracking/NOTES.md`](03_tracking/NOTES.md) for the evidence.

## Hardware
HackRF One (serial `…230865dc2a7a7347`, fw `n_250816`) + active GPS L1 patch
antenna (bias-tee powered). See [`CLAUDE.md`](CLAUDE.md) for the full hardware /
environment notes.
