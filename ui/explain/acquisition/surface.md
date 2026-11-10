---
title: Reading the search grid
code: bassanite/acquisition.py::correlation_surface, bassanite/acquisition.py::refine_doppler
---
## Intuition

**Along code phase**, the peak is narrow: about one chip on each side. While the
local copy is shifted by less than one chip it still partly overlaps the
satellite's code; beyond one chip the two are unrelated and the sum falls to the
noise.

**Along Doppler**, the peak is wider: zeros at ±1 kHz, then small side lobes. If the
frequency guess is off by $\Delta f$, the signal keeps turning while we add up 1 ms
of it. At $\Delta f$ = 1 kHz it makes exactly one full turn in 1 ms, and the sum
cancels to zero. This is why the search steps by 250 Hz: in the worst case the guess
is 125 Hz off, which costs only 5 % of the power.

After the search, a finer sweep in 10 Hz steps around the best bin gives the
"refined" Doppler. It is the reference for the Try-it sliders. Tracking makes its own
fine sweep before closing its loops.

The 3D view and the map keep the largest value of every 8 neighbouring code phases,
so the browser gets fewer numbers but the peak survives. The two slices below are at
full resolution.

## Math

**Code phase.** One sample is $1023 / 4000 = 0.256$ chip at 4 MSPS. One chip is
$c / 1.023\ \text{MHz} \approx 293$ m of range, so one sample is about 75 m. For a
code offset of $\Delta$ chips, the correlation is a triangle:

$$R(\Delta) \approx 1 - |\Delta| \quad (|\Delta| < 1), \qquad R(\Delta) \approx 0 \ \text{otherwise}$$

**Doppler.** A leftover frequency $\Delta f$ multiplies the sum by
$\frac{1}{N}\sum_n e^{j 2\pi \Delta f\, n / f_s}$, whose power is

$$L(\Delta f) \approx \mathrm{sinc}^2(\Delta f\, T) = \left(\frac{\sin \pi \Delta f T}{\pi \Delta f T}\right)^2, \qquad T = 1\ \text{ms}$$

with zeros at $\Delta f = \pm 1/T = \pm 1$ kHz. With 250 Hz steps the worst case is
$L(125\ \text{Hz}) = 0.95$ (−0.22 dB). The dotted curve on the Doppler slice is this
formula, centred on the refined Doppler.

**Doppler as a speed.** $v = \lambda f_D$ with $\lambda = c / f_{L1} = 0.190$ m:
+2500 Hz means the distance shrinks by about 475 m/s. Part of every Doppler here is
the HackRF's own clock error: 1 ppm of error shifts every satellite by 1575 Hz.
