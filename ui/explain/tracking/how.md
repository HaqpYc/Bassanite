---
title: Two loops that follow a moving satellite
code: bassanite/tracking.py::track_prn
---
## Intuition

Acquisition gives a rough starting point. Tracking then follows the satellite one
millisecond at a time:

- The **carrier loop (Costas PLL)** keeps our local oscillator aligned with the
  satellite's carrier phase. It is designed to ignore the 180° flips caused by data
  bits, so the data doesn't knock it off.
- The **code loop (DLL)** compares the signal with three copies of the code: one
  slightly early, one on time (prompt), one slightly late. When the prompt copy is
  centred, early and late are equally strong. If they differ, the loop nudges the
  copy toward the stronger side.

When both loops are locked, all the signal's energy ends up in one number per
millisecond, the prompt **I**. Its sign is the data: it stays constant for 20 ms,
then may flip. Grouping 20 ms at a time gives one bit.

## Math

**Prompt correlator** for one 1 ms epoch, with Doppler estimate $f_D$, carrier phase
$\phi$ and code replica $c$ at code phase $\tau_n$:

$$P = I_P + j\,Q_P = \sum_n x[n]\, e^{-j (2 \pi f_D n / f_s + \phi)}\, c(\tau_n)$$

**Costas carrier discriminator** (in cycles). It uses $\arctan$, not $\mathrm{atan2}$,
so a 180° data flip gives the same error:

$$e_\phi = \frac{1}{2\pi} \arctan\!\left( \frac{Q_P}{I_P} \right)$$

**Code discriminator**, with Early and Late correlators at ∓0.5 chip:

$$e_\tau = \frac{|E| - |L|}{|E| + |L|}$$

**Carrier aiding.** Code and carrier are stretched by the same motion, so the code
rate follows the carrier Doppler, and the DLL only trims a small residual
$\Delta_{\mathrm{DLL}}$:

$$f_{\mathrm{code}} = 1.023\ \mathrm{MHz} \cdot \left( 1 + \frac{f_D}{f_{L1}} \right) - \Delta_{\mathrm{DLL}}$$

Both errors go through second-order loop filters (defaults: PLL 12 Hz, DLL 1 Hz,
damping 0.7).

**Bits.** With the bit-edge offset $o$ found by bit sync, each bit is the sign of 20
consecutive prompt values:

$$b_k = \mathrm{sign}\left( \sum_{m=0}^{19} I_P[20k + m + o] \right)$$

**Lock indicator**: $|Q_P| / |I_P|$ well below 0.3 means the energy sits on I, so
the carrier is phase-locked.
