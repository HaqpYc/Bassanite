---
title: Real satellites drift smoothly
code: bassanite/acquisition.py::persistence, bassanite/acquisition.py::code_drift_chips_per_s
---
## Intuition

A single scan can be fooled. With 32 satellites × 324,000 cells, a noise spike above
the threshold now and then is to be expected. The test noise cannot pass is
**persistence**: rescan at several moments and see what comes back.

- A **real satellite** comes back at nearly the same Doppler, and its code phase
  slides slowly and steadily, at a rate set by its Doppler.
- A **noise peak** lands at a random Doppler and a random code phase every time
  (the grey crosses).

The direction of the slide: a satellite coming closer (positive Doppler) delivers
each code period a little earlier than the one before, so its code phase in our 1 ms
window decreases. The table checks the measured slide against the rate predicted from
the Doppler alone.

## Math

Code and carrier come from the same clock on the satellite, so motion stretches both
by the same factor:

$$f_{\text{code}} = 1.023\ \text{MHz}\left(1 + \frac{f_D}{f_{L1}}\right)$$

The code phase therefore drifts by

$$\dot\tau = -1.023\ \text{MHz} \cdot \frac{f_D}{f_{L1}} = -\frac{f_D}{1540}\ \ \text{chips/s}$$

PRN 20 in `capture.bin` (+2500 Hz): −1.62 chips/s, so −45 chips over 28 s, or
−178 samples. The stage 02 notes measured 3979 → 3804 samples (−175). In metres,
−1.62 chips/s × 293 m/chip ≈ −475 m/s $= -\lambda f_D$: the same speed seen
through the code instead of the carrier.

The HackRF's clock error changes nothing here: a clock off by $\varepsilon$ shifts the
carrier by $-\varepsilon f_{L1}$ and the code timing by $+\varepsilon \cdot$
1.023 Mchip/s, which keeps exactly the same ratio.

Code phases are measured from the start of the recording, $(n_0 + \tau)$ samples
modulo 1 ms, so windows starting mid-millisecond still line up. The measured drift
is the slope of a straight-line fit to the unwrapped code phase.
