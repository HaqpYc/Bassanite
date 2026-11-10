---
title: Gold codes: one code per satellite
code: bassanite/ca_code.py::register_states, ui/tabs/acquisition.py::circular_correlation
---
## Intuition

A satellite's code looks random, but a very simple machine makes it: two
**10-stage shift registers**, G1 and G2. At every tick each register moves its bits
one stage to the right, and stage 1 receives the XOR of a few stages (the dots on
the line above each register). With the right choice of stages, a 10-stage register
visits all 2¹⁰ − 1 = **1023** non-zero states before it repeats. That is why the
code is 1023 chips long.

Each chip is G1's last stage, XORed with **two stages of G2**. Which two stages
(amber) is the only thing that differs from one satellite to the next. Picking
different stages amounts to delaying G2 by a different amount, so every satellite
gets its own mix.

Why this matters: slide a code past a copy of itself and add up chip × chip. The sum
is huge (1023) only when the two are perfectly aligned. At any other shift, and
against another satellite's code at *any* shift, the sum stays tiny: −65, −1 or 63.
So when we look for PRN 20, the other satellites barely register, even though all of
them transmit at the same time on the same 1575.42 MHz. This is **CDMA**
(code-division multiple access).

## Math

Chips $c_k \in \{0, 1\}$ are transmitted as $s_k = 1 - 2c_k \in \{+1, -1\}$.

The registers' feedback follows two polynomials:

$$G_1 = 1 + x^3 + x^{10} \qquad G_2 = 1 + x^2 + x^3 + x^6 + x^8 + x^9 + x^{10}$$

so the new stage 1 of G1 is stage 3 ⊕ stage 10, and the new stage 1 of G2 is the XOR
of stages 2, 3, 6, 8, 9 and 10. Both registers start as all ones. Each chip is

$$c_k = g_{1,10}[k] \oplus g_{2,a}[k] \oplus g_{2,b}[k]$$

where $(a, b)$ is the satellite's pair from the GPS interface specification
(PRN 1: stages 2 and 6; PRN 20: stages 4 and 7).

**Circular correlation** of two codes at a shift of $\tau$ chips (indices wrap
around at 1023):

$$R_{ab}[\tau] = \sum_{k=0}^{1022} s_a[k + \tau]\, s_b[k]$$

For C/A codes, $R_{aa}[0] = 1023$, and every other value, $R_{aa}[\tau \ne 0]$
and $R_{ab}[\tau]$ for two different satellites, is one of $\{-65, -1, 63\}$. The
worst case, 65/1023, is 24 dB below the aligned peak. Every code has 512 ones and
511 zeros, so $\sum_k s_k = -1$.

The plot computes $R$ with the same FFT shortcut acquisition uses:
$R = \mathrm{IFFT}\{\mathrm{FFT}(s_a) \cdot \overline{\mathrm{FFT}(s_b)}\}$.
