---
title: Peak/mean: is this satellite here?
code: bassanite/acquisition.py::scan, bassanite/acquisition.py::acquire_prn, bassanite/acquisition.py::cn0_from_peak
---
## Intuition

For each satellite, the search fills a grid of guesses: 81 Doppler values × 4000 code
phases = 324,000 cells. If the satellite is there, one cell is far bigger than the
rest. One number summarises the grid: the **biggest cell divided by the average**.

Noise alone does not give 1. The largest of 324,000 random values is always
somewhat above their average: about **3** with 10 ms of signal (the dotted line).
The threshold of 5 leaves a margin above that. A bar only just above it, like PRN 7
in `capture.bin` (5.4), is a candidate rather than a certainty, and the persistence
check decides. PRN 7 passes it (same Doppler all along, code drifting exactly as its
Doppler predicts), so it is a real satellite, and tracking locks it.

The height of a bar also tells how strong the satellite is. Roughly, peak/mean − 1 is
the satellite's signal-to-noise ratio after 1 ms, so it gives a first estimate of
C/N0 (below). Tracking then measures C/N0 properly.

## Math

With $S_f[\tau]$ the accumulated correlation power (see "How a buried satellite is
found"), each Doppler bin $f$ gets a ratio, and the best bin gives the result:

$$r_f = \frac{\max_\tau S_f[\tau]}{\overline{S_f}} \qquad
\text{peak/mean} = \max_f r_f$$

**Noise floor.** With noise only, each correlation output is a complex Gaussian, so
$|R|^2$ is exponentially distributed. After $K$ ms, a cell is a sum of $K$ of them:
mean $K\sigma^2$, standard deviation $\sqrt{K}\,\sigma^2$. The biggest of the
324,000 cells is about $13\times$ the average for $K = 1$, $4.5\times$ for $K = 5$,
$3.2\times$ for $K = 10$ and $2.4\times$ for $K = 20$ (simulated). In `capture.bin`
the 24 undetected PRNs sit between 2.9 and 3.5.

**C/N0 from the peak.** A coherent sum over $T = 1$ ms raises the signal-to-noise
ratio of the aligned cell to $C/N_0 \cdot T$. The average is almost all noise, so

$$\text{peak/mean} \approx 1 + \frac{C}{N_0}\,T
\quad\Rightarrow\quad
\frac{C}{N_0} \approx 10 \log_{10}\frac{\text{peak/mean} - 1}{10^{-3}\ \text{s}}\ \text{dB-Hz}$$

PRN 20 in `capture.bin`: 23.3 → 43.5 dB-Hz; tracking measures 43.2 dB-Hz. Expect
±2 dB: the code phase and Doppler grids rarely hit the peak exactly.
