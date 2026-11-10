---
title: Doppler and code drift: what the loops follow
code: bassanite/tracking.py::LoopFilter.update, bassanite/tracking.py::calc_loop_coef
---
## Intuition

**Doppler** (left) is the carrier frequency the carrier loop settled on. It moves
slowly as the satellite's speed along our line of sight changes: by 3 to 18 Hz over
the 30 s of `capture.bin`. The jitter on top is noise the loop lets through.

**Code drift** (right) is how far the start of the code has slid since the first
millisecond, in chips. It slides steadily, at the rate the Doppler sets: the same
check as the persistence test of step 2, now measured every millisecond. For PRN 20
in `capture.bin`: −47.9 chips over 30 s, against −47.3 predicted from its Doppler.

Both loops use the same kind of filter: it reacts to the latest error (to correct
quickly) and to the sum of past errors (to remember a steady frequency offset). Its
**bandwidth** sets how fast it reacts: 12 Hz for the carrier, 1 Hz for the code.
The code loop can be that slow because carrier aiding already does most of its
work.

## Math

Each loop turns its error $e_k$ (one per millisecond, $T = 1$ ms) into a
correction $u_k$ with a second-order filter:

$$u_k = u_{k-1} + \frac{\tau_2}{\tau_1}\,(e_k - e_{k-1}) + \frac{T}{\tau_1}\, e_k$$

The carrier frequency is then $f_k = f_{\text{seed}} + u_k$; the code rate is the
carrier-aided rate minus the DLL's $u_k$. From the noise bandwidth $B_L$, the damping
$\zeta = 0.7$ and the gain $K$:

$$\omega_n = \frac{8\zeta B_L}{4\zeta^2 + 1}, \qquad \tau_1 = \frac{K}{\omega_n^2},
\qquad \tau_2 = \frac{2\zeta}{\omega_n}$$

PLL ($B_L$ = 12 Hz, $K$ = 0.25): $\omega_n$ = 22.7 rad/s, $\tau_2/\tau_1$ = 127,
$T/\tau_1$ = 2.06. DLL ($B_L$ = 1 Hz, $K$ = 1): $\omega_n$ = 1.89 rad/s,
$\tau_2/\tau_1$ = 2.65, $T/\tau_1$ = 0.0036.

**Code drift** plotted here: with $n_m$ the (fractional) sample where code period $m$
starts and $N = f_s \cdot 1$ ms,

$$\text{drift}_m = \left(n_m - n_0 - mN\right)\frac{1023}{N}\ \text{chips}, \qquad
\frac{d}{dt}\text{drift} \approx -\frac{1.023\ \text{MHz}\cdot f_D}{f_{L1}}$$
