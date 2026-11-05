---
title: The carrier loop: from a ring to two blobs
code: bassanite/tracking.py::costas_error
---
## Intuition

Each millisecond, the prompt correlator gives one complex number, $I + jQ$: the
satellite's signal after removing our best guess of its carrier and its code. Each
dot is one millisecond.

- **Before the carrier loop locks**, our oscillator's phase drifts against the
  satellite's, so the dots turn around the origin and draw a **ring**.
- **Once locked**, the phase is held still and all the energy sits on the I axis.
  The dots form **two blobs**, at +I and −I: the two values of the data bit. Q only
  holds noise.

Press Play from the start: for PRN 20 in `capture.bin` the ring collapses into two
blobs in about 0.2 s (|Q|/|I| drops from 1.7 to 0.1).

The loop has to ignore the data bits. A bit change flips the signal by 180°, which
sends a dot from one blob to the other. A loop that tried to follow that would jump
every time. This is why it uses $\arctan(Q/I)$: it gives the same answer for a dot
and for its mirror image through the origin.

## Math

The carrier phase error, in cycles, measured each millisecond:

$$e_\phi = \frac{1}{2\pi}\arctan\!\left(\frac{Q_P}{I_P}\right)$$

A data flip turns $(I_P, Q_P)$ into $(-I_P, -Q_P)$, and $Q_P / I_P$ stays the same,
so $e_\phi$ does not see the bits. The price: the loop can also settle with the
phase upside down (I negative for a "1"). Then every bit comes out inverted; stage 04
fixes this with the preamble, which may appear as `10001011` or its inverse.
$\mathrm{atan2}(Q, I)$ would use the full circle and follow every flip.

**Noise on one measurement.** With $C/N_0$ the signal strength and $T = 1$ ms,
each measurement wanders by about

$$\sigma_{e_\phi} \approx \frac{1}{\sqrt{2\, (C/N_0)\, T}}\ \text{rad}$$

which is 8.9° at 43.2 dB-Hz (PRN 20; measured: 8.7°) and 18.9° at 36.6 dB-Hz
(PRN 15; measured: 20°). The loop filter averages many of them, so the phase it
holds is much steadier (see "Loop bandwidth").
