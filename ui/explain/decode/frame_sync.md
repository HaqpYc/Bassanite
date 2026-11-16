---
title: Finding where the subframes start
code: bassanite/navigation.py::find_subframes
---
## Intuition

The plot shows every bit of the satellite, 300 per row. The rows start wherever
tracking happened to start, not at a subframe. Even so, the amber preambles sit in
one column: subframes follow each other exactly 300 bits apart, so each one starts
at the same place in its row. That column is the frame synchronisation.

Finding it takes more than looking for `10001011`:

- Eight bits are a short pattern. In 3000 bits of data it turns up by chance about
  a dozen times. The red crosses mark these chance matches.
- The stream may be inverted, and then the preamble arrives as `01110100`. The
  search handles both at once, because it decodes each word relative to the bit
  before it (see the parity panel below).
- What tells a true start from a chance match is what follows it. A true start is
  followed by two words whose parity is right, and the second one holds a subframe
  number between 1 and 5.
- A chance match that passes all this by luck is still caught: it would not sit on
  the 300-bit grid of the others.

## Math

Each word carries 6 parity bits, so random bits pass one word's parity check with
probability $2^{-6} = 1/64$. At a given position, random data show the preamble with
probability $2^{-8}$. A false start must show the preamble, pass two parity checks
and hold a valid subframe number (5 values out of 8):

$$P_{\text{false}} \approx 2^{-8} \cdot \left(2^{-6}\right)^2 \cdot \tfrac{5}{8}
\approx 6 \times 10^{-7} \text{ per bit position}$$

Over a 60 s recording (3000 positions) that is a chance of about 0.2 % of one false
start, before the grid test removes it. The preamble alone, by contrast, is expected
$3000 \cdot 2^{-8} \approx 12$ times.

A true start is missed only if one of the 62 bits involved is wrong. For a locked
satellite that practically never happens: a bit is the sum of 20 ms of signal, so its
signal-to-noise ratio is

$$\frac{E_b}{N_0} = \frac{C}{N_0} \cdot 20\ \mathrm{ms}
\qquad \text{(37 dB-Hz gives 20 dB)}$$
