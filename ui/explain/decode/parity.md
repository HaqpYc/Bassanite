---
title: Parity, and why the upside-down bits do not matter
code: bassanite/navigation.py::check_word, bassanite/navigation.py::parity_bits
---
## Intuition

Each word is 24 bits of data followed by 6 bits of parity. Each parity bit is the
XOR of about half of the data bits, a different half for each of the six. If one bit
is received wrong, at least one of the six comes out different from what was sent,
and the word is rejected (✗ next to it). A rejected word is not repaired: the
subframe is sent again 30 s later.

The parity also solves the polarity problem. The carrier loop cannot tell a signal
from its opposite, so half of the satellites come out with **every bit inverted**.
The satellite prepares for this: before sending the 24 data bits of a word, it XORs
them with the last bit of the previous word. The receiver XORs them again with that
same bit, as received.

- If the stream is upright, the two XORs cancel and the data come out.
- If the stream is inverted, the data bits and that last bit are both inverted. An
  XOR of two inverted bits is the same as the XOR of the two original bits. The data
  come out again, and the parity check still passes.

So the receiver never needs to know whether a satellite is inverted. The cards above
say which ones are, only because it can be read from the preamble afterwards.

## Math

Call $d_1 \dots d_{24}$ the data bits of a word and $D^\star_{29}, D^\star_{30}$ the
last two bits of the previous word, as sent. The satellite sends

$$D_i = d_i \oplus D^\star_{30} \quad (i = 1 \dots 24), \qquad
D_{24+j} = D^\star_{29\text{ or }30} \oplus \bigoplus_{i \in S_j} d_i \quad (j = 1 \dots 6)$$

where each $S_j$ is a fixed set of 13 to 15 data bits (table 20-XIV of the
specification IS-GPS-200, `PARITY_RULES` in the code). The receiver computes

$$\hat d_i = D_i \oplus D^\star_{30}$$

recomputes the six parity bits from $\hat d$ and compares them with $D_{25 \dots 30}$.

If every received bit is inverted, $D_i$ and $D^\star_{30}$ become
$\overline{D_i}$ and $\overline{D^\star_{30}}$, and

$$\overline{D_i} \oplus \overline{D^\star_{30}} = D_i \oplus D^\star_{30} = d_i$$

The recomputed parity bits each contain $D^\star_{29}$ or $D^\star_{30}$ once, so
they come out inverted, like the received ones: the comparison still holds.

The code detects every pattern of 1, 2 or 3 wrong bits among the 32 involved. A
random 30-bit word passes with probability $2^{-6} = 1/64$.
