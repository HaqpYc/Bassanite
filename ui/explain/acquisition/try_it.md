---
title: Doppler error and integration time
code: ui/tabs/acquisition.py::signal_power, ui/tabs/acquisition.py::doppler_loss
---
## Intuition

**Doppler error.** Move the first slider and watch the peak shrink. It vanishes at
±1 kHz, then comes back a little (about 5 % of the power near ±1.4 kHz). Every value
you visit is measured on the real samples and drawn as a dot on the right, next to
the theoretical curve.

**Milliseconds added up.** Adding more milliseconds does *not* make the satellite's
peak taller compared with the average. It makes the noise smoother, so the random
spikes around the peak get smaller. A weak satellite that hides among the spikes with
1 ms stands out with 10 or 20 ms. Try it with a weak one (PRN 15 or 7 in
`capture.bin`), then with a strong one (PRN 20).

Why not simply add up the samples themselves for longer (coherently) instead of
adding powers? Two reasons: the data bits flip the signal's sign every 20 ms, which
would cancel the sum, and a longer coherent sum narrows the Doppler peak to $1/T$,
so the search would need many more Doppler steps.

## Math

**Power left** after a carrier error $\Delta f$, for a coherent sum over
$T = 1$ ms:

$$L(\Delta f) = \mathrm{sinc}^2(\Delta f\, T) = \left(\frac{\sin \pi \Delta f T}{\pi \Delta f T}\right)^2$$

with zeros at multiples of 1 kHz and a first side lobe of 0.047 (−13 dB) at
1.43 kHz. The dots are measured as

$$\hat L = \frac{S_{\Delta f}[\tau_0] - \overline{S_{\Delta f}}}{S_0[\tau_0] - \overline{S_0}}$$

where $\tau_0$ is the satellite's code phase. Subtracting the row average removes
the noise's share of the peak cell, so far from the peak $\hat L$ scatters around 0.

**Noise after $K$ ms.** A noise cell is a sum of $K$ exponential values: mean
$K\sigma^2$, standard deviation $\sqrt{K}\,\sigma^2$. Compared with the average, its
spread shrinks as $1/\sqrt{K}$. The satellite's cell grows like the average,
$K(A^2 + \sigma^2)$, so its peak/mean stays near $1 + A^2/\sigma^2$. In one Doppler
row of {{spms}} cells, the highest noise spike is about ${{row_floor_1:.1f}}\times$ the average for
$K = 1$, ${{row_floor_10:.1f}}\times$ for $K = 10$ and ${{row_floor_20:.1f}}\times$ for $K = 20$ (simulated).
