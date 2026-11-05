---
title: The samples themselves: why they look like noise
code: common/signal_view.py::raw_samples
---
## Intuition

These are the first 200 samples of the window, 50 µs at 4 MSPS: I and Q exactly as
recorded. They look like random noise because, sample by sample, they almost are.
Even the strongest satellite contributes about **1/200 of the noise power** to each
sample.

With HackRF files the values are whole ADC codes, so the curves jump between a few
levels.

All the GPS information is in there. It only emerges when thousands of samples are
combined with the right code and frequency, which is what the next step does.

## Math

Each sample is the sum of every satellite's signal $s_i$ and the noise $w$:

$$x[n] = \sum_i s_i[n] + w[n]$$

A satellite's strength is measured by its carrier-to-noise density ratio $C/N_0$
(dB-Hz). Complex sampling at $f_s$ spreads the noise over a bandwidth $f_s$, so the
signal-to-noise ratio of one sample is

$$\mathrm{SNR}_{\mathrm{sample}} = C/N_0 - 10 \log_{10} f_s$$

For the strongest satellite in the bundled recording (43 dB-Hz, see the Track tab)
at 4 MSPS: $43 - 66 = -23$ dB, so the signal is about 1/200 of the noise.

Correlating over $T = 1$ ms (4000 samples) adds them up coherently and gives

$$\mathrm{SNR}_{1\,\mathrm{ms}} = C/N_0 - 10 \log_{10}(1/T) = 43 - 30 = 13\ \mathrm{dB}$$

That gain of $10 \log_{10}(4000) \approx 36$ dB is how the satellites are found.
