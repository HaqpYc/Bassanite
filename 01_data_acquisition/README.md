# Stage 01 — Data acquisition (getting the raw signal)

**Goal of this stage:** obtain a stream of **raw IQ samples** of the GPS L1 band
that the later stages can process. Nothing GPS-specific happens yet — we are just
recording (or loading) radio.

> Naming note: this is *data* acquisition (capturing samples). Don't confuse it
> with GPS **acquisition** (stage 02), which means detecting satellites.

---

## What is "raw IQ"?
An SDR shifts a slice of radio spectrum down to baseband and reports it as a stream
of **complex samples**: each sample has an in-phase (I) and quadrature (Q) part.
The pair encodes both amplitude and phase, which is exactly what we need to undo
Doppler and correlate against satellite codes later.

Key parameters for GPS L1 C/A:

| Parameter | Value | Why |
|---|---|---|
| Center frequency | **1575.42 MHz** | GPS L1 carrier |
| Sample rate | **4 MSPS** | Practical minimum for L1 C/A. The code is 1.023 Mchip/s; ~4× gives enough bandwidth and code-phase resolution (1 chip ≈ 3.9 samples). |
| Format (HackRF) | **int8 interleaved** | `I0,Q0,I1,Q1,…`, signed 8-bit → 2 bytes/sample |
| Format (GNU Radio) | **complex64** | interleaved 32-bit floats → 8 bytes/sample |
| Bias-tee | **on** | powers the active patch antenna's LNA |

At 4 MSPS, int8: 1 s ≈ 8 MB. The reference recording is 30 s ≈ 240 MB.

---

## The frequency-uncertainty budget (why later stages search ±10 kHz)
The satellite signal does not arrive exactly at 1575.42 MHz:
- **Satellite motion (Doppler):** up to ≈ **±5 kHz**.
- **Receiver oscillator error:** the HackRF TCXO is ±20 ppm → up to ≈ **±31 kHz**
  at L1. (Indoor GSM calibration proved unreliable. Stage 05 measures the error
  directly, as the drift of the receiver clock: −0.2 ppm on the HackRF used here.)

For *this* recording the offsets turned out small (detected Dopplers fall within
±4 kHz), so a ±10 kHz acquisition search is plenty. A cold/uncalibrated HackRF may
need a wider search.

---

## Two ways to get the signal

### Option A — use the existing recording (default for development)
`recordings/capture.bin` — a 30 s, 4 MSPS, int8 capture taken with the antenna
**fully outdoors**. It is known-good: stage 02 finds 6+ real satellites in it. This
is the reproducible offline input the pipeline develops against — no hardware
needed.

### Option B — capture live from the HackRF
See [`capture_hackrf.md`](capture_hackrf.md) for the exact `hackrf_transfer`
command (frequency, sample rate, bias-tee, gains, duration).

---

## Loading IQ in code
All stages load recordings through the shared helper
[`../bassanite/iq_loader.py`](../bassanite/iq_loader.py), which handles both formats,
scales to ~±1, and removes the DC offset:

```python
from bassanite.iq_loader import load_iq
x = load_iq("recordings/capture.bin", fmt="int8", fs=4e6, seconds=0.02)
```

Quick inspection:
```bash
python bassanite/iq_loader.py --iq recordings/capture.bin --seconds 0.1
```
