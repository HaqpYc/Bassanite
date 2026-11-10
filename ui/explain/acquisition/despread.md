---
title: Why multiplying by the code makes the satellite appear
code: bassanite/acquisition.py::despread, common/signal_view.py::psd
---
## Intuition

The satellite transmits its carrier multiplied by its code, a sequence of +1 and −1
switching 1.023 million times a second. That fast switching is what spreads its power
over 2 MHz, so thinly that it sits below the noise (step 1).

Multiply the recording by the same code, lined up at the right moment, and every +1
meets a +1 and every −1 meets a −1: the code turns into a constant +1 and disappears.
What is left of the satellite is its carrier alone, a single tone at its Doppler
frequency. All its power is now in one narrow bin, and it stands far above the noise.

The noise is not lined up with anything. Multiplying it by ±1 leaves it noise, just as
flat as before. And shift the code by one chip or more and it no longer matches the
satellite's: the signal stays spread and the tone vanishes. This is why acquisition
must search the code phase.

It is also why the search runs over frequency. Once the code is removed, the satellite
is a tone at an unknown Doppler; finding the satellite means finding that tone.
Acquisition does it for every code phase at once with the FFT, one Doppler bin at a
time.

## Math

The received satellite signal, with code $c(t) = \pm 1$, data $d(t) = \pm 1$, Doppler
$f_D$ and noise $n(t)$:

$$x(t) = A\, d(t)\, c(t - \tau)\, e^{j 2 \pi f_D t} + n(t)$$

Multiplying by the replica aligned at $\tau$ uses $c^2 = 1$:

$$x(t)\, c(t - \tau) = A\, d(t)\, e^{j 2 \pi f_D t} + n(t)\, c(t - \tau)$$

The first term is a tone at $f_D$ (its sign can flip with a data bit, at most once in
these 10 ms). The second is still white noise of the same power. Over $T = 10$ ms, one FFT bin is $1/T = 100$ Hz wide, so it collects the whole
tone but only $1/T$ worth of noise: the tone stands about

$$\frac{C}{N_0}\, T \quad\text{above the noise, e.g. } 43\ \text{dB-Hz} + 10 \log_{10}(0.01\ \text{s}) = 23\ \text{dB}$$

Worked example: PRN 20 in `capture.bin` (43 dB-Hz) shows +22 dB at its Doppler;
misaligned by one chip, nothing rises above the usual noise peaks (about 10 dB above
the median in 200 bins). A data bit flip in the middle of the 10 ms splits the tone
and lowers it by up to 6 dB.
