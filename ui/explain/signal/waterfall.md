---
title: Waterfall: the spectrum over time
code: bassanite/signal_view.py::waterfall
---
## Intuition

A waterfall stacks spectra on top of each other. Each row is the spectrum at one
moment, colour is power, and time runs downward across the whole recording.

It shows what a single averaged spectrum hides: interference that comes and goes, a
signal that drifts in frequency, or a sudden change of gain. A steady, even texture
from top to bottom means the recording conditions did not change.

GPS won't show here either. Besides being below the noise, a satellite's Doppler
changes by less than 1 Hz per second, far smaller than one bin (7.8 kHz).

## Math

Row $r$ is a spectrum (see the Spectrum panel) computed from a short block starting
at time $t_r$. The $R = 150$ rows are spread evenly over the recording of length $T$:

$$t_r = \frac{r}{R - 1}\left(T - T_{\mathrm{block}}\right), \qquad r = 0, \dots, R - 1$$

Each block is $K = 8$ FFTs of $N = 512$ samples:

$$T_{\mathrm{block}} = \frac{K N}{f_s} \approx 1\ \mathrm{ms}, \qquad \Delta f = \frac{f_s}{N} \approx 7.8\ \mathrm{kHz}$$

Reading only these blocks keeps it fast: about 0.6 million of the recording's 120
million samples. The colour scale spans the 1st to 99.9th percentile of all values,
so a single hot cell can't wash out the rest.
