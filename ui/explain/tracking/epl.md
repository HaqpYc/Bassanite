---
title: The code loop: early, prompt and late
code: bassanite/tracking.py::dll_error
---
## Intuition

The code loop keeps our copy of the satellite's code lined up with the incoming one.
It correlates the signal with three copies at once:

- **Prompt**: our best guess. This is the one that carries the data.
- **Early** and **Late**: the same code shifted by half a chip either way.

Remember the correlation peak from step 2: a triangle one chip wide on each side.
When the prompt sits exactly on top, early and late fall on the two slopes at the
same height. If our copy drifts ahead, one side gets stronger and the other weaker,
and the loop nudges the copy back. Balanced early and late means centred.

In `capture.bin`, early and late come out at about 0.55–0.65 of the prompt instead
of the ideal 0.5. The receiver's limited bandwidth rounds the tip of the triangle,
and noise adds to all three, more so for weak satellites. What matters is that they
are equal.

## Math

With $E$, $P$, $L$ the complex correlator outputs, the code error is

$$e_\tau = \frac{|E| - |L|}{|E| + |L|}$$

Dividing by $|E| + |L|$ makes the error independent of the signal strength. Using
magnitudes makes it independent of the carrier phase and the data bits.

In this code the Early replica reads the code half a chip *behind* the prompt, so
$e_\tau > 0$ means our copy runs ahead of the signal. The loop then slows the
code down:

$$f_{\text{code}} = 1.023\ \text{MHz}\left(1 + \frac{f_D}{f_{L1}}\right) - \Delta_{\text{DLL}}$$

The first term is **carrier aiding**: the carrier loop's Doppler already says how
much the code is stretched, so the DLL only trims a tiny remainder
$\Delta_{\text{DLL}}$ (filtered from $e_\tau$ with a 1 Hz loop).

For an ideal triangle $R(\Delta) = 1 - |\Delta|$, a centred prompt gives
$|E| = |L| = 0.5\,|P|$. If the peak sits $\delta$ chips off the prompt
($|\delta| < 0.5$), one side reads $0.5 + \delta$ and the other $0.5 - \delta$, so
$|e_\tau| = 2|\delta|$: the error grows in proportion to the misalignment.
