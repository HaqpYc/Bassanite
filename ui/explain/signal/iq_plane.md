---
title: The IQ plane: what noise looks like
code: ui/tabs/signal.py::_iq_density
---
## Intuition

Each sample is drawn as a point, with I across and Q up; darker cells hold more
samples. Pure noise gives a **round cloud**: I and Q are independent and have the
same spread, so no direction is preferred.

What problems would look like:

- **Off-centre cloud**: a DC offset. The bundled recording has one. Turn off
  "Remove DC offset" (Expert mode) to see the cloud move.
- **Ellipse instead of a circle**: I and Q have different gains (IQ imbalance).
- **Flat, square edges**: clipping at the ADC limits.
- **A ring**: a strong single-frequency signal, i.e. interference.

GPS adds nothing visible. With HackRF files, the cloud is a grid of points, because
I and Q can only take whole ADC codes.

## Math

Receiver noise is complex Gaussian: I and Q are independent, each with variance
$\sigma^2$. Their joint density depends only on the distance from the centre, so
its contours are circles:

$$p(I, Q) = \frac{1}{2 \pi \sigma^2} \exp\left( -\frac{I^2 + Q^2}{2 \sigma^2} \right)$$

A DC offset $\mu = \mu_I + j\,\mu_Q$ moves the centre to $(\mu_I, \mu_Q)$. It is
estimated as the mean and subtracted:

$$\hat{\mu} = \frac{1}{N} \sum_{n=0}^{N-1} x[n], \qquad x[n] \leftarrow x[n] - \hat{\mu}$$

IQ balance can be checked in the numbers above the plots: the spreads
$\sigma_I$ and $\sigma_Q$ should be equal.
