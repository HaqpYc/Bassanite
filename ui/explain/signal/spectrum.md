---
title: Spectrum: where the energy is
code: bassanite/signal_view.py::psd
---
## Intuition

The spectrum shows how much power there is at each frequency around the tuned
frequency (1575.42 MHz). The FFT splits a block of $N$ samples into $N$ frequency
**bins**, each $f_s/N$ wide: about {{bin_khz:.1f}} kHz for $N = {{spectrum_nfft}}$ at {{fs_msps:g}} MSPS. The spectrum of
a single block of noise is very jagged, so we average the spectra of many blocks.

What a healthy GPS recording looks like:

- **A flat noise floor in the middle.** This is thermal noise, amplified by the
  antenna's LNA and the HackRF.
- **Edges sloping down.** This is the HackRF's anti-aliasing filter, which removes
  signals outside the band before sampling.
- **No GPS.** The shaded band is where the GPS C/A signal is, spread over about
  ±1 MHz, but its power is 15–20 dB below the noise.
- **No narrow spikes.** A spike would be interference, or the DC offset at the
  centre if it is not removed. Even after removal, a bump of a few dB can remain in
  the centre bin: the offset drifts slightly, and only its average is subtracted.

## Math

Split the window into $M$ blocks of $N$ samples, multiply each by a Hann window
$w[n]$, and take the FFT:

$$X_m[k] = \sum_{n=0}^{N-1} w[n]\, x[mN + n]\, e^{-j 2 \pi k n / N}$$

Average the power over the blocks. Dividing by $(\sum w)^2$ makes a full-scale tone
(amplitude 1) on a bin read 0 dBFS:

$$P[k] = \frac{1}{M} \sum_{m=0}^{M-1} \frac{|X_m[k]|^2}{\left(\sum_n w[n]\right)^2}, \qquad P_{\mathrm{dB}}[k] = 10 \log_{10} P[k]$$

After re-centring, bin $k$ sits at $f_k = f_c + (k - N/2)\, f_s / N$.

**Why the noise floor depends on the FFT size.** For white noise of power
$\sigma^2$, each bin receives

$$P_{\mathrm{noise}} = \sigma^2 \frac{\sum_n w[n]^2}{\left(\sum_n w[n]\right)^2} \approx \frac{1.5\, \sigma^2}{N} \quad \text{(Hann window)}$$

so the floor drops 3 dB each time $N$ doubles. A narrow tone keeps its level and
stands out more. GPS is spread over 2 MHz, so its power per bin drops exactly like
the noise: **no FFT size can make it appear.** Only correlation with the satellite's
code can: step 2 opens with that moment, "The moment GPS appears".
