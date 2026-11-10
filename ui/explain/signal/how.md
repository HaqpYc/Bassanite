---
title: From radio wave to IQ samples
code: bassanite/iq_loader.py::load_iq
---
## Intuition

The SDR mixes the incoming wave with its own oscillator at 1575.42 MHz. This shifts
the GPS band down to around 0 Hz ("baseband"), where it can be sampled. It does this
twice, with oscillators 90° apart, giving two streams: **I** (in-phase) and **Q**
(quadrature). Together they form one complex number per sample, which keeps both the
amplitude and the phase of the signal, and tells frequencies just above 1575.42 MHz
apart from those just below.

The HackRF stores I and Q as signed 8-bit integers. Bassanite scales them to about
±1 and subtracts the constant offset (DC) that the radio front-end adds, which would
otherwise show up as a fake spike at 0 Hz.

Why can't you see GPS? The thermal noise in the receiver's bandwidth is far stronger
than the satellites, which are 20,000 km away and transmit with about the power of a
light bulb.

## Math

The antenna receives a real wave around the carrier $f_c = 1575.42$ MHz:

$$r(t) = \mathrm{Re}\left\{ x(t)\, e^{j 2 \pi f_c t} \right\}$$

The SDR recovers the complex baseband signal $x(t)$ and samples it at
$f_s = 4$ MSPS:

$$x[n] = I[n] + j\,Q[n]$$

Complex sampling covers $-f_s/2$ to $+f_s/2$, so ±2 MHz around L1. The GPS C/A
signal's main lobe is ±1.023 MHz, so it fits.

**Scaling and DC removal** (HackRF int8 format, $N$ samples loaded):

$$x[n] = \frac{i_n + j\,q_n}{128}, \qquad x[n] \leftarrow x[n] - \frac{1}{N}\sum_{k=0}^{N-1} x[k]$$

**Noise budget.** Thermal noise power is $kTB$, which is $-174$ dBm/Hz at room
temperature. Over $B = 2$ MHz:

$$P_N = -174 + 10 \log_{10}(2 \times 10^6) \approx -111 \text{ dBm}$$

The GPS C/A signal is specified at a minimum of $-128.5$ dBm at the ground, so it
arrives roughly 15–20 dB **below** the noise floor.
