---
title: How a buried satellite is found
code: bassanite/acquisition.py::correlation_surface, bassanite/acquisition.py::acquire_prn
---
## Intuition

For each satellite (PRN 1 to 32) there are two unknowns:

- **Code phase**: where the satellite's 1 ms code starts inside our 1 ms window
  (0 to 1022 chips). It depends on how long the signal travelled.
- **Doppler**: how far the carrier is shifted from 1575.42 MHz, because the
  satellite moves (up to about ±5 kHz) and the HackRF's clock is not perfect. We
  search ±10 kHz in 250 Hz steps.

For each Doppler guess we remove that frequency, then compare the signal with the
satellite's code at **all code phases at once**, using the FFT. We add up 10 ms of
results so that noise averages out while the satellite keeps adding up in the same
place. If one cell of the grid stands far above the average, the satellite is there.

## Math

**Carrier wipe-off** for a Doppler guess $f$:

$$y_f[n] = x[n]\, e^{-j 2 \pi f n / f_s}$$

**Circular correlation** with the local code replica $c$ over one 1 ms period
($N = 4000$ samples at 4 MSPS), for every code phase $\tau$ at once:

$$R_f[\tau] = \mathrm{IFFT}\left\{ \mathrm{FFT}(y_f) \cdot \overline{\mathrm{FFT}(c)} \right\}$$

**Non-coherent accumulation** over $K = 10$ periods. Squaring the magnitude makes
the sum insensitive to the data-bit sign flips (every 20 ms) and to leftover phase
rotation:

$$S_f[\tau] = \sum_{k=0}^{K-1} \left| R_{f,k}[\tau] \right|^2$$

**Detection metric**: for each Doppler bin, the highest code-phase cell against the
average of that bin. The bin with the largest ratio gives the Doppler and code-phase
estimates:

$$\text{peak/mean} = \max_f \frac{\max_\tau S_f[\tau]}{\overline{S_f}}$$

Pure noise gives about 3; the default detection threshold is 5.
