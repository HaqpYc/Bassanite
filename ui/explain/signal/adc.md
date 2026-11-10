---
title: ADC levels: is the gain right?
code: bassanite/signal_view.py::adc_stats
---
## Intuition

The HackRF's ADC turns each I and Q value into an 8-bit integer: 256 levels, from
−128 to 127. The gains (the antenna's LNA, the HackRF's LNA and VGA) decide how wide
the noise is compared with this range.

- **Too much gain**: samples pile up at −128 and 127. This is **clipping**: it
  distorts the signal and can create false signals.
- **Too little gain**: the noise covers only a few levels, and rounding to whole
  codes adds some noise of its own (**quantisation noise**).

GPS is very tolerant of coarse quantisation, because the signal is far below the
noise anyway. Many GPS receiver chips use only 1 or 2 bits per sample.

## Math

Rounding to a step $\Delta$ (one ADC code) adds an error roughly uniform over
$\pm\Delta/2$, with power

$$\sigma_q^2 = \frac{\Delta^2}{12}$$

Next to thermal noise of spread $\sigma$ (in codes), the loss in signal-to-noise
ratio is approximately

$$L \approx 10 \log_{10}\left( 1 + \frac{\Delta^2}{12\, \sigma^2} \right)$$

For $\sigma = 1$ code, $L \approx 0.35$ dB: small. The extreme case is keeping only
the sign of each sample (1 bit), which costs $10 \log_{10}(\pi/2) \approx 1.96$ dB
for signals below the noise.

**Clipping fraction**: the share of samples where I or Q equals −128 or 127. For
Gaussian noise of spread $\sigma$ and limit $A$, each component clips with
probability $2\,Q(A/\sigma)$, where $Q$ is the Gaussian tail function.

**Levels used**: the number of distinct codes that occur in I and Q.
